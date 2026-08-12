"""The generated notebook must be a real Marimo notebook, not a plausible file."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cellimo.marimo_runtime import check_notebook
from cellimo.project.project import Project
from cellimo.project.scaffold import PROFILE_REQUIREMENTS, project_pyproject, render_notebook
from cellimo.resources import template_path

pytest.importorskip("marimo", reason="notebook validation needs marimo")


def test_bundled_template_passes_marimo_check() -> None:
    result = check_notebook(template_path("analysis.py"))
    assert result.ran
    assert result.ok, result.issues


def test_generated_notebook_passes_marimo_check(project: Project) -> None:
    result = check_notebook(project.notebook_path)
    assert result.ran
    assert result.ok, result.issues


def test_generated_notebook_is_byte_identical_to_the_template(project: Project) -> None:
    # No string substitution: the file that is tested is the file that ships.
    assert project.notebook_path.read_bytes() == template_path("analysis.py").read_bytes()


def test_notebook_parses_as_a_marimo_notebook(project: Project) -> None:
    from marimo._ast.load import get_notebook_status

    status = get_notebook_status(str(project.notebook_path))
    assert status.status == "valid"
    assert status.notebook is not None
    assert len(status.notebook.cells) >= 12


def test_notebook_contains_every_required_section(project: Project) -> None:
    text = project.notebook_path.read_text(encoding="utf-8")
    for section in (
        "1. Project setup",
        "2. Project header",
        "3. Dataset audit",
        "4. Experimental-design declaration",
        "5. Analysis plan",
        "6. Quality-control configuration",
        "7. Quality-control execution gate",
        "8. Quality-control diagnostics",
        "9. Registered artifacts and lineage",
        "10. Provenance summary",
        "11. Scientific validation",
    ):
        assert section in text, section


def test_notebook_gates_expensive_work_behind_a_button(project: Project) -> None:
    text = project.notebook_path.read_text(encoding="utf-8")
    assert "mo.ui.run_button" in text
    assert "mo.stop" in text
    # The design controls the specification calls for are all present.
    for control in ("sample_select", "donor_select", "condition_select",
                    "time_select", "batch_select", "study_select", "unit_select"):
        assert control in text, control


def test_notebook_has_no_hidden_pipeline(project: Project) -> None:
    text = project.notebook_path.read_text(encoding="utf-8")
    for forbidden in ("run_full_pipeline", "run_pipeline(", "auto_analyze"):
        assert forbidden not in text, forbidden


def test_a_notebook_that_is_not_valid_python_is_rejected(project: Project) -> None:
    """`marimo check` passes a file with a trailing syntax error; we must not.

    It validates the cell graph it managed to read, so "valid Marimo notebook"
    would be a false claim about a file the interpreter cannot import.
    """
    notebook = project.notebook_path
    notebook.write_text(
        notebook.read_text(encoding="utf-8") + "\nthis is not python(\n", encoding="utf-8"
    )
    result = check_notebook(notebook)
    assert not result.ok
    assert "not valid Python" in result.note
    assert result.issues and result.issues[0]["type"] == "syntax-error"


def test_check_reports_an_unparsable_notebook(project: Project) -> None:
    from click.testing import CliRunner

    from cellimo.cli.main import cli

    project.notebook_path.write_text("def broken(:\n", encoding="utf-8")
    result = CliRunner().invoke(cli, ["check", str(project.root), "--json"])
    payload = json.loads(result.output)
    assert payload["notebook"]["ok"] is False
    assert payload["ok"] is False, "the combined verdict must include the notebook"
    assert result.exit_code == 1


def test_render_notebook_refuses_to_clobber(tmp_path: Path) -> None:
    from cellimo.errors import CellimoError

    target = tmp_path / "analysis.py"
    render_notebook(target)
    with pytest.raises(CellimoError, match="already exists"):
        render_notebook(target)
    render_notebook(target, force=True)


def test_project_pyproject_lists_the_profile_requirements(project: Project) -> None:
    text = (project.root / "pyproject.toml").read_text(encoding="utf-8")
    for requirement in PROFILE_REQUIREMENTS["scanpy"]:
        assert requirement in text


def test_existing_profile_installs_nothing_scientific(project: Project) -> None:
    project.config.environment = project.config.environment.model_copy(
        update={"profile": "existing"}
    )
    text = project_pyproject(project.config)
    assert "scanpy" not in text
    assert "marimo" in text


def test_unimplemented_profile_is_refused(project: Project) -> None:
    from cellimo.errors import CellimoError

    config = project.config.model_copy(deep=True)
    object.__setattr__(config.environment, "profile", "spatial")
    with pytest.raises(CellimoError, match="unknown profile"):
        project_pyproject(config)


def test_notebook_is_not_edited_in_place_by_any_command(project: Project, tmp_path: Path) -> None:
    """Cellimo never writes an existing notebook — the running kernel owns it."""
    before = project.notebook_path.read_bytes()
    backup = tmp_path / "backup.py"
    shutil.copyfile(project.notebook_path, backup)
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id")
    project.write_manifest()
    project.check()
    assert project.notebook_path.read_bytes() == before
