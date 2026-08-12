"""Creating the files a new project starts with.

The scaffold is deliberately thin: directories, ``cellimo.yaml``, a project
``pyproject.toml`` describing the *project runtime* (not the tool runtime), and
the Marimo notebook. The notebook is copied verbatim from the bundled template —
no string substitution — so the file that ships is exactly the file that is
tested with ``marimo check``, and a template that parses in CI parses in the
project too.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cellimo.config import CONFIG_FILENAME, CellimoConfig
from cellimo.errors import CellimoError
from cellimo.resources import template_path
from cellimo.util.atomic import atomic_write_text

__all__ = ["PROFILE_REQUIREMENTS", "render_notebook", "scaffold_project"]

#: What each profile installs into the project runtime. Only these two are
#: implemented; anything else is rejected rather than half-supported.
PROFILE_REQUIREMENTS: dict[str, list[str]] = {
    "scanpy": [
        "marimo>=0.23.8",
        "anndata>=0.10",
        "scanpy>=1.10",
        "numpy>=1.26",
        "pandas>=2.0",
        "scipy>=1.11",
        "scikit-learn>=1.3",
        "leidenalg>=0.10",
        "igraph>=0.11",
        "matplotlib>=3.9",
        "cellimo>=0.1.0",
    ],
    # "existing" adopts whatever environment the user already has; Cellimo adds
    # only itself and Marimo, and never installs a scientific stack over the top
    # of a working one.
    "existing": [
        "marimo>=0.23.8",
        "cellimo>=0.1.0",
    ],
}

_GITIGNORE = """\
# Large intermediates are reproducible from the source and the notebook.
artifacts/*.h5ad
artifacts/*.zarr/
results/models/
.venv/
__pycache__/
.marimo/

# Provenance is the point of the project — keep it.
!provenance/
"""


def project_pyproject(config: CellimoConfig) -> str:
    """Render the project's own ``pyproject.toml``.

    This describes the environment the *notebook* runs in. It is separate from
    Cellimo's own dependencies on purpose: the tool runtime must stay installable
    in seconds, and the project runtime is where Scanpy and friends live.
    """
    profile = config.environment.profile
    requirements = PROFILE_REQUIREMENTS.get(profile)
    if requirements is None:
        raise CellimoError(
            f"unknown profile {profile!r}; implemented profiles are "
            f"{sorted(PROFILE_REQUIREMENTS)}"
        )
    dependency_lines = "\n".join(f'  "{item}",' for item in requirements)
    slug = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in config.project.name.lower()
    ).strip("-") or "cellimo-project"
    return f"""\
# Project runtime for {config.project.name}.
# Cellimo's own tool runtime is installed separately; this file describes the
# environment the Marimo notebook executes in.
#
# The interpreter this project uses is recorded in {CONFIG_FILENAME} under
# `environment.interpreter`; `cellimo doctor` checks that environment, not
# Cellimo's own. Install into it with, for example:
#     uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python -r <(...)
# Cellimo 0.1.0 is not on PyPI yet, so install it from a checkout or a wheel.
[project]
name = "{slug}"
version = "0.0.0"
description = "Single-cell analysis project managed by Cellimo"
requires-python = ">=3.11"
dependencies = [
{dependency_lines}
]

[tool.cellimo]
profile = "{profile}"
"""


def render_notebook(destination: str | Path, *, force: bool = False) -> Path:
    """Copy the bundled Marimo notebook template to ``destination``."""
    target = Path(destination)
    if target.exists() and not force:
        raise CellimoError(
            f"{target} already exists; pass force=True to overwrite it. "
            f"Never overwrite a notebook that a Marimo session is editing — the "
            f"running kernel is the source of truth."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template_path("analysis.py"), target)
    return target


def scaffold_project(
    root: str | Path,
    config: CellimoConfig,
    *,
    force: bool = False,
    with_notebook: bool = True,
) -> list[Path]:
    """Create the directory tree and starter files. Returns what was written."""
    base = Path(root)
    written: list[Path] = []
    for relative in config.paths.all_dirs():
        (base / relative).mkdir(parents=True, exist_ok=True)

    pyproject = base / "pyproject.toml"
    if force or not pyproject.exists():
        written.append(atomic_write_text(pyproject, project_pyproject(config)))

    gitignore = base / ".gitignore"
    if force or not gitignore.exists():
        written.append(atomic_write_text(gitignore, _GITIGNORE))

    if with_notebook:
        notebook = base / config.paths.notebook
        if force or not notebook.exists():
            written.append(render_notebook(notebook, force=True))

    return written
