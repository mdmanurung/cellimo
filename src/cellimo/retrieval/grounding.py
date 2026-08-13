"""One read-only call from an analysis task to code worth adapting.

``search_workflows`` finds notebooks and ``get_reference`` reads cells.  The
gap between them matters: the published summary index does not identify the
relevant cells, so handing an agent a whole notebook can mean handing it ninety
unrelated cells and asking it to search again by eye.

``ground`` composes the two operations without adding another reasoning agent:

* rank code sections by literal overlap with the task and nearby prose;
* keep at most five sections, each with its ``# cellimo:source`` header;
* distinguish tutorial/API-shaped sources from paper-companion practice; and
* reject recognised C004, C006 and C008 method errors against the project's
  declared design before the code reaches a notebook;
* when proposed code is supplied, check custom AnnData plots against corpus
  usage and installed native signatures before the cell is created.

No dataset is opened and no code is run.  When there is no relevant, defensible
precedent, the result says that a user decision is required.  It never turns an
empty search into permission to write from memory.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from cellimo.corpus import CorpusUsage, calls_in_source
from cellimo.errors import CellimoError
from cellimo.reinvention import Reinvention, review_source
from cellimo.retrieval.base import KnowledgeIndex
from cellimo.retrieval.models import Reference, ReferenceSection, SearchHit
from cellimo.schema import DesignStatus, Severity

if TYPE_CHECKING:  # pragma: no cover
    from cellimo.project.project import Project

__all__ = [
    "GroundedCode",
    "GroundingDesign",
    "GroundingFinding",
    "GroundingMode",
    "GroundingResult",
    "SourceRole",
    "design_from_project",
    "ground",
]

GroundingMode = Literal["auto", "exploratory", "confirmatory"]
ResolvedMode = Literal["exploratory", "confirmatory"]
SourceRole = Literal["api_usage", "in_practice"]

_WORD = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "do",
        "for",
        "from",
        "how",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "use",
        "using",
        "with",
    }
)

# These markers classify the *role* of a source, not its quality.  The user
# chose to see both: tutorials answer which function to call; paper companion
# code shows how that function is used on real data.
_API_SOURCE_MARKERS = (
    "/docs/",
    "best-practice",
    "best_practice",
    "cookbook",
    "example",
    "jupyter-book",
    "tutorial",
    "vignette",
)

_CONFIRMATORY_QUERY_MARKERS = (
    "association",
    "case control",
    "compare",
    "comparison",
    "differential abundance",
    "differential expression",
    "effect of",
    "treatment effect",
    "versus",
)

_TEST_MARKERS = (
    "deseq",
    "differential_expression",
    "edger",
    "limma",
    "mannwhitney",
    "rank_genes_groups",
    "ttest",
    "wilcoxon",
)

_REPLICATE_AWARE_MARKERS = (
    ".groupby(",
    "aggregate",
    "dreamlet",
    "get_pseudobulk",
    "mixed_model",
    "mixedlm",
    "muscat",
    "pseudobulk",
    "random_effect",
)

_CORRECTED_MARKERS = (
    "bbknn",
    "combat",
    "harmony",
    "integrated_expression",
    "scanvi",
    "scvi",
    "sc.pp.scale",
    "x_integrated",
    "x_scvi",
)

_UNCORRECTED_INPUT_MARKERS = (
    "layer='counts'",
    'layer="counts"',
    "use_raw=true",
)

_QC_METRIC_MARKERS = (
    "n_genes",
    "n_genes_by_counts",
    "pct_counts",
    "total_counts",
    "mito",
    "mitochond",
)


class GroundingDesign(BaseModel):
    """The small, read-only part of a project that method checks need."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool = False
    project: str = ""
    status: DesignStatus = "unresolved"
    experimental_unit: str = ""
    n_experimental_units: int | None = None
    sample_column: str = ""
    n_samples: int | None = None
    condition: str = ""
    declared_fields: dict[str, str] = Field(default_factory=dict)


