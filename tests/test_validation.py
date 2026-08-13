"""Scientific and structural validation, driven by intentionally flawed projects.

Each required failure in the specification gets a project that exhibits it and a
test that asserts the corresponding check fires as an error. The clean project is
tested too: a validator that fires on correct work is worse than none.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cellimo.artifacts.descriptor import ArtifactDescriptor
from cellimo.project.project import Project
from cellimo.provenance.records import (
    EffectSizeReport,
    StatisticsRecord,
    UncertaintyReport,
)

# -- helpers ---------------------------------------------------------------


def _write(project: Project, relative: str, payload: bytes) -> str:
    target = project.root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return relative


def _register_qc(project: Project, **overrides: object) -> ArtifactDescriptor:
    fields: dict[str, object] = {
        "stage": "post_qc",
        "parent_sha256": project.config.source.sha256,
        "representation": "raw_counts",
        "counts_layer": "counts",
        "raw_counts_available": True,
        "n_obs": 600,
        "n_vars": 288,
        "exclusions": [
            {
                "reason": "low gene count",
                "axis": "obs",
                "n_before": 720,
                "n_removed": 120,
                "n_remaining": 600,
                "by_sample": {f"sample{i:02d}": 20 for i in range(6)},
                "stratified_by": "sample_id",
            }
        ],
    }
    fields.update(overrides)
    return project.register_artifact(
        _write(project, "artifacts/post_qc.h5ad", b"post-qc payload"), **fields
    )


def _sound_project(project: Project) -> Project:
    """A project that should pass every check."""
    project.audit_anndata(backed=True)
    project.record_design(
        sample="sample_id",
        donor="participant_id",
        condition="condition",
        batch="library_batch",
    )
    project.approve_design(approved_by="a human", actor="user")
    qc = _register_qc(project)
    project.record_reference(
        reference_id="notebook:theislab_pseudobulk_de",
        title="Pseudobulk differential expression",
        used_for="choice of test",
        stage="statistics",
    )
    project.record_statistics(
        name="stim vs ctrl",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        n_cells={"stim": 300, "ctrl": 300},
        groups=["stim", "ctrl"],
        input_artifact_sha256=qc.sha256,
        input_representation="pseudobulk_counts",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC", "column": "log2FoldChange"},
        uncertainty={"reported": True, "measure": "adjusted p-value", "column": "padj"},
    )
    project.write_manifest()
    return project


def _codes(project: Project, severity: str = "error") -> set[str]:
    return {
        finding.code
        for finding in project.check().findings
        if finding.severity == severity
    }


def _citation_findings(project: Project):
    from cellimo.validation.engine import run_checks

    return run_checks(project, only=["S009"]).findings


# -- the clean case --------------------------------------------------------


def test_a_sound_project_passes(project: Project) -> None:
    report = _sound_project(project).check()
    assert report.passed, report.to_text()
    assert report.exit_code() == 0
    assert report.checks_run >= 20


def test_a_freshly_initialised_project_has_no_errors(project: Project) -> None:
    report = project.check()
    assert report.passed, report.to_text()
    # But it does say the design is still missing.
    assert "C001" in {finding.code for finding in report.warnings}


# -- required failure 1: missing experimental unit -------------------------


def test_missing_experimental_unit_is_an_error(project: Project) -> None:
    project.audit_anndata(backed=True)
    qc = _register_qc(project)
    # Written straight to the store: the Project API refuses to create this.
    project.store.append_statistics(
        StatisticsRecord(
            name="stim vs ctrl",
            test="pseudobulk_deseq2",
            mode="confirmatory",
            experimental_unit=None,
            unit_level="donor",
            n_units={"stim": 3, "ctrl": 3},
            input_artifact_sha256=qc.sha256,
            input_representation="pseudobulk_counts",
            aggregation="pseudobulk",
            effect_size=EffectSizeReport(reported=True, measure="log2FC"),
            uncertainty=UncertaintyReport(reported=True, measure="padj"),
        )
    )
    errors = _codes(project)
    assert "C001" in errors
    assert "C002" in errors  # and the design was never approved


# -- required failure 2: unidentified raw counts ---------------------------


def test_unidentified_raw_counts_is_an_error(tmp_path: Path, normalized_h5ad: Path) -> None:
    root = tmp_path / "normalized-project"
    root.mkdir()
    project = Project.init(root, normalized_h5ad, name="normalized")
    project.audit_anndata(backed=True)
    _register_qc(
        project,
        representation="lognorm",
        counts_layer=None,
        raw_counts_available=False,
    )
    assert "C003" in _codes(project)


def test_declared_upstream_unavailability_downgrades_to_a_warning(
    tmp_path: Path, normalized_h5ad: Path
) -> None:
    root = tmp_path / "declared-project"
    root.mkdir()
    project = Project.init(root, normalized_h5ad, name="declared")
    project.config.source = project.config.source.model_copy(
        update={
            "raw_counts_unavailable_upstream": True,
            "raw_counts_note": "published object; authors discarded counts",
        }
    )
    project.save()
    project.audit_anndata(backed=True)
    _register_qc(project, representation="lognorm", counts_layer=None, raw_counts_available=False)
    report = project.check()
    assert "C003" not in {finding.code for finding in report.errors}
    assert "C003" in {finding.code for finding in report.warnings}


# -- required failure 3: cells as biological replicates --------------------


def test_cells_as_replicates_is_an_error(project: Project) -> None:
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="a human", actor="user")
    qc = _register_qc(project)
    project.record_statistics(
        name="stim vs ctrl per cell",
        test="wilcoxon",
        mode="confirmatory",
        unit_level="cell",
        n_units={"stim": 360, "ctrl": 360},
        input_artifact_sha256=qc.sha256,
        input_representation="lognorm",
        aggregation="none",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    findings = project.check().findings
    pseudoreplication = [item for item in findings if item.code == "C004"]
    assert pseudoreplication and pseudoreplication[0].severity == "error"
    assert "Squair" in " ".join(pseudoreplication[0].references)


def test_a_stated_justification_downgrades_cell_level_testing(project: Project) -> None:
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="a human", actor="user")
    qc = _register_qc(project)
    project.record_statistics(
        name="within-donor perturbation contrast",
        test="wilcoxon",
        mode="confirmatory",
        unit_level="cell",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="lognorm",
        aggregation="none",
        justification=(
            "single-donor perturbation screen; the well is the experimental unit "
            "and no biological replication axis exists by design"
        ),
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    report = project.check()
    assert "C004" not in {finding.code for finding in report.errors}
    assert "C004" in {finding.code for finding in report.warnings}


def test_a_group_with_one_replicate_is_an_error(project: Project) -> None:
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="a human", actor="user")
    qc = _register_qc(project)
    project.record_statistics(
        name="stim vs ctrl",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 1, "ctrl": 5},
        input_artifact_sha256=qc.sha256,
        input_representation="pseudobulk_counts",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    assert "C005" in _codes(project)


# -- required failure 4: DE on integrated values ---------------------------


def test_integrated_representation_as_de_input_is_an_error(project: Project) -> None:
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="a human", actor="user")
    qc = _register_qc(project)
    integrated = project.register_artifact(
        _write(project, "artifacts/integrated.h5ad", b"integrated payload"),
        stage="integrated",
        parent_sha256=qc.sha256,
        representation="integrated_expression",
        raw_counts_available=False,
    )
    project.record_statistics(
        name="stim vs ctrl on corrected values",
        test="pseudobulk_limma",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=integrated.sha256,
        input_representation="integrated_expression",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    findings = project.check().findings
    integration = [item for item in findings if item.code == "C006"]
    assert integration and integration[0].severity == "error"


# -- required failure 5: incomplete lineage --------------------------------


def test_incomplete_lineage_is_an_error(project: Project) -> None:
    project.store.append_artifact(
        ArtifactDescriptor(
            artifact_id="normalized:feedfacefeed",
            stage="normalized",
            kind="anndata",
            path="artifacts/normalized.h5ad",
            sha256="f" * 64,
            parent_sha256="e" * 64,  # never registered
        )
    )
    _write(project, "artifacts/normalized.h5ad", b"normalized payload")
    assert "S004" in _codes(project)


def test_a_missing_artifact_file_is_an_error(project: Project) -> None:
    qc = _register_qc(project)
    (project.root / qc.path).unlink()
    assert "S003" in _codes(project)


def test_a_modified_artifact_is_an_error(project: Project) -> None:
    qc = _register_qc(project)
    (project.root / qc.path).write_bytes(b"different content of a different length")
    assert "S008" in _codes(project)


def test_same_length_modification_is_still_detected(project: Project) -> None:
    """Size alone is not enough: a same-size overwrite must still be caught."""
    qc = _register_qc(project)
    target = project.root / qc.path
    original = target.read_bytes()
    target.write_bytes(b"X" * len(original))  # same size, newer mtime
    assert "S008" in _codes(project)


def test_untouched_artifacts_are_not_re_hashed(project: Project, monkeypatch) -> None:
    """Section 11 of the notebook re-runs check reactively; it must stay cheap."""
    import cellimo.validation.checks as checks_module

    _register_qc(project)
    calls: list[str] = []
    real = checks_module.hash_file

    def counting_hash_file(path, **kwargs):
        calls.append(str(path))
        return real(path, **kwargs)

    monkeypatch.setattr(checks_module, "hash_file", counting_hash_file)
    project.check()
    assert calls == [], f"re-hashed unchanged artifacts: {calls}"


def test_uncited_analysis_cell_is_visible_to_cellimo_check(project: Project) -> None:
    project.notebook_path.write_text(
        """\
