"""``cellimo-knowledge`` — a read-only MCP server over the retrieval index.

Four tools, all of them queries:

``search_workflows``      rank indexed analysis notebooks
``search_documentation``  rank indexed API/documentation sections
``get_reference``         return the exact source behind a reference id
``index_status``          say what is installed, and what is missing

"Read-only" is a statement about the **tool contract**, and it is exact:
nothing here executes Python, starts a kernel, reads your dataset, trains a
model, edits a notebook, computes a statistic, or writes anything into your
project. Those belong to Marimo, to marimo-pair and to the notebook — a
retrieval server that could also run code would be a second execution path with
none of the provenance.

It is *not* a claim that the index directory itself is never touched. The
ChromaDB backend writes to its own internal files when it opens a collection and
when it answers a query, so that backend needs its index directory to stay
writable by the serving process. The lexical backend genuinely only reads. See
docs/RETRIEVAL.md.

The index is opened **once**, when the server object is built. Re-opening it per
call would reload a sentence-transformers model on every query.
"""

from __future__ import annotations

import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

from cellimo import __version__
from cellimo.errors import CellimoError
from cellimo.retrieval.base import KnowledgeIndex, open_index
from cellimo.retrieval.models import IndexStatus, Reference, SearchResult

__all__ = ["SERVER_NAME", "build_server", "serve"]

SERVER_NAME = "cellimo-knowledge"

_INSTRUCTIONS = """\
Read-only retrieval over an index of published single-cell analysis notebooks.

Use search_workflows to find how a step is actually done in practice, then
get_reference with the returned reference_id (and section_ids) to read the exact
cells rather than working from the summary. Record what you used with
cellimo's record_reference so the notebook can cite it.

This server cannot run code, read the dataset, or edit the notebook. Use
marimo-pair for anything that touches the live session.
"""


def build_server(
    index: KnowledgeIndex | None = None,
    *,
    index_path: str | None = None,
) -> MCPServer:
    """Build the MCP server, opening the index once.

    ``index`` is injectable so tests can drive all four tools against a fixture
    without a ChromaDB installation.
    """
    knowledge = index if index is not None else open_index(index_path)
    server = MCPServer(
        SERVER_NAME,
        title="Cellimo knowledge",
        instructions=_INSTRUCTIONS,
        # Reported in the initialize handshake, so a client can tell which
        # Cellimo it is talking to. It matched the plugin manifests' 0.1.0
        # everywhere except here, where it was empty.
        version=__version__,
        website_url="https://github.com/mdmanurung/cellimo",
    )

    @server.tool()
    def search_workflows(
        query: str,
        packages: list[str] | None = None,
        modalities: list[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        """Search indexed single-cell analysis workflows.

        Returns ranked notebooks with stable reference ids. `packages` and
        `modalities` are best-effort on the published index — the result says
        which filters were exact and which were approximated.
        """
        return knowledge.search_workflows(
            query,
            packages=packages,
            modalities=modalities,
            top_k=_bounded(top_k),
        )

    @server.tool()
    def search_documentation(
        query: str,
        packages: list[str] | None = None,
        top_k: int = 8,
    ) -> SearchResult:
        """Search indexed package documentation and API reference sections.

        The published KAI index contains workflows only; when no documentation
        is indexed this returns an empty result with an explanation rather than
        silently nothing.
        """
        return knowledge.search_documentation(
            query, packages=packages, top_k=_bounded(top_k)
        )

    @server.tool()
    def get_reference(
        reference_id: str,
        section_ids: list[str] | None = None,
    ) -> Reference:
        """Return the exact source behind a reference id.

        `reference_id` comes from a search hit: `notebook:<id>` or
        `chunk:<collection>:<id>`. Pass `section_ids` to read specific cells
        instead of the whole notebook.
        """
        return knowledge.get_reference(reference_id, section_ids)

    @server.tool()
    def index_status() -> IndexStatus:
        """Report what retrieval index is installed and what it cannot answer."""
        return knowledge.status()

    return server


def _bounded(top_k: int, *, maximum: int = 50) -> int:
    """Keep result sets small enough to read and cheap enough to rank."""
    try:
        value = int(top_k)
    except (TypeError, ValueError):
        return 8
    return max(1, min(value, maximum))


def serve(index_path: str | None = None) -> None:
    """Run the server on stdio. This is what ``cellimo mcp serve`` calls."""
    try:
        server = build_server(index_path=index_path)
    except CellimoError as exc:
        print(f"cellimo-knowledge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    server.run(transport="stdio")


def main(argv: list[str] | None = None) -> Any:  # pragma: no cover - process entry point
    serve()


if __name__ == "__main__":  # pragma: no cover
    main()
