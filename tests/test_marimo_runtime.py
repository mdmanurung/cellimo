"""Marimo discovery, which has to look in the *project* runtime.

Cellimo is normally installed with ``uv tool install``, into an isolated
environment that deliberately contains no Marimo. Searching only next to
``sys.executable`` therefore reported "marimo is not installed" to every
correctly-configured user — this is the regression test for that.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cellimo.diagnostics import run_diagnostics
from cellimo.marimo_runtime import (
    MARIMO_MIN_VERSION,
    _marimo_executable,
    check_notebook,
    detect_marimo,
    edit_command,
)
from cellimo.project.project import Project


def _fake_marimo(directory: Path, version: str = "0.23.16") -> Path:
    """A stand-in ``marimo`` executable that answers ``--version``."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "marimo"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "marimo ' + version + '"; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell shim")
def test_project_runtime_is_preferred_over_this_interpreter(tmp_path: Path) -> None:
    project_bin = tmp_path / "project-venv" / "bin"
    _fake_marimo(project_bin)
    found = _marimo_executable(project_bin / "python")
    assert found == str(project_bin / "marimo")


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell shim")
def test_detect_marimo_reads_the_version_from_the_project_runtime(tmp_path: Path) -> None:
    project_bin = tmp_path / "venv" / "bin"
    _fake_marimo(project_bin, version="0.23.16")
    status = detect_marimo(project_bin / "python")
    assert status.installed
    assert status.version == "0.23.16"
    assert status.compatible


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell shim")
def test_an_old_marimo_is_reported_as_incompatible(tmp_path: Path) -> None:
    project_bin = tmp_path / "venv" / "bin"
    _fake_marimo(project_bin, version="0.21.1")
    status = detect_marimo(project_bin / "python")
    assert status.installed
    assert not status.compatible
    assert MARIMO_MIN_VERSION in status.note


def test_missing_marimo_outside_a_project_is_a_warning_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is broken yet — there is no project runtime to look in."""
    monkeypatch.setattr("cellimo.diagnostics.detect_marimo", lambda *a, **k: _absent())
    monkeypatch.chdir(tmp_path)
    report = run_diagnostics(check_agents=False)
    marimo = next(item for item in report.diagnostics if item.name == "marimo")
    assert marimo.status == "warn"
    assert "no project runtime to check yet" in marimo.detail


def test_missing_marimo_inside_a_project_is_a_failure(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`cellimo start` cannot work, so this one really is a failure."""
    monkeypatch.setattr("cellimo.diagnostics.detect_marimo", lambda *a, **k: _absent())
    report = run_diagnostics(project.root, check_agents=False)
    marimo = next(item for item in report.diagnostics if item.name == "marimo")
    assert marimo.status == "fail"


def _absent():
    from cellimo.marimo_runtime import MarimoStatus

    return MarimoStatus(note="marimo was not found in the project runtime")


def test_check_notebook_reports_not_checked_when_marimo_is_absent(tmp_path: Path) -> None:
    notebook = tmp_path / "analysis.py"
    notebook.write_text("import marimo\n", encoding="utf-8")
    result = check_notebook(notebook, interpreter=tmp_path / "nowhere" / "python")
    # Either marimo is genuinely on PATH here (then it ran), or it is not (then
    # it must say so rather than claiming the notebook is fine).
    if not result.ran:
        assert not result.ok
        assert "not installed" in result.note


def test_edit_command_defaults_to_discoverable_and_loopback(tmp_path: Path) -> None:
    command = edit_command(tmp_path / "analysis.py", executable="/usr/bin/marimo")
    assert command[:3] == ["/usr/bin/marimo", "edit", str(tmp_path / "analysis.py")]
    assert "--no-token" in command
    assert "--host" in command and "127.0.0.1" in command
    assert "--token" not in command


def test_edit_command_can_opt_back_into_a_token(tmp_path: Path) -> None:
    command = edit_command(tmp_path / "analysis.py", executable="/usr/bin/marimo", token=True)
    assert "--token" in command
    assert "--no-token" not in command
