"""AnnData audit.

The audit answers the questions that must be settled before any analysis
starts: how big is the object, what is in ``X``, are unmodified counts
recoverable, and which ``obs`` columns could carry the experimental design.

It reads the file in backed mode by default and samples the matrix, so auditing
a 40 GB dataset costs seconds and a few hundred megabytes rather than loading
the whole thing.

Nothing here decides anything. Design candidates are *proposals* with the
evidence attached; a human (or a recorded authorisation) still has to approve
them before inferential analysis is unblocked.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cellimo.errors import CellimoError
from cellimo.util.hashing import hash_file
from cellimo.util.time import utc_now_iso

__all__ = [
    "DESIGN_HINTS",
    "AuditReport",
    "ColumnSummary",
    "RawCountsFinding",
    "audit_anndata",
]

#: Substring hints used to *propose* design columns. Ordered by specificity;
#: matching is case-insensitive on a normalised column name.
DESIGN_HINTS: dict[str, tuple[str, ...]] = {
    "donor": ("donor", "participant", "subject", "patient", "individual", "person"),
    "sample": ("sample", "library", "specimen", "aliquot", "channel", "well", "soc"),
    "condition": (
        "condition",
        "treatment",
        "stim",
        "group",
        "disease",
        "status",
        "genotype",
        "perturbation",
        "case_control",
    ),
    "time": ("time", "timepoint", "day", "hour", "week", "visit", "age", "stage"),
    "batch": ("batch", "run", "lane", "chemistry", "pool", "seq", "10x", "experiment"),
    "study": ("study", "dataset", "cohort", "project", "site"),
}

#: Layer names conventionally holding unmodified counts.
COUNTS_LAYER_NAMES: tuple[str, ...] = ("counts", "raw_counts", "raw", "umi", "umi_counts")

_MAX_SAMPLED_CELLS = 1000
_MAX_EXAMPLES = 5


class ColumnSummary(BaseModel):
    """What a single ``obs`` or ``var`` column contains."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    dtype: str
    n_unique: int
    n_missing: int
    is_categorical: bool = False
    is_numeric: bool = False
    examples: list[str] = Field(default_factory=list)
    #: Rough cardinality class used by the design proposer: "constant",
    #: "low" (<= 50 levels), "medium" (<= 1000), "high" (per-cell-ish).
    cardinality: str = "unknown"


class RawCountsFinding(BaseModel):
    """Where unmodified counts live, and how confident the audit is."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    available: bool = False
    #: ``X``, ``layers/<name>``, ``raw/X`` or empty when not found.
    location: str = ""
    layer: str | None = None
    evidence: str = ""
    #: True when the sampled values were all non-negative integers.
    integer_like: bool = False
    sampled_cells: int = 0
    value_min: float | None = None
    value_max: float | None = None


class DesignCandidate(BaseModel):
    """A proposed ``obs`` column for one design field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    column: str
    n_unique: int
    evidence: str
    confidence: str = "low"  # low | medium | high


