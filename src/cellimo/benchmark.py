"""Held-out function-call evaluation with dataset-level leakage control.

The expert answer is a published notebook already stored in the retrieval
index.  Its calls are extracted with :mod:`ast`; they are never hand-labelled.
The candidate is scored as a set of canonical scientific calls, so import
aliases and model variable names do not turn equivalent choices into misses.

Leakage is checked before scoring.  Every indexed notebook mentioning a dataset
alias is denied to every recorded grounding call, not just the nominated expert
notebook.  Candidate citations must also come from the eligible references
recorded in that trace.  A high score without those conditions is not reported
as a held-out result.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from cellimo.retrieval.citations import malformed_headers, section_sha
from cellimo.retrieval.citations import parse as parse_citations
from cellimo.retrieval.ids import notebook_reference_id, parse_reference_id
from cellimo.util.hashing import hash_bytes, hash_json

__all__ = [
    "BenchmarkResult",
    "BenchmarkSpec",
    "CallScore",
    "DatasetReferenceMatch",
    "GroundingTrace",
    "GroundingTraceEntry",
    "LeakageManifest",
    "build_leakage_manifest",
    "canonical_calls",
    "exclusion_digest",
    "load_benchmark_spec",
    "load_candidate_sources",
    "load_notebook_sources",
    "run_call_benchmark",
    "score_calls",
]


class BenchmarkSpec(BaseModel):
    """A task and its published, still-hidden answer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    task: str
    dataset: str
    dataset_aliases: list[str]
    expert_reference_id: str
    package_roots: list[str]
    expert_cell_orders: list[int] = Field(default_factory=list)


class DatasetReferenceMatch(BaseModel):
    """One notebook conservatively treated as derived from the dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_id: str
    source_path: str
    matched_aliases: list[str]


class LeakageManifest(BaseModel):
    """The exact denylist discovered by scanning the installed notebook store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dataset: str
    aliases: list[str]
    expert_reference_id: str
    excluded_reference_ids: list[str]
    matches: list[DatasetReferenceMatch]
    notebooks_scanned: int


class GroundingTraceEntry(BaseModel):
    """The leakage-relevant part of one recorded ``ground`` result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    exclusion_digest: str
    selected_reference_ids: list[str]
    candidate_reviewed: bool = False
    candidate_sha256: str = ""
    needs_user_decision: bool = False


class GroundingTrace(BaseModel):
    """Every retrieval step used to write one candidate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark_id: str
    entries: list[GroundingTraceEntry]


class CallScore(BaseModel):
    """Exact set comparison between candidate and expert calls."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    expert_calls: list[str]
    candidate_calls: list[str]
    matched_calls: list[str]
    missing_calls: list[str]
    extra_calls: list[str]
    precision: float
    recall: float
    f1: float


class BenchmarkResult(BaseModel):
    """A score plus enough evidence to decide whether it was truly held out."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark_id: str
    task: str
    expert_reference_id: str
    candidate_path: str
    leakage: LeakageManifest
    candidate_citation_reference_ids: list[str]
    leaked_reference_ids: list[str]
    exclusions_applied: bool
    citations_grounded: bool
    citations_resolved: bool
    malformed_citation_lines: list[int]
    candidate_review_passed: bool
    leakage_blocked: bool
    score: CallScore


def load_benchmark_spec(path: str | Path) -> BenchmarkSpec:
    """Read a YAML benchmark definition."""
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return BenchmarkSpec.model_validate(payload)


def _notebook_paths(index_root: Path) -> Iterable[Path]:
    base = index_root / "notebook_summaries" / "notebooks"
    if base.is_dir():
        yield from sorted(base.rglob("*.json"))


def build_leakage_manifest(
    index_root: str | Path,
    *,
    dataset: str,
    aliases: Sequence[str],
    expert_reference_id: str,
) -> LeakageManifest:
    """Find every notebook that literally identifies the benchmark dataset.

    Matching the full stored JSON intentionally over-excludes: a dataset named
    only in prose, a download path, or notebook metadata is still evidence that
    the analysis could reveal the answer.  Under-exclusion invalidates a
    benchmark; over-exclusion merely makes retrieval harder.
    """
    root = Path(index_root)
    clean_aliases = sorted({alias.strip() for alias in aliases if alias.strip()})
    if not clean_aliases:
        raise ValueError("dataset_aliases must contain at least one non-empty alias")

    lowered = {alias: alias.casefold() for alias in clean_aliases}
    matches: list[DatasetReferenceMatch] = []
    scanned = 0
    for path in _notebook_paths(root):
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            continue
        scanned += 1
        notebook_id = payload.get("notebook_id")
        if not isinstance(notebook_id, str) or not notebook_id:
            continue
        haystack = raw.casefold()
        found = sorted(alias for alias, value in lowered.items() if value in haystack)
        if found:
            matches.append(
                DatasetReferenceMatch(
                    reference_id=notebook_reference_id(notebook_id),
                    source_path=str(path.relative_to(root)),
                    matched_aliases=found,
                )
            )

    matches.sort(key=lambda item: item.reference_id)
    excluded = [item.reference_id for item in matches]
    if expert_reference_id not in excluded:
        raise ValueError(
            f"expert reference {expert_reference_id!r} did not match any dataset alias"
        )
    return LeakageManifest(
        dataset=dataset,
        aliases=clean_aliases,
        expert_reference_id=expert_reference_id,
        excluded_reference_ids=excluded,
        matches=matches,
        notebooks_scanned=scanned,
    )


