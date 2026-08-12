"""Exception hierarchy for Cellimo.

Every error raised by Cellimo's own APIs derives from :class:`CellimoError` so
callers can distinguish "the tool refused" from "the scientific stack blew up".
Errors are never swallowed silently: the CLI turns them into a non-zero exit
status with the message intact.
"""

from __future__ import annotations

__all__ = [
    "ArtifactError",
    "CellimoError",
    "ConfigError",
    "DesignError",
    "EnvironmentError_",
    "IndexNotFoundError",
    "LineageError",
    "PathSafetyError",
    "ProjectExistsError",
    "ProjectNotFoundError",
    "ProvenanceError",
    "ReferenceNotFoundError",
    "RetrievalError",
    "SourceImmutabilityError",
]


class CellimoError(Exception):
    """Base class for all Cellimo errors."""


class ConfigError(CellimoError):
    """The project configuration file is missing, malformed or inconsistent."""


class ProjectNotFoundError(CellimoError):
    """No Cellimo project could be discovered from the given directory."""


class ProjectExistsError(CellimoError):
    """A Cellimo project already exists where a new one was requested."""


class PathSafetyError(CellimoError):
    """A path escapes the project root, or targets a protected location."""


class SourceImmutabilityError(PathSafetyError):
    """An operation would modify, overwrite or delete registered source data."""


class ArtifactError(CellimoError):
    """An artifact could not be registered or resolved."""


class LineageError(ArtifactError):
    """Artifact lineage is incomplete or inconsistent."""


class ProvenanceError(CellimoError):
    """A provenance record could not be written or read."""


class DesignError(CellimoError):
    """The experimental design is missing, invalid or not approved."""


class RetrievalError(CellimoError):
    """The retrieval index could not answer the request."""


class IndexNotFoundError(RetrievalError):
    """No retrieval index is installed at the configured location."""


class ReferenceNotFoundError(RetrievalError):
    """The requested reference identifier is not present in the index."""


class EnvironmentError_(CellimoError):
    """A required runtime component is missing or incompatible."""
