"""The append-only provenance store.

Everything lives under ``provenance/``:

``artifacts.jsonl``    one line per registered artifact
``decisions.jsonl``    one line per analytical decision
``references.jsonl``   one line per consulted reference
``statistics.jsonl``   one line per statistical comparison
``environment.json``   the last captured runtime snapshot
``manifest.json``      the rolled-up view, regenerated from the logs above
``runs/<run_id>.json`` one file per Cellimo command invocation

The ``.jsonl`` files are append-only and never rewritten, so a crash costs at
most the record being written. ``manifest.json`` and ``environment.json`` are
written atomically and can be rebuilt from the logs at any time.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from cellimo.artifacts.descriptor import ArtifactDescriptor
from cellimo.errors import ProvenanceError
from cellimo.provenance.records import (
    DecisionRecord,
    EnvironmentRecord,
    Manifest,
    ReferenceRecord,
    RunRecord,
    StatisticsRecord,
    make_record_id,
)
from cellimo.schema import PROVENANCE_FILES, SCHEMA_VERSION, STAGES
from cellimo.util.atomic import append_jsonl, atomic_write_json, read_json, read_jsonl
from cellimo.util.time import utc_now_iso

__all__ = ["ProvenanceStore"]


class ProvenanceStore:
    """Reads and writes one project's ``provenance/`` directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # -- locations ---------------------------------------------------------

    @property
    def artifacts_path(self) -> Path:
        return self.root / PROVENANCE_FILES["artifacts"]

    @property
    def decisions_path(self) -> Path:
        return self.root / PROVENANCE_FILES["decisions"]

    @property
    def references_path(self) -> Path:
        return self.root / PROVENANCE_FILES["references"]

    @property
    def statistics_path(self) -> Path:
        return self.root / PROVENANCE_FILES["statistics"]

    @property
    def environment_path(self) -> Path:
        return self.root / PROVENANCE_FILES["environment"]

    @property
    def manifest_path(self) -> Path:
        return self.root / PROVENANCE_FILES["manifest"]

    @property
    def runs_dir(self) -> Path:
        return self.root / PROVENANCE_FILES["runs"]

    def ensure_layout(self) -> None:
        """Create the provenance directory tree if it does not exist."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # -- writes ------------------------------------------------------------

    @staticmethod
    def _artifact_identity(descriptor: ArtifactDescriptor) -> tuple[str, str, str, str]:
        """What makes two artifact registrations the *same* registration.

        Content alone is not enough. A stage can legitimately produce a file
        byte-identical to its parent — a normalisation that turned out to be a
        no-op, a re-export — and that is still a distinct step in the lineage
        that must be recorded. Keying on the hash alone silently dropped those,
        leaving a gap no check could see.
        """
        return (
            descriptor.stage,
            descriptor.path,
            descriptor.sha256,
            descriptor.parent_sha256 or "",
        )

    def append_artifact(self, descriptor: ArtifactDescriptor) -> ArtifactDescriptor:
        """Append an artifact descriptor, idempotently.

        Re-registering exactly the same stage, path, content and parent returns
        the descriptor already on disk rather than duplicating it. Anything else
        is a new registration and is written.
        """
        identity = self._artifact_identity(descriptor)
        for record in self.artifacts():
            if self._artifact_identity(record) == identity:
                return record
        append_jsonl(self.artifacts_path, descriptor.model_dump(mode="json"))
        return descriptor

    def append_decision(self, record: DecisionRecord) -> DecisionRecord:
        return self._append(self.decisions_path, record, "decision")

    def append_reference(self, record: ReferenceRecord) -> ReferenceRecord:
        return self._append(self.references_path, record, "reference")

    def append_statistics(self, record: StatisticsRecord) -> StatisticsRecord:
        return self._append(self.statistics_path, record, "statistics")

    def write_environment(self, record: EnvironmentRecord) -> Path:
        return atomic_write_json(self.environment_path, record.model_dump(mode="json"))

    def write_run(self, record: RunRecord) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        return atomic_write_json(
            self.runs_dir / f"{record.run_id}.json", record.model_dump(mode="json")
        )

    @staticmethod
    def _with_id(record: Any, prefix: str) -> Any:
        """Give a record a content-derived id.

        ``created`` is excluded along with ``record_id``: a timestamp is not
        content, and leaving it in meant two identical records written a
        second apart got different ids — which is the opposite of what this
        function is for.
        """
        if record.record_id:
            return record
        payload = record.model_dump(mode="json")
        for volatile in ("record_id", "created"):
            payload.pop(volatile, None)
        return record.model_copy(update={"record_id": make_record_id(prefix, payload)})

    def _append(self, path: Path, record: Any, prefix: str) -> Any:
        record = self._with_id(record, prefix)
        append_jsonl(path, record.model_dump(mode="json"))
        return record

    # -- reads -------------------------------------------------------------

    def artifacts(self) -> list[ArtifactDescriptor]:
        return [
            ArtifactDescriptor.model_validate(row) for row in self._rows(self.artifacts_path)
        ]

    def decisions(self) -> list[DecisionRecord]:
        return [DecisionRecord.model_validate(row) for row in self._rows(self.decisions_path)]

    def references(self) -> list[ReferenceRecord]:
        return [ReferenceRecord.model_validate(row) for row in self._rows(self.references_path)]

    def statistics(self) -> list[StatisticsRecord]:
        return [StatisticsRecord.model_validate(row) for row in self._rows(self.statistics_path)]

    def environment(self) -> EnvironmentRecord | None:
        raw = read_json(self.environment_path)
        if raw is None:
            return None
        return EnvironmentRecord.model_validate(raw)

    def manifest(self) -> Manifest | None:
        raw = read_json(self.manifest_path)
        if raw is None:
            return None
        return Manifest.model_validate(raw)

    def _rows(self, path: Path) -> Iterator[dict[str, Any]]:
        try:
            yield from read_jsonl(path)
        except ValueError as exc:  # includes json.JSONDecodeError
            raise ProvenanceError(f"{path} contains a malformed record: {exc}") from exc

    # -- rollup ------------------------------------------------------------

    def build_manifest(
        self,
        *,
        project_name: str,
        cellimo_version: str,
        source: dict[str, Any],
        design: dict[str, Any],
    ) -> Manifest:
        """Recompute the manifest from the append-only logs."""
        artifacts = self.artifacts()
        latest: dict[str, str] = {}
        for stage in STAGES:
            for descriptor in artifacts:
                if descriptor.stage == stage:
                    latest[stage] = descriptor.sha256
        environment = self.environment()
        return Manifest(
            schema_version=SCHEMA_VERSION,
            cellimo_version=cellimo_version,
            generated=utc_now_iso(),
            project_name=project_name,
            source=source,
            design=design,
            counts={
                "artifacts": len(artifacts),
                "decisions": len(self.decisions()),
                "references": len(self.references()),
                "statistics": len(self.statistics()),
            },
            latest_by_stage=latest,
            artifacts=[descriptor.model_dump(mode="json") for descriptor in artifacts],
            environment=environment.model_dump(mode="json") if environment else {},
        )

    def write_manifest(self, manifest: Manifest) -> Path:
        return atomic_write_json(self.manifest_path, manifest.model_dump(mode="json"))

    # -- convenience -------------------------------------------------------

    def artifact_by_sha(self, sha256: str) -> ArtifactDescriptor | None:
        for descriptor in self.artifacts():
            if descriptor.sha256 == sha256:
                return descriptor
        return None

    def artifacts_for_stage(self, stage: str) -> list[ArtifactDescriptor]:
        return [descriptor for descriptor in self.artifacts() if descriptor.stage == stage]

    def reference_ids(self) -> set[str]:
        return {record.reference_id for record in self.references()}

    def iter_all(self) -> Iterable[tuple[str, Any]]:
        """Yield ``(kind, record)`` for every record, for reporting."""
        for descriptor in self.artifacts():
            yield "artifact", descriptor
        for decision in self.decisions():
            yield "decision", decision
        for reference in self.references():
            yield "reference", reference
        for statistic in self.statistics():
            yield "statistics", statistic
