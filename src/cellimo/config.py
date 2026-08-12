"""``cellimo.yaml`` — the project configuration file.

The configuration is the declared state of a project: which dataset is the
immutable source, which ``obs`` columns carry the experimental design, where
outputs go, and which safety policies are in force. It is small, human-editable
and written atomically.

History lives in ``provenance/`` instead: the configuration says *what is true
now*, provenance says *how it got that way*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cellimo.errors import ConfigError
from cellimo.schema import (
    CONFIG_FILENAME,
    SCHEMA_VERSION,
    DesignStatus,
    Profile,
)
from cellimo.util.atomic import atomic_write_text
from cellimo.util.time import utc_now_iso

__all__ = [
    "CONFIG_FILENAME",
    "CellimoConfig",
    "CheckpointSection",
    "DesignSection",
    "EnvironmentSection",
    "PathsSection",
    "PoliciesSection",
    "ProjectSection",
    "SourceSection",
    "find_config",
    "load_config",
    "save_config",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProjectSection(_Base):
    """Identity of the project."""

    name: str
    created: str = Field(default_factory=utc_now_iso)
    description: str = ""


class SourceSection(_Base):
    """The immutable source dataset.

    ``path`` is stored relative to the project root when the file lives inside
    it, and absolute otherwise — datasets on shared storage are the common case
    on a cluster and must not be copied.
    """

    path: str
    sha256: str = ""
    bytes: int = 0
    registered: str = Field(default_factory=utc_now_iso)
    format: str = "h5ad"
    immutable: bool = True
    #: Set when the dataset genuinely arrives without recoverable counts (a
    #: published, already-normalised object). This downgrades the raw-counts
    #: check from an error to a warning — nothing was done wrong locally — and
    #: requires a stated reason so the limitation travels with the project.
    raw_counts_unavailable_upstream: bool = False
    raw_counts_note: str = ""

    @model_validator(mode="after")
    def _unavailable_needs_reason(self) -> SourceSection:
        if self.raw_counts_unavailable_upstream and not self.raw_counts_note.strip():
            raise ValueError(
                "source.raw_counts_unavailable_upstream requires "
                "source.raw_counts_note explaining why counts are unrecoverable"
            )
        return self

    @field_validator("path")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source.path must not be empty")
        return value


class EnvironmentSection(_Base):
    """Which project runtime executes the notebook."""

    profile: Profile = "scanpy"
    python: str = ""
    interpreter: str = ""
    manager: str = "unknown"  # uv | conda | mamba | pixi | venv | system | unknown


class DesignSection(_Base):
    """The experimental design, as ``obs`` column names.

    Every field starts unresolved. The agent may *propose* values, which moves
    ``status`` to ``proposed``; only a human (or a recorded autonomous
    authorisation) moves it to ``approved``. Confirmatory statistics are blocked
    until then.
    """

    status: DesignStatus = "unresolved"
    sample: str | None = None
    donor: str | None = None
    condition: str | None = None
    time: str | None = None
    batch: str | None = None
    study: str | None = None
    #: The ``obs`` column that identifies the biological replicate. Usually the
    #: donor column; the sample column when each sample is an independent
    #: biological unit. Never a cell-level identifier.
    experimental_unit: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _approved_needs_unit(self) -> DesignSection:
        if self.status == "approved":
            if not self.experimental_unit:
                raise ValueError(
                    "design.status is 'approved' but design.experimental_unit is "
                    "unset; the biological replicate must be named before approval"
                )
            if not self.approved_by:
                raise ValueError("design.status is 'approved' but design.approved_by is unset")
        return self

    def is_approved(self) -> bool:
        return self.status == "approved" and bool(self.experimental_unit)

    def declared_fields(self) -> dict[str, str]:
        """Return the design fields that have been given a column name."""
        return {
            name: value
            for name, value in (
                ("sample", self.sample),
                ("donor", self.donor),
                ("condition", self.condition),
                ("time", self.time),
                ("batch", self.batch),
                ("study", self.study),
            )
            if value
        }


class PathsSection(_Base):
    """Project-relative output locations."""

    data: str = "data"
    artifacts: str = "artifacts"
    results: str = "results"
    figures: str = "results/figures"
    tables: str = "results/tables"
    models: str = "results/models"
    report: str = "results/report"
    provenance: str = "provenance"
    notebook: str = "analysis.py"

    def all_dirs(self) -> tuple[str, ...]:
        return (
            self.data,
            self.artifacts,
            self.results,
            self.figures,
            self.tables,
            self.models,
            self.report,
            self.provenance,
            f"{self.provenance}/runs",
        )


class PoliciesSection(_Base):
    """Safety policies enforced by Cellimo's own APIs."""

    #: Registered source data can never be written through Cellimo APIs. The
    #: flag exists so the refusal is visible in configuration, not so it can be
    #: switched off — setting it True is rejected.
    allow_source_overwrite: bool = False
    #: Network access (retrieval-index download, package installation) must be
    #: requested explicitly rather than happening as a side effect.
    allow_network: bool = False
    #: Confirmatory statistics require an approved design.
    require_design_approval_for_inference: bool = True
    #: Recorded when the user explicitly authorises the agent to approve the
    #: design itself. Kept separate from the flag above so the audit trail shows
    #: who lowered the bar.
    autonomous_authorization: bool = False

    @field_validator("allow_source_overwrite")
    @classmethod
    def _refuse_source_overwrite(cls, value: bool) -> bool:
        if value:
            raise ValueError(
                "policies.allow_source_overwrite cannot be enabled; registered "
                "source data is immutable through Cellimo APIs"
            )
        return value


