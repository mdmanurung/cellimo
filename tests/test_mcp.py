"""The cellimo-knowledge MCP server, driven in-process through the official client.

The server is exercised the way an agent would: list the tools, call each one,
read the structured content back. No subprocess, no ChromaDB, no model download —
the fixture index makes all five tools testable in milliseconds.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

import pytest

from cellimo.corpus import CorpusUsage, save_usage
from cellimo.mcp.server import SERVER_NAME, build_server
from cellimo.project.project import Project
from cellimo.retrieval.lexical_index import LexicalKnowledgeIndex

mcp_client = pytest.importorskip("mcp.client", reason="the MCP SDK is a core dependency")


def _run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


@pytest.fixture
def server(fixture_index: Path) -> Any:
    return build_server(index=LexicalKnowledgeIndex(fixture_index))


def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    from mcp.client import Client

    async def _inner() -> Any:
        async with Client(server) as client:
            return await asyncio.wait_for(client.call_tool(name, arguments), timeout=30)

    return _run(_inner())


def _tools(server: Any) -> list[Any]:
    from mcp.client import Client

    async def _inner() -> Any:
        async with Client(server) as client:
            listing = await asyncio.wait_for(client.list_tools(), timeout=30)
            return list(listing.tools)

    return _run(_inner())


def test_server_starts_and_exposes_exactly_five_tools(server: Any) -> None:
    names = sorted(tool.name for tool in _tools(server))
    assert names == [
        "get_reference",
        "ground",
        "index_status",
        "search_documentation",
        "search_workflows",
    ]


def test_server_is_named_for_the_plugin(server: Any) -> None:
    assert SERVER_NAME == "cellimo-knowledge"


def test_server_reports_its_version_in_the_handshake(server: Any) -> None:
    """A client needs to know which Cellimo it is talking to."""
    from mcp.client import Client

    from cellimo import __version__

    async def _inner() -> Any:
        async with Client(server) as client:
            return client.server_info

    info = _run(_inner())
    assert info is not None
    assert info.name == SERVER_NAME
    assert info.version == __version__


def test_no_tool_can_execute_code_or_mutate_state(server: Any) -> None:
    """The server is read-only by construction, so the tool surface must stay small."""
    forbidden = {
        "execute",
        "run",
        "eval",
        "write",
        "edit",
        "create",
        "delete",
        "train",
        "kernel",
        "notebook",
        "install",
    }
    for tool in _tools(server):
        assert not any(word in tool.name.lower() for word in forbidden), tool.name


def test_search_workflows_returns_structured_hits(server: Any) -> None:
    result = _call(server, "search_workflows", {"query": "mitochondrial quality control"})
    payload = result.structured_content
    assert payload["hits"]
    assert payload["hits"][0]["reference_id"] == "notebook:scverse_scanpy_pbmc3k_qc"
    assert payload["backend"] == "lexical"


def test_ground_returns_a_cited_section_in_one_call(server: Any) -> None:
    payload = _call(
        server,
        "ground",
        {"query": "quality control filter cells by genes"},
    ).structured_content
    assert payload["needs_user_decision"] is False
    assert payload["api_usage"]
    code = payload["api_usage"][0]
    assert code["section_id"] == "1"
    assert code["content"].startswith("# cellimo:source ")


def test_ground_preflights_proposed_code_through_the_mcp_tool(
    fixture_index: Path,
    project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cellimo.mcp.server as server_module

    save_usage(
        CorpusUsage(
            notebooks_by_call={"sc.pl.violin": 188},
            notebooks_scanned=2_845,
        ),
        fixture_index,
    )
    monkeypatch.setattr(
        server_module,
        "native_signatures",
        lambda _interpreter: {
            "sc.pl.violin": ["adata", "keys", "groupby"],
        },
    )
    checked_server = build_server(
        index=LexicalKnowledgeIndex(fixture_index),
        project=project,
    )

    payload = _call(
        checked_server,
        "ground",
        {
            "query": "quality control filter cells by genes",
            "candidate_code": "ax.boxplot(adata.obs['n_genes_by_counts'])",
        },
    ).structured_content

    assert payload["candidate_reviewed"] is True
    assert payload["needs_user_decision"] is True
    assert payload["reinvention"][0]["candidates"][0] == "sc.pl.violin"


def test_search_workflows_honours_filters(server: Any) -> None:
    result = _call(
        server,
        "search_workflows",
        {"query": "pseudobulk", "packages": ["decoupler"], "top_k": 3},
    )
    payload = result.structured_content
    assert all(hit["package"] == "decoupler" for hit in payload["hits"])
    assert "packages" in payload["exact_filters"]


def test_search_documentation_returns_sections(server: Any) -> None:
    result = _call(server, "search_documentation", {"query": "normalize counts per cell"})
    payload = result.structured_content
    assert payload["hits"]
    assert payload["hits"][0]["reference_id"].startswith("chunk:scanpy_api:")


def test_get_reference_returns_the_exact_section(server: Any) -> None:
    result = _call(
        server,
        "get_reference",
        {"reference_id": "notebook:scverse_scanpy_pbmc3k_qc", "section_ids": ["1"]},
    )
    payload = result.structured_content
    assert [section["section_id"] for section in payload["sections"]] == ["1"]
    assert "filter_cells" in payload["sections"][0]["content"]
    assert payload["content_hash"]


def test_get_reference_reports_an_unknown_id_as_an_error(server: Any) -> None:
    result = _call(server, "get_reference", {"reference_id": "notebook:nope"})
    assert result.is_error
    assert "not in the index" in str(result.content)


def test_index_status_reports_the_fixture(server: Any) -> None:
    payload = _call(server, "index_status", {}).structured_content
    assert payload["installed"] is True
    assert payload["backend"] == "lexical"
    assert payload["notebooks"] == 2


def test_top_k_is_bounded(server: Any) -> None:
    payload = _call(
        server, "search_workflows", {"query": "cells", "top_k": 10_000}
    ).structured_content
    assert len(payload["hits"]) <= 50


def test_missing_index_does_not_crash_the_server(tmp_path: Path) -> None:
    server = build_server(index_path=str(tmp_path / "absent"))
    payload = _call(server, "index_status", {}).structured_content
    assert payload["installed"] is False
    assert payload["unavailable"]


def test_index_is_opened_once_per_server(fixture_index: Path, monkeypatch) -> None:
    opened = {"count": 0}
    import cellimo.mcp.server as server_module

    real = server_module.open_index

    def counting_open(path: str | None = None) -> Any:
        opened["count"] += 1
        return real(path)

    monkeypatch.setattr(server_module, "open_index", counting_open)
    server = build_server(index_path=str(fixture_index))
    for _ in range(3):
        _call(server, "search_workflows", {"query": "quality"})
        _call(server, "index_status", {})
    assert opened["count"] == 1


def test_tool_docstrings_explain_the_contract(server: Any) -> None:
    for tool in _tools(server):
        assert tool.description and len(tool.description) > 40, tool.name


def test_server_module_does_not_import_marimo_internals() -> None:
    source = inspect.getsource(__import__("cellimo.mcp.server", fromlist=["x"]))
    assert "_code_mode" not in source
