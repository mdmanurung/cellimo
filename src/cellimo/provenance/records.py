"""Typed provenance records.

These are the structures ``cellimo check`` reads. Every scientific rule the
validator enforces is a predicate over these fields — not over free text, and
not over the notebook source — so a project either recorded the fact or it did
not, and the answer is unambiguous.

Record identifiers are content-derived, so writing the same record twice yields
the same id rather than a duplicate with a fresh UUID.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cellimo.schema import (
    AnalysisMode,
    DecisionKind,
    Representation,
    Stage,
    UnitLevel,
)
from cellimo.util.hashing import hash_json, short_hash
from cellimo.util.time import utc_now_iso

__all__ = [
    "DecisionRecord",
    "EffectSizeReport",
    "EnvironmentRecord",
    "Manifest",
    "ReferenceRecord",
    "RunRecord",
    "StatisticsRecord",
    "UncertaintyReport",
    "make_record_id",
]


def make_record_id(prefix: str, payload: dict[str, Any]) -> str:
    """Return a stable ``prefix:hash`` identifier for a record payload."""
    return f"{prefix}:{short_hash(hash_json(payload))}"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DecisionRecord(_Record):
    """One analytical decision and the reasoning behind it.

    Written whenever a choice is made that a reader would otherwise have to
    reverse-engineer from code: a threshold, a filter, a clustering resolution,
    an authorisation.
    """

    record_id: str = ""
    created: str = Field(default_factory=utc_now_iso)
    kind: DecisionKind
    stage: Stage | None = None
    summary: str
    rationale: str = ""
    #: The knobs that were turned, with their values.
    parameters: dict[str, Any] = Field(default_factory=dict)
    #: ``reference_id`` values from ``references.jsonl`` that informed this.
    references: list[str] = Field(default_factory=list)
    #: SHA-256 of artifacts this decision produced or applied to.
    artifacts: list[str] = Field(default_factory=list)
    #: Who made the call. ``user`` and ``agent`` are distinguished so an audit
    #: can tell a human choice from a model's.
    actor: str = "agent"

    @field_validator("actor")
    @classmethod
    def _known_actor(cls, value: str) -> str:
        if value not in {"user", "agent", "cellimo"}:
            raise ValueError(f"actor must be user, agent or cellimo; got {value!r}")
        return value


class ReferenceRecord(_Record):
    """A retrieved reference that informed the analysis.

    ``reference_id`` and ``content_hash`` come from the retrieval index, so a
    later reader can fetch exactly the section that was consulted rather than a
    reconstructed approximation of it.
    """

    record_id: str = ""
    created: str = Field(default_factory=utc_now_iso)
    reference_id: str
    title: str = ""
    source: str = ""
    url: str = ""
    package: str = ""
    package_version: str = ""
    section_ids: list[str] = Field(default_factory=list)
    content_hash: str = ""
    retrieval_score: float | None = None
    query: str = ""
    used_for: str = ""
    stage: Stage | None = None


class EffectSizeReport(_Record):
    """Whether, and how, effect sizes were reported."""

    reported: bool = False
    measure: str = ""  # e.g. log2FC, Cohen's d, proportion difference
    column: str = ""  # column name in the results table


class UncertaintyReport(_Record):
    """Whether, and how, uncertainty was reported."""

    reported: bool = False
    measure: str = ""  # e.g. adjusted p-value, 95% CI, posterior interval
    column: str = ""


class StatisticsRecord(_Record):
    """One statistical comparison.

    This is the record the replication rules are enforced against. It states
    which artifact went in, what its values were, what the unit of replication
    was, and how many independent units each group had — the facts that decide
    whether a p-value means anything.
    """

    record_id: str = ""
    created: str = Field(default_factory=utc_now_iso)
    name: str
    #: e.g. pseudobulk_deseq2, pseudobulk_limma, wilcoxon_rank_sum,
    #: linear_mixed_model, scanpy_rank_genes_groups
    test: str
    #: Exploratory results guide the next step. Confirmatory results are claims
    #: and are held to the replication rules.
    mode: AnalysisMode = "exploratory"

    #: The ``obs`` column that identified independent biological units.
    experimental_unit: str | None = None
    #: What a row in the tested matrix actually was.
    unit_level: UnitLevel = "unknown"
    #: Independent units per compared group, e.g. ``{"stim": 5, "ctrl": 4}``.
    #: Cell counts do not belong here.
    n_units: dict[str, int] = Field(default_factory=dict)
    n_cells: dict[str, int] = Field(default_factory=dict)
    groups: list[str] = Field(default_factory=list)

    #: SHA-256 of the artifact the test consumed.
    input_artifact_sha256: str = ""
    #: What the consumed values were. Batch-corrected expression here without a
    #: justification is a validation error.
    input_representation: Representation = "unknown"
    #: none | pseudobulk | mixed_model — how cells were collapsed to units.
    aggregation: str = "none"
    covariates: list[str] = Field(default_factory=list)

    effect_size: EffectSizeReport = Field(default_factory=EffectSizeReport)
    uncertainty: UncertaintyReport = Field(default_factory=UncertaintyReport)

    #: Required when ``input_representation`` is batch-corrected, or when a
    #: confirmatory test is run at cell level on purpose.
    justification: str = ""
    seed: int | None = None
    output_artifact_sha256: str | None = None
    packages: dict[str, str] = Field(default_factory=dict)

    @field_validator("aggregation")
    @classmethod
    def _known_aggregation(cls, value: str) -> str:
        allowed = {"none", "pseudobulk", "mixed_model", "meta_analysis"}
        if value not in allowed:
            raise ValueError(f"aggregation must be one of {sorted(allowed)}, got {value!r}")
        return value


class EnvironmentRecord(_Record):
    """A snapshot of the runtime that produced the results."""

    captured_at: str = Field(default_factory=utc_now_iso)
    cellimo_version: str = ""
    python_version: str = ""
    python_executable: str = ""
    platform: str = ""
    #: Installed versions of the packages that matter for reproducibility.
    packages: dict[str, str] = Field(default_factory=dict)
    random_seed: int = 0
    environment_manager: str = "unknown"
    #: The interpreter the project asked for, and the one actually queried.
    #: They differ when the project runtime could not be reached and the
    #: snapshot silently fell back to the tool runtime — which would otherwise
    #: look like a complete record of the wrong environment.
    requested_interpreter: str = ""
    queried_interpreter: str = ""


class RunRecord(_Record):
    """One invocation of a Cellimo command."""

    run_id: str
    started: str = Field(default_factory=utc_now_iso)
    finished: str | None = None
    command: str = ""
    argv: list[str] = Field(default_factory=list)
    cwd: str = ""
    cellimo_version: str = ""
    exit_status: int | None = None
    notes: str = ""


class Manifest(_Record):
    """The rolled-up view of a project, regenerated from the append-only logs.

    Nothing here is authoritative on its own: ``manifest.json`` can always be
    rebuilt from ``artifacts.jsonl``, ``decisions.jsonl``, ``references.jsonl``
    and ``statistics.jsonl``. It exists so a reader (or the notebook header) can
    see the state of a project without replaying every log.
    """

    schema_version: int
    cellimo_version: str = ""
    generated: str = Field(default_factory=utc_now_iso)
    project_name: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    design: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)
    #: Latest artifact SHA-256 per stage, in stage order.
    latest_by_stage: dict[str, str] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)
