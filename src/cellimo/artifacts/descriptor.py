"""Immutable artifact descriptors.

An artifact is a file that an analysis stage produced: a checkpointed AnnData,
a results table, a figure, a fitted model. Its descriptor is the record that
makes the analysis reconstructible — what stage produced it, from which parent,
with which parameters, and what the matrix inside it actually contains.

Descriptors are frozen. Re-registering the same path with different content
appends a new descriptor with a new hash; nothing is ever rewritten in place.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cellimo.schema import (
    ArtifactKind,
    Representation,
    Stage,
)
from cellimo.util.hashing import short_hash
from cellimo.util.time import utc_now_iso

__all__ = ["ArtifactDescriptor", "Exclusion", "artifact_id_for"]


class Exclusion(BaseModel):
    """One recorded removal of cells or genes.

    Every filtering step must produce one of these. ``by_sample`` is what makes
    "stratify QC by sample" checkable: a filter that removed 90% of one donor's
    cells and 2% of everyone else's is visible here and nowhere else.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    axis: str = "obs"  # obs (cells) | var (genes)
    #: Counts before and after this exclusion. ``n_before - n_removed`` must
    #: equal ``n_remaining``; the validator reconciles them.
    n_before: int = 0
    n_removed: int = 0
    n_remaining: int = 0
    criteria: dict[str, Any] = Field(default_factory=dict)
    #: Cells removed per sample. Non-empty is what proves the threshold was
    #: applied within samples rather than to a pooled mixture distribution.
    by_sample: dict[str, int] = Field(default_factory=dict)
    #: The obs column QC was stratified by, when it was.
    stratified_by: str = ""
    #: Why a pooled (unstratified) threshold was defensible, when it was.
    pooling_justification: str = ""

    @field_validator("axis")
    @classmethod
    def _known_axis(cls, value: str) -> str:
        if value not in {"obs", "var"}:
            raise ValueError(f"exclusion.axis must be 'obs' or 'var', got {value!r}")
        return value


class ArtifactDescriptor(BaseModel):
    """An immutable description of one produced file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    stage: Stage
    kind: ArtifactKind
    #: Project-relative POSIX path. Absolute paths are rejected at registration.
    path: str
    sha256: str
    bytes: int = 0
    created: str = Field(default_factory=utc_now_iso)
    #: The file's modification time in nanoseconds when it was registered.
    #: Lets validation skip re-hashing a file that demonstrably has not been
    #: written since. ``0`` means unknown, and forces a re-hash.
    mtime_ns: int = 0

    #: SHA-256 of the artifact this one was derived from. ``None`` only for the
    #: registered source. Lineage validation walks this backwards to the source.
    parent_sha256: str | None = None
    #: Additional parents for merge-like steps (e.g. concatenating two objects).
    additional_parents: list[str] = Field(default_factory=list)

    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)

    # --- AnnData-specific facts, recorded at registration time -------------
    #: What the values in ``X`` are. Read by the raw-counts and integration
    #: checks; never inferred from the matrix after the fact.
    representation: Representation = "unknown"
    n_obs: int | None = None
    n_vars: int | None = None
    #: Name of the layer holding unmodified counts, e.g. ``"counts"``. ``None``
    #: when counts live in ``X`` (then ``representation`` says ``raw_counts``)
    #: or when they are genuinely absent.
    counts_layer: str | None = None
    #: True when unmodified counts are recoverable from this artifact at all,
    #: whether from ``X``, a layer, or ``.raw``.
    raw_counts_available: bool = False
    obs_keys: list[str] = Field(default_factory=list)
    var_keys: list[str] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)
    obsm_keys: list[str] = Field(default_factory=list)

    exclusions: list[Exclusion] = Field(default_factory=list)

    @field_validator("layers", mode="before")
    @classmethod
    def _real_layer_names(cls, value: Any) -> Any:
        """Drop the ``None`` anndata 0.13 reports as a layer.

        ``adata.layers.keys()`` on anndata 0.13 yields a spurious ``None`` —
        alone when the object has no layers, and alongside the real names when
        it has some (reproduced against 0.13.2; 0.12.19 does not). The obvious
        caller spelling, ``layers=list(adata.layers.keys())``, therefore fails
        validation here on one anndata and passes on another. A layer with no
        name is not a fact about the artifact under any version, so it is
        dropped rather than made the caller's problem.
        """
        if isinstance(value, (list, tuple, set)):
            return [str(name) for name in value if name is not None]
        return value

    @field_validator("path")
    @classmethod
    def _relative_posix(cls, value: str) -> str:
        if value.startswith("/") or value.startswith("~"):
            raise ValueError(f"artifact path must be project-relative, got {value!r}")
        if "\\" in value:
            raise ValueError(f"artifact path must use forward slashes, got {value!r}")
        return value

    @field_validator("sha256")
    @classmethod
    def _looks_like_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError(f"sha256 must be 64 lowercase hex characters, got {value!r}")
        return value

    def parents(self) -> list[str]:
        """All parent hashes, primary first."""
        parents = [self.parent_sha256] if self.parent_sha256 else []
        return parents + list(self.additional_parents)


def artifact_id_for(stage: str, sha256: str) -> str:
    """Build the stable public identifier for an artifact.

    Derived from content, not from insertion order, so the same file registered
    twice gets the same id and references to it stay valid across re-runs.
    """
    return f"{stage}:{short_hash(sha256)}"
