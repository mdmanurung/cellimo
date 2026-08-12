"""Locating Cellimo's own files and user-level data directories.

Two layouts have to work: an installed wheel (where ``plugin/`` is force-included
as ``cellimo/_plugin``) and a source checkout (where it is ``<repo>/plugin``).
Everything that needs the plugin tree or the notebook template goes through here
rather than guessing relative paths.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir, user_state_dir

from cellimo.errors import CellimoError

__all__ = [
    "cellimo_data_dir",
    "index_root",
    "marimo_server_registry_dirs",
    "plugin_root",
    "template_path",
    "vendored_marimo_pair_root",
]

_APP_NAME = "cellimo"


def plugin_root() -> Path:
    """Return the directory containing ``plugin.toml``, ``skills/`` and manifests."""
    installed = Path(__file__).resolve().parent / "_plugin"
    if (installed / "plugin.toml").is_file():
        return installed
    checkout = Path(__file__).resolve().parents[2] / "plugin"
    if (checkout / "plugin.toml").is_file():
        return checkout
    raise CellimoError(
        "cannot locate the Cellimo plugin tree; expected it at "
        f"{installed} (installed) or {checkout} (source checkout)"
    )


def vendored_marimo_pair_root() -> Path:
    """Return the vendored, pinned copy of the marimo-pair skill."""
    return plugin_root() / "skills" / "marimo-pair"


def template_path(name: str = "analysis.py") -> Path:
    """Return a bundled notebook template by filename."""
    candidate = Path(__file__).resolve().parent / "templates" / name
    if not candidate.is_file():
        raise CellimoError(f"bundled template {name!r} is missing from {candidate.parent}")
    return candidate


def cellimo_data_dir() -> Path:
    """User-level data directory, overridable with ``CELLIMO_HOME``."""
    override = os.environ.get("CELLIMO_HOME")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir(_APP_NAME, appauthor=False))


def index_root() -> Path:
    """Where retrieval indexes are installed.

    ``CELLIMO_INDEX_DIR`` overrides it, which is what the tests use so they never
    touch a real user installation.
    """
    override = os.environ.get("CELLIMO_INDEX_DIR")
    if override:
        return Path(override).expanduser()
    return cellimo_data_dir() / "index"


def marimo_server_registry_dirs() -> list[Path]:
    """Directories where marimo records its running servers.

    Mirrors what marimo-pair's ``discover-servers.sh`` reads: one JSON file per
    running instance, written only by servers started with ``--no-token``.
    """
    candidates: list[Path] = []
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        candidates.append(Path(xdg_state) / "marimo" / "servers")
    else:
        candidates.append(Path(user_state_dir("marimo", appauthor=False)) / "servers")
        candidates.append(Path.home() / ".local" / "state" / "marimo" / "servers")
    candidates.append(Path.home() / ".marimo" / "servers")
    seen: list[Path] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return seen
