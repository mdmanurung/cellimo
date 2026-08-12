"""Artifact registration, immutability and lineage."""

from __future__ import annotations

import pytest

from cellimo.artifacts.descriptor import ArtifactDescriptor, artifact_id_for
from cellimo.errors import ArtifactError, LineageError
from cellimo.project.project import Project


def _write(project: Project, relative: str, payload: bytes) -> str:
    target = project.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return relative


def test_artifact_id_is_derived_from_content() -> None:
    digest = "a" * 64
    assert artifact_id_for("post_qc", digest) == "post_qc:aaaaaaaaaaaa"
    # Same content, same id — independent of insertion order.
    assert artifact_id_for("post_qc", digest) == artifact_id_for("post_qc", digest)


def test_descriptor_rejects_absolute_paths() -> None:
    with pytest.raises(ValueError, match="project-relative"):
        ArtifactDescriptor(
            artifact_id="x", stage="post_qc", kind="anndata", path="/tmp/x.h5ad", sha256="a" * 64
        )


def test_descriptor_rejects_a_malformed_hash() -> None:
    with pytest.raises(ValueError, match="64 lowercase hex"):
        ArtifactDescriptor(
            artifact_id="x", stage="post_qc", kind="anndata", path="x.h5ad", sha256="short"
        )


def test_registration_requires_a_parent_outside_the_source_stage(project: Project) -> None:
    relative = _write(project, "artifacts/post_qc.h5ad", b"payload")
    with pytest.raises(LineageError, match="no parent_sha256"):
        project.artifacts.register(relative, stage="post_qc")


def test_registration_rejects_an_unknown_stage(project: Project) -> None:
    relative = _write(project, "artifacts/x.h5ad", b"payload")
    with pytest.raises(ArtifactError, match="unknown stage"):
        project.artifacts.register(relative, stage="not-a-stage", parent_sha256="a" * 64)


def test_registration_rejects_a_missing_file(project: Project) -> None:
    with pytest.raises(ArtifactError, match="does not exist"):
        project.register_artifact("artifacts/absent.h5ad", stage="post_qc")


def test_lineage_walks_back_to_the_source(project: Project) -> None:
    source_sha = project.config.source.sha256
    qc = project.register_artifact(
        _write(project, "artifacts/post_qc.h5ad", b"qc"),
        stage="post_qc",
        parent_sha256=source_sha,
    )
    normalized = project.register_artifact(
        _write(project, "artifacts/normalized.h5ad", b"norm"),
        stage="normalized",
        parent_sha256=qc.sha256,
    )
    chain = project.artifacts.lineage_of(normalized.sha256)
    assert [item.stage for item in chain] == ["normalized", "post_qc", "source"]


def test_parent_is_inferred_from_the_preceding_stage(project: Project) -> None:
    qc = project.register_artifact(
        _write(project, "artifacts/post_qc.h5ad", b"qc"),
        stage="post_qc",
        parent_sha256=project.config.source.sha256,
    )
    normalized = project.register_artifact(
        _write(project, "artifacts/normalized.h5ad", b"norm"), stage="normalized"
    )
    assert normalized.parent_sha256 == qc.sha256


def test_lineage_reports_a_missing_parent(project: Project) -> None:
    project.store.append_artifact(
        ArtifactDescriptor(
            artifact_id="post_qc:deadbeef",
            stage="post_qc",
            kind="anndata",
            path="artifacts/orphan.h5ad",
            sha256="b" * 64,
            parent_sha256="c" * 64,
        )
    )
    with pytest.raises(LineageError, match="not registered"):
        project.artifacts.lineage_of("b" * 64)
    assert [item.path for item in project.artifacts.orphans()] == ["artifacts/orphan.h5ad"]


def test_lineage_detects_a_cycle(project: Project) -> None:
    first = "d" * 64
    second = "e" * 64
    for identifier, parent, name in ((first, second, "a"), (second, first, "b")):
        project.store.append_artifact(
            ArtifactDescriptor(
                artifact_id=f"post_qc:{identifier[:12]}",
                stage="post_qc",
                kind="anndata",
                path=f"artifacts/{name}.h5ad",
                sha256=identifier,
                parent_sha256=parent,
            )
        )
    with pytest.raises(LineageError, match="cycle"):
        project.artifacts.lineage_of(first)


def test_byte_identical_output_at_a_later_stage_is_still_registered(
    project: Project,
) -> None:
    """A no-op stage is still a step in the lineage and must be recorded.

    Deduplicating on content alone silently dropped it, and returned a
    descriptor that had never been written — so no check could see the gap.
    """
    payload = b"identical bytes"
    qc = project.register_artifact(
        _write(project, "artifacts/post_qc.h5ad", payload),
        stage="post_qc",
        parent_sha256=project.config.source.sha256,
    )
    normalized = project.register_artifact(
        _write(project, "artifacts/normalized.h5ad", payload),
        stage="normalized",
        parent_sha256=qc.sha256,
    )
    stages = [item.stage for item in project.store.artifacts()]
    assert stages == ["source", "post_qc", "normalized"]
    assert normalized.sha256 == qc.sha256
    assert normalized.stage == "normalized"
    # And the record on disk is the one that was returned.
    persisted = [item for item in project.store.artifacts() if item.stage == "normalized"]
    assert persisted[0].path == "artifacts/normalized.h5ad"


def test_registering_identical_content_twice_does_not_duplicate(project: Project) -> None:
    relative = _write(project, "artifacts/post_qc.h5ad", b"qc")
    first = project.register_artifact(
        relative, stage="post_qc", parent_sha256=project.config.source.sha256
    )
    second = project.register_artifact(
        relative, stage="post_qc", parent_sha256=project.config.source.sha256
    )
    assert first.sha256 == second.sha256
    assert len(project.store.artifacts_for_stage("post_qc")) == 1
