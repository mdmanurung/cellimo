"""Search-to-cell grounding and the pre-write scientific gate."""

from __future__ import annotations

import json
from pathlib import Path

from cellimo.corpus import CorpusUsage
from cellimo.project.project import Project
from cellimo.retrieval.grounding import GroundingDesign, design_from_project, ground
from cellimo.retrieval.lexical_index import LexicalKnowledgeIndex


def _index(tmp_path: Path, workflows: list[dict[str, object]]) -> LexicalKnowledgeIndex:
    root = tmp_path / "index"
    root.mkdir()
    (root / "cellimo-index.json").write_text(
        json.dumps(
            {
                "meta": {"name": "grounding-fixture", "version": "1"},
                "workflows": workflows,
                "documentation": [],
            }
        ),
        encoding="utf-8",
    )
    return LexicalKnowledgeIndex(root)


def _workflow(
    notebook_id: str,
    *,
    title: str,
    summary: str,
    repository: str,
    path: str,
    sections: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "notebook_id": notebook_id,
        "title": title,
        "summary": summary,
        "source_repository": repository,
        "source_path": path,
        "url": f"https://github.com/{repository}",
        "package": "scanpy",
        "sections": sections,
    }


def _section(section_id: str, content: str, *, heading: str = "") -> dict[str, object]:
    return {
        "section_id": section_id,
        "kind": "code",
        "heading": heading,
        "content": content,
        "order": int(section_id),
    }


def _multi_donor() -> GroundingDesign:
    return GroundingDesign(
        available=True,
        project="kang",
        status="approved",
        experimental_unit="donor",
        n_experimental_units=8,
        sample_column="sample_id",
        n_samples=8,
        condition="condition",
        declared_fields={"donor": "donor", "sample": "sample_id"},
    )


_PLOT_USAGE = CorpusUsage(
    notebooks_by_call={"sc.pl.violin": 188, "sc.pl.dotplot": 185},
    notebooks_scanned=2_845,
)
_PLOT_SIGNATURES = {
    "sc.pl.violin": ["adata", "keys", "groupby"],
    "sc.pl.dotplot": ["adata", "var_names", "groupby"],
}


def test_ground_selects_a_relevant_cell_and_keeps_its_citation(
    fixture_index: Path,
) -> None:
    result = ground(
        LexicalKnowledgeIndex(fixture_index),
        "quality control filter cells by genes",
    )

    assert not result.needs_user_decision
    assert result.api_usage
    code = result.api_usage[0]
    assert code.reference_id == "notebook:scverse_scanpy_pbmc3k_qc"
    assert code.section_id == "1"
    assert code.content.startswith(
        "# cellimo:source notebook:scverse_scanpy_pbmc3k_qc section=1 sha="
    )
    assert "filter_cells" in code.content


def test_ground_separates_api_usage_from_paper_practice(tmp_path: Path) -> None:
    workflows = [
        _workflow(
            "tutorial",
            title="Pseudobulk tutorial",
            summary="Pseudobulk differential expression by donor",
            repository="scverse/scanpy-tutorials",
            path="docs/tutorials/pseudobulk.ipynb",
            sections=[
                _section("0", "pb = dc.get_pseudobulk(adata, sample_col='donor')")
            ],
        ),
        _workflow(
            "paper",
            title="Stimulus response companion analysis",
            summary="Pseudobulk differential expression by donor",
            repository="saezlab/reheat2_pub",
            path="analysis/03_de.ipynb",
            sections=[
                _section("0", "pb = dc.get_pseudobulk(adata, sample_col='donor')")
            ],
        ),
    ]
    result = ground(_index(tmp_path, workflows), "pseudobulk donor expression")

    assert [item.reference_id for item in result.api_usage] == ["notebook:tutorial"]
    assert [item.reference_id for item in result.in_practice] == ["notebook:paper"]


