"""The retrieval backend interface, and how a backend is chosen.

Two backends exist:

``chroma``
    Reads the inherited KAI knowledge index (ChromaDB + a notebook store).
    Needs the ``retrieval`` extra.

``lexical``
    Reads a plain JSON index with stdlib-only BM25-style scoring. Used by the
    tests, and usable as a small self-built index.

Both backends answer the same four primitive questions. ``ground`` composes
those primitives into the design-checked section selection exposed by MCP.
Each backend is loaded exactly once per process — an MCP server that re-opened
its index on every call would pay the model-load cost on every tool invocation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from cellimo.errors import IndexNotFoundError
from cellimo.resources import index_root
from cellimo.retrieval.models import IndexStatus, Reference, SearchResult

__all__ = ["KnowledgeIndex", "MissingIndex", "detect_backend", "open_index"]


class KnowledgeIndex(ABC):
    """What every retrieval backend must be able to answer."""

    backend: str = "unknown"

    @abstractmethod
    def search_workflows(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        modalities: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        """Rank indexed analysis workflows against ``query``."""

    @abstractmethod
    def search_documentation(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        """Rank indexed API/documentation sections against ``query``."""

    @abstractmethod
    def get_reference(
        self,
        reference_id: str,
        section_ids: Sequence[str] | None = None,
        *,
        with_provenance: bool = True,
    ) -> Reference:
        """Return the exact source behind ``reference_id``.

        ``with_provenance`` prefixes each code section with a
        ``# cellimo:source`` header so the origin travels with the code once the
        agent adapts it into a cell. Pass ``False`` to get the section exactly as
        published — which is what verifying a citation needs, since the hash
        covers the raw content.
        """

    @abstractmethod
    def status(self) -> IndexStatus:
        """Describe what is installed, including what is missing."""



class MissingIndex(KnowledgeIndex):
    """Stand-in used when no index is installed.

    Every tool answers truthfully instead of raising: the agent is told the
    index is absent and how to install it, rather than seeing a stack trace or,
    worse, an empty result that looks like "no such workflow exists".
    """

    backend = "missing"

    def __init__(self, path: Path, reason: str) -> None:
        self.path = Path(path)
        self.reason = reason

    def _empty(self, query: str) -> SearchResult:
        return SearchResult(
            query=query,
            hits=[],
            backend=self.backend,
            note=self.reason,
        )

    def search_workflows(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        modalities: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        return self._empty(query)

    def search_documentation(
        self,
        query: str,
        *,
        packages: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        return self._empty(query)

    def get_reference(
        self,
        reference_id: str,
        section_ids: Sequence[str] | None = None,
        *,
        with_provenance: bool = True,
    ) -> Reference:
        raise IndexNotFoundError(self.reason)

    def status(self) -> IndexStatus:
        return IndexStatus(
            installed=False,
            backend=self.backend,
            path=str(self.path),
            note=self.reason,
            unavailable=[
                "ground",
                "search_workflows",
                "search_documentation",
                "get_reference",
            ],
        )


def detect_backend(path: Path) -> str:
    """Decide which backend can read ``path``, or return an empty string."""
    if (path / "cellimo-index.json").is_file():
        return "lexical"
    if (path / "chromadb").is_dir() or (path / "notebook_summaries").is_dir():
        return "chroma"
    # The published KAI archive nests everything one level deeper; accept that
    # layout rather than silently reporting "no index".
    nested = path / "retrieval"
    if (nested / "chromadb").is_dir() or (nested / "notebook_summaries").is_dir():
        return "chroma-nested"
    return ""


def open_index(path: str | Path | None = None) -> KnowledgeIndex:
    """Open the installed index, returning :class:`MissingIndex` when absent."""
    root = Path(path) if path is not None else index_root()
    try:
        if not root.exists():
            return MissingIndex(
                root,
                f"no retrieval index at {root}; run `cellimo index install` to fetch one",
            )
        backend = detect_backend(root)
    except OSError as exc:
        # An unreadable directory (mode 000, a dead NFS mount) raises from
        # ``is_file()``. That must not take the MCP server down at startup.
        return MissingIndex(
            root, f"the retrieval index at {root} could not be read: {exc}"
        )
    if backend == "lexical":
        from cellimo.retrieval.lexical_index import LexicalKnowledgeIndex

        return LexicalKnowledgeIndex(root)
    if backend in {"chroma", "chroma-nested"}:
        from cellimo.retrieval.chroma_index import ChromaKnowledgeIndex

        effective = root / "retrieval" if backend == "chroma-nested" else root
        try:
            return ChromaKnowledgeIndex(effective)
        except ImportError as exc:
            return MissingIndex(
                root,
                (
                    f"a ChromaDB index is installed at {effective} but the retrieval "
                    f"extra is not: {exc}. Install it with "
                    f"`pip install 'cellimo[retrieval]'`."
                ),
            )
        except Exception as exc:
            # Anything else — a read-only index directory, a corrupt sqlite file,
            # an incompatible ChromaDB — must degrade to the same clean "no
            # usable index" answer. The MCP server would otherwise die with an
            # unhandled traceback at startup, which the client sees as a broken
            # server rather than as a missing index.
            return MissingIndex(
                root,
                (
                    f"the ChromaDB index at {effective} could not be opened: "
                    f"{type(exc).__name__}: {exc}. Note that ChromaDB requires its "
                    f"index directory to be writable even for queries."
                ),
            )
    return MissingIndex(
        root,
        (
            f"{root} exists but does not look like a Cellimo index (expected "
            f"cellimo-index.json, chromadb/ or notebook_summaries/)"
        ),
    )
