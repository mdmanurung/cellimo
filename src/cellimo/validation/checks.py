"""The rules ``cellimo check`` enforces.

Structural checks (``S###``) verify that the record is internally consistent:
files exist, hashes match, lineage closes, arithmetic reconciles.

Scientific checks (``C###``) verify that what was recorded could support the
claims being made. Each one is a predicate over structured provenance, with the
false-positive case handled explicitly — a rule that fires on legitimate work
gets switched off by its users, so exploratory analysis, single-sample designs
and genuinely count-free source data are exempted by construction rather than by
the analyst's patience.

Severity is deliberate. An ``error`` means a result is not interpretable as
stated. A ``warning`` means a reader will have to take something on trust.
"""

from __future__ import annotations

from pathlib import Path

from cellimo.artifacts.descriptor import ArtifactDescriptor
from cellimo.config import DesignSection
from cellimo.provenance.records import StatisticsRecord
from cellimo.schema import INTEGRATED_REPRESENTATIONS
from cellimo.util.hashing import hash_file, short_hash
from cellimo.validation.engine import Finding, ValidationContext, register

__all__ = ["CELL_LEVEL_TESTS", "DE_TESTS"]

SQUAIR = "Squair et al. 2021, Nat Commun 12:5692, doi:10.1038/s41467-021-25960-2"
ZIMMERMAN = "Zimmerman et al. 2021, Nat Commun 12:738, doi:10.1038/s41467-021-21038-1"
MURPHY = "Murphy & Skene 2022, Nat Commun 13:7851, doi:10.1038/s41467-022-35519-4"
HEUMOS = "Heumos et al. 2023, Nat Rev Genet 24:550-572, doi:10.1038/s41576-023-00586-w"
OSCA_QC = "OSCA, 'Quality control redux' — compute thresholds within, not across, batches"
SEURAT_DE = (
    "Seurat integration vignette — 'for performing differential expression after "
    "integration, we switch back to the original data'"
)

#: Test names that are recognisably differential expression. Used only to make
#: *messages* more specific — never to decide whether a rule applies to a
#: record. Nothing here is a gate: a rule that can be escaped by renaming your
#: test is not a rule.
DE_TESTS: tuple[str, ...] = (
    "deseq",
    "edger",
    "limma",
    "voom",
    "wilcoxon",
    "rank_genes_groups",
    "mast",
    "t-test",
    "ttest",
    "differential",
    "glmmtmb",
    "mixed_model",
    "mannwhitney",
    "kruskal",
    "anova",
    "glm",
)

#: Tests that model each cell as an independent observation unless told
#: otherwise. Same caveat: informational, never an exemption.
CELL_LEVEL_TESTS: tuple[str, ...] = (
    "wilcoxon",
    "t-test",
    "ttest",
    "logreg",
    "rank_genes_groups",
    "mannwhitney",
)

#: Unit levels that positively identify a biological replicate. A confirmatory
#: analysis must declare one of these; ``cell`` and ``unknown`` do not qualify.
REPLICATE_UNIT_LEVELS: frozenset[str] = frozenset({"sample", "donor"})

#: Aggregations that estimate between-replicate variance.
REPLICATE_AWARE_AGGREGATIONS: frozenset[str] = frozenset(
    {"pseudobulk", "mixed_model", "meta_analysis"}
)


def _is_de(test: str) -> bool:
    lowered = test.lower()
    return any(marker in lowered for marker in DE_TESTS)


def _label(record_id: str, name: str) -> str:
    return f"statistics:{name or record_id}"


#: A justification has to say something. A single character, "n/a" or "see
#: notebook" is not a reason, and treating it as one turns every error in this
#: file into an opt-out.
_NON_ANSWERS = frozenset(
    {"", "n/a", "na", "none", "tbd", "todo", "see notebook", "see above", "ok", "yes"}
)
_MIN_JUSTIFICATION_CHARS = 25
_MIN_JUSTIFICATION_WORDS = 4


def _is_substantive(justification: str) -> bool:
    """True when a justification is long enough to be an actual reason."""
    text = justification.strip()
    if text.lower().strip(".!- ") in _NON_ANSWERS:
        return False
    return len(text) >= _MIN_JUSTIFICATION_CHARS and len(text.split()) >= _MIN_JUSTIFICATION_WORDS