def test_section_selection_does_not_return_the_whole_notebook(tmp_path: Path) -> None:
    sections = [_section(str(i), f"np.asarray(matrix_{i})") for i in range(8)]
    sections.append(
        _section(
            "8",
            "sc.pp.calculate_qc_metrics(adata, qc_vars=['mt'], inplace=True)",
            heading="Mitochondrial quality control",
        )
    )
    index = _index(
        tmp_path,
        [
            _workflow(
                "long",
                title="Long analysis",
                summary="Mitochondrial quality control in a long notebook",
                repository="lab/paper",
                path="analysis.ipynb",
                sections=sections,
            )
        ],
    )

    result = ground(index, "mitochondrial quality control", top_k=5)

    assert [(item.reference_id, item.section_id) for item in result.examples] == [
        ("notebook:long", "8")
    ]


def test_no_precedent_requires_a_user_decision(fixture_index: Path) -> None:
    result = ground(
        LexicalKnowledgeIndex(fixture_index),
        "trajectory velocity dynamical latent time",
    )

    assert result.examples == []
    assert result.needs_user_decision
    assert "stop and ask the user" in result.note


def test_per_cell_confirmatory_de_is_withheld_for_a_multi_donor_design(
    tmp_path: Path,
) -> None:
    index = _index(
        tmp_path,
        [
            _workflow(
                "per_cell_de",
                title="Differential expression by condition",
                summary="Compare conditions with a Wilcoxon test",
                repository="lab/paper",
                path="de.ipynb",
                sections=[
                    _section(
                        "0",
                        "sc.tl.rank_genes_groups(adata, groupby='condition', "
                        "method='wilcoxon')",
                    )
                ],
            )
        ],
    )

    result = ground(
        index,
        "differential expression condition",
        design=_multi_donor(),
        analysis_mode="confirmatory",
    )

    assert result.examples == []
    assert result.needs_user_decision
    assert {finding.code for finding in result.rejected} == {"C004"}
    assert "8 levels" in result.rejected[0].detail


def test_a_single_donor_is_escalated_as_a_design_limit(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _workflow(
                "per_cell_de",
                title="Differential expression",
                summary="Differential expression test",
                repository="lab/paper",
                path="de.ipynb",
                sections=[_section("0", "sc.tl.rank_genes_groups(adata, groupby='group')")],
            )
        ],
    )
    design = _multi_donor().model_copy(update={"n_experimental_units": 1})

    result = ground(
        index,
        "differential expression",
        design=design,
        analysis_mode="confirmatory",
    )

    assert result.needs_user_decision
    assert "one level" in result.rejected[0].detail
    assert "settled as exploratory" in result.rejected[0].detail


def test_pseudobulk_ancestry_makes_confirmatory_de_eligible(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _workflow(
                "pseudobulk_de",
                title="Pseudobulk differential expression",
                summary="Pseudobulk counts per donor then DESeq2",
                repository="lab/paper",
                path="analysis/de.ipynb",
                sections=[
                    _section("0", "pb = dc.get_pseudobulk(adata, sample_col='donor')"),
                    _section("1", "dds = pydeseq2.dds.DeseqDataSet(counts=pb)"),
                ],
            )
        ],
    )

    result = ground(
        index,
        "pseudobulk differential expression donor deseq",
        design=_multi_donor(),
        analysis_mode="confirmatory",
    )

    assert result.examples
    assert not result.rejected


def test_de_after_corrected_values_is_withheld(tmp_path: Path) -> None:
    index = _index(
        tmp_path,
        [
            _workflow(
                "scaled_de",
                title="Differential expression after scaling",
                summary="Scale then differential expression",
                repository="lab/paper",
                path="analysis/de.ipynb",
                sections=[
                    _section(
                        "0",
                        "sc.pp.scale(adata)\n"
                        "sc.tl.rank_genes_groups(adata, groupby='condition')",
                    )
                ],
            )
        ],
    )

    result = ground(
        index,
        "differential expression after scaling",
        design=_multi_donor(),
        analysis_mode="confirmatory",
    )

    assert "C006" in {finding.code for finding in result.rejected}
    assert result.needs_user_decision


