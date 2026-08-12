"""Cellimo — agentic, reproducible single-cell analysis in Marimo.

Cellimo is a deterministic toolkit, not an agent. The reasoning agent is Codex
or Claude Code; Marimo owns the notebook and the kernel; Cellimo owns project
structure, provenance, artifact lineage and scientific validation, and exposes a
read-only retrieval server so the agent can cite what it did.

    from cellimo import Project

    project = Project.open()
    audit = project.audit_anndata("data/source.h5ad", backed=True)

Cellimo never calls an LLM. There is no provider configuration, no API key, and
no internal model of any kind.

Derived from KAI (https://github.com/davidfischerlab/kai), Apache-2.0; see
THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

from cellimo.artifacts.descriptor import ArtifactDescriptor, Exclusion
from cellimo.audit.anndata_audit import AuditReport, audit_anndata
from cellimo.config import CellimoConfig, load_config, save_config
from cellimo.errors import (
    ArtifactError,
    CellimoError,
    ConfigError,
    DesignError,
    LineageError,
    PathSafetyError,
    ProjectNotFoundError,
    ProvenanceError,
    RetrievalError,
    SourceImmutabilityError,
)
from cellimo.project.project import Project
from cellimo.schema import SCHEMA_VERSION, STAGES

try:
    __version__ = _dist_version("cellimo")
except PackageNotFoundError:  # running from a source checkout without install
    __version__ = "0.1.0"

__all__ = [
    "SCHEMA_VERSION",
    "STAGES",
    "ArtifactDescriptor",
    "ArtifactError",
    "AuditReport",
    "CellimoConfig",
    "CellimoError",
    "ConfigError",
    "DesignError",
    "Exclusion",
    "LineageError",
    "PathSafetyError",
    "Project",
    "ProjectNotFoundError",
    "ProvenanceError",
    "RetrievalError",
    "SourceImmutabilityError",
    "__version__",
    "audit_anndata",
    "load_config",
    "save_config",
]