def _effective_representation(
    context: ValidationContext, record: StatisticsRecord
) -> tuple[str, str | None]:
    """What the analysis *actually* consumed, and the artifact's own claim.

    The statistics record's ``input_representation`` is written by whoever
    recorded the analysis. The artifact descriptor's ``representation`` was
    pinned when the file was hashed. When they disagree, the artifact wins —
    otherwise relabelling the record would be enough to walk past C006.
    """
    artifact = context.by_sha.get(record.input_artifact_sha256)
    if artifact is None:
        return record.input_representation, None
    return artifact.representation, artifact.representation


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


@register("S001", "Registered source data is present and unchanged")
def check_source_integrity(context: ValidationContext) -> list[Finding]:
    ok, message = context.project.verify_source()
    if ok:
        return []
    return [
        Finding(
            code="S001",
            severity="error",
            title="Source dataset is missing or has changed",
            detail=message,
            location=f"source:{context.config.source.path}",
            remedy=(
                "Restore the original file, or initialise a new project if the "
                "dataset was intentionally replaced. Source data is immutable."
            ),
        )
    ]


@register("S002", "Project directories exist")
def check_layout(context: ValidationContext) -> list[Finding]:
    missing = [
        relative
        for relative in context.config.paths.all_dirs()
        if not (context.project.root / relative).is_dir()
    ]
    if not missing:
        return []
    return [
        Finding(
            code="S002",
            severity="warning",
            title="Project directories are missing",
            detail=f"expected but absent: {', '.join(sorted(missing))}",
            remedy="Run `cellimo init` in place, or recreate the directories.",
        )
    ]


