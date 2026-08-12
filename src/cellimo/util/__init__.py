"""Small, dependency-light helpers shared by the Cellimo tool runtime."""

from __future__ import annotations

from cellimo.util.atomic import (
    append_jsonl,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    read_json,
    read_jsonl,
)
from cellimo.util.hashing import hash_bytes, hash_file, hash_json, short_hash
from cellimo.util.paths import (
    is_within,
    resolve_in_project,
    same_file,
)
from cellimo.util.time import utc_now_iso

__all__ = [
    "append_jsonl",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "hash_bytes",
    "hash_file",
    "hash_json",
    "is_within",
    "read_json",
    "read_jsonl",
    "resolve_in_project",
    "same_file",
    "short_hash",
    "utc_now_iso",
]