def exclusion_digest(reference_ids: Iterable[str]) -> str:
    """Stable digest recorded by every grounding call in a benchmark trace."""
    return hash_json(sorted(set(reference_ids)))


def _notebook_path(index_root: Path, reference_id: str) -> Path:
    parsed = parse_reference_id(reference_id)
    if parsed.kind != "notebook":
        raise ValueError("the expert answer must be a notebook reference")
    matches = list(
        (index_root / "notebook_summaries" / "notebooks").rglob(
            f"{parsed.identifier}.json"
        )
    )
    if len(matches) != 1:
        raise ValueError(
            f"expected one stored notebook for {reference_id!r}, found {len(matches)}"
        )
    return matches[0]


def _sources_from_payload(
    payload: dict[str, Any], orders: Sequence[int] | None = None
) -> list[str]:
    selected = set(orders or ())
    sources: list[tuple[int, str]] = []
    for position, cell in enumerate(payload.get("cells") or []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        order = cell.get("order", position)
        if not isinstance(order, int) or (selected and order not in selected):
            continue
        raw = cell.get("content", cell.get("source", ""))
        source = "".join(raw) if isinstance(raw, list) else str(raw or "")
        if source.strip():
            sources.append((order, source))
    sources.sort(key=lambda item: item[0])
    return [source for _, source in sources]


def load_notebook_sources(
    index_root: str | Path,
    reference_id: str,
    *,
    orders: Sequence[int] | None = None,
) -> list[str]:
    """Load code cells from one KAI-format stored notebook."""
    root = Path(index_root)
    path = _notebook_path(root, reference_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _sources_from_payload(payload, orders)


def load_candidate_sources(path: str | Path) -> list[str]:
    """Load candidate code from Python, Jupyter, or KAI JSON."""
    candidate = Path(path)
    if candidate.suffix == ".py":
        return [candidate.read_text(encoding="utf-8")]
    if candidate.suffix not in {".ipynb", ".json"}:
        raise ValueError("candidate must be a .py, .ipynb, or .json file")
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("candidate notebook must contain a JSON object")
    return _sources_from_payload(payload)


def _citation_resolves(index_root: Path, reference_id: str, section_id: str, sha: str) -> bool:
    try:
        path = _notebook_path(index_root, reference_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    for position, cell in enumerate(payload.get("cells") or []):
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        order = cell.get("order", position)
        if str(order) != section_id:
            continue
        raw = cell.get("content", cell.get("source", ""))
        source = "".join(raw) if isinstance(raw, list) else str(raw or "")
        return section_sha(source) == sha
    return False


def _dotted(node: ast.expr) -> str | None:
    parts: list[str] = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


class _CanonicalCallCollector(ast.NodeVisitor):
    def __init__(self, package_roots: Sequence[str]) -> None:
        self.package_roots = frozenset(package_roots)
        self.imports: dict[str, str] = {}
        self.objects: dict[str, str] = {}
        self.calls: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            bound = name.asname or name.name.split(".", 1)[0]
            self.imports[bound] = name.name if name.asname else bound

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not node.module:
            return
        for name in node.names:
            if name.name == "*":
                continue
            self.imports[name.asname or name.name] = f"{node.module}.{name.name}"

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        constructor = self._constructor(node.value)
        if constructor is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.objects[target.id] = constructor

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is None:
            return
        self.visit(node.value)
        constructor = self._constructor(node.value)
        if constructor is not None and isinstance(node.target, ast.Name):
            self.objects[node.target.id] = constructor

    def visit_Call(self, node: ast.Call) -> None:
        raw = _dotted(node.func)
        if raw:
            canonical = self._canonical(raw)
            if self._in_scope(canonical):
                self.calls.add(canonical)
        self.generic_visit(node)

    def _canonical(self, call: str) -> str:
        root, separator, rest = call.partition(".")
        prefix = self.objects.get(root, self.imports.get(root, root))
        return prefix + (separator + rest if separator else "")

    def _constructor(self, node: ast.expr) -> str | None:
        if not isinstance(node, ast.Call):
            return None
        raw = _dotted(node.func)
        if raw is None:
            return None
        canonical = self._canonical(raw)
        final = canonical.rsplit(".", 1)[-1]
        if self._in_scope(canonical) and final[:1].isupper():
            return canonical
        return None

    def _in_scope(self, call: str) -> bool:
        root = call.split(".", 1)[0]
        return root in self.package_roots


def canonical_calls(
    sources: Iterable[str], *, package_roots: Sequence[str]
) -> set[str]:
    """Canonical package calls across ordered cells.

    Import aliases are expanded (``sc.pp.neighbors`` becomes
    ``scanpy.pp.neighbors``), and calls on a named class instance are traced to
    that constructor (``model.train`` becomes ``pertpy.tl.SCGEN.train``).
    """
    collector = _CanonicalCallCollector(package_roots)
    for source in sources:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        collector.visit(tree)
    return collector.calls


def score_calls(expert_calls: Iterable[str], candidate_calls: Iterable[str]) -> CallScore:
    """Compare unique calls; repeated plotting cannot inflate the score."""
    expert = set(expert_calls)
    candidate = set(candidate_calls)
    matched = expert & candidate
    precision = len(matched) / len(candidate) if candidate else 0.0
    recall = len(matched) / len(expert) if expert else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return CallScore(
        expert_calls=sorted(expert),
        candidate_calls=sorted(candidate),
        matched_calls=sorted(matched),
        missing_calls=sorted(expert - candidate),
        extra_calls=sorted(candidate - expert),
        precision=precision,
        recall=recall,
        f1=f1,
    )


def _load_trace(path: str | Path) -> GroundingTrace:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return GroundingTrace.model_validate(payload)


def run_call_benchmark(
    index_root: str | Path,
    spec: BenchmarkSpec,
    candidate_path: str | Path,
    grounding_trace_path: str | Path,
) -> BenchmarkResult:
    """Score a candidate only after verifying its held-out grounding trace."""
    leakage = build_leakage_manifest(
        index_root,
        dataset=spec.dataset,
        aliases=spec.dataset_aliases,
        expert_reference_id=spec.expert_reference_id,
    )
    trace = _load_trace(grounding_trace_path)
    if trace.benchmark_id != spec.id:
        raise ValueError(
            f"grounding trace is for {trace.benchmark_id!r}, expected {spec.id!r}"
        )

    denylist = set(leakage.excluded_reference_ids)
    expected_digest = exclusion_digest(denylist)
    exclusions_applied = bool(trace.entries) and all(
        entry.exclusion_digest == expected_digest for entry in trace.entries
    )
    selected = {
        reference_id
        for entry in trace.entries
        for reference_id in entry.selected_reference_ids
    }
    selected_leaks = selected & denylist
    candidate_sources = load_candidate_sources(candidate_path)
    candidate_text = "\n".join(candidate_sources)
    candidate_digest = hash_bytes(candidate_text.encode("utf-8"))
    candidate_review_passed = any(
        entry.candidate_reviewed
        and entry.query == spec.task
        and entry.candidate_sha256 == candidate_digest
        and not entry.needs_user_decision
        for entry in trace.entries
    )
    parsed_citations = parse_citations(candidate_text)
    citations = sorted({citation.reference_id for citation in parsed_citations})
    malformed = malformed_headers(candidate_text)
    citation_set = set(citations)
    leaked = sorted((citation_set & denylist) | selected_leaks)
    citations_grounded = bool(citation_set) and citation_set <= selected
    citations_resolved = bool(parsed_citations) and not malformed and all(
        _citation_resolves(
            Path(index_root),
            citation.reference_id,
            citation.section_id,
            citation.sha,
        )
        for citation in parsed_citations
    )
    leakage_blocked = (
        exclusions_applied
        and citations_grounded
        and citations_resolved
        and candidate_review_passed
        and not leaked
    )

    expert_sources = load_notebook_sources(
        index_root,
        spec.expert_reference_id,
        orders=spec.expert_cell_orders,
    )
    expert_calls = canonical_calls(
        expert_sources,
        package_roots=spec.package_roots,
    )
    candidate_calls = canonical_calls(
        candidate_sources,
        package_roots=spec.package_roots,
    )
    return BenchmarkResult(
        benchmark_id=spec.id,
        task=spec.task,
        expert_reference_id=spec.expert_reference_id,
        candidate_path=str(candidate_path),
        leakage=leakage,
        candidate_citation_reference_ids=citations,
        leaked_reference_ids=leaked,
        exclusions_applied=exclusions_applied,
        citations_grounded=citations_grounded,
        citations_resolved=citations_resolved,
        malformed_citation_lines=malformed,
        candidate_review_passed=candidate_review_passed,
        leakage_blocked=leakage_blocked,
        score=score_calls(expert_calls, candidate_calls),
    )