@register("S003", "Registered artifacts exist on disk")
def check_artifacts_exist(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for descriptor in context.artifacts:
        if descriptor.stage == "source":
            continue
        path = context.project.root / descriptor.path
        if not path.exists():
            findings.append(
                Finding(
                    code="S003",
                    severity="error",
                    title="Registered artifact is missing from disk",
                    detail=f"{descriptor.path} ({descriptor.stage}) is recorded but absent",
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy=(
                        "Re-run the stage that produced it, or remove the stale "
                        "record from provenance/artifacts.jsonl."
                    ),
                )
            )
    return findings


@register("S004", "Artifact lineage closes on the registered source")
def check_lineage(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    source = context.source_descriptor()
    for descriptor in context.artifacts:
        if descriptor.stage == "source":
            continue
        parents = descriptor.parents()
        if not parents:
            findings.append(
                Finding(
                    code="S004",
                    severity="error",
                    title="Artifact has no parent",
                    detail=(
                        f"{descriptor.path} ({descriptor.stage}) declares no "
                        f"parent_sha256, so its lineage cannot be traced to the source"
                    ),
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy="Register it with the SHA-256 of the artifact it was derived from.",
                )
            )
            continue
        unknown = [parent for parent in parents if parent not in context.by_sha]
        if unknown:
            findings.append(
                Finding(
                    code="S004",
                    severity="error",
                    title="Artifact lineage is incomplete",
                    detail=(
                        f"{descriptor.path} names parent(s) "
                        f"{', '.join(short_hash(value) for value in unknown)} "
                        f"which are not registered"
                    ),
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy="Register the parent artifact, or correct parent_sha256.",
                )
            )
            continue
        try:
            chain = context.registry.lineage_of(descriptor.sha256)
        except Exception as exc:  # LineageError, including cycles
            findings.append(
                Finding(
                    code="S004",
                    severity="error",
                    title="Artifact lineage is broken",
                    detail=str(exc),
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy="Correct parent_sha256 so the chain terminates at the source.",
                )
            )
            continue
        if source is not None and chain[-1].sha256 != source.sha256:
            findings.append(
                Finding(
                    code="S004",
                    severity="error",
                    title="Artifact lineage does not reach the source",
                    detail=(
                        f"{descriptor.path} traces back to "
                        f"{chain[-1].path} ({chain[-1].stage}) rather than to the "
                        f"registered source dataset"
                    ),
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy="Re-register the chain so every artifact descends from the source.",
                )
            )
    return findings


@register("S005", "Exclusion counts reconcile")
def check_exclusion_arithmetic(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for descriptor in context.artifacts:
        for exclusion in descriptor.exclusions:
            if exclusion.n_before and (
                exclusion.n_before - exclusion.n_removed != exclusion.n_remaining
            ):
                findings.append(
                    Finding(
                        code="S005",
                        severity="error",
                        title="Exclusion counts do not reconcile",
                        detail=(
                            f"{descriptor.path}: '{exclusion.reason}' records "
                            f"n_before={exclusion.n_before}, n_removed={exclusion.n_removed}, "
                            f"n_remaining={exclusion.n_remaining}; "
                            f"{exclusion.n_before} - {exclusion.n_removed} != "
                            f"{exclusion.n_remaining}"
                        ),
                        location=f"artifact:{descriptor.artifact_id}",
                        remedy="Record the counts read from the object, not recomputed ones.",
                    )
                )
            if exclusion.by_sample:
                total = sum(exclusion.by_sample.values())
                if total != exclusion.n_removed:
                    findings.append(
                        Finding(
                            code="S005",
                            severity="warning",
                            title="Per-sample exclusion counts do not sum to the total",
                            detail=(
                                f"{descriptor.path}: '{exclusion.reason}' removed "
                                f"{exclusion.n_removed} cells but by_sample sums to {total}"
                            ),
                            location=f"artifact:{descriptor.artifact_id}",
                            remedy="Record every sample's contribution, including zeros.",
                        )
                    )
    return findings


@register("S006", "Cited references are recorded")
def check_reference_records(context: ValidationContext) -> list[Finding]:
    known = {record.reference_id for record in context.references}
    findings: list[Finding] = []
    for decision in context.decisions:
        missing = [value for value in decision.references if value not in known]
        if missing:
            findings.append(
                Finding(
                    code="S006",
                    severity="warning",
                    title="Decision cites an unrecorded reference",
                    detail=(
                        f"decision {decision.record_id} cites "
                        f"{', '.join(missing)}, which is not in references.jsonl"
                    ),
                    location=f"decision:{decision.record_id}",
                    remedy="Record the reference with project.record_reference(...).",
                )
            )
    return findings


@register("S007", "Environment was captured")
def check_environment(context: ValidationContext) -> list[Finding]:
    if context.environment is None:
        return [
            Finding(
                code="S007",
                severity="warning",
                title="No environment snapshot was recorded",
                detail="provenance/environment.json is missing",
                remedy="Call project.capture_environment() (cellimo init does this).",
                references=[HEUMOS],
            )
        ]
    configured = context.config.environment.interpreter
    queried = context.environment.queried_interpreter
    if configured and queried and queried != configured:
        return [
            Finding(
                code="S007",
                severity="warning",
                title="The environment snapshot is of the wrong interpreter",
                detail=(
                    f"the project runtime is {configured}, but the snapshot was "
                    f"taken from {queried} — the recorded package versions are not "
                    f"the ones the notebook runs with"
                ),
                remedy=(
                    "Check that the project interpreter still exists and runs, then "
                    "re-capture with project.capture_environment()."
                ),
                references=[HEUMOS],
            )
        ]
    if not context.environment.packages:
        return [
            Finding(
                code="S007",
                severity="warning",
                title="Environment snapshot records no package versions",
                detail="provenance/environment.json contains an empty package map",
                remedy="Re-capture the environment from the project runtime, not the tool runtime.",
                references=[HEUMOS],
            )
        ]
    return []


#: Files larger than this are never re-hashed by S008, even when they look
#: modified. S003 still catches disappearance, and an analyst who really wants a
#: 40 GB checkpoint verified can hash it themselves.
_HASH_SIZE_LIMIT = 256 * 1024 * 1024

def _looks_untouched(path: Path, descriptor: ArtifactDescriptor) -> bool:
    """True when the file demonstrably has not been written since registration.

    Section 11 of the generated notebook runs ``project.check()`` reactively, so
    S008 must not re-hash hundreds of megabytes on every interaction. Size and
    nanosecond modification time answer the common case for free, and they are
    compared for *exact* equality rather than with a tolerance — any write moves
    the mtime, including one in the same second as the registration.

    A descriptor with no recorded ``mtime_ns`` (written before the field
    existed) always falls through to hashing.

    This is a correctness aid, not a security control: whoever can write the
    file can also set its mtime.
    """
    if not descriptor.mtime_ns:
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_mtime_ns == descriptor.mtime_ns and stat.st_size == descriptor.bytes


@register("S008", "Artifact hashes match their files")
def check_artifact_hashes(context: ValidationContext) -> list[Finding]:
    """Verify that registered artifacts still contain what they were registered with.

    Cheap checks first: a file whose size differs from the recorded size has
    definitely changed, and one whose mtime predates registration definitely has
    not. Only the remaining cases are hashed, and only up to a size limit.
    """
    findings: list[Finding] = []
    for descriptor in context.artifacts:
        if descriptor.stage == "source":
            continue
        path = context.project.root / descriptor.path
        if not path.exists():
            continue  # reported by S003
        if _looks_untouched(path, descriptor):
            continue
        if descriptor.bytes > _HASH_SIZE_LIMIT:
            findings.append(
                Finding(
                    code="S008",
                    severity="warning",
                    title="Large artifact looks modified but was not re-hashed",
                    detail=(
                        f"{descriptor.path} is "
                        f"{descriptor.bytes / (1024**3):.1f} GiB and its size or "
                        f"modification time has changed since registration; it is above "
                        f"the {_HASH_SIZE_LIMIT // 1024**2} MiB verification limit"
                    ),
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy=(
                        f"Verify it yourself: "
                        f"python -c \"from cellimo.util.hashing import hash_file; "
                        f"print(hash_file('{descriptor.path}'))\" — expected "
                        f"{descriptor.sha256}"
                    ),
                )
            )
            continue
        if hash_file(path) != descriptor.sha256:
            findings.append(
                Finding(
                    code="S008",
                    severity="error",
                    title="Artifact content changed after registration",
                    detail=(
                        f"{descriptor.path} no longer hashes to "
                        f"{short_hash(descriptor.sha256)}; artifacts are immutable once "
                        f"registered"
                    ),
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy="Register the new file as a new artifact instead of overwriting.",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Scientific checks
# ---------------------------------------------------------------------------


@register("C001", "The experimental unit is declared")
def check_experimental_unit(context: ValidationContext) -> list[Finding]:
    design = context.config.design
    if design.experimental_unit:
        return []
    if context.confirmatory:
        return [
            Finding(
                code="C001",
                severity="error",
                title="No experimental unit is declared",
                detail=(
                    f"{len(context.confirmatory)} confirmatory analysis/analyses are "
                    f"recorded but design.experimental_unit is unset, so there is no "
                    f"statement of what counts as a biological replicate"
                ),
                remedy=(
                    "Name the obs column identifying independent biological units "
                    "(usually the donor) with project.record_design(...) and approve it."
                ),
                references=[SQUAIR, ZIMMERMAN],
            )
        ]
    return [
        Finding(
            code="C001",
            severity="warning",
            title="The experimental unit is not yet declared",
            detail=(
                "design.experimental_unit is unset. Inferential analysis is blocked "
                "until it is named and the design approved."
            ),
            remedy="Declare the design once the audit has identified the donor/sample columns.",
            references=[SQUAIR],
        )
    ]


@register("C002", "Confirmatory analysis followed design approval")
def check_design_approved(context: ValidationContext) -> list[Finding]:
    """Was the design approved, and by whom?

    ``approved_by`` is a free string the caller supplies; it cannot establish
    that a human was present. What can be checked is the *decision* that
    recorded the approval — who made the call, and whether an autonomous
    authorisation it claims actually exists.
    """
    design = context.config.design
    if not context.confirmatory:
        return []
    if design.is_approved():
        return _check_approval_provenance(context, design)
    if not context.config.policies.require_design_approval_for_inference:
        return [
            Finding(
                code="C002",
                severity="warning",
                title="Design approval is disabled by policy",
                detail=(
                    "policies.require_design_approval_for_inference is false, so "
                    "confirmatory analyses ran without a recorded sign-off"
                ),
                remedy="Re-enable the policy unless there is a recorded reason not to.",
            )
        ]
    return [
        Finding(
            code="C002",
            severity="error",
            title="Confirmatory analysis ran without an approved design",
            detail=(
                f"design.status is {design.status!r}; "
                f"{len(context.confirmatory)} confirmatory analysis/analyses are recorded"
            ),
            remedy=(
                "Approve the design (project.approve_design(approved_by=...)) or "
                "record the analyses as exploratory."
            ),
        )
    ]


def _check_approval_provenance(
    context: ValidationContext, design: DesignSection
) -> list[Finding]:
    """Who actually approved this design?"""
    if design.approved_by == "autonomous_authorization":
        # An approval attributed to autonomous authorisation must be backed by a
        # recorded authorisation from the user. Otherwise the agent could write
        # that string itself and the trail would show an approval nobody gave.
        authorised = any(
            record.kind == "authorization" and record.actor == "user"
            for record in context.decisions
        )
        if not authorised or not context.config.policies.autonomous_authorization:
            return [
                Finding(
                    code="C002",
                    severity="error",
                    title="Design approval claims an authorisation that was never recorded",
                    detail=(
                        "design.approved_by is 'autonomous_authorization' but no "
                        "decision of kind 'authorization' by the user is recorded, "
                        "or policies.autonomous_authorization is false"
                    ),
                    remedy=(
                        "Have the user approve the design, or record the "
                        "authorisation with project.authorize_autonomous(reason)."
                    ),
                )
            ]
        return [
            Finding(
                code="C002",
                severity="warning",
                title="Design was approved under autonomous authorisation",
                detail=(
                    "the user authorised the agent to approve the design itself; "
                    "no human reviewed the specific columns chosen"
                ),
                remedy="Review design.experimental_unit before reporting results.",
            )
        ]

    approvals = [
        record
        for record in context.decisions
        if record.kind == "design" and record.parameters.get("approved") is True
    ]
    if not approvals:
        return [
            Finding(
                code="C002",
                severity="warning",
                title="The design is approved but no approval decision was recorded",
                detail=(
                    f"cellimo.yaml says approved by {design.approved_by!r}, but "
                    f"decisions.jsonl contains no design approval to attribute it to"
                ),
                remedy="Re-approve through project.approve_design(...) so it is logged.",
            )
        ]
    if approvals[-1].actor != "user":
        return [
            Finding(
                code="C002",
                severity="warning",
                title="The design was approved by the agent, not by a person",
                detail=(
                    f"the approving decision records actor={approvals[-1].actor!r} "
                    f"and approved_by={design.approved_by!r}. Nothing in a library can "
                    f"verify a human was present; this reports what was recorded."
                ),
                remedy=(
                    "Have the user approve the design in the notebook, or record an "
                    "explicit authorisation with project.authorize_autonomous(reason)."
                ),
            )
        ]
    return []


@register("C003", "Unmodified counts are identified")
def check_raw_counts(context: ValidationContext) -> list[Finding]:
    if context.config.source.raw_counts_unavailable_upstream:
        return [
            Finding(
                code="C003",
                severity="warning",
                title="Source dataset arrives without recoverable counts",
                detail=(
                    "source.raw_counts_unavailable_upstream is set: "
                    f"{context.config.source.raw_counts_note}"
                ),
                remedy=(
                    "Count-based models (DESeq2, edgeR) cannot be used. State this "
                    "limitation wherever results are reported."
                ),
                references=[HEUMOS],
            )
        ]
    audits = context.artifacts_at("audit")
    anndata_artifacts = [
        descriptor
        for descriptor in context.artifacts
        if descriptor.kind == "anndata" and descriptor.stage != "source"
    ]
    known = [
        descriptor
        for descriptor in audits + anndata_artifacts
        if descriptor.raw_counts_available or descriptor.counts_layer
    ]
    if known:
        return []
    if not audits and not anndata_artifacts:
        # Skipping the audit entirely must not be safer than doing it: if
        # confirmatory results already exist, the absence of any record of
        # where counts live is an error, not a nudge.
        return [
            Finding(
                code="C003",
                severity="error" if context.confirmatory else "warning",
                title="Unmodified counts have not been identified",
                detail="No audit or AnnData artifact records where raw counts live",
                remedy="Run project.audit_anndata(...) before analysis.",
                references=[HEUMOS],
            )
        ]
    return [
        Finding(
            code="C003",
            severity="error",
            title="Unmodified counts are not identified anywhere in the project",
            detail=(
                "no audit or registered AnnData artifact reports "
                "raw_counts_available or a counts_layer, so count-based models and "
                "pseudobulk aggregation cannot be justified"
            ),
            remedy=(
                "Record where counts live (layers['counts'], .raw or X) when "
                "registering artifacts, or set source.raw_counts_unavailable_upstream "
                "with a reason if the dataset genuinely lacks them."
            ),
            references=[HEUMOS, SQUAIR],
        )
    ]


@register("C004", "Cells are not treated as biological replicates")
def check_pseudoreplication(context: ValidationContext) -> list[Finding]:
    """A confirmatory analysis must *positively declare* its unit of replication.

    Deliberately not keyed on the test name. Matching ``wilcoxon`` and friends
    against a free-text field means anyone who calls their test
    ``kruskal_wallis`` — or ``my_comparison`` — walks straight past the rule, and
    a rule you escape by renaming a string is not a rule. Instead: a confirmatory
    record either names ``sample``/``donor`` as its unit, or aggregates in a way
    that estimates between-replicate variance, or it fails.
    """
    findings: list[Finding] = []
    for record in context.confirmatory:
        declared_unit = record.unit_level in REPLICATE_UNIT_LEVELS
        aggregated = record.aggregation in REPLICATE_AWARE_AGGREGATIONS
        if declared_unit or aggregated:
            continue
        if _is_substantive(record.justification):
            findings.append(
                Finding(
                    code="C004",
                    severity="warning",
                    title="Cell-level confirmatory test with a stated justification",
                    detail=(
                        f"{record.name!r} uses {record.test} at unit_level="
                        f"{record.unit_level!r} with aggregation={record.aggregation!r}; "
                        f"justification: {record.justification}"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy="Confirm the justification holds for the reported claim.",
                    references=[SQUAIR, ZIMMERMAN, MURPHY],
                )
            )
            continue
        findings.append(
            Finding(
                code="C004",
                severity="error",
                title="No biological replicate is declared for a confirmatory analysis",
                detail=(
                    f"{record.name!r} is a confirmatory {record.test} with "
                    f"unit_level={record.unit_level!r} and aggregation="
                    f"{record.aggregation!r}, so nothing in the record identifies an "
                    f"independent biological unit. Cells from one donor are not "
                    f"independent; without pseudobulk aggregation or a donor random "
                    f"effect, between-replicate variance is not estimated and false "
                    f"discovery is inflated."
                    + (
                        f" {record.test} is a cell-level test."
                        if any(
                            marker in record.test.lower() for marker in CELL_LEVEL_TESTS
                        )
                        else ""
                    )
                ),
                location=_label(record.record_id, record.name),
                remedy=(
                    "Aggregate to pseudobulk per donor/sample and re-test, or fit a "
                    "mixed model with a donor random effect. If the design genuinely "
                    "has no biological replication, record it as exploratory."
                ),
                references=[SQUAIR, ZIMMERMAN, MURPHY],
            )
        )
    return findings


@register("C005", "Confirmatory groups have replication")
def check_replication_depth(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for record in context.confirmatory:
        if not record.n_units:
            findings.append(
                Finding(
                    code="C005",
                    severity="error",
                    title="Confirmatory analysis records no independent unit counts",
                    detail=(
                        f"{record.name!r} does not record n_units per group, so the "
                        f"amount of biological replication behind the result is unknown"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy="Record n_units={'group': n_donors, ...} (not cell counts).",
                    references=[SQUAIR],
                )
            )
            continue
        thin = {group: n for group, n in record.n_units.items() if n < 2}
        if thin:
            findings.append(
                Finding(
                    code="C005",
                    severity="error",
                    title="A compared group has fewer than two biological replicates",
                    detail=(
                        f"{record.name!r}: "
                        + ", ".join(f"{group} n={n}" for group, n in sorted(thin.items()))
                        + " — between-replicate variance cannot be estimated"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy=(
                        "Report this as a descriptive comparison rather than an "
                        "inferential one, or add replicates."
                    ),
                    references=[SQUAIR, ZIMMERMAN],
                )
            )
    return findings


@register("C006", "Confirmatory statistics do not use corrected values")
def check_de_on_integrated(context: ValidationContext) -> list[Finding]:
    """No confirmatory claim may rest on batch-corrected values, whatever it is called.

    Two deliberate scoping decisions:

    *Confirmatory only.* Clusters are found on the integrated embedding — that is
    what integration is for — and ranking markers between those clusters is
    routine exploratory work. Firing there would make the rule noise.

    *Not keyed on the test name.* Requiring the name to look like differential
    expression let ``kruskal_wallis`` and ``my_comparison`` walk past it. Any
    confirmatory statistic computed on corrected values is suspect; the test name
    only sharpens the message.
    """
    findings: list[Finding] = []
    for record in context.confirmatory:
        effective, artifact_says = _effective_representation(context, record)
        laundered = (
            artifact_says in INTEGRATED_REPRESENTATIONS
            and record.input_representation not in INTEGRATED_REPRESENTATIONS
        )
        if (
            artifact_says is not None
            and artifact_says != record.input_representation
            and record.aggregation == "none"
            and not laundered
        ):
            # No aggregation step to explain the difference, so one of the two
            # records is wrong. Reported, but not fatal on its own.
            findings.append(
                Finding(
                    code="C006",
                    severity="warning",
                    title="Analysis and artifact disagree about the input",
                    detail=(
                        f"{record.name!r} records input_representation="
                        f"{record.input_representation!r} with aggregation='none', but "
                        f"artifact {short_hash(record.input_artifact_sha256)} was "
                        f"registered as {artifact_says!r}"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy="Correct whichever record is wrong.",
                )
            )
        if effective not in INTEGRATED_REPRESENTATIONS:
            continue
        if _is_substantive(record.justification):
            findings.append(
                Finding(
                    code="C006",
                    severity="warning",
                    title="Confirmatory analysis on corrected values, with justification",
                    detail=(
                        f"{record.name!r} consumes {effective!r}; "
                        f"justification: {record.justification}"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy="Confirm the justification is defensible for the reported claim.",
                    references=[SEURAT_DE],
                )
            )
            continue
        findings.append(
            Finding(
                code="C006",
                severity="error",
                title="Integration-corrected values were used as confirmatory input",
                detail=(
                    f"{record.name!r} ({record.test}) is a confirmatory analysis "
                    f"consuming {effective!r}. Batch correction distorts the expression "
                    f"values themselves, so p-values computed on them do not mean what "
                    f"they appear to."
                    + (
                        f" The record claims {record.input_representation!r}, but the "
                        f"artifact it names was registered as {artifact_says!r} — the "
                        f"artifact's own record is what counts here."
                        if laundered
                        else ""
                    )
                    + (
                        " This looks like differential expression."
                        if _is_de(record.test)
                        else ""
                    )
                ),
                location=_label(record.record_id, record.name),
                remedy=(
                    "Test on counts or log-normalised expression with batch as a "
                    "covariate; use the corrected representation for clustering and "
                    "embedding only. Record a justification if this is deliberate."
                ),
                references=[SEURAT_DE, HEUMOS],
            )
        )
    return findings


@register("C007", "Effect sizes and uncertainty are reported")
def check_effect_and_uncertainty(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for record in context.confirmatory:
        missing = []
        if not record.effect_size.reported:
            missing.append("effect size")
        if not record.uncertainty.reported:
            missing.append("uncertainty")
        if missing:
            findings.append(
                Finding(
                    code="C007",
                    severity="warning",
                    title="Confirmatory result reports no " + " or ".join(missing),
                    detail=(
                        f"{record.name!r} records "
                        f"effect_size.reported={record.effect_size.reported}, "
                        f"uncertainty.reported={record.uncertainty.reported}"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy=(
                        "Record the effect-size and uncertainty columns "
                        "(e.g. log2FC and adjusted p-value or CI) from the results table."
                    ),
                    references=[HEUMOS],
                )
            )
    return findings


@register("C008", "Quality control is stratified by sample")
def check_qc_stratification(context: ValidationContext) -> list[Finding]:
    """Warn when QC thresholds were computed on a pooled mixture distribution.

    Only fires when the project actually has more than one sample: for a
    single-sample object, stratification is meaningless.
    """
    design = context.config.design
    sample_column = design.sample or design.donor
    if not sample_column:
        return []
    findings: list[Finding] = []
    for descriptor in context.artifacts:
        for exclusion in descriptor.exclusions:
            if exclusion.axis != "obs":
                continue
            if exclusion.by_sample or exclusion.stratified_by:
                continue
            if _is_substantive(exclusion.pooling_justification):
                continue
            findings.append(
                Finding(
                    code="C008",
                    severity="warning",
                    title="Cell exclusion was not stratified by sample",
                    detail=(
                        f"{descriptor.path}: '{exclusion.reason}' removed "
                        f"{exclusion.n_removed} cells with no per-sample breakdown, "
                        f"while {sample_column!r} identifies multiple samples"
                    ),
                    location=f"artifact:{descriptor.artifact_id}",
                    remedy=(
                        "Compute thresholds within each sample and record by_sample "
                        "counts, or record why a pooled threshold is defensible."
                    ),
                    references=[OSCA_QC],
                )
            )
    return findings


@register("C009", "Integration was justified, not reflexive")
def check_integration_justified(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for descriptor in context.artifacts_at("integrated"):
        decisions = [
            record
            for record in context.decisions_for(descriptor.sha256)
            if record.kind == "integration"
        ]
        justified = any(
            record.rationale.strip()
            or record.parameters.get("diagnostic")
            or record.parameters.get("justification")
            for record in decisions
        )
        if justified:
            continue
        findings.append(
            Finding(
                code="C009",
                severity="warning",
                title="Integration was applied without a recorded justification",
                detail=(
                    f"{descriptor.path} is an integrated artifact, but no decision of "
                    f"kind 'integration' records a diagnostic or rationale showing a "
                    f"batch effect was present"
                ),
                location=f"artifact:{descriptor.artifact_id}",
                remedy=(
                    "Record the pre-integration diagnostic (batch mixing metric or "
                    "embedding coloured by batch) that motivated correction."
                ),
                references=[HEUMOS],
            )
        )
    return findings


@register("C010", "Filtering stages record their exclusions")
def check_exclusions_recorded(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for descriptor in context.artifacts_at("post_qc"):
        if descriptor.exclusions:
            continue
        parent = context.by_sha.get(descriptor.parent_sha256 or "")
        shrank = (
            parent is not None
            and parent.n_obs is not None
            and descriptor.n_obs is not None
            and descriptor.n_obs < parent.n_obs
        )
        severity = "error" if shrank else "warning"
        detail = (
            f"{descriptor.path} is a post-QC artifact with no recorded exclusions"
            + (
                f", but it has {descriptor.n_obs} cells where its parent had {parent.n_obs}"
                if shrank and parent is not None
                else ""
            )
        )
        findings.append(
            Finding(
                code="C010",
                severity=severity,  # type: ignore[arg-type]
                title="Quality control recorded no exclusions",
                detail=detail,
                location=f"artifact:{descriptor.artifact_id}",
                remedy=(
                    "Record every removal with reason, counts and, where possible, a "
                    "per-sample breakdown."
                ),
                references=[HEUMOS],
            )
        )
    return findings


@register("C011", "Confirmatory analysis names its input artifact")
def check_statistics_inputs(context: ValidationContext) -> list[Finding]:
    findings: list[Finding] = []
    for record in context.confirmatory:
        if not record.input_artifact_sha256:
            findings.append(
                Finding(
                    code="C011",
                    severity="error",
                    title="Confirmatory analysis does not name its input",
                    detail=(
                        f"{record.name!r} records no input_artifact_sha256, so the "
                        f"result cannot be traced to the data it was computed from"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy="Pass input_artifact_sha256 when recording the analysis.",
                )
            )
        elif record.input_artifact_sha256 not in context.by_sha:
            findings.append(
                Finding(
                    code="C011",
                    severity="error",
                    title="Confirmatory analysis names an unregistered input",
                    detail=(
                        f"{record.name!r} consumes artifact "
                        f"{short_hash(record.input_artifact_sha256)}, which is not registered"
                    ),
                    location=_label(record.record_id, record.name),
                    remedy="Register the artifact before recording analyses that use it.",
                )
            )
    return findings


@register("C012", "Confirmatory analysis is sample-aware")
def check_sample_aware(context: ValidationContext) -> list[Finding]:
    """Catch a declared replicate unit that the analysis never aggregated to.

    C004 accepts any record naming ``sample`` or ``donor`` as its unit. Naming
    one is not the same as computing at it: a record can declare
    ``unit_level="donor"`` while recording ``aggregation="none"``, which is a
    cell-level test wearing a donor-level label. C012 owns exactly the records
    C004 lets through, so the two never report the same record twice — an
    earlier version keyed on the test name and duplicated C004 on every hit,
    always at the weaker severity.
    """
    findings: list[Finding] = []
    for record in context.confirmatory:
        if record.unit_level not in REPLICATE_UNIT_LEVELS:
            continue  # C004 owns this record; reporting it again adds nothing.
        if record.aggregation in REPLICATE_AWARE_AGGREGATIONS:
            continue
        if _is_substantive(record.justification):
            continue
        findings.append(
            Finding(
                code="C012",
                severity="warning",
                title="A replicate unit is declared but never aggregated to",
                detail=(
                    f"{record.name!r} declares unit_level={record.unit_level!r} but "
                    f"records aggregation={record.aggregation!r}, so nothing shows "
                    f"the test was computed across replicates rather than across "
                    f"cells that merely carry a {record.unit_level} label"
                ),
                location=_label(record.record_id, record.name),
                remedy=(
                    "Record how the analysis reached that unit — aggregation="
                    "pseudobulk, mixed_model or meta_analysis — or justify why a "
                    "cell-level computation answers a sample-level question."
                ),
                references=[SQUAIR, MURPHY],
            )
        )
    return findings


@register("C013", "Analyses cite the references that informed them")
def check_references_recorded(context: ValidationContext) -> list[Finding]:
    if not context.confirmatory or context.references:
        return []
    return [
        Finding(
            code="C013",
            severity="warning",
            title="No references were recorded",
            detail=(
                f"{len(context.confirmatory)} confirmatory analysis/analyses are "
                f"recorded but references.jsonl is empty"
            ),
            remedy=(
                "Record the workflow or documentation section each method choice came "
                "from with project.record_reference(...)."
            ),
            references=[HEUMOS],
        )
    ]