@app.cell
def _(adata, sc):
    sc.pp.normalize_total(adata)
    return
""",
        encoding="utf-8",
    )

    findings = _citation_findings(project)
    assert len(findings) == 1
    assert findings[0].title == "Scientific notebook cell has no grounding citation"
    assert "sc.pp.normalize_total" in findings[0].detail


def test_one_cited_cell_does_not_cover_the_next_uncited_cell(
    project: Project,
    fixture_index: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cellimo.retrieval.lexical_index import LexicalKnowledgeIndex

    monkeypatch.setenv("CELLIMO_INDEX_DIR", str(fixture_index))
    reference = LexicalKnowledgeIndex(fixture_index).get_reference(
        "notebook:scverse_scanpy_pbmc3k_qc", ["1"]
    )
    header = reference.sections[0].content.splitlines()[0]
    project.notebook_path.write_text(
        f"""\
@app.cell
def _(adata, sc):
    {header}
    sc.pp.filter_cells(adata, min_genes=250)
    return

@app.cell
def _(adata, sc):
    sc.pp.normalize_total(adata)
    return
""",
        encoding="utf-8",
    )

    findings = _citation_findings(project)
    assert len(findings) == 1
    assert findings[0].title == "Scientific notebook cell has no grounding citation"
    assert findings[0].location.endswith(":8")


def test_a_resolvable_cited_analysis_cell_passes_the_citation_audit(
    project: Project,
    fixture_index: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cellimo.retrieval.lexical_index import LexicalKnowledgeIndex

    monkeypatch.setenv("CELLIMO_INDEX_DIR", str(fixture_index))
    reference = LexicalKnowledgeIndex(fixture_index).get_reference(
        "notebook:scverse_scanpy_pbmc3k_qc", ["1"]
    )
    header = reference.sections[0].content.splitlines()[0]
    project.notebook_path.write_text(
        f"""\
