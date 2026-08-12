"""Content hashing.

SHA-256 is the single identity function in Cellimo: source data, artifacts and
retrieval references are all identified by the hash of their bytes, so lineage
survives file moves and renames.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

__all__ = ["CHUNK_SIZE", "hash_bytes", "hash_file", "hash_json", "short_hash"]

CHUNK_SIZE = 1024 * 1024


def hash_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def hash_file(path: str | Path, *, chunk_size: int = CHUNK_SIZE) -> str:
    """Return the hex SHA-256 digest of the file at ``path``.

    The file is streamed, so hashing a 40 GB ``.h5ad`` does not read it into
    memory. Raises ``FileNotFoundError`` if the path does not exist.
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def hash_json(obj: Any) -> str:
    """Return the SHA-256 digest of a JSON-serialisable object.

    Keys are sorted and separators are fixed so the digest is stable across
    processes and Python versions.
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hash_bytes(payload.encode("utf-8"))


def short_hash(digest: str, length: int = 12) -> str:
    """Return a truncated digest for display and identifier construction."""
    if length <= 0:
        raise ValueError("length must be positive")
    return digest[:length]
