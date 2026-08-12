"""The command line: help, init, doctor, check, index and mcp wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cellimo.cli.main import cli
from cellimo.project.project import Project


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_help_lists_every_documented_command(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ("install", "init", "start", "doctor", "check", "index", "mcp"):
        assert command in result.output


def test_version(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_init_creates_a_project(runner: CliRunner, tmp_path: Path, synthetic_h5ad: Path) -> None:
    root = tmp_path / "cli-project"
    root.mkdir()
    local = root / "source.h5ad"
    local.write_bytes(synthetic_h5ad.read_bytes())
    result = runner.invoke(
        cli, ["init", str(local), "--dir", str(root), "--name", "cli-demo"]
    )
    assert result.exit_code == 0, result.output
    assert (root / "cellimo.yaml").is_file()
    assert (root / "analysis.py").is_file()
    assert "notebook: analysis.py" in result.output


def test_init_refuses_a_missing_dataset(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(cli, ["init", str(tmp_path / "absent.h5ad")])
    assert result.exit_code != 0


def test_init_with_the_existing_profile(
    runner: CliRunner, tmp_path: Path, synthetic_h5ad: Path
) -> None:
    """`--profile existing` adopts the environment and installs nothing scientific."""
    root = tmp_path / "existing-project"
    root.mkdir()
    local = root / "source.h5ad"
    local.write_bytes(synthetic_h5ad.read_bytes())
    result = runner.invoke(
        cli, ["init", str(local), "--dir", str(root), "--profile", "existing"]
    )
    assert result.exit_code == 0, result.output

    from cellimo.project.project import Project

    project = Project.open(root)
    assert project.config.environment.profile == "existing"
    requirements = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "marimo" in requirements
    for scientific in ("scanpy", "leidenalg", "scvi-tools", "squidpy"):
        assert scientific not in requirements, scientific

    # And the project it produced is valid.
    assert runner.invoke(cli, ["check", str(root)]).exit_code == 0


def test_both_implemented_profiles_produce_valid_projects(
    runner: CliRunner, tmp_path: Path, synthetic_h5ad: Path
) -> None:
    for profile in ("scanpy", "existing"):
        root = tmp_path / f"profile-{profile}"
        root.mkdir()
        local = root / "source.h5ad"
        local.write_bytes(synthetic_h5ad.read_bytes())
        init = runner.invoke(
            cli, ["init", str(local), "--dir", str(root), "--profile", profile]
        )
        assert init.exit_code == 0, init.output
        assert (root / "analysis.py").is_file()
        assert runner.invoke(cli, ["check", str(root)]).exit_code == 0


def test_check_only_restricts_which_checks_run(runner: CliRunner, project) -> None:
    result = runner.invoke(cli, ["check", str(project.root), "--only", "S001,S004", "--json"])
    payload = json.loads(result.output)
    assert payload["checks_run"] == 2
    assert all(finding["code"] in {"S001", "S004"} for finding in payload["findings"])


def test_check_only_rejects_an_unknown_code(runner: CliRunner, project) -> None:
    """Running zero checks and reporting success is the worst answer to a typo."""
    result = runner.invoke(cli, ["check", str(project.root), "--only", "BOGUS999"])
    assert result.exit_code != 0
    assert "unknown check code" in result.output


def test_check_refreshes_a_stale_manifest(runner: CliRunner, project) -> None:
    """The agent's inspect step must not hand it a manifest from before the work.

    Only three of nine mutating paths rewrite `manifest.json`, and recording
    statistics is not one of them — so an interrupted session leaves the file
    claiming nothing was analysed, which is precisely what the `cellimo` and
    `notebook-review` skills tell the agent to read on resume.
    """
    project.record_statistics(name="marker ranking", test="wilcoxon", mode="exploratory")
    project.record_reference(reference_id="notebook:x", title="X", used_for="qc")
    stale = project.store.manifest()
    assert stale is not None
    assert stale.counts["statistics"] == 0, "precondition: the manifest is stale"

    result = runner.invoke(cli, ["check", str(project.root), "--json"])
    assert json.loads(result.output)["manifest_refreshed"] is True

    fresh = project.store.manifest()
    assert fresh is not None
    assert fresh.counts["statistics"] == 1
    assert fresh.counts["references"] == 1


def test_check_still_runs_when_the_manifest_cannot_be_written(
    runner: CliRunner, project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project you cannot write to is still a project you can check."""

    def refuse(self) -> Path:
        raise OSError("read-only file system")

    monkeypatch.setattr(Project, "write_manifest", refuse)
    result = runner.invoke(cli, ["check", str(project.root), "--json"])
    payload = json.loads(result.output)
    assert payload["manifest_refreshed"] is False
    assert "read-only" in payload["manifest_error"]
    assert payload["checks_run"] > 0


