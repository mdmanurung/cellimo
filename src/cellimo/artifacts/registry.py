"""Artifact registration and lineage.

Registration is the single point where a produced file becomes part of the
record. It hashes the file, refuses anything outside the project or anything
that is the registered source, and appends an immutable descriptor.

Lineage is then a graph over SHA-256 values: every artifact except the source
names its parent, and :meth:`ArtifactRegistry.lineage_of` walks that chain back
to the source. A chain that does not terminate at the source is what
``cellimo check`` reports as incomplete lineage.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cellimo.artifacts.descriptor import ArtifactDescriptor, Exclusion, artifact_id_for
from cellimo.errors import (
    ArtifactError,
    LineageError,
    PathSafetyError,
    SourceImmutabilityError,
)
from cellimo.provenance.store import ProvenanceStore
from cellimo.schema import ARTIFACT_KINDS, STAGES
from cellimo.util.hashing import hash_file
from cellimo.util.paths import resolve_in_project, same_file

__all__ = ["ArtifactRegistry"]


class ArtifactRegistry:
    """Registers artifacts for one project and answers lineage questions."""

    def __init__(
        self,
        root: str | Path,
        store: ProvenanceStore,
        *,
        source_path: str | Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.store = store
        self.source_path = Path(source_path) if source_path else None

    # -- registration ------------------------------------------------------

    def register(
        self,
        path: str | Path,
        *,
        stage: str,
        kind: str = "anndata",
        parent_sha256: str | None = None,
        additional_parents: Sequence[str] = (),
        description: str = "",
        params: dict[str, Any] | None = None,
        representation: str = "unknown",
        n_obs: int | None = None,
        n_vars: int | None = None,
        counts_layer: str | None = None,
        raw_counts_available: bool = False,
        obs_keys: Sequence[str] = (),
        var_keys: Sequence[str] = (),
        layers: Sequence[str] = (),
        obsm_keys: Sequence[str] = (),
        exclusions: Sequence[Exclusion | dict[str, Any]] = (),
        is_source: bool = False,
    ) -> ArtifactDescriptor:
        """Hash ``path`` and append an immutable descriptor for it.

        ``is_source`` is used exactly once per project, when registering the
        immutable source dataset; it is the only registration allowed to point
        at the source file and the only one allowed to have no parent.
        """
        if stage not in STAGES:
            raise ArtifactError(f"unknown stage {stage!r}; expected one of {list(STAGES)}")
        if kind not in ARTIFACT_KINDS:
            raise ArtifactError(f"unknown kind {kind!r}; expected one of {list(ARTIFACT_KINDS)}")

        resolved = self._resolve_artifact_path(path, is_source=is_source)
        if not resolved.exists():
            raise ArtifactError(f"cannot register {path}: file does not exist")
        if not resolved.is_file():
            raise ArtifactError(f"cannot register {path}: not a regular file")

        # Stat before hashing: if the file is rewritten while it is being
        # read, the recorded mtime belongs to the older state and a later
        # check re-hashes rather than trusting a stale digest.
        stat = resolved.stat()
        digest = hash_file(resolved)
        if parent_sha256 is None and stage != "source" and not is_source:
            known = {descriptor.sha256 for descriptor in self.store.artifacts()}
            raise LineageError(
                f"artifact {path} at stage {stage!r} has no parent_sha256; "
                f"pass the SHA-256 of the artifact it was derived from "
                f"({len(known)} artifact(s) currently registered)"
            )

        descriptor = ArtifactDescriptor(
            artifact_id=artifact_id_for(stage, digest),
            stage=stage,  # type: ignore[arg-type]
            kind=kind,  # type: ignore[arg-type]
            path=self._relative(resolved, is_source=is_source),
            sha256=digest,
            bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            parent_sha256=parent_sha256,
            additional_parents=list(additional_parents),
            description=description,
            params=dict(params or {}),
            representation=representation,  # type: ignore[arg-type]
            n_obs=n_obs,
            n_vars=n_vars,
            counts_layer=counts_layer,
            raw_counts_available=raw_counts_available,
            obs_keys=list(obs_keys),
            var_keys=list(var_keys),
            layers=list(layers),
            obsm_keys=list(obsm_keys),
            exclusions=[
                item if isinstance(item, Exclusion) else Exclusion.model_validate(item)
                for item in exclusions
            ],
        )
        return self.store.append_artifact(descriptor)

    def _resolve_artifact_path(self, path: str | Path, *, is_source: bool) -> Path:
        """Resolve and refuse unsafe artifact destinations."""
        candidate = Path(path)
        if is_source:
            # The source may legitimately live outside the project (shared
            # storage), so containment is not required — but it is still
            # resolved so symlinks cannot hide what is being registered, and it
            # must actually *be* this project's registered source. Without that
            # second condition, `is_source=True` would be a public way to hash
            # any readable file on the machine into the provenance ledger.
            resolved = Path(candidate).expanduser().resolve()
            if self.source_path is None:
                raise ArtifactError(
                    "cannot register a source artifact: this registry has no "
                    "registered source path"
                )
            if not same_file(resolved, self.source_path) and resolved != Path(
                self.source_path
            ).expanduser().resolve():
                raise PathSafetyError(
                    f"refusing to register {path} as the source: this project's "
                    f"source is {self.source_path}. Register other files as "
                    f"ordinary artifacts."
                )
            return resolved
        resolved = resolve_in_project(self.root, candidate, what="artifact path")
        if (
            self.source_path is not None
            and self.source_path.exists()
            and resolved.exists()
            and same_file(resolved, self.source_path)
        ):
            raise SourceImmutabilityError(
                f"refusing to register {path} as an artifact: it is the same "
                f"file as the registered source {self.source_path}"
            )
        return resolved

    def _relative(self, resolved: Path, *, is_source: bool) -> str:
        if is_source:
            try:
                return resolved.relative_to(self.root.resolve()).as_posix()
            except ValueError:
                # Source outside the project: record the absolute path in the
                # config instead, and give the descriptor a stable stand-in.
                return f"external/{resolved.name}"
        return resolved.relative_to(self.root.resolve()).as_posix()

    # -- lineage -----------------------------------------------------------

    def lineage_of(self, sha256: str) -> list[ArtifactDescriptor]:
        """Return the chain from ``sha256`` back to the source, source last.

        Raises :class:`LineageError` when a parent is missing or when the chain
        contains a cycle.
        """
        by_sha = {descriptor.sha256: descriptor for descriptor in self.store.artifacts()}
        if sha256 not in by_sha:
            raise LineageError(f"no registered artifact with sha256 {sha256}")
        chain: list[ArtifactDescriptor] = []
        seen: set[str] = set()
        current: str | None = sha256
        while current is not None:
            if current in seen:
                raise LineageError(f"artifact lineage contains a cycle at {current}")
            seen.add(current)
            descriptor = by_sha.get(current)
            if descriptor is None:
                raise LineageError(
                    f"artifact lineage is incomplete: parent {current} is not registered"
                )
            chain.append(descriptor)
            current = descriptor.parent_sha256
        return chain

    def latest(self, stage: str) -> ArtifactDescriptor | None:
        """Return the most recently registered artifact for ``stage``."""
        candidates = self.store.artifacts_for_stage(stage)
        return candidates[-1] if candidates else None

    def orphans(self) -> list[ArtifactDescriptor]:
        """Artifacts whose declared parent is not registered."""
        artifacts = self.store.artifacts()
        known = {descriptor.sha256 for descriptor in artifacts}
        orphaned = []
        for descriptor in artifacts:
            missing = [
                parent
                for parent in descriptor.parents()
                if parent not in known
            ]
            if missing or (descriptor.stage != "source" and not descriptor.parents()):
                orphaned.append(descriptor)
        return orphaned