class AuditReport(BaseModel):
    """The full result of auditing one AnnData file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str
    bytes: int
    audited_at: str = Field(default_factory=utc_now_iso)
    backed: bool = True

    n_obs: int = 0
    n_vars: int = 0
    x_dtype: str = ""
    x_is_sparse: bool = False

    obs_columns: list[ColumnSummary] = Field(default_factory=list)
    var_columns: list[ColumnSummary] = Field(default_factory=list)
    layers: list[str] = Field(default_factory=list)
    obsm_keys: list[str] = Field(default_factory=list)
    varm_keys: list[str] = Field(default_factory=list)
    obsp_keys: list[str] = Field(default_factory=list)
    uns_keys: list[str] = Field(default_factory=list)
    raw_present: bool = False

    raw_counts: RawCountsFinding = Field(default_factory=RawCountsFinding)
    design_candidates: dict[str, list[DesignCandidate]] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def obs_names(self) -> list[str]:
        return [column.name for column in self.obs_columns]

    def best_candidate(self, field: str) -> str | None:
        candidates = self.design_candidates.get(field) or []
        return candidates[0].column if candidates else None

    def summary_lines(self) -> list[str]:
        """Compact human-readable summary, used by the CLI and the notebook."""
        lines = [
            f"{self.n_obs:,} cells x {self.n_vars:,} genes  ({self.bytes / 1e9:.2f} GB)",
            f"X dtype={self.x_dtype} sparse={self.x_is_sparse}",
        ]
        if self.raw_counts.available:
            lines.append(f"raw counts: {self.raw_counts.location} ({self.raw_counts.evidence})")
        else:
            lines.append("raw counts: NOT identified — this blocks most analyses")
        for field, candidates in self.design_candidates.items():
            if candidates:
                shown = ", ".join(
                    f"{candidate.column} (n={candidate.n_unique})" for candidate in candidates[:3]
                )
                lines.append(f"{field} candidates: {shown}")
        lines.extend(f"warning: {warning}" for warning in self.warnings)
        return lines


def _require_anndata() -> Any:
    try:
        import anndata
    except ImportError as exc:  # pragma: no cover - exercised in envs without anndata
        raise CellimoError(
            "auditing an .h5ad file requires anndata; install the project runtime "
            "with `pip install 'cellimo[scanpy]'` or add anndata to the project "
            "environment"
        ) from exc
    return anndata


def _cardinality_class(n_unique: int, n_obs: int) -> str:
    if n_unique <= 1:
        return "constant"
    if n_unique <= 50:
        return "low"
    if n_unique <= 1000 and n_unique < n_obs * 0.5:
        return "medium"
    return "high"


def _summarise_frame(frame: Any, n_rows: int) -> list[ColumnSummary]:
    summaries: list[ColumnSummary] = []
    for name in frame.columns:
        series = frame[name]
        dtype = str(series.dtype)
        is_categorical = dtype in {"category", "object", "bool"}
        is_numeric = bool(getattr(series.dtype, "kind", "O") in "iuf")
        try:
            n_unique = int(series.nunique(dropna=True))
        except TypeError:
            n_unique = -1
        try:
            n_missing = int(series.isna().sum())
        except TypeError:
            n_missing = 0
        examples: list[str] = []
        try:
            for value in series.dropna().unique()[:_MAX_EXAMPLES]:
                examples.append(str(value))
        except (TypeError, ValueError):
            pass
        summaries.append(
            ColumnSummary(
                name=str(name),
                dtype=dtype,
                n_unique=n_unique,
                n_missing=n_missing,
                is_categorical=is_categorical,
                is_numeric=is_numeric,
                examples=examples,
                cardinality=_cardinality_class(n_unique, n_rows),
            )
        )
    return summaries


def _sample_matrix(matrix: Any, n_obs: int) -> Any:
    """Return a small in-memory slice of a possibly backed matrix."""
    if matrix is None:
        return None
    take = min(_MAX_SAMPLED_CELLS, n_obs)
    if take == 0:
        return None
    block = matrix[:take]
    to_memory = getattr(block, "to_memory", None)
    if callable(to_memory):
        block = to_memory()
    return block


def _matrix_stats(block: Any) -> tuple[bool, float | None, float | None]:
    """Return ``(looks_like_counts, min, max)`` for a sampled matrix block.

    "Looks like counts" means every sampled value is a non-negative integer.
    NumPy is available whenever AnnData is, so there is no fallback path here.
    """
    import numpy as np

    if block is None:
        return False, None, None
    # Sparse matrices expose only their stored values through ``.data``, which
    # is what should be inspected: the implicit zeros are integers anyway.
    array = np.asarray(getattr(block, "data", block))
    if array.size == 0:
        return False, None, None
    finite = array[np.isfinite(array)]
    if finite.size == 0:
        return False, None, None
    minimum = float(finite.min())
    maximum = float(finite.max())
    integer_like = bool(
        np.issubdtype(array.dtype, np.integer)
        or np.allclose(finite, np.rint(finite), rtol=0, atol=1e-8)
    )
    return integer_like and minimum >= 0, minimum, maximum


def _safe_x(adata: Any) -> Any:
    """Return ``adata.X`` or ``None``.

    ``X`` is genuinely optional in AnnData — an object can carry its data only in
    ``layers`` — and in backed mode reading a missing ``X`` raises a raw
    ``KeyError`` from h5py rather than returning ``None``.
    """
    try:
        return adata.X
    except (KeyError, AttributeError, TypeError, ValueError):
        return None


def _layer_names(adata: Any) -> list[str]:
    """Real layer names, sorted.

    anndata 0.13 yields a spurious ``None`` from ``layers.keys()`` — alone when
    the object has no layers, and *alongside* the real names when it does
    (reproduced against 0.13.2; 0.12.19 does not do this). Left in, it reaches
    ``AuditReport.layers: list[str]`` as a validation error, and reaches
    ``name.lower()`` in the counts search as an AttributeError on any object
    whose ``X`` is not already counts.
    """
    return sorted(str(name) for name in adata.layers if name is not None)


def _find_raw_counts(adata: Any, n_obs: int) -> RawCountsFinding:
    """Look for unmodified counts in X, then in conventional layers, then .raw."""
    x_integer, x_min, x_max = _matrix_stats(_sample_matrix(_safe_x(adata), n_obs))
    sampled = min(_MAX_SAMPLED_CELLS, n_obs)
    if x_integer:
        return RawCountsFinding(
            available=True,
            location="X",
            evidence=f"sampled {sampled} cells of X: all non-negative integers",
            integer_like=True,
            sampled_cells=sampled,
            value_min=x_min,
            value_max=x_max,
        )

    for name in _layer_names(adata):
        if name.lower() not in COUNTS_LAYER_NAMES:
            continue
        integer_like, minimum, maximum = _matrix_stats(
            _sample_matrix(adata.layers[name], n_obs)
        )
        if integer_like:
            return RawCountsFinding(
                available=True,
                location=f"layers/{name}",
                layer=name,
                evidence=f"sampled {sampled} cells of layers['{name}']: non-negative integers",
                integer_like=True,
                sampled_cells=sampled,
                value_min=minimum,
                value_max=maximum,
            )

    if getattr(adata, "raw", None) is not None:
        try:
            integer_like, minimum, maximum = _matrix_stats(
                _sample_matrix(adata.raw.X, n_obs)
            )
        except (AttributeError, TypeError, ValueError):
            integer_like, minimum, maximum = False, None, None
        if integer_like:
            return RawCountsFinding(
                available=True,
                location="raw/X",
                evidence=f"sampled {sampled} cells of .raw.X: non-negative integers",
                integer_like=True,
                sampled_cells=sampled,
                value_min=minimum,
                value_max=maximum,
            )

    return RawCountsFinding(
        available=False,
        location="",
        evidence=(
            f"sampled {sampled} cells: X is not integer-valued "
            f"(min={x_min}, max={x_max}) and no counts layer or .raw was found"
        ),
        integer_like=False,
        sampled_cells=sampled,
        value_min=x_min,
        value_max=x_max,
    )


def _normalise(name: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in name.lower())


def _propose_design(
    obs_columns: Sequence[ColumnSummary], n_obs: int
) -> dict[str, list[DesignCandidate]]:
    """Propose design columns by name hint plus cardinality plausibility."""
    proposals: dict[str, list[DesignCandidate]] = {field: [] for field in DESIGN_HINTS}
    rank = {"high": 0, "medium": 1, "low": 2}
    for field, hints in DESIGN_HINTS.items():
        scored: list[tuple[int, int, int, DesignCandidate]] = []
        for column in obs_columns:
            normalised = _normalise(column.name)
            matched_index = next(
                (index for index, hint in enumerate(hints) if hint in normalised), None
            )
            if matched_index is None:
                continue
            matched = hints[matched_index]
            if column.cardinality in {"constant", "high"} and field != "study":
                # A per-cell identifier is never a design column, and a column
                # with a single level cannot define a comparison.
                continue
            # ``sample_id`` should beat ``library_batch`` for the sample field:
            # an exact or id-suffixed match of the most specific hint wins.
            if normalised in {matched, f"{matched}_id", f"{matched}id", f"id_{matched}"}:
                confidence = "high"
            elif column.is_numeric and field in {"donor", "sample", "batch", "study"}:
                confidence = "low"
            else:
                confidence = "medium"
            candidate = DesignCandidate(
                column=column.name,
                n_unique=column.n_unique,
                evidence=f"name contains {matched!r}; {column.n_unique} level(s)",
                confidence=confidence,
            )
            # Hint order encodes specificity, so a 'sample' match outranks a
            # 'library' match even when both are plausible.
            scored.append((matched_index, rank[confidence], column.n_unique, candidate))
        scored.sort(key=lambda item: (item[0], item[1], item[2]))
        proposals[field] = [item[3] for item in scored]
    return proposals


def audit_anndata(path: str | Path, *, backed: bool = True) -> AuditReport:
    """Audit an ``.h5ad`` file without modifying it.

    ``backed=True`` reads the object lazily; set it to ``False`` only for small
    files where a full load is cheap.
    """
    anndata = _require_anndata()
    target = Path(path).expanduser()
    if not target.exists():
        raise CellimoError(f"cannot audit {target}: file does not exist")
    if target.suffix.lower() not in {".h5ad", ".h5"}:
        raise CellimoError(
            f"cannot audit {target}: expected an .h5ad file, got {target.suffix!r}"
        )

    warnings: list[str] = []
    try:
        adata = anndata.read_h5ad(target, backed="r" if backed else None)
    except Exception as exc:  # anndata raises a variety of IO errors
        raise CellimoError(f"cannot read {target} as AnnData: {exc}") from exc

    try:
        n_obs = int(adata.n_obs)
        n_vars = int(adata.n_vars)
        obs_columns = _summarise_frame(adata.obs, n_obs)
        var_columns = _summarise_frame(adata.var, n_vars)
        raw_counts = _find_raw_counts(adata, n_obs)
        x = _safe_x(adata)
        x_dtype = "absent" if x is None else str(getattr(x, "dtype", "unknown"))
        x_is_sparse = x is not None and (
            hasattr(x, "format") or "sparse" in type(x).__name__.lower()
        )

        report_kwargs: dict[str, Any] = {
            "n_obs": n_obs,
            "n_vars": n_vars,
            "x_dtype": x_dtype,
            "x_is_sparse": bool(x_is_sparse),
            "obs_columns": obs_columns,
            "var_columns": var_columns,
            "layers": _layer_names(adata),
            "obsm_keys": sorted(adata.obsm.keys()),
            "varm_keys": sorted(adata.varm.keys()),
            "obsp_keys": sorted(adata.obsp.keys()),
            "uns_keys": sorted(str(key) for key in adata.uns),
            "raw_present": getattr(adata, "raw", None) is not None,
            "raw_counts": raw_counts,
            "design_candidates": _propose_design(obs_columns, n_obs),
        }
    except CellimoError:
        raise
    except Exception as exc:
        # Anything the object throws while being inspected — a missing field, an
        # unexpected dtype, a truncated file — is reported as "this dataset could
        # not be audited", naming the cause, rather than as a raw traceback from
        # somewhere inside anndata or h5py.
        raise CellimoError(
            f"cannot audit {target}: {type(exc).__name__}: {exc}"
        ) from exc
    finally:
        handle = getattr(adata, "file", None)
        if handle is not None and hasattr(handle, "close"):
            # Closing an already-closed backed file is not an error worth raising.
            with contextlib.suppress(Exception):
                handle.close()

    if not raw_counts.available:
        warnings.append(
            "unmodified counts were not identified; record where they live "
            "before normalisation or differential expression"
        )
    if n_obs == 0:
        warnings.append("the object contains no cells")
    if not any(report_kwargs["design_candidates"].get("donor") or []) and not any(
        report_kwargs["design_candidates"].get("sample") or []
    ):
        warnings.append(
            "no obs column looks like a donor or sample identifier; the "
            "biological replicate must be declared explicitly"
        )

    return AuditReport(
        path=str(target),
        sha256=hash_file(target),
        bytes=target.stat().st_size,
        backed=backed,
        warnings=warnings,
        **report_kwargs,
    )
