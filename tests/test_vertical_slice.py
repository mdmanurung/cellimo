"""The 0.1.0 vertical slice, end to end on a synthetic multi-donor dataset.

    init project
        -> generate analysis.py
        -> audit data
        -> record design
        -> register QC artifact
        -> record reference
        -> write provenance
        -> run scientific checks successfully

Everything the specification's acceptance criteria list is asserted here, in one
place, on data that is generated rather than downloaded.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from cellimo.cli.main import cli
from cellimo.project.project import Project

pytest.importorskip("anndata")


def _quality_control(project: Project) -> tuple[Any, dict[str, int]]:
    """Ordinary, visible QC — the same shape as the notebook's QC cell."""
    import anndata as ad
    import numpy as np

    adata = ad.read_h5ad(project.source_path)
    adata.layers["counts"] = adata.X.copy()

    counts = np.asarray(adata.X.todense())
    genes_per_cell = (counts > 0).sum(axis=1)
    mitochondrial = np.array([name.startswith("MT-") for name in adata.var_names])
    percent_mito = 100 * counts[:, mitochondrial].sum(axis=1) / np.maximum(counts.sum(axis=1), 1)

    keep = (genes_per_cell >= 50) & (percent_mito <= 20)
    labels = adata.obs["sample_id"].astype(str).to_numpy()
    removed = {
        sample: int(((labels == sample) & ~keep).sum()) for sample in sorted(set(labels))
    }
    filtered = adata[keep].copy()
    detected = (np.asarray(filtered.X.todense()) > 0).sum(axis=0) >= 3
    filtered = filtered[:, detected].copy()
    return (adata, filtered, removed)  # type: ignore[return-value]


def test_vertical_slice(tmp_path: Path, synthetic_h5ad: Path) -> None:
    root = tmp_path / "slice"
    root.mkdir()
    data = root / "data"
    data.mkdir()
    source = data / "source.h5ad"
    source.write_bytes(synthetic_h5ad.read_bytes())

    # 1. init -----------------------------------------------------------
    project = Project.init(root, source, profile="scanpy", name="vertical-slice")
    assert project.notebook_path.is_file()
    assert project.config_path.is_file()
    assert project.config.source.sha256

    # 2. audit ----------------------------------------------------------
    audit = project.audit_anndata(backed=True)
    assert audit.n_obs == 720
    assert audit.raw_counts.available
    assert audit.raw_counts.location == "X"
    assert audit.best_candidate("donor") == "participant_id"
    assert audit.best_candidate("sample") == "sample_id"

    # 3. design ---------------------------------------------------------
    design = project.record_design(
        sample="sample_id",
        donor="participant_id",
        condition="condition",
        time="timepoint",
        batch="library_batch",
    )
    assert design.status == "proposed"
    assert design.experimental_unit == "participant_id"
    approved = project.approve_design(approved_by="the analyst")
    assert approved.is_approved()

    # 4. quality control, registered as an artifact ----------------------
    original, filtered, removed = _quality_control(project)
    with project.stage(
        "post_qc",
        summary="Sample-stratified quality control",
        parent_sha256=project.config.source.sha256,
        params={"min_genes": 50, "max_pct_mt": 20, "min_cells": 3},
    ) as stage:
        filtered.write_h5ad(stage.output("artifacts/post_qc.h5ad"))
        stage.add_exclusion(
            "low gene count or high mitochondrial fraction",
            axis="obs",
            n_before=int(original.n_obs),
            n_removed=int(original.n_obs) - int(filtered.n_obs),
            n_remaining=int(filtered.n_obs),
            by_sample=removed,
            stratified_by="sample_id",
            criteria={"min_genes": 50, "max_pct_mt": 20},
        )
        stage.add_exclusion(
            "genes detected in fewer than 3 cells",
            axis="var",
            n_before=int(original.n_vars),
            n_removed=int(original.n_vars) - int(filtered.n_vars),
            n_remaining=int(filtered.n_vars),
        )
        stage.set_matrix_facts(
            representation="raw_counts",
            counts_layer="counts",
            raw_counts_available=True,
            n_obs=int(filtered.n_obs),
            n_vars=int(filtered.n_vars),
            obs_keys=list(filtered.obs.columns),
            layers=list(filtered.layers.keys()),
        )

    post_qc = stage.descriptor
    assert post_qc is not None
    assert post_qc.parent_sha256 == project.config.source.sha256
    # Quality control actually removed the low-quality cells the fixture planted.
    assert int(filtered.n_obs) < int(original.n_obs)
    assert sum(removed.values()) == int(original.n_obs) - int(filtered.n_obs)

    # 5. reference -------------------------------------------------------
    reference = project.record_reference(
        reference_id="notebook:scverse_scanpy_pbmc3k_qc",
        title="PBMC3k quality control",
        source="scverse/scanpy",
        package="scanpy",
        section_ids=["0", "1"],
        used_for="quality-control thresholds",
        stage="post_qc",
    )
    assert reference.record_id.startswith("reference:")

    # 6. a confirmatory analysis that respects the replication structure --
    project.record_statistics(
        name="stim vs ctrl, pseudobulk",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        n_cells={"stim": 300, "ctrl": 300},
        groups=["stim", "ctrl"],
        input_artifact_sha256=post_qc.sha256,
        input_representation="pseudobulk_counts",
        aggregation="pseudobulk",
        covariates=["library_batch"],
        effect_size={"reported": True, "measure": "log2FC", "column": "log2FoldChange"},
        uncertainty={"reported": True, "measure": "adjusted p-value", "column": "padj"},
    )

    # 7. provenance ------------------------------------------------------
    project.capture_environment()
    manifest_path = project.write_manifest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["counts"]["artifacts"] == 3  # source, audit, post_qc
    assert manifest["counts"]["references"] == 1
    assert manifest["counts"]["statistics"] == 1
    assert manifest["latest_by_stage"]["post_qc"] == post_qc.sha256
    assert manifest["design"]["status"] == "approved"
    assert manifest["environment"]["packages"]

    # 8. lineage closes on the source ------------------------------------
    chain = project.artifacts.lineage_of(post_qc.sha256)
    assert [item.stage for item in chain] == ["post_qc", "source"]

    # 9. the checks pass --------------------------------------------------
    report = project.check()
    assert report.passed, report.to_text()
    assert report.exit_code() == 0

    # 10. and so does the CLI, from a subdirectory ------------------------
    runner = CliRunner()
    result = runner.invoke(cli, ["check", str(root / "results" / "figures"), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["passed"] is True
    assert payload["counts"]["error"] == 0


def test_source_survives_the_whole_slice(tmp_path: Path, synthetic_h5ad: Path) -> None:
    root = tmp_path / "immutable"
    root.mkdir()
    source = root / "source.h5ad"
    source.write_bytes(synthetic_h5ad.read_bytes())
    before = source.read_bytes()

    project = Project.init(root, source, name="immutable")
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="the analyst")
    project.write_manifest()
    project.check()

    assert source.read_bytes() == before
    ok, message = project.verify_source()
    assert ok, message