class CheckpointSection(_Base):
    """When to write an AnnData checkpoint to disk."""

    policy: str = "expensive_only"  # every_stage | expensive_only | never
    backed: bool = True

    @field_validator("policy")
    @classmethod
    def _known_policy(cls, value: str) -> str:
        allowed = {"every_stage", "expensive_only", "never"}
        if value not in allowed:
            raise ValueError(f"checkpoint.policy must be one of {sorted(allowed)}, got {value!r}")
        return value


class CellimoConfig(_Base):
    """The whole of ``cellimo.yaml``."""

    schema_version: int = SCHEMA_VERSION
    cellimo_version: str = ""
    project: ProjectSection
    source: SourceSection
    environment: EnvironmentSection = Field(default_factory=EnvironmentSection)
    design: DesignSection = Field(default_factory=DesignSection)
    paths: PathsSection = Field(default_factory=PathsSection)
    policies: PoliciesSection = Field(default_factory=PoliciesSection)
    checkpoint: CheckpointSection = Field(default_factory=CheckpointSection)
    random_seed: int = 0

    @field_validator("schema_version")
    @classmethod
    def _supported_schema(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"{CONFIG_FILENAME} declares schema_version {value}, but this "
                f"Cellimo understands schema_version {SCHEMA_VERSION}"
            )
        return value

    def to_yaml(self) -> str:
        payload: dict[str, Any] = self.model_dump(mode="json")
        header = (
            "# cellimo.yaml — project configuration.\n"
            "# Edit by hand or through the cellimo CLI; both write atomically.\n"
            "# History lives in provenance/, not here.\n"
        )
        return header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)


def save_config(config: CellimoConfig, path: str | Path) -> Path:
    """Write ``config`` to ``path`` atomically."""
    return atomic_write_text(path, config.to_yaml())


def load_config(path: str | Path) -> CellimoConfig:
    """Load and validate a configuration file.

    Raises :class:`ConfigError` with the underlying message rather than letting
    a pydantic or YAML exception escape.
    """
    target = Path(path)
    if not target.exists():
        raise ConfigError(f"no {CONFIG_FILENAME} at {target}")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{target} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{target} must contain a YAML mapping, got {type(raw).__name__}")
    try:
        return CellimoConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError and friends
        raise ConfigError(f"{target} is not a valid Cellimo configuration: {exc}") from exc


def find_config(start: str | Path | None = None) -> Path | None:
    """Walk upwards from ``start`` looking for ``cellimo.yaml``.

    Returns the first match, or ``None`` at the filesystem root. Symlinked
    directories are resolved so a project reached through a symlink is found
    exactly once.
    """
    current = Path(start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        config_path = candidate / CONFIG_FILENAME
        if config_path.is_file():
            return config_path
    return None
