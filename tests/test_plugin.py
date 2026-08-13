"""The plugin tree: manifest consistency, skills, and the vendored marimo-pair copy."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from cellimo.marimo_pair import pair_status, vendor_info, verify_vendored_copy
from cellimo.plugin_manifest import (
    check_manifests,
    load_definition,
    render_claude_marketplace,
    render_claude_plugin,
    render_codex_plugin,
    render_mcp_config,
)
from cellimo.resources import plugin_root

REQUIRED_SKILLS = {
    "cellimo",
    "project-audit",
    "quality-control",
    "statistics",
    "notebook-review",
    "marimo-pair",
}


@pytest.fixture(scope="module")
def definition() -> dict:
    return load_definition()


def test_generated_manifests_match_the_canonical_definition() -> None:
    assert check_manifests() == []


def test_claude_and_codex_manifests_agree_on_identity(definition: dict) -> None:
    claude = render_claude_plugin(definition)
    codex = render_codex_plugin(definition)
    for field in ("name", "version", "description", "license", "repository"):
        assert claude[field] == codex[field], field
    # Both must point at the same component trees.
    assert claude["skills"] == codex["skills"]
    assert claude["mcpServers"] == codex["mcpServers"]


def test_marketplace_entry_matches_the_plugin_manifest(definition: dict) -> None:
    marketplace = render_claude_marketplace(definition)
    plugin = render_claude_plugin(definition)
    entry = marketplace["plugins"][0]
    assert entry["name"] == plugin["name"]
    assert entry["version"] == plugin["version"]
    assert entry["source"] == "./"


def test_manifest_version_matches_the_distribution(definition: dict) -> None:
    from cellimo import __version__

    assert definition["version"] == __version__
    assert definition["runtime_package"] == f"cellimo=={__version__}"


def test_mcp_config_declares_the_read_only_server(definition: dict) -> None:
    config = render_mcp_config(definition)
    assert list(config["mcpServers"]) == ["cellimo-knowledge"]
    server = config["mcpServers"]["cellimo-knowledge"]
    assert server["command"] == "cellimo"
    assert server["args"] == ["mcp", "serve"]


def test_mcp_config_avoids_platform_specific_variables() -> None:
    raw = (plugin_root() / ".mcp.json").read_text(encoding="utf-8")
    # ${CLAUDE_PLUGIN_ROOT} is a Claude Code feature; the same file has to work
    # under Codex, so the command must not depend on it.
    assert "CLAUDE_PLUGIN_ROOT" not in raw
    assert "CODEX_PLUGIN_ROOT" not in raw


def test_component_directories_live_at_the_plugin_root() -> None:
    root = plugin_root()
    # Only plugin.json belongs inside .claude-plugin/ and .codex-plugin/.
    assert not (root / ".claude-plugin" / "skills").exists()
    assert not (root / ".codex-plugin" / "skills").exists()
    assert (root / "skills").is_dir()


def test_every_declared_skill_exists() -> None:
    root = plugin_root()
    present = {path.parent.name for path in (root / "skills").glob("*/SKILL.md")}
    assert present >= REQUIRED_SKILLS, f"missing: {REQUIRED_SKILLS - present}"


def test_skill_frontmatter_is_valid() -> None:
    root = plugin_root()
    for skill_file in (root / "skills").glob("*/SKILL.md"):
        text = skill_file.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
        assert match, f"{skill_file} has no YAML frontmatter"
        frontmatter = yaml.safe_load(match.group(1))
        assert frontmatter.get("name"), skill_file
        assert frontmatter.get("description"), skill_file
        assert re.fullmatch(r"[a-z0-9-]+", frontmatter["name"]), frontmatter["name"]
        assert frontmatter["name"] == skill_file.parent.name, skill_file


def test_cellimo_skills_do_not_claim_to_run_code() -> None:
    """Only the vendored marimo-pair skill may touch the live kernel."""
    root = plugin_root() / "skills"
    for skill_file in root.glob("*/SKILL.md"):
        if skill_file.parent.name == "marimo-pair":
            continue
        assert "_code_mode" not in skill_file.read_text(encoding="utf-8"), skill_file


def test_scientific_skills_require_grounding_before_cell_creation() -> None:
    root = plugin_root() / "skills"
    for name in ("project-audit", "quality-control", "statistics"):
        text = (root / name / "SKILL.md").read_text(encoding="utf-8")
        for contract in (
            "needs_user_decision",
            "candidate_code",
            "candidate_reviewed=true",
            "# cellimo:source",
            "`create_cell`",
            "`run_cell`",
        ):
            assert contract in text, f"{name} omits {contract}"
        assert text.index("candidate_code") < text.index("`create_cell`"), name
        assert "search_workflows" not in text, name


def test_router_uses_the_same_grounding_contract() -> None:
    text = (plugin_root() / "skills" / "cellimo" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "candidate_code" in text
    assert "candidate_reviewed=true" in text
    assert "needs_user_decision=false" in text
    assert text.index("candidate_code") < text.index("`create_cell`")


# -- vendored marimo-pair --------------------------------------------------


def test_vendor_record_states_origin_version_and_licence() -> None:
    info = vendor_info()
    assert info.upstream_repository == "https://github.com/marimo-team/marimo-pair"
    assert info.pinned_tag == "v0.0.18"
    assert re.fullmatch(r"[0-9a-f]{40}", info.pinned_commit)
    assert info.license == "Apache-2.0"
    assert info.copyright
    assert info.requires["marimo"].startswith(">=")


def test_vendored_copy_is_unmodified() -> None:
    assert verify_vendored_copy() == []


def test_vendored_licence_file_is_shipped() -> None:
    licence = plugin_root() / "vendor" / "marimo-pair" / "LICENSE"
    assert licence.is_file()
    assert "Apache License" in licence.read_text(encoding="utf-8")


def test_vendored_skill_scripts_are_executable() -> None:
    scripts = sorted((plugin_root() / "skills" / "marimo-pair" / "scripts").glob("*.sh"))
    assert scripts
    for script in scripts:
        assert script.stat().st_mode & 0o111, f"{script} is not executable"


@pytest.fixture
def plugin_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway copy of the plugin tree, with ``plugin_root()`` pointed at it.

    Tests that mutate the vendored skill must never touch the real checkout: a
    process killed between the write and the restore would leave the working tree
    permanently reporting tampering on a file nobody edited.
    """
    import cellimo.marimo_pair as pair_module
    import cellimo.resources as resources_module

    copy = tmp_path / "plugin"
    shutil.copytree(plugin_root(), copy)
    monkeypatch.setattr(resources_module, "plugin_root", lambda: copy)
    monkeypatch.setattr(pair_module, "plugin_root", lambda: copy)
    monkeypatch.setattr(
        pair_module, "vendored_marimo_pair_root", lambda: copy / "skills" / "marimo-pair"
    )
    return copy


