"""A stdlib-only lexical retrieval backend.

Reads a single ``cellimo-index.json`` file and scores with BM25. It exists for
two reasons: the test suite must be able to exercise all four MCP tools without
a 345 MB download and a PyTorch install, and a small hand-built index is a
reasonable thing for a lab to ship alongside its own notebooks.

Scoring is deterministic — same index and same query give the same order every
time, which is what makes retrieval assertions in tests meaningful.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cellimo.errors import ReferenceNotFoundError, RetrievalError
from cellimo.retrieval.base import KnowledgeIndex
from cellimo.retrieval.diversify import diversify
from cellimo.retrieval.ids import (
    ParsedReference,
    chunk_reference_id,
    notebook_reference_id,
    parse_reference_id,
)
from cellimo.retrieval.models import (
    MAX_SUMMARY_CHARS,
    IndexStatus,
    Reference,
    ReferenceSection,
    SearchHit,
    SearchResult,
    bound_sections,
)
from cellimo.util.hashing import hash_bytes

__all__ = ["INDEX_FILENAME", "LexicalKnowledgeIndex", "tokenize"]

INDEX_FILENAME = "cellimo-index.json"

_TOKEN = re.compile(r"[a-z0-9_]+")
_K1 = 1.2
_B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, keeping identifiers like ``rank_genes_groups`` whole."""
    return _TOKEN.findall(text.lower())


@dataclass
class _Document:
    """One searchable entry, with its text flattened once at load time."""

    reference_id: str
    kind: str  # workflow | documentation
    title: str
    summary: str
    source_repository: str
    source_path: str
    url: str
    package: str
    package_version: str
    organization: str
    license: str
    sections: list[dict[str, Any]]
    text: str
    tokens: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)

    def __post_init__(self) -> None:
        self.tokens = tokenize(self.text)
        self.counts = Counter(self.tokens)


