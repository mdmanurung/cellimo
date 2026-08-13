"""``cellimo-knowledge`` — a read-only MCP server over the retrieval index.

Five tools, all of them queries:

``ground``                return a few cited, design-checked code cells
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

import anyio
from mcp.server.mcpserver import MCPServer

from cellimo import __version__
from cellimo.corpus import CorpusUsage, build_usage, load_usage
from cellimo.errors import CellimoError, ProjectNotFoundError
from cellimo.project.project import Project
from cellimo.reinvention import native_signatures
from cellimo.retrieval.base import KnowledgeIndex, open_index
from cellimo.retrieval.grounding import (
    GroundingMode,
    GroundingResult,
    design_from_project,
)
from cellimo.retrieval.grounding import ground as ground_query
from cellimo.retrieval.models import IndexStatus, Reference, SearchResult

__all__ = ["SERVER_NAME", "build_server", "serve"]

SERVER_NAME = "cellimo-knowledge"

_INSTRUCTIONS = """\
Read-only retrieval over an index of published single-cell analysis notebooks.

Use ground before writing an analysis cell. First retrieve a small set of exact,
cited sections. Adapt one cell in working memory, keeping its source header,
then call ground again with that exact candidate_code before creating the cell.
The second result must have candidate_reviewed=true and
needs_user_decision=false. Otherwise stop and ask the user. search_workflows and
get_reference remain available for diagnosis and narrower follow-up reads.

This server cannot run code, read the dataset, or edit the notebook. Use
marimo-pair for anything that touches the live session.
"""


def build_server(
    index: KnowledgeIndex | None = None,
    *,
    index_path: str | None = None,
    project: Project | None = None,
) -> MCPServer:
    """Build the MCP server, opening the index once.

    ``index`` and ``project`` are injectable so tests can drive all five tools
    against fixtures without a ChromaDB installation or ambient project.
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
    usage_loaded = False
    corpus_usage: CorpusUsage | None = None
    signature_cache: dict[str, dict[str, list[str]]] = {}

    # These handlers are intentionally async even though the index API is
    # synchronous. MCPServer otherwise dispatches them through a worker thread.
    # A stdio server handles one agent request at a time, so running each bounded
    # read-only query in its event loop avoids needless cross-thread dispatch
    # without changing the concurrency users actually have.
    @server.tool()
    async def ground(
        query: str,
        packages: list[str] | None = None,
        modalities: list[str] | None = None,
        top_k: int = 5,
        analysis_mode: GroundingMode = "auto",
        exclude_reference_ids: list[str] | None = None,
        candidate_code: str | None = None,
    ) -> GroundingResult:
        """Return cited code worth adapting, checked before a cell is written.

        The result has separate `api_usage` and `in_practice` examples and
        withholds recognised design errors. Pass the exact proposed cell as
        `candidate_code` for the required native-function preflight. When
        `needs_user_decision` is true, do not improvise a replacement — ask.
        `exclude_reference_ids` is an exact denylist for held-out benchmarks.
        """
        current = project
        if current is None:
            try:
                # Re-open per call so a design approved while the MCP server is
                # alive is visible immediately. The expensive index remains open
                # once; this reads one small YAML file and provenance metadata.
                current = Project.open()
            except ProjectNotFoundError:
                current = None
        nonlocal usage_loaded, corpus_usage
        signatures: dict[str, list[str]] | None = None
        if candidate_code and not usage_loaded:
            corpus_usage = _corpus_usage(knowledge)
            usage_loaded = True
        if candidate_code and current is not None:
            interpreter = current.config.environment.interpreter
            if interpreter:
                if interpreter not in signature_cache:
                    signature_cache[interpreter] = native_signatures(interpreter)
                signatures = signature_cache[interpreter]
        return ground_query(
            knowledge,
            query,
            design=design_from_project(current),
            packages=packages,
            modalities=modalities,
            top_k=_bounded(top_k, maximum=8),
            analysis_mode=analysis_mode,
            exclude_reference_ids=exclude_reference_ids,
            candidate_code=candidate_code,
            usage=corpus_usage,
            signatures=signatures,
        )

    @server.tool()
    async def search_workflows(
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
    async def search_documentation(
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
    async def get_reference(
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
    async def index_status() -> IndexStatus:
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


def _corpus_usage(index: KnowledgeIndex) -> CorpusUsage | None:
    """Load the installed call table, or derive it read-only for an old index."""
    status = index.status()
    if not status.path:
        return None
    usage = load_usage(status.path)
    if usage is None:
        usage = build_usage(status.path)
    return usage if usage.notebooks_scanned and usage.notebooks_by_call else None


def serve(index_path: str | None = None) -> None:
    """Run the server on stdio. This is what ``cellimo mcp serve`` calls."""
    try:
        server = build_server(index_path=index_path)
    except CellimoError as exc:
        print(f"cellimo-knowledge: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    anyio.run(_run_stdio, server)


async def _run_stdio(server: MCPServer) -> None:
    """Run MCP stdio while keeping worker-backed pipe I/O responsive.

    The MCP SDK wraps the process's text streams with AnyIO worker calls. Some
    Unix selector environments can lose the cross-thread wake-up after a
    blocking read completes; a scheduled checkpoint bounds that stall without
    changing the transport or its file-descriptor safety guarantees.
    """
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(_stdio_checkpoint)
        try:
            await server.run_stdio_async()
        finally:
            tasks.cancel_scope.cancel()


async def _stdio_checkpoint() -> None:
    while True:
        await anyio.sleep(0.05)


def main(argv: list[str] | None = None) -> Any:  # pragma: no cover - process entry point
    serve()


if __name__ == "__main__":  # pragma: no cover
    main()
