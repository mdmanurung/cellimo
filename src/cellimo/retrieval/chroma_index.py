"""Reading the inherited KAI knowledge index.

Ported from KAI (https://github.com/davidfischerlab/kai), Apache-2.0 —
``kai/retrieval/snippets/storage/chromadb_manager.py`` and
``kai/retrieval/workflow_summaries/{notebook_storage,summary_search}.py``. The
LLM-driven selection layer around them is not ported: this module ranks and
returns, and Codex or Claude decides what to use.

The published archive (Zenodo record 17660667, ``kai_retrieval_251121.zip``)
contains two separate ChromaDB instances built with different embedding
functions, plus a filesystem notebook store:

``chromadb/``
    249 collections named ``{org}_{repo}_workflows``, 100,999 chunks, built with
    ``sentence-transformers/all-MiniLM-L6-v2`` **passed explicitly**. ChromaDB
    does not persist which embedding function built a collection, so it has to be
    supplied again on every open or the query embeddings will not match.

``notebook_summaries/summary_index/``
    A ``notebook_summaries`` collection built with ChromaDB's *default* (ONNX)
    embedding function. Its ``notebook_id`` metadata matches the filesystem
    store exactly, which is why workflow search goes through here: the ids are
    stable and resolvable.

``notebook_summaries/notebooks/{org}/{repo}/{notebook_id}.json``
    The notebooks themselves, as ``cells: [{cell_type, content, section, order}]``.

Two honest limitations, both verified against the published archive rather than
assumed, are reported through the API instead of being papered over:

* there are **no documentation collections** in the published index (all 249
  are ``content_type='workflows'``), so ``search_documentation`` returns an
  empty result with an explanation;
* there is **no modality field** and the package field is the repository name
  rather than an importable package, so those filters are marked approximate.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cellimo.errors import ReferenceNotFoundError, RetrievalError
from cellimo.retrieval.base import KnowledgeIndex
from cellimo.retrieval.diversify import diversify
from cellimo.retrieval.ids import (
    chunk_reference_id,
    notebook_reference_id,
    parse_reference_id,
)
from cellimo.retrieval.models import (
    IndexStatus,
    Reference,
    ReferenceSection,
    SearchHit,
    SearchResult,
    bound_sections,
)
from cellimo.util.hashing import hash_bytes

__all__ = ["EMBEDDING_MODEL", "SUMMARY_COLLECTION", "ChromaKnowledgeIndex"]

#: The model KAI built the main index with. Changing it silently degrades every
#: search, so it is stated here and reported by ``index_status``.
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
SUMMARY_COLLECTION = "notebook_summaries"

#: How many candidates to fetch per requested hit. Anything that narrows results
#: afterwards — dedup, a per-repository cap — needs a pool to backfill from, or
#: it can only shrink the answer.
OVER_FETCH = 4
_WORKFLOW_SUFFIX = "_workflows"
_API_SUFFIX = "_api"


class ChromaKnowledgeIndex(KnowledgeIndex):
    """Read-only access to a KAI-format knowledge index.

    Everything expensive — the ChromaDB clients, the embedding model, the
    notebook path map — is built once in ``__init__`` and reused, because this
    object lives for the lifetime of the MCP server process.
    """

    backend = "chroma"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise RetrievalError(f"retrieval index directory {self.root} does not exist")

        import chromadb

        self._chromadb = chromadb
        self._main_client: Any = None
        self._summary_client: Any = None
        self._summary_collection: Any = None
        self._embedding_function: Any = None

        main_path = self.root / "chromadb"
        if main_path.is_dir():
            self._main_client = chromadb.PersistentClient(
                path=str(main_path),
                settings=chromadb.config.Settings(anonymized_telemetry=False),
            )

        summary_path = self.root / "notebook_summaries" / "summary_index"
        if summary_path.is_dir():
            self._summary_client = chromadb.PersistentClient(path=str(summary_path))
            try:
                self._summary_collection = self._summary_client.get_collection(
                    SUMMARY_COLLECTION
                )
            except Exception:  # collection genuinely absent
                self._summary_collection = None

        if self._main_client is None and self._summary_collection is None:
            raise RetrievalError(
                f"{self.root} contains neither chromadb/ nor "
                f"notebook_summaries/summary_index/"
            )

        self._notebook_paths = self._map_notebooks()
        self._registry = self._load_registry()

    # -- loading -----------------------------------------------------------

    def _map_notebooks(self) -> dict[str, Path]:
        """Map ``notebook_id`` to its JSON file, once.

        The store is ``notebooks/{org}/{repo}/{notebook_id}.json``; the id is
        unique across the tree, so a flat map is enough and avoids re-globbing
        3,000-odd paths per lookup.
        """
        base = self.root / "notebook_summaries" / "notebooks"
        if not base.is_dir():
            return {}
        return {path.stem: path for path in base.rglob("*.json")}

    def _load_registry(self) -> dict[str, Any]:
        path = self.root / "collection_registry.json"
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _get_embedding_function(self) -> Any:
        """Build the sentence-transformers embedding function on first use.

        Loading the model takes seconds and hundreds of megabytes, so it is
        deferred until a query actually needs the main index — ``index_status``
        and notebook lookups never pay for it.
        """
        if self._embedding_function is None:
            from chromadb.utils import embedding_functions

            self._embedding_function = (
                embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name=EMBEDDING_MODEL
                )
            )
        return self._embedding_function

    def _collection_names(self) -> list[str]:
        if self._main_client is None:
            return []
        try:
            return sorted(collection.name for collection in self._main_client.list_collections())
        except Exception as exc:  # pragma: no cover - corrupt index
            raise RetrievalError(f"cannot list collections in {self.root}: {exc}") from exc

    # -- search ------------------------------------------------------------

    def search_workflows(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        modalities: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        if self._summary_collection is None:
            return self._search_chunks(query, packages=packages, top_k=top_k)

        # Over-fetch unconditionally. This used to widen only when `packages` or
        # `modalities` were set, which meant the ordinary no-filter call — every
        # call the MCP tool makes by default — asked Chroma for exactly `top_k`
        # and left nothing to draw on. Any later step that drops a candidate
        # (deduplicating checkpoint copies, limiting one repository's share)
        # could then only return fewer results, never better ones.
        fetch = max(top_k * OVER_FETCH, top_k)
        try:
            raw = self._summary_collection.query(query_texts=[query], n_results=max(1, fetch))
        except Exception as exc:
            raise RetrievalError(f"summary index query failed: {exc}") from exc

        hits: list[SearchHit] = []
        ids = (raw.get("ids") or [[]])[0]
        documents = (raw.get("documents") or [[]])[0]
        metadatas = (raw.get("metadatas") or [[]])[0]
        distances = (raw.get("distances") or [[]])[0]
        for index, notebook_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            summary = documents[index] if index < len(documents) else ""
            distance = distances[index] if index < len(distances) else None
            repository = str(metadata.get("source_repository", ""))
            organization = repository.split("/", maxsplit=1)[0] if "/" in repository else ""
            hits.append(
                SearchHit(
                    reference_id=notebook_reference_id(
                        str(metadata.get("notebook_id", notebook_id))
                    ),
                    title=str(metadata.get("title", "")),
                    summary=_truncate(summary or "", 1200),
                    source_repository=repository,
                    source_path=str(metadata.get("full_notebook_id", "")),
                    url=_github_url(repository, str(metadata.get("full_notebook_id", ""))),
                    package=_package_from_repository(repository),
                    organization=organization,
                    score=_score_from_distance(distance),
                    section_ids=[],
                    content_hash=hash_bytes((summary or "").encode("utf-8")),
                    chunk_level="document",
                )
            )

        total = len(hits)
        approximate: list[str] = []
        if packages:
            approximate.append("packages")
            wanted = {value.lower() for value in packages}
            hits = [
                hit
                for hit in hits
                if any(
                    value in hit.source_repository.lower() or value in hit.package.lower()
                    for value in wanted
                )
            ]
        if modalities:
            approximate.append("modalities")
            wanted_modalities = [value.lower() for value in modalities]
            hits = [
                hit
                for hit in hits
                if any(
                    value in (hit.title + " " + hit.summary).lower()
                    for value in wanted_modalities
                )
            ]

        hits, filtered = diversify(hits, top_k=top_k)

        notes = []
        if approximate:
            notes.append(
                "package and modality filters are best-effort: the index records the "
                "source repository, not an importable package name, and has no "
                "modality field"
            )
        if filtered:
            notes.append(filtered)
        note = "; ".join(notes)
        return SearchResult(
            query=query,
            hits=hits,
            backend=self.backend,
            approximate_filters=approximate,
            note=note,
            total_candidates=total,
        )

    def _search_chunks(
        self,
        query: str,
        *,
        packages: Sequence[str] | None,
        top_k: int,
    ) -> SearchResult:
        """Fall back to chunk-level search across workflow collections.

        Used only when the summary index is absent. Chunk ids resolve through
        ``chunk:<collection>:<id>``, which is a direct ``get(ids=[…])`` lookup.
        """
        names = [name for name in self._collection_names() if name.endswith(_WORKFLOW_SUFFIX)]
        if packages:
            wanted = {value.lower().replace("-", "_") for value in packages}
            names = [
                name
                for name in names
                if any(value in name.lower().replace("-", "_") for value in wanted)
            ]
        if not names:
            return SearchResult(
                query=query,
                hits=[],
                backend=self.backend,
                note="no workflow collections matched the requested packages",
            )

        embedding_function = self._get_embedding_function()
        per_collection = max(2, top_k // max(1, min(len(names), 8)))
        hits: list[SearchHit] = []
        for name in names[:32]:  # bound the fan-out; 249 collections is too many per query
            try:
                collection = self._main_client.get_collection(  # type: ignore[union-attr]
                    name, embedding_function=embedding_function
                )
                raw = collection.query(query_texts=[query], n_results=per_collection)
            except Exception:
                continue
            ids = (raw.get("ids") or [[]])[0]
            documents = (raw.get("documents") or [[]])[0]
            metadatas = (raw.get("metadatas") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]
            for index, chunk_id in enumerate(ids):
                metadata = metadatas[index] if index < len(metadatas) else {}
                content = documents[index] if index < len(documents) else ""
                distance = distances[index] if index < len(distances) else None
                hits.append(
                    SearchHit(
                        reference_id=chunk_reference_id(name, str(chunk_id)),
                        title=str(metadata.get("chunk_id", chunk_id)),
                        summary=_truncate(content, 1200),
                        source_repository=(
                            f"{metadata.get('organization', '')}/{metadata.get('library', '')}"
                        ).strip("/"),
                        package=str(metadata.get("library", "")),
                        package_version=str(metadata.get("version", "")),
                        organization=str(metadata.get("organization", "")),
                        score=_score_from_distance(distance),
                        content_hash=hash_bytes(content.encode("utf-8")),
                        chunk_level=str(metadata.get("chunk_level", "")),
                    )
                )
        hits.sort(key=lambda hit: (-hit.score, hit.reference_id))
        return SearchResult(
            query=query,
            hits=hits[:top_k],
            backend=self.backend,
            approximate_filters=["packages"] if packages else [],
            note="chunk-level search (no summary index installed)",
            total_candidates=len(hits),
        )

    def search_documentation(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        names = [name for name in self._collection_names() if name.endswith(_API_SUFFIX)]
        if not names:
            return SearchResult(
                query=query,
                hits=[],
                backend=self.backend,
                note=(
                    "the installed index contains no documentation collections — the "
                    "published KAI index (kai_retrieval_251121) is workflows only. "
                    "Use search_workflows, or read the package documentation directly."
                ),
                total_candidates=0,
            )
        if packages:
            wanted = {value.lower().replace("-", "_") for value in packages}
            names = [
                name
                for name in names
                if any(value in name.lower().replace("-", "_") for value in wanted)
            ]
        embedding_function = self._get_embedding_function()
        hits: list[SearchHit] = []
        for name in names[:32]:
            try:
                collection = self._main_client.get_collection(  # type: ignore[union-attr]
                    name, embedding_function=embedding_function
                )
                raw = collection.query(query_texts=[query], n_results=max(2, top_k))
            except Exception:
                continue
            ids = (raw.get("ids") or [[]])[0]
            documents = (raw.get("documents") or [[]])[0]
            metadatas = (raw.get("metadatas") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]
            for index, chunk_id in enumerate(ids):
                metadata = metadatas[index] if index < len(metadatas) else {}
                content = documents[index] if index < len(documents) else ""
                distance = distances[index] if index < len(distances) else None
                hits.append(
                    SearchHit(
                        reference_id=chunk_reference_id(name, str(chunk_id)),
                        title=str(metadata.get("doc_type", "documentation")),
                        summary=_truncate(content, 1200),
                        package=str(metadata.get("library", "")),
                        package_version=str(metadata.get("version", "")),
                        organization=str(metadata.get("organization", "")),
                        score=_score_from_distance(distance),
                        content_hash=hash_bytes(content.encode("utf-8")),
                    )
                )
        hits.sort(key=lambda hit: (-hit.score, hit.reference_id))
        return SearchResult(
            query=query,
            hits=hits[:top_k],
            backend=self.backend,
            exact_filters=["packages"] if packages else [],
            total_candidates=len(hits),
        )

    # -- references --------------------------------------------------------

    def get_reference(
        self, reference_id: str, section_ids: Sequence[str] | None = None
    ) -> Reference:
        parsed = parse_reference_id(reference_id)
        if parsed.kind == "notebook":
            return self._notebook_reference(parsed.identifier, section_ids)
        return self._chunk_reference(parsed.collection, parsed.identifier)

    def _notebook_reference(
        self, notebook_id: str, section_ids: Sequence[str] | None
    ) -> Reference:
        path = self._notebook_paths.get(notebook_id)
        if path is None:
            raise ReferenceNotFoundError(
                f"notebook {notebook_id!r} is not in the store at "
                f"{self.root / 'notebook_summaries' / 'notebooks'}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RetrievalError(f"cannot read {path}: {exc}") from exc

        # Provenance lives under "metadata", not at the top level. The stored
        # notebook JSON has exactly four top-level keys — cell_count, cells,
        # metadata, notebook_id — so reading source_repository/title/license
        # off the payload blanked them for all 2,845 notebooks in the published
        # archive. Verified against kai_retrieval_251121.
        meta = payload.get("metadata") or {}
        cells = payload.get("cells") or []
        wanted = {str(value) for value in section_ids} if section_ids else None
        sections: list[ReferenceSection] = []
        seen_ids: set[str] = set()
        for index, cell in enumerate(cells):
            order = cell.get("order", index)
            identifier = str(order)
            if identifier in seen_ids:
                # Duplicate `order` values in the stored notebook would make
                # section_ids ambiguous; fall back to position, which is unique.
                identifier = f"{identifier}#{index}"
            seen_ids.add(identifier)
            if wanted is not None and identifier not in wanted:
                continue
            sections.append(
                ReferenceSection(
                    section_id=identifier,
                    kind=str(cell.get("cell_type", "code")),
                    heading=str(cell.get("section", "")),
                    content=str(cell.get("content", "")),
                    order=int(order) if isinstance(order, int) else index,
                )
            )
        if wanted is not None and not sections:
            available = [str(cell.get("order", index)) for index, cell in enumerate(cells)]
            raise ReferenceNotFoundError(
                f"notebook {notebook_id!r} has no sections {sorted(wanted)}; "
                f"available: {available[:20]}"
            )

        repository = str(meta.get("source_repository", payload.get("source_repository", "")))
        sections, omitted = bound_sections(sections)
        body = "\n\n".join(section.content for section in sections)
        return Reference(
            reference_id=notebook_reference_id(notebook_id),
            title=str(meta.get("title") or payload.get("title") or notebook_id),
            source_repository=repository,
            source_path=str(
                meta.get("workflow_filename")
                or meta.get("source_path")
                or payload.get("notebook_path", payload.get("full_notebook_id", ""))
            ),
            url=_github_url(
                repository,
                str(meta.get("workflow_filename") or payload.get("full_notebook_id", "")),
            ),
            package=_package_from_repository(repository),
            organization=repository.split("/", maxsplit=1)[0] if "/" in repository else "",
            summary=self._read_summary(notebook_id, repository),
            sections=sections,
            content_hash=hash_bytes(body.encode("utf-8")),
            license=str(meta.get("license", payload.get("license", ""))),
            note=(
                (
                    f"{omitted} characters omitted; request narrower section_ids to "
                    f"read the rest. "
                    if omitted
                    else ""
                )
                + "Source repository licences are collected under licenses/ in the "
                "index; they are not guaranteed to be complete."
            ),
        )

    def _read_summary(self, notebook_id: str, repository: str) -> str:
        base = self.root / "notebook_summaries" / "summaries"
        if not base.is_dir():
            return ""
        if repository and "/" in repository:
            organization, _, repo = repository.partition("/")
            candidate = base / organization / repo / f"{notebook_id}.txt"
            if candidate.is_file():
                return _truncate(candidate.read_text(encoding="utf-8", errors="replace"), 4000)
        for candidate in base.rglob(f"{notebook_id}.txt"):
            return _truncate(candidate.read_text(encoding="utf-8", errors="replace"), 4000)
        return ""

    def _chunk_reference(self, collection_name: str, chunk_id: str) -> Reference:
        if self._main_client is None:
            raise ReferenceNotFoundError(
                f"chunk references need the chromadb/ index, which is not installed "
                f"at {self.root}"
            )
        try:
            collection = self._main_client.get_collection(collection_name)
            raw = collection.get(ids=[chunk_id], include=["documents", "metadatas"])
        except Exception as exc:
            raise ReferenceNotFoundError(
                f"cannot read chunk {chunk_id!r} from collection {collection_name!r}: {exc}"
            ) from exc
        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        if not documents:
            raise ReferenceNotFoundError(
                f"chunk {chunk_id!r} is not present in collection {collection_name!r}"
            )
        content = documents[0] or ""
        metadata = metadatas[0] if metadatas else {}
        sections, omitted = bound_sections(
            [
                ReferenceSection(
                    section_id="0",
                    kind=str(metadata.get("chunk_level", "code")),
                    heading=str(metadata.get("chunk_level", "")),
                    content=content,
                    order=0,
                )
            ]
        )
        repository = (
            f"{metadata.get('organization', '')}/{metadata.get('library', '')}"
        ).strip("/")
        return Reference(
            reference_id=chunk_reference_id(collection_name, chunk_id),
            title=str(metadata.get("chunk_id", chunk_id)),
            source_repository=repository,
            package=str(metadata.get("library", "")),
            package_version=str(metadata.get("version", "")),
            organization=str(metadata.get("organization", "")),
            sections=sections,
            content_hash=hash_bytes(
                "".join(section.content for section in sections).encode("utf-8")
            ),
            note=(
                f"{omitted} characters omitted from this chunk" if omitted else ""
            ),
        )

    # -- status ------------------------------------------------------------

    def status(self) -> IndexStatus:
        names = self._collection_names()
        workflow = [name for name in names if name.endswith(_WORKFLOW_SUFFIX)]
        documentation = [name for name in names if name.endswith(_API_SUFFIX)]
        documents = 0
        for entry in self._registry.values():
            if isinstance(entry, dict):
                count = entry.get("document_count")
                if isinstance(count, int):
                    documents += count
        organizations = sorted(
            {name.split("_", 1)[0] for name in workflow if "_" in name}
        )
        unavailable: list[str] = []
        note = ""
        if not documentation:
            unavailable.append("search_documentation (no documentation collections indexed)")
            note = (
                "this index contains workflow collections only; search_documentation "
                "will return an empty result"
            )
        if self._summary_collection is None:
            unavailable.append("summary search (falling back to chunk-level search)")
        return IndexStatus(
            installed=True,
            backend=self.backend,
            path=str(self.root),
            version=_index_version(self.root),
            workflow_collections=len(workflow),
            documentation_collections=len(documentation),
            notebooks=len(self._notebook_paths),
            documents=documents,
            embedding_model=EMBEDDING_MODEL,
            organizations=organizations,
            unavailable=unavailable,
            note=note,
            extra={
                "registry_entries": len(self._registry),
                "summary_index": self._summary_collection is not None,
            },
        )


def _score_from_distance(distance: Any) -> float:
    """Turn a cosine distance into a higher-is-better score in ``[0, 1]``."""
    if distance is None:
        return 0.0
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, 1.0 - value / 2.0), 6)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _package_from_repository(repository: str) -> str:
    if "/" not in repository:
        return repository
    return repository.split("/", 1)[1]


def _github_url(repository: str, full_notebook_id: str) -> str:
    if not repository:
        return ""
    base = f"https://github.com/{repository}"
    if full_notebook_id and full_notebook_id.startswith(repository + "/"):
        return f"{base}/blob/HEAD/{full_notebook_id[len(repository) + 1:]}"
    return base


def _index_version(root: Path) -> str:
    marker = root / "VERSION"
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()
    readme = root / "README.md"
    if readme.is_file():
        for line in readme.read_text(encoding="utf-8", errors="replace").splitlines():
            if "version" in line.lower():
                return line.strip()[:120]
    return ""