class LexicalKnowledgeIndex(KnowledgeIndex):
    """BM25 over a JSON index. Loaded once; queries touch memory only."""

    backend = "lexical"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        index_file = self.root / INDEX_FILENAME
        if not index_file.is_file():
            raise RetrievalError(f"no {INDEX_FILENAME} at {self.root}")
        try:
            payload = json.loads(index_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RetrievalError(f"{index_file} is not valid JSON: {exc}") from exc

        self.meta: dict[str, Any] = payload.get("meta", {})
        self.documents: list[_Document] = []
        for entry in payload.get("workflows", []):
            self.documents.append(self._build(entry, kind="workflow"))
        for entry in payload.get("documentation", []):
            self.documents.append(self._build(entry, kind="documentation"))
        self._by_id = {document.reference_id: document for document in self.documents}
        self._document_frequency: Counter[str] = Counter()
        for document in self.documents:
            self._document_frequency.update(set(document.tokens))
        lengths = [len(document.tokens) for document in self.documents]
        self._average_length = (sum(lengths) / len(lengths)) if lengths else 0.0

    # -- loading -----------------------------------------------------------

    def _build(self, entry: dict[str, Any], *, kind: str) -> _Document:
        sections = list(entry.get("sections", []))
        if kind == "workflow":
            reference_id = notebook_reference_id(entry["notebook_id"])
        else:
            reference_id = chunk_reference_id(
                entry.get("collection", "documentation"), entry["chunk_id"]
            )
        text_parts = [
            entry.get("title", ""),
            entry.get("summary", ""),
            entry.get("source_repository", ""),
            entry.get("package", ""),
        ]
        text_parts += [str(section.get("heading", "")) for section in sections]
        text_parts += [str(section.get("content", "")) for section in sections]
        return _Document(
            reference_id=reference_id,
            kind=kind,
            title=entry.get("title", ""),
            summary=entry.get("summary", ""),
            source_repository=entry.get("source_repository", ""),
            source_path=entry.get("source_path", ""),
            url=entry.get("url", ""),
            package=entry.get("package", ""),
            package_version=entry.get("package_version", ""),
            organization=entry.get("organization", ""),
            license=entry.get("license", ""),
            sections=sections,
            text="\n".join(part for part in text_parts if part),
        )

    # -- scoring -----------------------------------------------------------

    def _bm25(self, query_tokens: Sequence[str], document: _Document) -> float:
        if not document.tokens:
            return 0.0
        total = len(self.documents)
        length = len(document.tokens)
        score = 0.0
        for token in set(query_tokens):
            frequency = document.counts.get(token, 0)
            if not frequency:
                continue
            containing = self._document_frequency.get(token, 0)
            idf = math.log(1 + (total - containing + 0.5) / (containing + 0.5))
            denominator = frequency + _K1 * (
                1 - _B + _B * (length / self._average_length if self._average_length else 1)
            )
            score += idf * (frequency * (_K1 + 1)) / denominator
        return score

    def _section_ids(self, document: _Document) -> list[str]:
        return [
            str(section.get("section_id", index))
            for index, section in enumerate(document.sections)
        ]

    def _to_hit(self, document: _Document, score: float) -> SearchHit:
        return SearchHit(
            reference_id=document.reference_id,
            title=document.title,
            summary=_truncate(document.summary or _first_words(document.text, 60)),
            source_repository=document.source_repository,
            source_path=document.source_path,
            url=document.url,
            package=document.package,
            package_version=document.package_version,
            organization=document.organization,
            score=round(score, 6),
            section_ids=self._section_ids(document),
            content_hash=hash_bytes(document.text.encode("utf-8")),
        )

    def _search(
        self,
        query: str,
        *,
        kind: str,
        packages: Sequence[str] | None,
        modalities: Sequence[str] | None,
        top_k: int,
    ) -> SearchResult:
        tokens = tokenize(query)
        candidates = [document for document in self.documents if document.kind == kind]
        exact: list[str] = []
        approximate: list[str] = []

        if packages:
            wanted = {value.lower() for value in packages}
            exact.append("packages")
            candidates = [
                document
                for document in candidates
                if document.package.lower() in wanted
                or any(value in document.source_repository.lower() for value in wanted)
            ]
        if modalities:
            approximate.append("modalities")
            wanted_modalities = [value.lower() for value in modalities]
            candidates = [
                document
                for document in candidates
                if any(value in document.text.lower() for value in wanted_modalities)
            ]

        scored = [
            (self._bm25(tokens, document), document)
            for document in candidates
        ]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: (-pair[0], pair[1].reference_id))
        ranked = [self._to_hit(document, score) for score, document in scored]
        hits, filtered = diversify(ranked, top_k=top_k)
        notes = []
        if modalities:
            notes.append(
                "modality filtering is best-effort text matching: the index has no "
                "modality field"
            )
        if filtered:
            notes.append(filtered)
        note = "; ".join(notes)
        return SearchResult(
            query=query,
            hits=hits,
            backend=self.backend,
            exact_filters=exact,
            approximate_filters=approximate,
            note=note,
            total_candidates=len(candidates),
        )

    # -- interface ---------------------------------------------------------

    def search_workflows(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        modalities: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        return self._search(
            query, kind="workflow", packages=packages, modalities=modalities, top_k=top_k
        )

    def search_documentation(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        return self._search(
            query, kind="documentation", packages=packages, modalities=None, top_k=top_k
        )

    def get_reference(
        self, reference_id: str, section_ids: Sequence[str] | None = None
    ) -> Reference:
        parsed: ParsedReference = parse_reference_id(reference_id)
        document = self._by_id.get(reference_id)
        if document is None:
            raise ReferenceNotFoundError(
                f"{reference_id!r} is not in the index at {self.root} "
                f"({parsed.kind} namespace)"
            )
        wanted = {str(value) for value in section_ids} if section_ids else None
        sections: list[ReferenceSection] = []
        for index, raw in enumerate(document.sections):
            identifier = str(raw.get("section_id", index))
            if wanted is not None and identifier not in wanted:
                continue
            sections.append(
                ReferenceSection(
                    section_id=identifier,
                    kind=str(raw.get("kind", "code")),
                    heading=str(raw.get("heading", "")),
                    content=str(raw.get("content", "")),
                    order=_as_order(raw.get("order", index), index),
                )
            )
        if wanted is not None and not sections:
            raise ReferenceNotFoundError(
                f"{reference_id!r} has no sections {sorted(wanted)}; available: "
                f"{self._section_ids(document)}"
            )
        sections, omitted = bound_sections(sections)
        body = "\n\n".join(section.content for section in sections)
        return Reference(
            reference_id=reference_id,
            title=document.title,
            source_repository=document.source_repository,
            source_path=document.source_path,
            url=document.url,
            package=document.package,
            package_version=document.package_version,
            organization=document.organization,
            summary=_truncate(document.summary),
            sections=sections,
            # Hashes what was actually returned, so a provenance record matches
            # the text the agent read.
            content_hash=hash_bytes(body.encode("utf-8")),
            license=document.license,
            note=(
                f"{omitted} characters omitted; request narrower section_ids to "
                f"read the rest"
                if omitted
                else ""
            ),
        )

    def status(self) -> IndexStatus:
        workflows = [document for document in self.documents if document.kind == "workflow"]
        documentation = [
            document for document in self.documents if document.kind == "documentation"
        ]
        unavailable = [] if documentation else ["search_documentation (no documentation indexed)"]
        return IndexStatus(
            installed=True,
            backend=self.backend,
            path=str(self.root),
            version=str(self.meta.get("version", "")),
            workflow_collections=len({document.source_repository for document in workflows}),
            documentation_collections=len({document.package for document in documentation}),
            notebooks=len(workflows),
            documents=len(self.documents),
            embedding_model="none (BM25 lexical scoring)",
            organizations=sorted(
                {document.organization for document in workflows if document.organization}
            ),
            unavailable=unavailable,
            note=str(self.meta.get("note", "")),
            extra={"name": self.meta.get("name", "")},
        )


def _as_order(value: Any, fallback: int) -> int:
    """Index data is third-party; a non-numeric order must not raise."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _truncate(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Cap a summary. A summary is a preview; the sections carry the material."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"… ({len(text) - limit} more characters)"


def _first_words(text: str, count: int) -> str:
    words = text.split()
    excerpt = " ".join(words[:count])
    return excerpt + ("…" if len(words) > count else "")
