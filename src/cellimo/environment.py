"""Runtime environment capture.

Reproducibility needs the versions that actually ran, not the ones a lockfile
hoped for. This module reads installed distributions with
:mod:`importlib.metadata` — no imports of the scientific stack, so capturing an
environment never drags Scanpy or Torch into the lightweight tool runtime.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import Any

from cellimo.provenance.records import EnvironmentRecord

__all__ = [
    "TRACKED_PACKAGES",
    "capture_environment",
    "detect_environment_manager",
    "detect_project_interpreter",
    "package_version",
]

#: Packages whose versions change results. Missing ones are simply absent from
#: the captured mapping rather than recorded as ``None``.
TRACKED_PACKAGES: tuple[str, ...] = (
    "cellimo",
    "marimo",
    "anndata",
    "scanpy",
    "numpy",
    "scipy",
    "pandas",
    "matplotlib",
    "seaborn",
    "scikit-learn",
    "h5py",
    "zarr",
    "numba",
    "leidenalg",
    "igraph",
    "statsmodels",
    "scvi-tools",
    "torch",
    "squidpy",
    "mudata",
    "muon",
    "decoupler",
    "pydeseq2",
    "harmonypy",
    "scikit-misc",
    "chromadb",
    "sentence-transformers",
    "mcp",
    "pydantic",
)


def package_version(name: str) -> str | None:
    """Return the installed version of ``name``, or ``None`` when absent."""
    try:
        return dist_version(name)
    except PackageNotFoundError:
        return None


def detect_environment_manager(executable: str | Path | None = None) -> str:
    """Guess which tool manages the current interpreter.

    Detection is heuristic and reported as such; ``doctor`` prints it as
    information, never as a gate.
    """
    # Not resolved: a virtualenv is identified by the path you invoke, and
    # following bin/python's symlink lands on the base interpreter, which has no
    # pyvenv.cfg and would be reported as "system".
    exe = Path(_absolute(Path(executable or sys.executable)))
    parts = {part.lower() for part in exe.parts}
    if os.environ.get("PIXI_PROJECT_NAME") or {"pixi", ".pixi"} & parts:
        return "pixi"
    if os.environ.get("CONDA_PREFIX"):
        conda_prefix = Path(os.environ["CONDA_PREFIX"]).resolve()
        if conda_prefix in exe.parents or conda_prefix == exe.parent.parent:
            return "mamba" if os.environ.get("MAMBA_ROOT_PREFIX") else "conda"
    cfg = exe.parent.parent / "pyvenv.cfg"
    if os.environ.get("UV") or cfg.exists():
        # Parse keys rather than substring-matching the whole file: a venv
        # whose base interpreter merely lives under a path containing "uv"
        # is not a uv venv.
        if cfg.exists():
            for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, _value = line.partition("=")
                if key.strip().lower() == "uv":
                    return "uv"
        return "venv"
    if os.environ.get("VIRTUAL_ENV"):
        return "venv"
    return "system"


def detect_project_interpreter(
    root: str | Path, explicit: str | Path | None = None
) -> str:
    """Find the Python that will run the notebook — not the one running Cellimo.

    Cellimo is normally installed with ``uv tool install``, so ``sys.executable``
    points at an isolated tool environment with no Marimo and no Scanpy in it.
    Recording that as the project runtime would make every later check look in
    the wrong place.

    Precedence: an explicit choice, then an activated virtualenv, then a
    ``.venv`` inside the project, then this interpreter — which is the right
    answer when Cellimo was pip-installed into the analysis environment itself.
    """
    if explicit:
        return _absolute(Path(explicit))

    active = os.environ.get("VIRTUAL_ENV")
    if active:
        candidate = Path(active) / _BIN / "python"
        if candidate.exists():
            return _absolute(candidate)

    for name in (".venv", "venv"):
        candidate = Path(root) / name / _BIN / "python"
        if candidate.exists():
            return _absolute(candidate)

    return sys.executable


_BIN = "Scripts" if os.name == "nt" else "bin"


def _absolute(path: Path) -> str:
    """Make ``path`` absolute **without** resolving symlinks.

    A virtualenv's ``bin/python`` is a symlink to the base interpreter, and a
    virtualenv is identified by the path you invoke, not by the file that link
    points at. Calling ``resolve()`` here would silently record the base
    interpreter — an environment with none of the project's packages in it.
    """
    return os.path.abspath(str(Path(path).expanduser()))


def interpreter_version(interpreter: str | Path) -> str:
    """Return ``major.minor`` for ``interpreter``, or an empty string.

    Asking the interpreter itself is also how we confirm it is usable at all, so
    a stale recorded path shows up as an empty version rather than as a silent
    wrong answer.
    """
    candidate = Path(interpreter)
    if str(candidate) == sys.executable:
        return f"{sys.version_info.major}.{sys.version_info.minor}"
    if not candidate.exists():
        return ""
    try:
        completed = subprocess.run(
            [
                str(candidate),
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


_REMOTE_CAPTURE = """\
import json, platform, sys
from importlib.metadata import PackageNotFoundError, version
names = json.loads(sys.argv[1])
packages = {}
for name in names:
    try:
        packages[name] = version(name)
    except PackageNotFoundError:
        pass