def test_check_reports_a_missing_notebook(runner: CliRunner, project) -> None:
    project.notebook_path.unlink()
    result = runner.invoke(cli, ["check", str(project.root)])
    assert result.exit_code == 1
    assert "is missing" in result.output

    as_json = runner.invoke(cli, ["check", str(project.root), "--json"])
    payload = json.loads(as_json.output)
    assert payload["notebook"]["ran"] is False
    assert "missing" in payload["notebook"]["note"]


def test_force_reinit_preserves_design_policies_and_seed(
    runner: CliRunner, tmp_path: Path, synthetic_h5ad: Path
) -> None:
    """Provenance survives a re-init, so the configuration must not contradict it."""
    from cellimo.project.project import Project

    root = tmp_path / "reinit"
    root.mkdir()
    source = root / "source.h5ad"
    source.write_bytes(synthetic_h5ad.read_bytes())

    first = runner.invoke(
        cli, ["init", str(source), "--dir", str(root), "--seed", "99", "--name", "reinit"]
    )
    assert first.exit_code == 0, first.output
    project = Project.open(root)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="a human")
    project.authorize_autonomous("unattended run")

    again = runner.invoke(cli, ["init", str(source), "--dir", str(root), "--force"])
    assert again.exit_code == 0, again.output
    assert "kept design" in again.output

    reloaded = Project.open(root)
    assert reloaded.config.design.is_approved()
    assert reloaded.config.design.donor == "participant_id"
    assert reloaded.config.random_seed == 99
    assert reloaded.config.policies.autonomous_authorization is True


def test_force_reinit_can_still_change_the_seed(
    runner: CliRunner, tmp_path: Path, synthetic_h5ad: Path
) -> None:
    from cellimo.project.project import Project

    root = tmp_path / "reseed"
    root.mkdir()
    source = root / "source.h5ad"
    source.write_bytes(synthetic_h5ad.read_bytes())
    runner.invoke(cli, ["init", str(source), "--dir", str(root), "--seed", "1"])
    runner.invoke(cli, ["init", str(source), "--dir", str(root), "--force", "--seed", "7"])
    assert Project.open(root).config.random_seed == 7


def test_init_reports_a_permission_error_cleanly(
    runner: CliRunner, tmp_path: Path, synthetic_h5ad: Path
) -> None:
    import os

    if os.geteuid() == 0:  # pragma: no cover - root ignores mode bits
        pytest.skip("root can write anywhere")
    parent = tmp_path / "readonly"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        result = runner.invoke(
            cli, ["init", str(synthetic_h5ad), "--dir", str(parent / "child")]
        )
        assert result.exit_code != 0
        combined = result.output + str(result.exception or "")
        assert "Permission denied" in combined or "cannot create the project" in combined
        assert not isinstance(result.exception, KeyError)
    finally:
        parent.chmod(0o755)


def test_init_rejects_an_unimplemented_profile(
    runner: CliRunner, tmp_path: Path, synthetic_h5ad: Path
) -> None:
    result = runner.invoke(cli, ["init", str(synthetic_h5ad), "--profile", "spatial"])
    assert result.exit_code != 0
    assert "spatial" in result.output


def test_doctor_json_is_structured(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["doctor", "--json", "--no-agents"])
    payload = json.loads(result.output)
    assert "diagnostics" in payload
    assert payload["cellimo_version"] == "0.1.0"
    names = {item["name"] for item in payload["diagnostics"]}
    assert {"python", "plugin tree", "plugin manifests", "marimo-pair"} <= names
    for item in payload["diagnostics"]:
        assert item["status"] in {"ok", "warn", "fail", "skip"}


def test_doctor_reports_a_missing_project_as_skipped(runner: CliRunner, tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["doctor", "--json", "--no-agents"])
        payload = json.loads(result.output)
        project = next(item for item in payload["diagnostics"] if item["name"] == "project")
        assert project["status"] == "skip"


def test_doctor_text_output_is_readable(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["doctor", "--no-agents"])
    assert "diagnostics" in result.output
    assert "[ok" in result.output or "[warn" in result.output


def test_check_passes_on_a_fresh_project(runner: CliRunner, project) -> None:
    result = runner.invoke(cli, ["check", str(project.root)])
    assert result.exit_code == 0, result.output


def test_check_json_reports_counts(runner: CliRunner, project) -> None:
    result = runner.invoke(cli, ["check", str(project.root), "--json"])
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["counts"]["error"] == 0
    assert payload["checks_run"] >= 20


