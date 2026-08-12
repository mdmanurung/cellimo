"""Atomic file writes.

Provenance must survive a kill -9 in the middle of an analysis. Every managed
write goes to a temporary file in the destination directory and is then moved
into place with :func:`os.replace`, which is atomic on POSIX and on Windows for
same-volume moves. Readers therefore never observe a half-written manifest.

JSONL append is the one exception: it is an ``O_APPEND`` write of a single line,
which is atomic for writes below ``PIPE_BUF`` on POSIX and, more importantly, is
recoverable — a torn trailing line can be detected and skipped on read.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

__all__ = [
    "append_jsonl",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "json_default",
    "read_json",
    "read_jsonl",
]


def atomic_write_bytes(path: str | Path, data: bytes, *, mode: int | None = None) -> Path:
    """Write ``data`` to ``path`` atomically. Returns the resolved path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        else:
            # mkstemp creates 0600; relax to the process umask default for
            # ordinary project files so collaborators can read them.
            os.chmod(tmp_path, 0o666 & ~_umask())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    _fsync_dir(target.parent)
    return target


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def _fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync so the rename itself is durable."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # Some filesystems (and some network mounts) refuse directory fsync.
        # The rename is still atomic; only durability across power loss weakens.
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` atomically."""
    return atomic_write_bytes(path, text.encode(encoding))


def json_default(value: Any) -> Any:
    """Convert values JSON does not know, preferring numbers over strings.

    Parameters recorded from a notebook routinely arrive as NumPy scalars and
    arrays. Falling straight back to ``str`` would turn a threshold of ``0.5``
    into the string ``"0.5"``, which silently changes the type of everything the
    validator reads, so numeric types are unwrapped first and only genuinely
    unrepresentable objects become strings.
    """
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return tolist()
        except (TypeError, ValueError):
            pass
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return str(value)


def atomic_write_json(path: str | Path, obj: Any, *, indent: int = 2) -> Path:
    """Serialise ``obj`` as JSON and write it atomically, with a trailing newline."""
    payload = json.dumps(
        obj, indent=indent, sort_keys=False, ensure_ascii=False, default=json_default
    )
    return atomic_write_text(path, payload + "\n")


def append_jsonl(path: str | Path, record: Any) -> Path:
    """Append one JSON record as a line to ``path``, creating parents as needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = (
        json.dumps(record, sort_keys=False, ensure_ascii=False, default=json_default) + "\n"
    )
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return target


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read a JSON file, returning ``default`` when the file does not exist."""
    target = Path(path)
    if not target.exists():
        return default
    return json.loads(target.read_text(encoding="utf-8"))


def read_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    """Yield records from a JSONL file, skipping a torn trailing line.

    A truncated final line is the expected outcome of a crash mid-append; every
    complete record before it is still valid and is returned.
    """
    target = Path(path)
    if not target.exists():
        return
    with target.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            yield json.loads(stripped)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                # Torn trailing write: recoverable, everything before it stands.
                return
            raise
