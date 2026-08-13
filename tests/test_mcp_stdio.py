"""The MCP server over a real stdio subprocess — the path the plugin actually uses.

`tests/test_mcp.py` drives the server in-process, which is fast but skips
everything the plugin's `.mcp.json` depends on: spawning `cellimo mcp serve`,
opening the index from the environment rather than from an injected object, and
the stdio transport itself.

These tests are slower because they start a process. They are the ones that
would have caught a broken entry point.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is a core dependency")

pytestmark = pytest.mark.slow


def _server_command() -> tuple[str, list[str]]:
    """Prefer the installed console script; fall back to the module."""
    executable = shutil.which("cellimo") or str(Path(sys.executable).parent / "cellimo")
    if Path(executable).is_file():
        return executable, ["mcp", "serve"]
    return sys.executable, ["-m", "cellimo.cli.main", "mcp", "serve"]


def _drive(index_dir: Path | None) -> dict[str, Any]:
    """Start the server as a subprocess and exercise all five tools."""
    from mcp import ClientSession, StdioServerParameters, stdio_client

    command, arguments = _server_command()
    environment = dict(os.environ)
    if index_dir is not None:
        environment["CELLIMO_INDEX_DIR"] = str(index_dir)

    async def _inner() -> dict[str, Any]:
        params = StdioServerParameters(command=command, args=arguments, env=environment)
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            listing = await session.list_tools()
            results: dict[str, Any] = {"tools": sorted(t.name for t in listing.tools)}
            results["status"] = (await session.call_tool("index_status", {})).structured_content
            results["workflows"] = (
                await session.call_tool("search_workflows", {"query": "quality control"})
            ).structured_content
            results["ground"] = (
                await session.call_tool(
                    "ground", {"query": "quality control filter cells by genes"}
                )
            ).structured_content
            results["documentation"] = (
                await session.call_tool("search_documentation", {"query": "normalize"})
            ).structured_content
            results["reference"] = await session.call_tool(
                "get_reference", {"reference_id": "notebook:scverse_scanpy_pbmc3k_qc"}
            )
            return results

    return asyncio.run(asyncio.wait_for(_inner(), timeout=120))


def test_server_starts_over_stdio_and_serves_all_five_tools(fixture_index: Path) -> None:
    results = _drive(fixture_index)
    assert results["tools"] == [
        "get_reference",
        "ground",
        "index_status",
        "search_documentation",
        "search_workflows",
    ]
    assert results["status"]["installed"] is True
    assert results["status"]["backend"] == "lexical"
    assert results["workflows"]["hits"]
    assert results["ground"]["api_usage"]
    assert results["reference"].structured_content["sections"]


def test_first_run_with_no_index_answers_instead_of_crashing(tmp_path: Path) -> None:
    """The state every user is in before `cellimo index install`."""
    results = _drive(tmp_path / "no-index-here")
    assert results["tools"]
    status = results["status"]
    assert status["installed"] is False
    assert "cellimo index install" in status["note"]
    assert status["unavailable"]
    # Searching still answers, with an explanation rather than a stack trace.
    assert results["workflows"]["hits"] == []
    assert results["workflows"]["note"]
    # And an unresolvable reference is an error result, not a dead server.
    assert results["reference"].is_error


def test_the_plugin_mcp_config_matches_what_was_driven() -> None:
    """The command these tests spawn is the one the plugin declares."""
    from cellimo.resources import plugin_root

    config = json.loads((plugin_root() / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["cellimo-knowledge"]
    assert server["command"] == "cellimo"
    assert server["args"] == ["mcp", "serve"]