def test_vendor_tampering_is_detected(plugin_copy: Path) -> None:
    """The integrity check is real: change a byte and it must complain."""
    import cellimo.marimo_pair as module

    assert module.verify_vendored_copy() == []
    target = plugin_copy / "skills" / "marimo-pair" / "SKILL.md"
    target.write_bytes(target.read_bytes() + b"\n# local edit\n")
    problems = module.verify_vendored_copy()
    assert any("modified" in problem for problem in problems)


def test_an_extra_vendored_file_is_detected(plugin_copy: Path) -> None:
    """An added file is as much a divergence from 'unmodified v0.0.18' as an edit."""
    import cellimo.marimo_pair as module

    (plugin_copy / "skills" / "marimo-pair" / "EXTRA.md").write_text("hi", encoding="utf-8")
    assert any("unrecorded file" in problem for problem in module.verify_vendored_copy())


def test_a_deleted_vendored_file_is_detected(plugin_copy: Path) -> None:
    import cellimo.marimo_pair as module

    (plugin_copy / "skills" / "marimo-pair" / "reference" / "gotchas.md").unlink()
    assert any("missing" in problem for problem in module.verify_vendored_copy())


def test_the_real_checkout_is_never_written_by_these_tests() -> None:
    """Guard against reintroducing the hazard: the real tree stays byte-identical."""
    from cellimo.marimo_pair import verify_vendored_copy

    assert verify_vendored_copy() == []


def test_pair_status_is_serialisable() -> None:
    payload = pair_status().to_dict()
    assert json.dumps(payload)
    assert payload["vendored"] is True
    assert payload["intact"] is True