def test_check_exits_nonzero_on_a_scientific_error(runner: CliRunner, project) -> None:
    from cellimo.provenance.records import StatisticsRecord

    project.store.append_statistics(
        StatisticsRecord(
            name="bad", test="wilcoxon", mode="confirmatory", unit_level="cell"
        )
    )
    result = runner.invoke(cli, ["check", str(project.root)])
    assert result.exit_code == 1
    assert "C004" in result.output or "C001" in result.output


def test_index_status_without_an_index(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["index", "status"])
    assert result.exit_code == 0
    assert "no index installed" in result.output


def test_index_status_json_without_an_index(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["index", "status", "--json"])
    payload = json.loads(result.output)
    assert payload["installed"] is False


def test_index_status_with_a_fixture_index(
    runner: CliRunner, fixture_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CELLIMO_INDEX_DIR", str(fixture_index))
    result = runner.invoke(cli, ["index", "status"])
    assert result.exit_code == 0
    assert "lexical" in result.output
    assert "notebooks:    2" in result.output


def test_index_install_asks_before_downloading(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["index", "install"], input="n\n")
    assert result.exit_code == 1
    assert "zenodo.org" in result.output
    assert "Not downloading" in result.output


def test_index_install_from_a_local_archive(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zipfile

    payload = {
        "meta": {"version": "test"},
        "workflows": [
            {
                "notebook_id": "demo_nb",
                "title": "Demo",
                "summary": "demo workflow about clustering",
                "source_repository": "demo/repo",
                "package": "scanpy",
                "sections": [{"section_id": "0", "content": "sc.tl.leiden(adata)"}],
            }
        ],
        "documentation": [],
    }
    archive = tmp_path / "index.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("retrieval/cellimo-index.json", json.dumps(payload))

    destination = tmp_path / "installed"
    monkeypatch.setenv("CELLIMO_INDEX_DIR", str(destination))
    result = runner.invoke(
        cli, ["index", "install", "--from-archive", str(archive)]
    )
    assert result.exit_code == 0, result.output
    # The archive's single wrapping directory is stripped, so the index lands
    # where the readers look for it rather than one level too deep.
    assert (destination / "cellimo-index.json").is_file()
    status = runner.invoke(cli, ["index", "status", "--json"])
    payload_out = json.loads(status.output)
    assert payload_out["installed"] is True
    assert payload_out["notebooks"] == 1


def test_install_dry_run_changes_nothing(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["install", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "plugin tree:" in result.output


def test_install_rejects_an_unknown_agent(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["install", "--agents", "copilot"])
    assert result.exit_code != 0
    assert "unknown agent" in result.output


def test_main_returns_an_int_for_a_string_systemexit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SystemExit("message")` must not become `ValueError` in the entry point.

    Called through `main()` directly rather than CliRunner, because CliRunner
    catches SystemExit itself and never exercises the production path.
    """
    from cellimo.cli.main import main

    monkeypatch.chdir(tmp_path)
    code = main(["start"])
    assert isinstance(code, int)
    assert code == 1


def test_main_reports_an_oserror_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_h5ad: Path
) -> None:
    import os

    from cellimo.cli.main import main

    if os.geteuid() == 0:  # pragma: no cover
        pytest.skip("root can write anywhere")
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        code = main(["init", str(synthetic_h5ad), "--dir", str(parent / "child")])
        assert isinstance(code, int)
        assert code == 1
    finally:
        parent.chmod(0o755)


def test_start_without_a_project_explains_itself(runner: CliRunner, tmp_path: Path) -> None:
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(cli, ["start"])
        assert result.exit_code != 0
        assert "cellimo init" in str(result.output) + str(result.exception)


def test_start_print_command_uses_no_token(
    runner: CliRunner, project, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("marimo")
    monkeypatch.chdir(project.root)
    result = runner.invoke(cli, ["start", "--print-command"])
    assert result.exit_code == 0, result.output
    assert "--no-token" in result.output
    assert "analysis.py" in result.output


def test_sessions_reports_nothing_when_none_are_running(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["sessions", "--json"])
    assert result.exit_code == 0
    assert isinstance(json.loads(result.output), list)


def test_mcp_serve_is_wired_to_the_server(runner: CliRunner, monkeypatch) -> None:
    called: dict[str, object] = {}

    def fake_serve(path: str | None = None) -> None:
        called["path"] = path

    import cellimo.mcp.server as server_module

    monkeypatch.setattr(server_module, "serve", fake_serve)
    result = runner.invoke(cli, ["mcp", "serve", "--index-path", "/tmp/idx"])
    assert result.exit_code == 0, result.output
    assert called["path"] == "/tmp/idx"
