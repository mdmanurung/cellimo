"""Path safety.

Two properties must hold for every managed write:

1. the target lives inside the project root (no ``..`` traversal, no absolute
   escape, no symlinked directory pointing elsewhere);
2. the target is not the registered source dataset (no overwrite via a
   different spelling, a symlink, or a hard link).

Both are enforced on *fully resolved* paths, because ``artifacts/x.h5ad`` may be
a symlink to ``/data/source.h5ad`` and a string comparison would miss it.

These guarantees cover Cellimo's own APIs only. Arbitrary Python executed in the
notebook by the agent bypasses them entirely; see ``docs/SAFETY.md``.
"""

from __future__ import annotations

import os
from pathlib import Path

from cellimo.errors import PathSafetyError

__all__ = ["is_within", "resolve_existing_parent", "resolve_in_project", "same_file"]


def resolve_existing_parent(path: Path) -> Path:
    """Resolve ``path`` as far as it exists, so symlinked parents are followed.

    ``Path.resolve(strict=False)`` already does this on CPython, but it is
    spelled out here because the behaviour is load-bearing for the containment
    check below.

    Malformed input — an embedded NUL byte, a component longer than the
    filesystem allows — becomes a :class:`PathSafetyError` rather than a raw
    ``ValueError``/``OSError``, so callers see one exception type for "that path
    is not usable" however it got that way.
    """
    try:
        resolved = Path(os.path.realpath(str(path)))
    except (ValueError, OSError) as exc:
        raise PathSafetyError(f"{path!r} is not a usable path: {exc}") from exc
    try:
        os.lstat(resolved)
    except (FileNotFoundError, NotADirectoryError):
        # Not existing yet is normal — outputs are reserved before they are
        # written — and a file where a directory was expected is caught by the
        # containment check instead.
        pass
    except (ValueError, OSError) as exc:
        # ENAMETOOLONG and friends: the path cannot be used at all, and every
        # later ``.exists()`` on it would raise the same way from somewhere less
        # helpful.
        raise PathSafetyError(f"{path!r} is not a usable path: {exc}") from exc
    return resolved


def is_within(root: str | Path, candidate: str | Path) -> bool:
    """Return True when ``candidate`` resolves to a location inside ``root``."""
    root_resolved = resolve_existing_parent(Path(root))
    candidate_resolved = resolve_existing_parent(Path(candidate))
    if candidate_resolved == root_resolved:
        return True
    return root_resolved in candidate_resolved.parents


def resolve_in_project(
    root: str | Path,
    candidate: str | Path,
    *,
    what: str = "path",
) -> Path:
    """Resolve ``candidate`` relative to ``root`` and reject anything outside it.

    Absolute candidates are permitted only when they already live inside the
    project. The returned path is fully resolved, so callers can compare it
    against other resolved paths without worrying about symlinks.
    """
    root_path = Path(root)
    candidate_path = Path(candidate)
    joined = candidate_path if candidate_path.is_absolute() else root_path / candidate_path
    resolved = resolve_existing_parent(joined)
    if not is_within(root_path, resolved):
        raise PathSafetyError(
            f"{what} {candidate!s} resolves to {resolved} which is outside the "
            f"project root {resolve_existing_parent(root_path)}"
        )
    return resolved


def same_file(left: str | Path, right: str | Path) -> bool:
    """Return True when both paths denote the same file on disk.

    Uses ``st_dev``/``st_ino`` when both exist, which catches hard links and
    symlinks alike, and falls back to resolved-path comparison otherwise.
    """
    left_path = Path(left)
    right_path = Path(right)
    try:
        return os.path.samefile(str(left_path), str(right_path))
    except OSError:
        return resolve_existing_parent(left_path) == resolve_existing_parent(right_path)
