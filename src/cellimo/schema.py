"""The frozen field names shared by every consumer of a Cellimo project.

Four things read these names: the provenance writer, ``cellimo check``, the MCP
result payloads, and the generated ``analysis.py``. If they drift apart the
validator silently passes on nothing, so the vocabulary lives in exactly one
module and everything else imports it.

``SCHEMA_VERSION`` is bumped whenever a field is renamed or its meaning changes.
Projects written by an older schema are rejected with a clear message rather
than misread.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

__all__ = [
    "ANALYSIS_MODES",
    "ANNDATA_STAGES",
    "ARTIFACT_KINDS",
    "CONFIG_FILENAME",
    "COUNT_REPRESENTATIONS",
    "DECISION_KINDS",
    "DESIGN_FIELDS",
    "DESIGN_STATUSES",
    "INTEGRATED_REPRESENTATIONS",
    "PROFILES",
    "PROVENANCE_FILES",
    "REPRESENTATIONS",
    "SCHEMA_VERSION",
    "STAGES",
    "UNIT_LEVELS",
    "AnalysisMode",
    "ArtifactKind",
    "DecisionKind",
    "DesignField",
    "DesignStatus",
    "Profile",
    "Representation",
    "Severity",
    "Stage",
    "UnitLevel",
]

SCHEMA_VERSION: Final[int] = 1
CONFIG_FILENAME: Final[str] = "cellimo.yaml"

#: Ordered analysis stages. Order matters: lineage is checked against it, and
#: ``analysis.py`` sections are laid out in this sequence.
Stage = Literal[
    "source",
    "audit",
    "post_qc",
    "normalized",
    "integrated",
    "annotated",
    "statistics",
]
STAGES: Final[tuple[str, ...]] = get_args(Stage)

#: Stages whose primary artifact is an AnnData object.
ANNDATA_STAGES: Final[frozenset[str]] = frozenset(
    {"source", "post_qc", "normalized", "integrated", "annotated"}
)

ArtifactKind = Literal["anndata", "table", "figure", "model", "report", "audit"]
ARTIFACT_KINDS: Final[tuple[str, ...]] = get_args(ArtifactKind)

#: What the values in an expression matrix actually are. This is the field the
#: raw-counts and integration checks key off, so it is recorded explicitly
#: rather than inferred from a matrix at check time.
Representation = Literal[
    "raw_counts",
    "normalized_counts",
    "lognorm",
    "scaled",
    "integrated_expression",
    "integrated_embedding",
    "pseudobulk_counts",
    "unknown",
]
REPRESENTATIONS: Final[tuple[str, ...]] = get_args(Representation)

#: Representations produced by batch-effect correction. Differential expression
#: computed on these is not valid without explicit, recorded justification.
INTEGRATED_REPRESENTATIONS: Final[frozenset[str]] = frozenset(
    {"integrated_expression", "integrated_embedding"}
)

#: Representations that are, or aggregate, unmodified counts.
COUNT_REPRESENTATIONS: Final[frozenset[str]] = frozenset({"raw_counts", "pseudobulk_counts"})

#: Exploratory results guide the next step; confirmatory results are claims.
#: Only confirmatory analyses are held to the replication rules.
AnalysisMode = Literal["exploratory", "confirmatory"]
ANALYSIS_MODES: Final[tuple[str, ...]] = get_args(AnalysisMode)

#: The unit of replication a statistical test was actually computed over.
UnitLevel = Literal["cell", "sample", "donor", "unknown"]
UNIT_LEVELS: Final[tuple[str, ...]] = get_args(UnitLevel)

#: Unit levels that are never a biological replicate on their own.
PSEUDOREPLICATED_UNIT_LEVELS: Final[frozenset[str]] = frozenset({"cell"})

DesignStatus = Literal["unresolved", "proposed", "approved"]
DESIGN_STATUSES: Final[tuple[str, ...]] = get_args(DesignStatus)

#: ``obs`` columns that describe the experiment. ``sample`` and ``donor`` carry
#: the replication structure; the rest describe the comparison.
DesignField = Literal["sample", "donor", "condition", "time", "batch", "study"]
DESIGN_FIELDS: Final[tuple[str, ...]] = get_args(DesignField)

Profile = Literal["scanpy", "existing"]
PROFILES: Final[tuple[str, ...]] = get_args(Profile)

Severity = Literal["error", "warning", "info"]

DecisionKind = Literal[
    "design",
    "qc",
    "filtering",
    "normalization",
    "integration",
    "annotation",
    "statistics",
    "checkpoint",
    "exclusion",
    "authorization",
    "note",
]
DECISION_KINDS: Final[tuple[str, ...]] = get_args(DecisionKind)

#: Filenames under ``provenance/``. Written atomically; the ``.jsonl`` files are
#: append-only.
PROVENANCE_FILES: Final[dict[str, str]] = {
    "manifest": "manifest.json",
    "decisions": "decisions.jsonl",
    "references": "references.jsonl",
    "artifacts": "artifacts.jsonl",
    "statistics": "statistics.jsonl",
    "environment": "environment.json",
    "runs": "runs",
}
