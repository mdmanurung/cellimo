"""One canonical plugin definition, two platform manifests.

``plugin/plugin.toml`` is the source of truth. Claude Code reads
``.claude-plugin/marketplace.json`` and ``.claude-plugin/plugin.json``; Codex
reads ``.codex-plugin/plugin.json``; both read ``.mcp.json`` from the plugin
root. Those four files are generated from the TOML here, and
:func:`check_manifests` compares what is on disk against what would be generated
so drift is a test failure rather than a support ticket.

    python -m cellimo.plugin_manifest --check    # verify (what the test runs)
    python -m cellimo.plugin_manifest --write    # regenerate
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

from cellimo.resources import plugin_root

__all__ = [
    "GENERATED_FILES",
    "check_manifests",
    "load_definition",
    "render_claude_marketplace",
    "render_claude_plugin",
    "render_codex_plugin",
    "render_mcp_config",
    "write_manifests",
]

#: Relative path -> renderer name. Order is the order they are written.
GENERATED_FILES: dict[str, str] = {
    ".claude-plugin/plugin.json": "render_claude_plugin",
    ".claude-plugin/marketplace.json": "render_claude_marketplace",
    ".codex-plugin/plugin.json": "render_codex_plugin",
    ".mcp.json": "render_mcp_config",
}


def load_definition(root: Path | None = None) -> dict[str, Any]:
    """Read ``plugin.toml`` from the plugin root."""
    base = Path(root) if root is not None else plugin_root()
    path = base / "plugin.toml"
    with path.open("rb") as handle:
        return tomllib.load(handle)


def render_claude_plugin(definition: dict[str, Any]) -> dict[str, Any]:
    """``.claude-plugin/plugin.json`` — the plugin manifest Claude Code reads."""
    return {
        "name": definition["name"],
        "displayName": definition.get("display_name", definition["name"]),
        "version": definition["version"],
        "description": definition["description"],
        "author": definition.get("author", {}),
        "homepage": definition.get("homepage", ""),
        "repository": definition.get("repository", ""),
        "license": definition.get("license", ""),
        "keywords": definition.get("keywords", []),
        "skills": definition.get("skills", "./skills/"),
        "mcpServers": definition.get("mcp", "./.mcp.json"),
    }


def render_claude_marketplace(definition: dict[str, Any]) -> dict[str, Any]:
    """``.claude-plugin/marketplace.json`` — makes this directory installable.

    The plugin lives at the marketplace root (``"source": "./"``), which is what
    lets ``claude plugin marketplace add <path>`` point straight at the
    installed package directory.
    """
    return {
        "name": definition["name"],
        "owner": definition.get("author", {}),
        "metadata": {
            "description": definition["description"],
            "version": definition["version"],
        },
        "plugins": [
            {
                "name": definition["name"],
                "description": definition["description"],
                "version": definition["version"],
                "source": "./",
                "license": definition.get("license", ""),
                "category": definition.get("category", ""),
                "keywords": definition.get("keywords", []),
                "skills": [definition.get("skills", "./skills/").rstrip("/")],
            }
        ],
    }


def render_codex_plugin(definition: dict[str, Any]) -> dict[str, Any]:
    """``.codex-plugin/plugin.json`` — the manifest Codex reads.

    Codex requires component paths to be relative to the plugin root and to
    start with ``./``; the same values are used for both platforms so the two
    manifests cannot describe different trees.
    """
    return {
        "name": definition["name"],
        "version": definition["version"],
        "description": definition["description"],
        "author": definition.get("author", {}),
        "homepage": definition.get("homepage", ""),
        "repository": definition.get("repository", ""),
        "license": definition.get("license", ""),
        "keywords": definition.get("keywords", []),
        "skills": definition.get("skills", "./skills/"),
        "mcpServers": definition.get("mcp", "./.mcp.json"),
        "interface": {
            "displayName": definition.get("display_name", definition["name"]),
            "shortDescription": definition["description"],
            "longDescription": definition.get("long_description", definition["description"]),
            "developerName": definition.get("author", {}).get("name", ""),
            "category": definition.get("category", ""),
            "capabilities": definition.get("capabilities", []),
            "websiteURL": definition.get("homepage", ""),
            "defaultPrompt": definition.get("default_prompts", []),
        },
    }


def render_mcp_config(definition: dict[str, Any]) -> dict[str, Any]:
    """``.mcp.json`` — the same stdio server stanza for both platforms."""
    servers: dict[str, Any] = {}
    for name, config in (definition.get("mcp_servers") or {}).items():
        entry: dict[str, Any] = {"command": config["command"]}
        if config.get("args"):
            entry["args"] = list(config["args"])
        if config.get("env"):
            entry["env"] = dict(config["env"])
        servers[name] = entry
    return {"mcpServers": servers}


def _render_all(definition: dict[str, Any]) -> dict[str, dict[str, Any]]:
    module = sys.modules[__name__]
    return {
        relative: getattr(module, renderer)(definition)
        for relative, renderer in GENERATED_FILES.items()
    }


def _serialise(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def write_manifests(root: Path | None = None) -> list[Path]:
    """Regenerate every platform manifest. Returns the paths written."""
    base = Path(root) if root is not None else plugin_root()
    definition = load_definition(base)
    written: list[Path] = []
    for relative, payload in _render_all(definition).items():
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_serialise(payload), encoding="utf-8")
        written.append(target)
    return written


def check_manifests(root: Path | None = None) -> list[str]:
    """Return a list of drift descriptions; empty means everything is in sync."""
    base = Path(root) if root is not None else plugin_root()
    definition = load_definition(base)
    problems: list[str] = []
    for relative, payload in _render_all(definition).items():
        target = base / relative
        if not target.is_file():
            problems.append(f"{relative} is missing")
            continue
        try:
            actual = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            problems.append(f"{relative} is not valid JSON: {exc}")
            continue
        if actual != payload:
            problems.append(
                f"{relative} differs from plugin.toml; run "
                f"`python -m cellimo.plugin_manifest --write`"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="regenerate the manifests")
    parser.add_argument("--check", action="store_true", help="fail if they have drifted")
    parser.add_argument("--root", default=None, help="plugin root (default: bundled)")
    arguments = parser.parse_args(argv)
    root = Path(arguments.root) if arguments.root else None

    if arguments.write:
        for path in write_manifests(root):
            print(f"wrote {path}")
        return 0
    problems = check_manifests(root)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
