"""Which interpreter is the project runtime, and what is installed in it.

Cellimo is normally installed with ``uv tool install``, into an environment that
deliberately contains no Marimo and no Scanpy. Every one of these tests exists
because getting this wrong makes Cellimo record and report the *tool*
environment as if it were the environment that produced the results.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from cellimo.diagnostics import _profile_packages
from cellimo.environment import (
    TRACKED_PACKAGES,
    capture_environment,
    detect_environment_manager,
    detect_project_interpreter,
    interpreter_version,
)
from cellimo.project.project import Project
from cellimo.project.scaffold import PROFILE_REQUIREMENTS


def _fake_venv(root: Path, *, uv: bool = True) -> Path:
    """A virtualenv whose ``bin/python`` is a symlink, as real ones are."""
    binary = root / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    (binary / "python").symlink_to(sys.executable)
    (root / "pyvenv.cfg").write_text(
        "home = /somewhere\nimplementation = CPython\n" + ("uv = 0.11.14\n" if uv else ""),
        encoding="utf-8",
    )
    return binary / "python"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_explicit_interpreter_is_not_resolved_through_its_symlink(tmp_path: Path) -> None:
    """The whole point of a venv is the path you invoke, not the file behind it."""
    venv_python = _fake_venv(tmp_path / "analysis-env")
    chosen = detect_project_interpreter(tmp_path, venv_python)
    assert chosen == str(venv_python)
    assert chosen != sys.executable


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_an_activated_virtualenv_is_preferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = tmp_path / "activated"
    _fake_venv(venv)
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    assert detect_project_interpreter(tmp_path) == str(venv / "bin" / "python")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_a_dot_venv_in_the_project_is_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    _fake_venv(tmp_path / ".venv")
    assert detect_project_interpreter(tmp_path) == str(tmp_path / ".venv" / "bin" / "python")


def test_falling_back_to_this_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Correct when Cellimo was pip-installed into the analysis environment."""
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    assert detect_project_interpreter(tmp_path) == sys.executable


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_a_virtualenv_is_not_reported_as_a_system_interpreter(tmp_path: Path) -> None:
    venv_python = _fake_venv(tmp_path / "env", uv=True)
    assert detect_environment_manager(venv_python) == "uv"
    plain = _fake_venv(tmp_path / "plain", uv=False)
    assert detect_environment_manager(plain) == "venv"


def test_interpreter_version_of_this_interpreter() -> None:
    assert interpreter_version(sys.executable) == (
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )


def test_interpreter_version_of_a_missing_interpreter(tmp_path: Path) -> None:
    assert interpreter_version(tmp_path / "nope" / "python") == ""


def _fake_interpreter(path: Path, payload: dict[str, Any]) -> Path:
    """An 'interpreter' that answers the capture probe with a canned payload.

    A real second Python is not available in a test, and passing
    ``sys.executable`` is worse than useless here: ``capture_environment``'s own
    guard (``str(target) != sys.executable``) routes that straight back to the
    in-process branch, so such a test cannot reach the subprocess code at all.
    An executable that prints the expected JSON proves the subprocess path ran
    *and* that its answer was used.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\ncat <<'JSON'\n" + json.dumps(payload) + "\nJSON\n", encoding="utf-8"
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell shim")
def test_capture_environment_uses_the_other_interpreters_answer(tmp_path: Path) -> None:
    """The project runtime's packages, not this process's."""
    sentinel = {
        "packages": {"scanpy": "9.9.9-sentinel", "cellimo": "0.1.0"},
        "python_version": "3.11.99",
        "python_executable": "/somewhere/else/bin/python",
        "platform": "SentinelOS",
    }
    fake = _fake_interpreter(tmp_path / "env" / "bin" / "python", sentinel)

    record = capture_environment(interpreter=fake)

    # These values exist nowhere in this process — they can only have come from
    # the subprocess.
    assert record.packages["scanpy"] == "9.9.9-sentinel"
    assert record.python_version == "3.11.99"
    assert record.platform == "SentinelOS"
    assert record.python_executable == "/somewhere/else/bin/python"
    assert record.requested_interpreter == str(fake)
    assert record.queried_interpreter == "/somewhere/else/bin/python"
    assert record.packages.get("pytest") is None, "did not capture this process"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell shim")
def test_capture_environment_marks_a_fallback_rather_than_hiding_it(
    tmp_path: Path,
) -> None:
    """An unusable project interpreter must not look like a successful capture."""
    broken = tmp_path / "env" / "bin" / "python"
    broken.parent.mkdir(parents=True)
    broken.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    broken.chmod(broken.stat().st_mode | stat.S_IEXEC)

    record = capture_environment(interpreter=broken)

    assert record.python_executable == sys.executable
    assert record.requested_interpreter == str(broken)
    assert record.queried_interpreter == sys.executable
    assert record.requested_interpreter != record.queried_interpreter


def test_a_fallback_snapshot_is_reported_by_check(project: Project) -> None:
    """S007 must surface a snapshot taken from the wrong interpreter."""
    from cellimo.provenance.records import EnvironmentRecord

    project.store.write_environment(
        EnvironmentRecord(
            packages={"cellimo": "0.1.0"},
            requested_interpreter="/project/env/bin/python",
            queried_interpreter=sys.executable,
            python_executable=sys.executable,
        )
    )
    project.config.environment = project.config.environment.model_copy(
        update={"interpreter": "/project/env/bin/python"}
    )
    project.save()
    findings = [item for item in project.check().findings if item.code == "S007"]
    assert findings
    assert "wrong interpreter" in findings[0].title


def test_capture_environment_falls_back_when_the_interpreter_is_unusable(
    tmp_path: Path,
) -> None:
    record = capture_environment(interpreter=tmp_path / "not-a-python")
    # Falls back to this process rather than recording an empty environment.
    assert record.python_executable == sys.executable
    assert record.packages


def test_tracked_packages_cover_every_profile_requirement() -> None:
    """A profile package missing here would make doctor warn about nothing."""
    for profile in PROFILE_REQUIREMENTS:
        for name in _profile_packages(profile):
            assert name in TRACKED_PACKAGES, f"{name} (profile {profile}) is not tracked"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_init_records_the_project_runtime_not_the_tool_runtime(
    tmp_path: Path, synthetic_h5ad: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    root = tmp_path / "project"
    root.mkdir()
    source = root / "source.h5ad"
    source.write_bytes(synthetic_h5ad.read_bytes())
    venv_python = _fake_venv(root / ".venv")

    project = Project.init(root, source, name="runtime")
    assert project.config.environment.interpreter == str(venv_python)
    assert project.config.environment.manager == "uv"
    # And it survives a reload.
    assert Project.open(root).config.environment.interpreter == str(venv_python)


def test_init_honours_an_explicit_interpreter(tmp_path: Path, synthetic_h5ad: Path) -> None:
    root = tmp_path / "explicit"
    root.mkdir()
    source = root / "source.h5ad"
    source.write_bytes(synthetic_h5ad.read_bytes())
    project = Project.init(root, source, name="explicit", interpreter=sys.executable)
    assert project.config.environment.interpreter == sys.executable
    assert project.config.environment.python == (
        f"{sys.version_info.major}.{sys.version_info.minor}"
    )
