"""What the field actually calls, measured from the published corpus.

Three questions are answered by one measurement, which is why this module comes
before the things that use it:

*What is the standard way to do this?* Not what an API surface offers, but what
2,845 published analyses reach for. ``sc.pl.umap`` appears in a quarter of
sampled notebooks; a hand-rolled equivalent appears in none of them by that name.

*Did the agent reinvent something?* If it wrote a matplotlib figure where the
corpus overwhelmingly calls ``sc.pl.violin``, that is visible here.

*What would an expert have called?* The benchmark's ground truth, extracted
rather than hand-labelled.

Two counting decisions, both load-bearing:

**Notebooks, not calls.** One notebook that plots in a loop would otherwise
outvote fifty that plot once. The unit is "how many published analyses reached
for this", which is the question being asked.

**Failures are reported, not hidden.** About 7% of code cells do not parse as
Python — ``%%R`` magics, shell escapes, genuinely broken saves. A silent skip
would let the denominator drift without anyone noticing.

This module imports nothing scientific. It reads JSON and uses ``ast``, so it
runs in the tool runtime where scanpy deliberately is not installed.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cellimo.util.atomic import atomic_write_json, read_json

__all__ = [
    "USAGE_FILENAME",
    "CorpusUsage",
    "build_usage",
    "calls_in_source",
    "load_usage",
]

#: Written beside the index, next to the collections it was derived from.
USAGE_FILENAME = "cellimo-call-usage.json"


def _dotted(node: ast.expr) -> str | None:
    """``sc.pl.umap`` from the AST of a call's function expression.

    Only fully-named chains rooted in a plain name are returned. A call on a
    subscript or a temporary (``adatas[0].obs.groupby``) has no stable name to
    count, and guessing one would inflate the counts with noise.
    """
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def calls_in_source(source: str) -> set[str]:
    """Dotted call names in one cell. Empty when the cell does not parse.

    Bare calls (``print``, ``len``) are dropped: an unqualified name says
    nothing about which library the field reached for.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name and "." in name:
            found.add(name)
    return found


class CorpusUsage(BaseModel):
    """How often each call appears, across how many published notebooks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: call name -> number of notebooks containing it
    notebooks_by_call: dict[str, int] = Field(default_factory=dict)
    notebooks_scanned: int = 0
    code_cells: int = 0
    #: Cells that are not Python — R magics, shell escapes, broken saves.
    unparsed_cells: int = 0

    @property
    def unparsed_share(self) -> float:
        return self.unparsed_cells / self.code_cells if self.code_cells else 0.0

    def count(self, call: str) -> int:
        return self.notebooks_by_call.get(call, 0)

    def most_used(self, prefix: str = "", limit: int = 20) -> list[tuple[str, int]]:
        """The calls the field reaches for, most first.

        ``prefix`` narrows to one namespace — ``"sc.pl."`` for scanpy's
        plotting, which is the question the reinvention check asks.
        """
        matching = [
            (call, n)
            for call, n in self.notebooks_by_call.items()
            if call.startswith(prefix)
        ]
        matching.sort(key=lambda item: (-item[1], item[0]))
        return matching[:limit]


def _notebook_paths(index_root: Path) -> Iterator[Path]:
    base = index_root / "notebook_summaries" / "notebooks"
    if not base.is_dir():
        return
    yield from sorted(base.rglob("*.json"))


def build_usage(index_root: str | Path) -> CorpusUsage:
    """Walk the installed index and count what every notebook calls."""
    root = Path(index_root)
    counts: Counter[str] = Counter()
    scanned = cells = unparsed = 0

    for path in _notebook_paths(root):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        seen: set[str] = set()
        for cell in payload.get("cells") or []:
            if cell.get("cell_type") != "code":
                continue
            source = str(cell.get("content") or "")
            if not source.strip():
                continue
            cells += 1
            found = calls_in_source(source)
            if not found and not _looks_like_python(source):
                unparsed += 1
            seen |= found
        scanned += 1
        counts.update(seen)

    return CorpusUsage(
        notebooks_by_call=dict(counts),
        notebooks_scanned=scanned,
        code_cells=cells,
        unparsed_cells=unparsed,
    )


def _looks_like_python(source: str) -> bool:
    """Whether a cell that yielded no calls was nonetheless valid Python.

    Without this, an import-only cell would be counted as a parse failure and
    the reported failure rate would be a measure of cell style rather than of
    how much of the corpus is not Python.
    """
    try:
        ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    return True


def usage_path(index_root: str | Path) -> Path:
    return Path(index_root) / USAGE_FILENAME


def save_usage(usage: CorpusUsage, index_root: str | Path) -> Path:
    return atomic_write_json(usage_path(index_root), usage.model_dump(mode="json"))


def load_usage(index_root: str | Path) -> CorpusUsage | None:
    """The stored table, or None when the index has not been measured yet."""
    raw = read_json(usage_path(index_root))
    return CorpusUsage.model_validate(raw) if raw is not None else None