class GroundingFinding(BaseModel):
    """Why a retrieved section was not offered for adaptation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    severity: Severity
    title: str
    detail: str
    reference_id: str = ""
    section_id: str = ""
    remedy: str = ""


class GroundedCode(BaseModel):
    """One relevant, checked code section, ready for the agent to adapt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_role: SourceRole
    reference_id: str
    section_id: str
    title: str = ""
    heading: str = ""
    source_repository: str = ""
    source_path: str = ""
    url: str = ""
    package: str = ""
    content: str
    calls: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    retrieval_score: float = 0.0


class GroundingResult(BaseModel):
    """Relevant precedent, or an explicit instruction to ask the user."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    analysis_mode: ResolvedMode
    backend: str = ""
    design: GroundingDesign = Field(default_factory=GroundingDesign)
    api_usage: list[GroundedCode] = Field(default_factory=list)
    in_practice: list[GroundedCode] = Field(default_factory=list)
    candidate_reviewed: bool = False
    reinvention: list[Reinvention] = Field(default_factory=list)
    rejected: list[GroundingFinding] = Field(default_factory=list)
    needs_user_decision: bool = False
    note: str = ""

    @property
    def examples(self) -> list[GroundedCode]:
        return [*self.api_usage, *self.in_practice]


@dataclass(frozen=True)
class _Candidate:
    hit: SearchHit
    section: ReferenceSection
    source_role: SourceRole
    retrieval_rank: int
    relevance: int
    matched_terms: tuple[str, ...]
    ancestry: str


def design_from_project(project: Project | None) -> GroundingDesign:
    """Read design fields and audit cardinalities without opening the dataset."""
    if project is None:
        return GroundingDesign()

    design = project.config.design
    unit = design.experimental_unit or ""
    sample = design.sample or design.donor or ""
    cardinalities = _audit_cardinalities(project)
    return GroundingDesign(
        available=True,
        project=project.config.project.name,
        status=design.status,
        experimental_unit=unit,
        n_experimental_units=cardinalities.get(unit) if unit else None,
        sample_column=sample,
        n_samples=cardinalities.get(sample) if sample else None,
        condition=design.condition or "",
        declared_fields=design.declared_fields(),
    )


def _audit_cardinalities(project: Project) -> dict[str, int]:
    """Column cardinalities from the newest readable registered audit."""
    for descriptor in reversed(project.store.artifacts()):
        if descriptor.kind != "audit":
            continue
        path = project.root / descriptor.path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        columns = payload.get("obs_columns") if isinstance(payload, dict) else None
        if not isinstance(columns, list):
            continue
        found: dict[str, int] = {}
        for column in columns:
            if not isinstance(column, dict):
                continue
            name = column.get("name")
            count = column.get("n_unique")
            if isinstance(name, str) and isinstance(count, int) and count >= 0:
                found[name] = count
        if found:
            return found
    return {}


def ground(
    index: KnowledgeIndex,
    query: str,
    *,
    design: GroundingDesign | None = None,
    packages: Sequence[str] | None = None,
    modalities: Sequence[str] | None = None,
    top_k: int = 5,
    analysis_mode: GroundingMode = "auto",
    candidate_code: str | None = None,
    usage: CorpusUsage | None = None,
    signatures: Mapping[str, Sequence[str]] | None = None,
) -> GroundingResult:
    """Find cited cells and optionally preflight the exact proposed adaptation.

    A section is relevant only when the task shares a concrete term with its
    code, heading, or neighbouring prose.  Backend scores rank notebooks, not
    cells, and have different scales across backends; inventing one global
    numeric threshold would make an empty result look more principled than it
    is.  Literal section evidence is therefore the admission rule.

    Pass ``candidate_code`` on the second call, after adapting one result in
    working memory but before creating the notebook cell. That review fails
    closed when the evidence needed to settle possible reinvention is missing.
    """
    task = query.strip()
    proposed = (candidate_code or "").strip()
    resolved_mode = _resolve_mode(task, analysis_mode)
    current_design = design or GroundingDesign()
    limit = max(1, min(int(top_k), 8))
    search = index.search_workflows(
        task,
        packages=packages,
        modalities=modalities,
        top_k=max(8, limit * 3),
    )

    candidates: list[_Candidate] = []
    read_failures: list[str] = []
    for rank, hit in enumerate(search.hits):
        try:
            reference = index.get_reference(hit.reference_id, with_provenance=False)
        except CellimoError as exc:
            read_failures.append(f"{hit.reference_id}: {exc}")
            continue
        candidates.extend(_candidates(task, hit, reference, rank))

    candidates.sort(
        key=lambda item: (
            -item.relevance,
            item.retrieval_rank,
            item.section.order,
            item.hit.reference_id,
        )
    )

    safe: list[_Candidate] = []
    rejected: list[GroundingFinding] = []
    # A noisy notebook can contain dozens of weak one-word matches.  Twenty is
    # enough room to find five safe cells while keeping the rejection report
    # readable and the number of follow-up reference reads bounded.
    for candidate in candidates[:20]:
        findings = _design_findings(candidate, current_design, resolved_mode)
        if findings:
            rejected.extend(findings)
        else:
            safe.append(candidate)

    selected = _balanced(safe, limit)
    examples: list[GroundedCode] = []
    for candidate in selected:
        try:
            reference = index.get_reference(
                candidate.hit.reference_id, [candidate.section.section_id]
            )
        except CellimoError as exc:
            read_failures.append(f"{candidate.hit.reference_id}: {exc}")
            continue
        if not reference.sections:
            continue
        section = reference.sections[0]
        if section.truncated:
            rejected.append(
                GroundingFinding(
                    code="G001",
                    severity="warning",
                    title="Relevant source cell is too large to adapt safely",
                    detail=(
                        f"{candidate.hit.reference_id} section "
                        f"{candidate.section.section_id} was truncated by "
                        "the retrieval safety bound"
                    ),
                    reference_id=candidate.hit.reference_id,
                    section_id=candidate.section.section_id,
                    remedy="Read the source directly and narrow the task before adapting it.",
                )
            )
            continue
        examples.append(_to_grounded(candidate, section))

    api = [item for item in examples if item.source_role == "api_usage"]
    practice = [item for item in examples if item.source_role == "in_practice"]
    candidate_reviewed = False
    reinvention: list[Reinvention] = []
    if proposed:
        if usage is None:
            rejected.append(
                GroundingFinding(
                    code="G002",
                    severity="warning",
                    title="Corpus usage is unavailable for the proposed cell",
                    detail=(
                        "the installed index has no cellimo-call-usage.json, so "
                        "ground could not check whether the cell reinvents a "
                        "field-standard function"
                    ),
                    remedy="Rebuild the index call table, then ground the cell again.",
                )
            )
        else:
            preliminary = review_source(proposed, usage=usage, signatures=None)
            if not preliminary:
                # No hand-built AnnData plot (or a native plot is already used),
                # so installed plotting signatures are irrelevant to this cell.
                candidate_reviewed = True
            elif not signatures:
                rejected.append(
                    GroundingFinding(
                        code="G003",
                        severity="warning",
                        title="Installed native signatures are unavailable",
                        detail=(
                            "the proposed cell may reinvent a native plot, but ground "
                            "could not ask the project interpreter which candidates "
                            "and parameters are installed"
                        ),
                        remedy=(
                            "Repair the recorded project interpreter, then ground the "
                            "cell again."
                        ),
                    )
                )
            else:
                reinvention = review_source(
                    proposed,
                    usage=usage,
                    signatures=signatures,
                )
                candidate_reviewed = True

    needs_user = (
        not examples
        or bool(reinvention)
        or (bool(proposed) and not candidate_reviewed)
    )
    notes = [part for part in [search.note] if part]
    if not current_design.available:
        notes.append("no Cellimo project was found; design checks were not applied")
    if not candidates:
        notes.append("no code section had concrete term overlap with the task")
    if rejected:
        codes = sorted({finding.code for finding in rejected})
        notes.append(f"{len(rejected)} grounding finding(s): " + ", ".join(codes))
    if reinvention:
        notes.append(
            "the proposed cell reinvents a native plotting function; ask the user "
            "whether to use the native candidate or keep the custom implementation"
        )
    if read_failures:
        notes.append(f"{len(read_failures)} reference read(s) failed")
    if needs_user:
        notes.append(
            "no relevant, checked precedent remains; stop and ask the user before "
            "writing an analysis cell"
        )
    return GroundingResult(
        query=task,
        analysis_mode=resolved_mode,
        backend=search.backend,
        design=current_design,
        api_usage=api,
        in_practice=practice,
        candidate_reviewed=candidate_reviewed,
        reinvention=reinvention,
        rejected=rejected[:20],
        needs_user_decision=needs_user,
        note="; ".join(notes),
    )


def _candidates(
    query: str, hit: SearchHit, reference: Reference, retrieval_rank: int
) -> list[_Candidate]:
    query_terms = _terms(query) - _STOP_WORDS
    if not query_terms:
        return []
    sections = reference.sections
    role = _source_role(hit, reference)
    found: list[_Candidate] = []
    for position, section in enumerate(sections):
        if section.kind != "code":
            continue
        prose = "\n".join(
            neighbour.content
            for neighbour in sections[max(0, position - 2) : position + 3]
            if neighbour.kind != "code"
        )
        heading_terms = _terms(section.heading)
        code_terms = _terms(section.content)
        prose_terms = _terms(prose)
        matched = query_terms & (heading_terms | code_terms | prose_terms)
        if not matched:
            continue
        relevance = (
            5 * len(query_terms & heading_terms)
            + 3 * len(query_terms & code_terms)
            + 2 * len(query_terms & prose_terms)
        )
        ancestry = "\n".join(
            item.content
            for item in sections[max(0, position - 3) : position + 1]
            if item.kind == "code"
        )
        found.append(
            _Candidate(
                hit=hit,
                section=section,
                source_role=role,
                retrieval_rank=retrieval_rank,
                relevance=relevance,
                matched_terms=tuple(sorted(matched)),
                ancestry=ancestry,
            )
        )
    return found


def _terms(text: str) -> set[str]:
    # Underscores and dotted paths become word boundaries, so
    # ``sc.pp.filter_cells`` can answer "filter cells".
    lowered = text.lower()
    terms = set(_WORD.findall(lowered.replace("_", " ")))
    # Two native names describe their implementation rather than the task a
    # scientist asks for.  Without these aliases, a pure-code cell containing
    # rank_genes_groups has zero literal overlap with "differential expression",
    # and filter_cells has none with "quality control".  Keep this deliberately
    # narrow; the neighbouring prose remains the general section selector.
    if "rank_genes_groups" in lowered:
        terms.update({"de", "differential", "expression", "genes", "markers"})
    if "filter_cells" in lowered or "calculate_qc_metrics" in lowered:
        terms.update({"cells", "control", "filter", "qc", "quality"})
    return terms


def _source_role(hit: SearchHit, reference: Reference) -> SourceRole:
    label = " ".join(
        [
            hit.title,
            hit.source_repository,
            hit.source_path,
            reference.title,
            reference.source_repository,
            reference.source_path,
        ]
    ).lower()
    return (
        "api_usage"
        if any(marker in label for marker in _API_SOURCE_MARKERS)
        else "in_practice"
    )


def _resolve_mode(query: str, requested: GroundingMode) -> ResolvedMode:
    if requested in {"exploratory", "confirmatory"}:
        return requested
    lowered = f" {query.lower()} "
    if re.search(r"\bde\b", lowered) or re.search(r"\bvs\.?\b", lowered):
        return "confirmatory"
    if any(marker in lowered for marker in _CONFIRMATORY_QUERY_MARKERS):
        return "confirmatory"
    return "exploratory"


def _design_findings(
    candidate: _Candidate,
    design: GroundingDesign,
    mode: ResolvedMode,
) -> list[GroundingFinding]:
    if not design.available:
        return []
    source = candidate.ancestry.lower()
    current = candidate.section.content.lower()
    findings: list[GroundingFinding] = []

    if mode == "confirmatory" and _contains_any(current, _TEST_MARKERS):
        if not _contains_any(source, _REPLICATE_AWARE_MARKERS):
            if not design.experimental_unit:
                detail = (
                    "the retrieved cell performs a confirmatory test, but the project "
                    "has no approved experimental unit"
                )
            elif design.n_experimental_units == 1:
                detail = (
                    f"the audit records one level of {design.experimental_unit!r}; "
                    "between-replicate variance cannot be estimated, so this must be "
                    "settled as exploratory or redesigned by the user"
                )
            else:
                count = (
                    f" ({design.n_experimental_units} levels in the latest audit)"
                    if design.n_experimental_units is not None
                    else ""
                )
                detail = (
                    "the retrieved cell runs a cell-level confirmatory test without "
                    f"pseudobulk or a mixed model, while {design.experimental_unit!r} "
                    f"is the biological replicate{count}"
                )
            findings.append(
                _finding(
                    candidate,
                    "C004",
                    "Retrieved code would treat cells as biological replicates",
                    detail,
                    (
                        "Use donor/sample pseudobulk or a replicate-aware mixed model; "
                        "if this is intentionally exploratory, ask the user to settle it."
                    ),
                )
            )

        if _contains_any(source, _CORRECTED_MARKERS) and not _contains_any(
            current, _UNCORRECTED_INPUT_MARKERS
        ):
            findings.append(
                _finding(
                    candidate,
                    "C006",
                    "Retrieved confirmatory code depends on corrected values",
                    (
                        "the test is downstream of a scale, Harmony, scVI/scanVI, "
                        "ComBat or integrated-expression step and does not explicitly "
                        "select an uncorrected counts input"
                    ),
                    (
                        "Test counts or log-normalised expression with batch as a "
                        "covariate; use corrected representations for structure only."
                    ),
                )
            )

    if (
        _looks_like_pooled_qc(current)
        and not _looks_stratified(source, design)
        and design.n_samples != 1
        and design.sample_column
    ):
        count = (
            f" ({design.n_samples} levels in the latest audit)"
            if design.n_samples is not None
            else ""
        )
        findings.append(
            _finding(
                candidate,
                "C008",
                "Retrieved QC code pools samples",
                (
                    "the cell applies a cell-exclusion threshold without grouping "
                    f"by {design.sample_column!r}{count}"
                ),
                "Compute thresholds within each sample and retain per-sample counts.",
                severity="warning",
            )
        )
    return findings


def _finding(
    candidate: _Candidate,
    code: str,
    title: str,
    detail: str,
    remedy: str,
    *,
    severity: Severity = "error",
) -> GroundingFinding:
    return GroundingFinding(
        code=code,
        severity=severity,
        title=title,
        detail=detail,
        reference_id=candidate.hit.reference_id,
        section_id=candidate.section.section_id,
        remedy=remedy,
    )


def _contains_any(source: str, markers: Sequence[str]) -> bool:
    return any(marker in source for marker in markers)


def _looks_like_pooled_qc(source: str) -> bool:
    if "filter_cells" in source:
        return True
    return "adata[" in source and _contains_any(source, _QC_METRIC_MARKERS)


def _looks_stratified(source: str, design: GroundingDesign) -> bool:
    sample = design.sample_column.lower()
    if not sample or sample not in source:
        return False
    return any(
        marker in source
        for marker in ("groupby", "stratified_by", "by_sample", ".unique()")
    )


def _balanced(candidates: list[_Candidate], limit: int) -> list[_Candidate]:
    """Keep both source roles visible when both have relevant evidence."""
    chosen: list[_Candidate] = []
    for role in ("api_usage", "in_practice"):
        candidate = next(
            (item for item in candidates if item.source_role == role), None
        )
        if candidate is not None and candidate not in chosen:
            chosen.append(candidate)
            if len(chosen) == limit:
                return chosen
    for candidate in candidates:
        if candidate not in chosen:
            chosen.append(candidate)
            if len(chosen) == limit:
                break
    return chosen


def _to_grounded(candidate: _Candidate, section: ReferenceSection) -> GroundedCode:
    return GroundedCode(
        source_role=candidate.source_role,
        reference_id=candidate.hit.reference_id,
        section_id=section.section_id,
        title=candidate.hit.title,
        heading=section.heading,
        source_repository=candidate.hit.source_repository,
        source_path=candidate.hit.source_path,
        url=candidate.hit.url,
        package=candidate.hit.package,
        content=section.content,
        calls=sorted(calls_in_source(candidate.section.content)),
        matched_terms=list(candidate.matched_terms),
        retrieval_score=candidate.hit.score,
    )