@app.cell
def _(adata, sc):
    {header}
    sc.pp.filter_cells(adata, min_genes=250)
    return
""",
        encoding="utf-8",
    )

    assert _citation_findings(project) == []


def test_a_drifted_notebook_citation_is_reported(
    project: Project,
    fixture_index: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CELLIMO_INDEX_DIR", str(fixture_index))
    project.notebook_path.write_text(
        """\
@app.cell
def _(adata, sc):
    # cellimo:source notebook:scverse_scanpy_pbmc3k_qc section=1 sha=000000000000
    sc.pp.filter_cells(adata, min_genes=250)
    return
""",
        encoding="utf-8",
    )

    findings = _citation_findings(project)
    assert len(findings) == 1
    assert findings[0].title == "Notebook grounding citation does not resolve"
    assert "source now hashes" in findings[0].detail


def test_exclusion_arithmetic_is_reconciled(project: Project) -> None:
    _register_qc(
        project,
        exclusions=[
            {
                "reason": "low gene count",
                "axis": "obs",
                "n_before": 720,
                "n_removed": 120,
                "n_remaining": 700,  # does not add up
            }
        ],
    )
    assert "S005" in _codes(project)


def test_quality_control_without_exclusions_is_flagged(project: Project) -> None:
    project.record_design(sample="sample_id", donor="participant_id")
    _register_qc(project, exclusions=[], n_obs=100)
    report = project.check()
    assert "C010" in {finding.code for finding in report.findings}


def test_unstratified_exclusions_are_warned_about(project: Project) -> None:
    project.record_design(sample="sample_id", donor="participant_id")
    _register_qc(
        project,
        exclusions=[
            {
                "reason": "pooled threshold",
                "axis": "obs",
                "n_before": 720,
                "n_removed": 120,
                "n_remaining": 600,
            }
        ],
    )
    assert "C008" in _codes(project, severity="warning")


def test_integration_without_a_recorded_diagnostic_is_warned_about(project: Project) -> None:
    qc = _register_qc(project)
    project.register_artifact(
        _write(project, "artifacts/integrated.h5ad", b"integrated"),
        stage="integrated",
        parent_sha256=qc.sha256,
        representation="integrated_embedding",
    )
    assert "C009" in _codes(project, severity="warning")


def test_findings_carry_a_remedy_and_a_location(project: Project) -> None:
    qc = _register_qc(project)
    (project.root / qc.path).unlink()
    findings = [item for item in project.check().findings if item.code == "S003"]
    assert findings[0].remedy
    assert findings[0].location.startswith("artifact:")


# -- the checks must not be escapable by naming things differently ---------


def _approved(project: Project) -> None:
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="a human", actor="user")


def test_an_unrecognised_test_name_does_not_escape_the_replication_rule(
    project: Project,
) -> None:
    """The old rule matched test names; anything unlisted walked straight past it."""
    _approved(project)
    qc = _register_qc(project)
    project.record_statistics(
        name="stim vs ctrl",
        test="kruskal_wallis",  # in no denylist
        mode="confirmatory",
        unit_level="unknown",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="lognorm",
        aggregation="none",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    assert "C004" in _codes(project)


def test_an_unrecognised_test_name_does_not_escape_the_integration_rule(
    project: Project,
) -> None:
    _approved(project)
    qc = _register_qc(project)
    integrated = project.register_artifact(
        _write(project, "artifacts/integrated.h5ad", b"integrated payload"),
        stage="integrated",
        parent_sha256=qc.sha256,
        representation="integrated_expression",
    )
    project.record_statistics(
        name="a comparison",
        test="my_custom_statistic",  # deliberately unrecognisable
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=integrated.sha256,
        input_representation="integrated_expression",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    assert "C006" in _codes(project)


def test_exploratory_marker_ranking_on_an_integrated_object_is_not_an_error(
    project: Project,
) -> None:
    """Clusters are found on the integrated embedding; ranking markers there is normal."""
    _approved(project)
    qc = _register_qc(project)
    integrated = project.register_artifact(
        _write(project, "artifacts/integrated.h5ad", b"integrated payload"),
        stage="integrated",
        parent_sha256=qc.sha256,
        representation="integrated_embedding",
    )
    project.record_statistics(
        name="cluster markers",
        test="rank_genes_groups",
        mode="exploratory",
        unit_level="cell",
        input_artifact_sha256=integrated.sha256,
        input_representation="integrated_embedding",
        aggregation="none",
    )
    report = project.check()
    assert "C006" not in {finding.code for finding in report.errors}
    assert "C004" not in {finding.code for finding in report.errors}


def test_an_agent_approving_its_own_design_is_reported(project: Project) -> None:
    """`approved_by` is a string the caller writes; it cannot mean a human was there."""
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    # The default actor is "agent" — an unattended call, however it labels itself.
    project.approve_design(approved_by="the user")
    qc = _register_qc(project)
    project.record_statistics(
        name="stim vs ctrl",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="pseudobulk_counts",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    findings = [item for item in project.check().findings if item.code == "C002"]
    assert findings, "an agent self-approval must not pass silently"
    assert "approved by the agent" in findings[0].title
    # The decision log must not claim a human did it.
    approvals = [
        record
        for record in project.store.decisions()
        if record.kind == "design" and record.parameters.get("approved") is True
    ]
    assert approvals[-1].actor == "agent"


def test_a_human_approval_is_accepted_without_comment(project: Project) -> None:
    project.audit_anndata(backed=True)
    project.record_design(donor="participant_id", condition="condition")
    project.approve_design(approved_by="Dr Someone", actor="user")
    qc = _register_qc(project)
    project.record_statistics(
        name="stim vs ctrl",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="pseudobulk_counts",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    assert "C002" not in {finding.code for finding in project.check().findings}


@pytest.mark.parametrize("excuse", [".", "-", "n/a", "NA", "ok", "  ", "see notebook"])
def test_a_token_justification_does_not_downgrade_an_error(
    project: Project, excuse: str
) -> None:
    """The escape hatch must cost something, or every rule here is opt-out."""
    _approved(project)
    qc = _register_qc(project)
    project.record_statistics(
        name="per-cell test",
        test="wilcoxon",
        mode="confirmatory",
        unit_level="cell",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="lognorm",
        aggregation="none",
        justification=excuse,
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    assert "C004" in _codes(project), f"{excuse!r} should not buy a downgrade"


def test_a_substantive_justification_still_downgrades(project: Project) -> None:
    _approved(project)
    qc = _register_qc(project)
    project.record_statistics(
        name="per-cell test",
        test="wilcoxon",
        mode="confirmatory",
        unit_level="cell",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="lognorm",
        aggregation="none",
        justification=(
            "single-donor perturbation screen; the well is the experimental unit "
            "and no biological replication axis exists by design"
        ),
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    report = project.check()
    assert "C004" not in {finding.code for finding in report.errors}
    assert "C004" in {finding.code for finding in report.warnings}


def _sample_aware_record(project: Project, **overrides: object) -> None:
    qc = _register_qc(project)
    fields: dict[str, object] = {
        "name": "stim vs ctrl",
        "test": "wilcoxon",
        "mode": "confirmatory",
        "unit_level": "donor",
        "n_units": {"stim": 3, "ctrl": 3},
        "input_artifact_sha256": qc.sha256,
        "input_representation": "raw_counts",
        "aggregation": "none",
        "effect_size": {"reported": True, "measure": "log2FC"},
        "uncertainty": {"reported": True, "measure": "padj"},
    }
    project.record_statistics(**(fields | overrides))  # type: ignore[arg-type]


def test_a_declared_replicate_unit_that_was_never_aggregated_to_is_reported(
    project: Project,
) -> None:
    """Naming donors as the unit is not the same as computing across them."""
    _approved(project)
    _sample_aware_record(project)
    assert "C012" in _codes(project, "warning")


def test_aggregating_to_the_declared_unit_satisfies_the_rule(project: Project) -> None:
    _approved(project)
    _sample_aware_record(project, aggregation="pseudobulk", test="pseudobulk_deseq2")
    assert "C012" not in _codes(project, "warning")


def test_c012_does_not_restate_what_c004_already_reported(project: Project) -> None:
    """The two checks partition the confirmatory records; they do not overlap.

    A cell-level record with no justification is C004's, and an earlier C012
    keyed on the test name fired on it too — a second, weaker finding saying the
    same thing about the same record.
    """
    _approved(project)
    _sample_aware_record(project, unit_level="cell")
    assert "C004" in _codes(project, "error")
    assert "C012" not in _codes(project, "warning")


def test_relabelling_the_input_does_not_launder_a_corrected_artifact(
    project: Project,
) -> None:
    """The artifact's own hash-pinned representation wins over the record's claim."""
    _approved(project)
    qc = _register_qc(project)
    integrated = project.register_artifact(
        _write(project, "artifacts/integrated.h5ad", b"integrated payload"),
        stage="integrated",
        parent_sha256=qc.sha256,
        representation="integrated_expression",
    )
    project.record_statistics(
        name="stim vs ctrl",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=integrated.sha256,
        # The lie: the artifact is batch-corrected, the record says it is not.
        input_representation="lognorm",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    findings = [item for item in project.check().findings if item.code == "C006"]
    assert findings and findings[0].severity == "error"
    assert "artifact's own record is what counts" in findings[0].detail


def test_a_fabricated_autonomous_authorisation_is_rejected(project: Project) -> None:
    """The agent must not be able to sign off on its own design."""
    project.audit_anndata(backed=True)
    project.record_design(
        donor="participant_id",
        condition="condition",
        approve=True,
        approved_by="autonomous_authorization",
    )
    qc = _register_qc(project)
    project.record_statistics(
        name="stim vs ctrl",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="pseudobulk_counts",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    findings = [item for item in project.check().findings if item.code == "C002"]
    assert findings and findings[0].severity == "error"
    assert "never recorded" in findings[0].title


def test_a_real_autonomous_authorisation_is_accepted_with_a_warning(
    project: Project,
) -> None:
    project.audit_anndata(backed=True)
    project.authorize_autonomous("user is running this unattended overnight")
    project.record_design(
        donor="participant_id",
        condition="condition",
        approve=True,
        approved_by="autonomous_authorization",
    )
    qc = _register_qc(project)
    project.record_statistics(
        name="stim vs ctrl",
        test="pseudobulk_deseq2",
        mode="confirmatory",
        unit_level="donor",
        n_units={"stim": 3, "ctrl": 3},
        input_artifact_sha256=qc.sha256,
        input_representation="pseudobulk_counts",
        aggregation="pseudobulk",
        effect_size={"reported": True, "measure": "log2FC"},
        uncertainty={"reported": True, "measure": "padj"},
    )
    report = project.check()
    assert report.passed, report.to_text()
    assert "C002" in {finding.code for finding in report.warnings}


@pytest.mark.parametrize(
    "code",
    [
        "S003",
        "S004",
        "S005",
        "S009",
        "C001",
        "C003",
        "C004",
        "C005",
        "C006",
        "C008",
        "C010",
    ],
)
def test_every_required_check_is_still_registered(code: str) -> None:
    """A smoke test that nothing was dropped from the registry — nothing more.

    It proves the code exists, not that the rule fires: a check whose body was
    replaced with ``return []`` would satisfy it. Each code's actual behaviour is
    covered by its own test above, which is what catches that.
    """
    from cellimo.validation.engine import CHECKS

    assert code in {check.code for check in CHECKS}


def test_every_registered_check_runs_against_a_real_project(project: Project) -> None:
    """Each check must execute and return findings without blowing up.

    Importing ``checks`` explicitly rather than relying on another test having
    done it: ``CHECKS`` is populated by the ``@register`` decorators at import
    time, so a check module that failed to import would otherwise look like an
    empty-but-passing registry.
    """
    import cellimo.validation.checks  # noqa: F401  (registers the checks)
    from cellimo.validation.engine import CHECKS, ValidationContext

    assert len(CHECKS) >= 22
    context = ValidationContext(project)
    for check in CHECKS:
        assert check.title
        findings = check(context)
        assert all(finding.code == check.code for finding in findings), check.code