print(json.dumps({
    "packages": packages,
    "python_version": platform.python_version(),
    "python_executable": sys.executable,
    "platform": platform.platform(),
}))
"""


def _capture_from(interpreter: Path, names: tuple[str, ...]) -> dict[str, Any] | None:
    """Ask another interpreter what it has installed. ``None`` if it cannot answer."""
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", _REMOTE_CAPTURE, json.dumps(list(names))],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def capture_environment(
    *,
    cellimo_version: str = "",
    random_seed: int = 0,
    extra_packages: tuple[str, ...] = (),
    interpreter: str | Path | None = None,
) -> EnvironmentRecord:
    """Snapshot the interpreter, platform and installed package versions.

    ``interpreter`` names the *project* runtime. It matters: when Cellimo is
    installed with ``uv tool install``, capturing in-process would record the
    tool's own dependencies (pydantic, click, mcp) and none of the scientific
    stack that actually produced the results — an environment record that is
    worse than none, because it looks complete.

    When the project runtime cannot be queried, the record falls back to this
    process and says so by recording a ``queried_interpreter`` that differs
    from ``requested_interpreter``. ``cellimo check`` (S007) reports that
    mismatch rather than letting a snapshot of the wrong environment pass as a
    complete one.
    """
    names = tuple(dict.fromkeys(TRACKED_PACKAGES + tuple(extra_packages)))

    target = Path(interpreter) if interpreter else None
    if target is not None and str(target) != sys.executable and target.exists():
        remote = _capture_from(target, names)
        if remote is not None:
            packages = dict(remote.get("packages", {}))
            return EnvironmentRecord(
                requested_interpreter=str(target),
                queried_interpreter=str(remote.get("python_executable", target)),
                cellimo_version=cellimo_version or packages.get("cellimo", ""),
                python_version=str(remote.get("python_version", "")),
                python_executable=str(remote.get("python_executable", target)),
                platform=str(remote.get("platform", platform.platform())),
                packages=packages,
                random_seed=random_seed,
                environment_manager=detect_environment_manager(target),
            )

    packages = {}
    for name in names:
        found = package_version(name)
        if found is not None:
            packages[name] = found
    return EnvironmentRecord(
        requested_interpreter=str(target) if target else sys.executable,
        queried_interpreter=sys.executable,
        cellimo_version=cellimo_version or packages.get("cellimo", ""),
        python_version=platform.python_version(),
        python_executable=sys.executable,
        platform=platform.platform(),
        packages=packages,
        random_seed=random_seed,
        environment_manager=detect_environment_manager(),
    )
