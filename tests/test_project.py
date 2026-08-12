"""Project lifecycle: initialisation, source immutability, design, stages."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cellimo.errors import (
    ArtifactError,
    DesignError,
    PathSafetyError,
    ProjectExistsError,
    ProjectNotFoundError,
    SourceImmutabilityError,
)
from cellimo.project.project import Project


def test_init_creates_the_documented_scaffold(project: Project) -> None:
    root = project.root
    for relative in (
        "cellimo.yaml",
        "analysis.py",
        "pyproject.toml",
        "data",
        "artifacts",
        "results/figures",
        "results/tables",
        "results/models",
        "results/report",
        "provenance/manifest.json",
        "provenance/environment.json",
        "provenance/runs",
    ):
        assert (root / relative).exists(), f"{relative} was not created"


def test_init_registers_the_source_as_the_root_of_lineage(project: Project) -> None:
    artifacts = project.store.artifacts()
    assert [item.stage for item in artifacts] == ["source"]
    assert artifacts[0].parent_sha256 is None
    assert artifacts[0].sha256 == project.config.source.sha256


def test_init_refuses_to_clobber_an_existing_project(project: Project) -> None:
    with pytest.raises(ProjectExistsError):
        Project.init(project.root, project.source_path)


def test_open_from_a_subdirectory(project: Project) -> None:
    nested = project.root / "results" / "figures"
    assert Project.open(nested).root == project.root


def test_open_outside_a_project_raises(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        Project.open(tmp_path)


def test_verify_source_detects_modification(project: Project) -> None:
    ok, message = project.verify_source()
    assert ok, message
    with project.source_path.open("ab") as handle:
        handle.write(b"tampered")
    ok, message = project.verify_source()
    assert not ok
    assert "changed on disk" in message


def test_assert_writable_refuses_the_source(project: Project) -> None:
    relative = project.source_path.relative_to(project.root)
    with pytest.raises(SourceImmutabilityError):
        project.assert_writable(relative)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_assert_writable_refuses_a_symlink_to_the_source(project: Project) -> None:
    link = project.root / "artifacts" / "sneaky.h5ad"
    link.symlink_to(project.source_path)
    with pytest.raises(SourceImmutabilityError):
        project.assert_writable("artifacts/sneaky.h5ad")


def test_assert_writable_refuses_paths_outside_the_project(project: Project) -> None:
    with pytest.raises(PathSafetyError):
        project.assert_writable("../escape.h5ad")


def test_register_artifact_refuses_to_overwrite_the_source(project: Project) -> None:
    relative = project.source_path.relative_to(project.root)
    with pytest.raises(SourceImmutabilityError):
        project.register_artifact(relative, stage="post_qc")


def test_is_source_cannot_hash_an_arbitrary_file_into_provenance(
    project: Project, tmp_path: Path
) -> None:
    """`is_source=True` skips containment, so it must match the real source."""
    secret = tmp_path / "id_rsa"
    secret.write_bytes(b"not a dataset")
    with pytest.raises(PathSafetyError, match="refusing to register"):
        project.artifacts.register(secret, stage="source", is_source=True)
    assert [item.stage for item in project.store.artifacts()] == ["source"]


def test_registering_the_real_source_still_works(project: Project) -> None:
    descriptor = project.register_source()
    assert descriptor.sha256 == project.config.source.sha256


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_stage_output_creates_missing_parents(project: Project) -> None:
    with project.stage("post_qc") as stage:
        target = stage.output("artifacts/nested/deeper/out.h5ad")
        assert target.parent.is_dir()
        target.write_bytes(b"payload")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_stage_output_detects_a_symlink_planted_during_mkdir(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-validation after mkdir is real: race it and it must catch the escape.

    An earlier version of this test just called ``output()`` once and checked the
    directory existed — which cannot tell one validation call from two. This
    plants the symlink in the exact window the second call exists to close.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    planted = (project.root / "artifacts" / "staged").resolve()
    real_mkdir = Path.mkdir

    def sneaky_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if str(self) == str(planted):
            # Instead of the directory the caller asked for, a way out of the
            # project appears — after the first check has already passed.
            self.symlink_to(outside, target_is_directory=True)
            return None
        return real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", sneaky_mkdir)
    with pytest.raises(PathSafetyError), project.stage("post_qc") as stage:
        stage.output("artifacts/staged/out.h5ad")


def test_malformed_paths_raise_a_typed_error(project: Project) -> None:
    with pytest.raises(PathSafetyError):
        project.assert_writable("artifacts/\x00evil.h5ad")
    with pytest.raises(PathSafetyError):
        project.assert_writable("artifacts/" + "x" * 5000 + ".h5ad")


def test_record_design_proposes_then_approves(project: Project) -> None:
    design = project.record_design(
        sample="sample_id", donor="participant_id", condition="condition"
    )
    assert design.status == "proposed"
    assert design.experimental_unit == "participant_id"
    assert not design.is_approved()

    approved = project.approve_design(approved_by="a human")
    assert approved.status == "approved"
    assert approved.is_approved()
    # It survives a reload from disk.
    assert Project.open(project.root).config.design.is_approved()


def test_editing_an_approved_design_revokes_approval(project: Project) -> None:
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="a human")
    updated = project.record_design(condition="library_batch")
    assert updated.status == "proposed"
    assert updated.approved_by is None


def test_approval_requires_an_experimental_unit(project: Project) -> None:
    with pytest.raises(DesignError):
        project.approve_design(approved_by="a human")


def test_confirmatory_analysis_is_blocked_without_approval(project: Project) -> None:
    project.record_design(donor="participant_id", condition="condition")
    with pytest.raises(DesignError, match="not approved"):
        project.record_statistics(
            name="stim vs ctrl", test="pseudobulk_deseq2", mode="confirmatory"
        )


def test_exploratory_analysis_is_allowed_without_approval(project: Project) -> None:
    record = project.record_statistics(
        name="marker ranking", test="wilcoxon", mode="exploratory"
    )
    assert record.record_id.startswith("statistics:")


def test_stage_registers_output_and_decision(project: Project) -> None:
    with project.stage(
        "post_qc", summary="QC", parent_sha256=project.config.source.sha256
    ) as stage:
        target = stage.output("artifacts/post_qc.h5ad")
        target.write_bytes(b"pretend h5ad")
        stage.add_exclusion("low counts", n_before=100, n_removed=10, n_remaining=90)
        stage.set_matrix_facts(representation="raw_counts", counts_layer="counts", n_obs=90)

    descriptor = stage.descriptor
    assert descriptor is not None
    assert descriptor.stage == "post_qc"
    assert descriptor.parent_sha256 == project.config.source.sha256
    assert descriptor.exclusions[0].n_remaining == 90
    assert any(decision.kind == "filtering" for decision in project.store.decisions())


def test_stage_requires_something_to_be_written(project: Project) -> None:
    with (
        pytest.raises(ArtifactError, match="nothing was written"),
        project.stage("post_qc") as stage,
    ):
        stage.output("artifacts/never_written.h5ad")


def test_record_ids_are_content_derived(project: Project) -> None:
    first = project.record_reference(reference_id="notebook:x", title="X", used_for="qc")
    second = project.record_reference(reference_id="notebook:x", title="X", used_for="qc")
    # Same content in the same second yields the same id rather than a duplicate.
    assert first.record_id.startswith("reference:")
    assert second.reference_id == first.reference_id


def test_authorize_autonomous_is_recorded(project: Project) -> None:
    project.authorize_autonomous("running unattended overnight")
    assert project.config.policies.autonomous_authorization is True
    kinds = [decision.kind for decision in project.store.decisions()]
    assert "authorization" in kinds
