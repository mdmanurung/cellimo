"""Atomic writes, hashing and path safety — the guarantees everything else rests on."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cellimo.errors import PathSafetyError
from cellimo.util.atomic import append_jsonl, atomic_write_json, atomic_write_text, read_jsonl
from cellimo.util.hashing import hash_bytes, hash_file, hash_json, short_hash
from cellimo.util.paths import is_within, resolve_in_project, same_file


def test_hash_file_matches_hash_bytes(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    payload = b"cellimo" * 1000
    target.write_bytes(payload)
    assert hash_file(target) == hash_bytes(payload)


def test_hash_json_is_key_order_independent() -> None:
    assert hash_json({"a": 1, "b": 2}) == hash_json({"b": 2, "a": 1})
    assert hash_json({"a": 1}) != hash_json({"a": 2})


def test_short_hash_rejects_zero_length() -> None:
    with pytest.raises(ValueError):
        short_hash("a" * 64, 0)


def test_atomic_write_leaves_no_temporary_files(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "config.json"
    atomic_write_json(target, {"value": 1})
    assert json.loads(target.read_text()) == {"value": 1}
    assert [path.name for path in tmp_path.rglob("*.tmp")] == []


def test_atomic_write_replaces_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "config.txt"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")
    assert target.read_text() == "second"


def test_atomic_write_does_not_leave_partial_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "config.txt"
    atomic_write_text(target, "original")

    def _explode(source: str, destination: str) -> None:
        raise OSError("simulated failure during rename")

    monkeypatch.setattr(os, "replace", _explode)
    with pytest.raises(OSError, match="simulated failure"):
        atomic_write_text(target, "replacement")
    # The pre-existing file is untouched and no debris is left behind.
    assert target.read_text() == "original"
    assert [path.name for path in tmp_path.glob("*.tmp")] == []


def test_json_default_unwraps_numpy_scalars(tmp_path: Path) -> None:
    numpy = pytest.importorskip("numpy")
    target = tmp_path / "params.json"
    atomic_write_json(
        target,
        {"threshold": numpy.float64(0.5), "count": numpy.int64(7), "grid": numpy.arange(3)},
    )
    payload = json.loads(target.read_text())
    # Numbers stay numbers rather than becoming strings.
    assert payload == {"threshold": 0.5, "count": 7, "grid": [0, 1, 2]}


def test_json_default_falls_back_to_string_for_opaque_objects(tmp_path: Path) -> None:
    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    target = tmp_path / "params.json"
    atomic_write_json(target, {"thing": Opaque()})
    assert json.loads(target.read_text()) == {"thing": "<opaque>"}


def test_read_jsonl_skips_a_torn_trailing_line(tmp_path: Path) -> None:
    target = tmp_path / "log.jsonl"
    append_jsonl(target, {"n": 1})
    append_jsonl(target, {"n": 2})
    with target.open("a", encoding="utf-8") as handle:
        handle.write('{"n": 3')  # a crash mid-append
    assert [row["n"] for row in read_jsonl(target)] == [1, 2]


def test_read_jsonl_raises_on_corruption_in_the_middle(tmp_path: Path) -> None:
    target = tmp_path / "log.jsonl"
    target.write_text('{"n": 1}\nnot json\n{"n": 3}\n', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        list(read_jsonl(target))


def test_resolve_in_project_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(PathSafetyError):
        resolve_in_project(root, "../escape.h5ad")
    with pytest.raises(PathSafetyError):
        resolve_in_project(root, "artifacts/../../escape.h5ad")


def test_resolve_in_project_rejects_absolute_outside_paths(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(PathSafetyError):
        resolve_in_project(root, tmp_path / "elsewhere.h5ad")


def test_resolve_in_project_accepts_paths_inside(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "artifacts").mkdir(parents=True)
    resolved = resolve_in_project(root, "artifacts/post_qc.h5ad")
    assert resolved == (root / "artifacts" / "post_qc.h5ad").resolve()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_resolve_in_project_follows_symlinks_out_of_the_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    (root / "artifacts").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "artifacts" / "linked").symlink_to(outside)
    with pytest.raises(PathSafetyError):
        resolve_in_project(root, "artifacts/linked/sneaky.h5ad")


@pytest.mark.skipif(os.name == "nt", reason="POSIX link semantics")
def test_same_file_sees_through_symlinks_and_hard_links(tmp_path: Path) -> None:
    original = tmp_path / "source.h5ad"
    original.write_bytes(b"data")
    symlink = tmp_path / "link.h5ad"
    symlink.symlink_to(original)
    hardlink = tmp_path / "hard.h5ad"
    os.link(original, hardlink)
    assert same_file(original, symlink)
    assert same_file(original, hardlink)
    other = tmp_path / "other.h5ad"
    other.write_bytes(b"data")
    assert not same_file(original, other)


def test_is_within_handles_identical_paths(tmp_path: Path) -> None:
    assert is_within(tmp_path, tmp_path)