def test_pooled_qc_is_withheld_but_sample_stratified_qc_is_returned(
    tmp_path: Path,
) -> None:
    index = _index(
        tmp_path,
        [
            _workflow(
                "pooled",
                title="Pooled quality control",
                summary="Filter low quality cells by genes",
                repository="lab/pooled-paper",
                path="qc.ipynb",
                sections=[_section("0", "sc.pp.filter_cells(adata, min_genes=200)")],
            ),
            _workflow(
                "stratified",
                title="Sample stratified quality control",
                summary="Filter low quality cells within each sample",
                repository="lab/stratified-paper",
                path="qc.ipynb",
                sections=[
                    _section(
                        "0",
                        "for sample in adata.obs['sample_id'].unique():\n"
                        "    sample_adata = adata[adata.obs['sample_id'] == sample].copy()\n"
                        "    sc.pp.filter_cells(sample_adata, min_genes=200)",
                    )
                ],
            ),
        ],
    )

    result = ground(index, "quality control filter cells", design=_multi_donor())

    assert [item.reference_id for item in result.examples] == ["notebook:stratified"]
    assert {finding.code for finding in result.rejected} == {"C008"}
    assert not result.needs_user_decision


def test_project_design_uses_latest_audit_cardinalities(project: Project) -> None:
    project.audit_anndata()
    project.record_design(
        sample="sample_id",
        donor="participant_id",
        condition="condition",
        experimental_unit="participant_id",
    )
    project.approve_design(approved_by="the user")

    design = design_from_project(project)

    assert design.available
    assert design.n_experimental_units == 6
    assert design.n_samples == 6


def test_candidate_code_reinvention_requires_a_user_decision(
    fixture_index: Path,
) -> None:
    result = ground(
        LexicalKnowledgeIndex(fixture_index),
        "quality control filter cells by genes",
        candidate_code="ax.boxplot(adata.obs['n_genes_by_counts'])",
        usage=_PLOT_USAGE,
        signatures=_PLOT_SIGNATURES,
    )

    assert result.candidate_reviewed
    assert result.needs_user_decision
    assert result.reinvention[0].wrote == "boxplot()"
    assert result.reinvention[0].candidates[0] == "sc.pl.violin"


def test_candidate_using_a_native_plot_passes_reinvention_review(
    fixture_index: Path,
) -> None:
    result = ground(
        LexicalKnowledgeIndex(fixture_index),
        "quality control filter cells by genes",
        candidate_code=(
            "sc.pl.violin(adata, ['n_genes_by_counts'], groupby='sample_id')"
        ),
        usage=_PLOT_USAGE,
        signatures=_PLOT_SIGNATURES,
    )

    assert result.candidate_reviewed
    assert not result.reinvention
    assert not result.needs_user_decision


def test_candidate_review_fails_closed_without_corpus_usage(
    fixture_index: Path,
) -> None:
    result = ground(
        LexicalKnowledgeIndex(fixture_index),
        "quality control filter cells by genes",
        candidate_code="ax.boxplot(adata.obs['n_genes_by_counts'])",
        signatures=_PLOT_SIGNATURES,
    )

    assert not result.candidate_reviewed
    assert result.needs_user_decision
    assert "G002" in {finding.code for finding in result.rejected}


def test_candidate_review_fails_closed_without_installed_signatures(
    fixture_index: Path,
) -> None:
    result = ground(
        LexicalKnowledgeIndex(fixture_index),
        "quality control filter cells by genes",
        candidate_code="ax.boxplot(adata.obs['n_genes_by_counts'])",
        usage=_PLOT_USAGE,
    )

    assert not result.candidate_reviewed
    assert result.needs_user_decision
    assert "G003" in {finding.code for finding in result.rejected}
