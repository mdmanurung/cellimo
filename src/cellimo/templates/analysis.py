import marimo

__generated_with = "0.23.16"
app = marimo.App(width="medium", app_title="Cellimo analysis")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    # --- 1. Project setup -------------------------------------------------
    # Cellimo owns provenance, artifacts and validation. Marimo owns the
    # kernel. Nothing here calls a language model.
    from pathlib import Path

    from cellimo import Project

    _root = mo.notebook_dir() or Path.cwd()
    project = Project.open(_root)
    config = project.config
    return Path, config, project


@app.cell
def _(config, mo, project):
    # --- 2. Project header ------------------------------------------------
    _design = config.design
    _declared = _design.declared_fields()
    _design_line = (
        ", ".join(f"`{key}` = `{value}`" for key, value in _declared.items())
        or "_not declared yet_"
    )
    mo.md(
        f"""
        # {config.project.name}

        **Source** `{config.source.path}` · sha256 `{config.source.sha256[:12]}` ·
        immutable
        **Profile** `{config.environment.profile}` · **seed** `{config.random_seed}`
        **Design** {_design_line}
        **Design status** `{_design.status}` ·
        **experimental unit** `{_design.experimental_unit or "unset"}`

        Registered source data cannot be written through Cellimo's APIs. Analysis
        outputs go to `{config.paths.artifacts}/` and `{config.paths.results}/`;
        the trail goes to `{config.paths.provenance}/`.

        Project root: `{project.root}`
        """
    )
    return


@app.cell
def _(mo):
    # --- 3. Dataset audit -------------------------------------------------
    # Reads the source in backed mode and samples the matrix, so auditing a
    # large .h5ad costs seconds rather than loading the whole object.
    run_audit = mo.ui.run_button(label="Audit the source dataset", kind="neutral")
    mo.md(f"### 3. Dataset audit\n\n{run_audit}")
    return (run_audit,)


@app.cell
def _(mo, project, run_audit):
    mo.stop(
        not run_audit.value,
        mo.md("_Run the audit to see the shape of the data, where counts live, and "
              "which `obs` columns could carry the design._"),
    )
    audit = project.audit_anndata(backed=True)
    mo.md(
        "```\n" + "\n".join(audit.summary_lines()) + "\n```"
    )
    return (audit,)


@app.cell
def _(audit, mo):
    # --- 4. Experimental-design declaration -------------------------------
    # The audit *proposes*; a human approves. Confirmatory statistics stay
    # blocked until the biological replicate is named and signed off.
    _columns = ["(none)", *audit.obs_names()]

    def _pick(field):
        best = audit.best_candidate(field)
        return best if best in _columns else "(none)"

    sample_select = mo.ui.dropdown(
        options=_columns, value=_pick("sample"), label="sample", searchable=True
    )
    donor_select = mo.ui.dropdown(
        options=_columns, value=_pick("donor"), label="donor / participant", searchable=True
    )
    condition_select = mo.ui.dropdown(
        options=_columns, value=_pick("condition"), label="condition", searchable=True
    )
    time_select = mo.ui.dropdown(
        options=_columns, value=_pick("time"), label="time", searchable=True
    )
    batch_select = mo.ui.dropdown(
        options=_columns, value=_pick("batch"), label="batch", searchable=True
    )
    study_select = mo.ui.dropdown(
        options=_columns, value=_pick("study"), label="study", searchable=True
    )
    unit_select = mo.ui.dropdown(
        options=_columns,
        value=_pick("donor") if _pick("donor") != "(none)" else _pick("sample"),
        label="experimental unit (the biological replicate)",
        searchable=True,
    )
    mo.vstack(
        [
            mo.md("### 4. Experimental design"),
            mo.md(
                "The **experimental unit** is the column that identifies independent "
                "biological replicates — usually the donor. It is never a cell-level "
                "identifier."
            ),
            mo.hstack([sample_select, donor_select, condition_select], justify="start"),
            mo.hstack([time_select, batch_select, study_select], justify="start"),
            unit_select,
        ]
    )
    return (
        batch_select,
        condition_select,
        donor_select,
        sample_select,
        study_select,
        time_select,
        unit_select,
    )


@app.cell
def _(mo):
    propose_design = mo.ui.run_button(label="Record as proposed", kind="neutral")
    approve_design = mo.ui.run_button(
        label="Approve design — unblocks inferential analysis", kind="success"
    )
    mo.hstack([propose_design, approve_design], justify="start")
    return approve_design, propose_design


@app.cell
def _(
    approve_design,
    batch_select,
    condition_select,
    donor_select,
    mo,
    project,
    propose_design,
    sample_select,
    study_select,
    time_select,
    unit_select,
):
    mo.stop(
        not (propose_design.value or approve_design.value),
        mo.md("_Choose the columns above, then record or approve the design._"),
    )

    def _value(element):
        return None if element.value == "(none)" else element.value

    design = project.record_design(
        sample=_value(sample_select),
        donor=_value(donor_select),
        condition=_value(condition_select),
        time=_value(time_select),
        batch=_value(batch_select),
        study=_value(study_select),
        experimental_unit=_value(unit_select),
        approve=bool(approve_design.value),
        approved_by="notebook user" if approve_design.value else None,
        actor="user",
    )
    mo.md(
        f"Design status **{design.status}** · experimental unit "
        f"**{design.experimental_unit or 'unset'}**"
        + ("\n\nInferential analysis is unblocked." if design.is_approved() else
           "\n\nInferential analysis remains blocked until the design is approved.")
    )
    return (design,)


@app.cell
def _(design, mo):
    # --- 5. Analysis plan -------------------------------------------------
    mo.md(
        f"""
        ### 5. Analysis plan

        One objective at a time. For each step: retrieve references, write a
        bounded section, run it, inspect the output, record what was decided.

        | Stage | Status |
        | --- | --- |
        | audit | done |
        | design | `{design.status}` |
        | post_qc | below |
        | normalized | not started |
        | integrated | only if a batch effect is demonstrated |
        | annotated | not started |
        | statistics | {"available" if design.is_approved() else "**blocked** until design approval"} |

        Invariants enforced by `cellimo check`: raw counts identified; QC
        stratified by sample; every exclusion recorded; cells are not biological
        replicates; differential expression does not consume batch-corrected
        values; lineage closes on the source.
        """
    )
    return


@app.cell
def _(mo):
    # --- 6. Quality-control configuration ---------------------------------
    min_genes = mo.ui.number(start=0, stop=5000, step=10, value=200, label="min genes per cell")
    min_cells = mo.ui.number(start=0, stop=1000, step=1, value=3, label="min cells per gene")
    max_mito = mo.ui.number(
        start=0.0, stop=100.0, step=0.5, value=15.0, label="max % mitochondrial counts"
    )
    mad_threshold = mo.ui.number(
        start=1.0, stop=10.0, step=0.5, value=5.0, label="MAD threshold (within sample)"
    )
    mito_prefix = mo.ui.text(value="MT-", label="mitochondrial gene prefix")
    mo.vstack(
        [
            mo.md("### 6. Quality control — thresholds"),
            mo.md(
                "Thresholds are applied **within each sample**. A pooled threshold "
                "computed across samples conflates batch coverage differences with "
                "cell quality."
            ),
            mo.hstack([min_genes, min_cells, max_mito], justify="start"),
            mo.hstack([mad_threshold, mito_prefix], justify="start"),
        ]
    )
    return mad_threshold, max_mito, min_cells, min_genes, mito_prefix


@app.cell
def _(mo):
    # --- 7. Quality-control execution gate --------------------------------
    # Expensive and destructive, so it never runs on a stray re-render.
    run_qc = mo.ui.run_button(label="Run quality control", kind="warn")
    mo.md(f"### 7. Run quality control\n\n{run_qc}")
    return (run_qc,)


@app.cell
def _(
    audit,
    config,
    mad_threshold,
    max_mito,
    min_cells,
    min_genes,
    mito_prefix,
    mo,
    project,
    run_qc,
):
    mo.stop(
        not run_qc.value,
        mo.md("_Quality control has not been run. Nothing has been written._"),
    )

    import numpy as _np
    import scanpy as _sc

    _adata = _sc.read_h5ad(project.source_path)

    # Preserve unmodified counts before anything touches X.
    if "counts" not in _adata.layers:
        _adata.layers["counts"] = _adata.X.copy()

    _sample_key = config.design.sample or config.design.donor
    _adata.var["mt"] = _adata.var_names.str.startswith(mito_prefix.value)
    _sc.pp.calculate_qc_metrics(
        _adata, qc_vars=["mt"], inplace=True, percent_top=None, log1p=False
    )

    _n_before = int(_adata.n_obs)
    _keep = _np.ones(_adata.n_obs, dtype=bool)
    _keep &= _adata.obs["n_genes_by_counts"].to_numpy() >= min_genes.value
    _keep &= _adata.obs["pct_counts_mt"].to_numpy() <= max_mito.value

    # Stratified outlier detection: median and MAD are computed within each
    # sample, so a low-depth sample does not move another sample's threshold.
    if _sample_key and _sample_key in _adata.obs:
        for _sample in _adata.obs[_sample_key].unique():
            _mask = (_adata.obs[_sample_key] == _sample).to_numpy()
            _counts = _np.log1p(_adata.obs.loc[_mask, "total_counts"].to_numpy())
            if _counts.size < 3:
                continue
            _median = _np.median(_counts)
            _mad = _np.median(_np.abs(_counts - _median)) or 1e-9
            _outlier = _np.abs(_counts - _median) > mad_threshold.value * _mad
            _indices = _np.where(_mask)[0]
            _keep[_indices[_outlier]] = False

    _removed_by_sample = {}
    if _sample_key and _sample_key in _adata.obs:
        _labels = _adata.obs[_sample_key].astype(str).to_numpy()
        for _sample in sorted(set(_labels)):
            _removed_by_sample[str(_sample)] = int(((_labels == _sample) & ~_keep).sum())

    _filtered = _adata[_keep].copy()
    _n_after = int(_filtered.n_obs)
    _genes_before = int(_filtered.n_vars)
    _sc.pp.filter_genes(_filtered, min_cells=min_cells.value)

    with project.stage(
        "post_qc",
        summary="Sample-stratified quality control",
        parent_sha256=config.source.sha256,
        params={
            "min_genes": min_genes.value,
            "min_cells": min_cells.value,
            "max_pct_mt": max_mito.value,
            "mad_threshold": mad_threshold.value,
            "stratified_by": _sample_key,
            "mito_prefix": mito_prefix.value,
        },
    ) as _stage:
        _out = _stage.output("artifacts/post_qc.h5ad")
        _filtered.write_h5ad(_out)
        _stage.add_exclusion(
            "low gene count, high mitochondrial fraction, or within-sample count outlier",
            axis="obs",
            n_before=_n_before,
            n_removed=_n_before - _n_after,
            n_remaining=_n_after,
            criteria={
                "min_genes": min_genes.value,
                "max_pct_mt": max_mito.value,
                "mad_threshold": mad_threshold.value,
            },
            by_sample=_removed_by_sample,
            stratified_by=_sample_key or "",
            pooling_justification=(
                "" if _sample_key else "no sample column declared; thresholds are global"
            ),
        )
        _stage.add_exclusion(
            f"genes detected in fewer than {min_cells.value} cells",
            axis="var",
            n_before=_genes_before,
            n_removed=_genes_before - int(_filtered.n_vars),
            n_remaining=int(_filtered.n_vars),
            criteria={"min_cells": min_cells.value},
        )
        _stage.set_matrix_facts(
            representation="raw_counts" if audit.raw_counts.location == "X" else "unknown",
            counts_layer="counts",
            raw_counts_available=True,
            n_obs=int(_filtered.n_obs),
            n_vars=int(_filtered.n_vars),
            obs_keys=list(_filtered.obs.columns),
            layers=list(_filtered.layers.keys()),
        )

    post_qc = _stage.descriptor
    qc_frame = _filtered.obs.copy()
    mo.md(
        f"Kept **{_n_after:,}** of **{_n_before:,}** cells and "
        f"**{int(_filtered.n_vars):,}** of **{_genes_before:,}** genes. "
        f"Registered `{post_qc.path}` (`{post_qc.sha256[:12]}`)."
    )
    return post_qc, qc_frame


@app.cell
def _(config, mo, qc_frame):
    # --- 8. Quality-control diagnostics -----------------------------------
    import matplotlib.pyplot as _plt

    _sample_key = config.design.sample or config.design.donor
    _figure, _axes = _plt.subplots(1, 3, figsize=(13, 3.6))
    for _axis, _column, _title in zip(
        _axes,
        ["n_genes_by_counts", "total_counts", "pct_counts_mt"],
        ["genes per cell", "counts per cell", "% mitochondrial"],
    ):
        if _sample_key and _sample_key in qc_frame:
            _groups = [
                qc_frame.loc[qc_frame[_sample_key] == _sample, _column].to_numpy()
                for _sample in sorted(qc_frame[_sample_key].astype(str).unique())
            ]
            _axis.boxplot(_groups, tick_labels=sorted(qc_frame[_sample_key].astype(str).unique()))
            _axis.tick_params(axis="x", rotation=90, labelsize=7)
        else:
            _axis.hist(qc_frame[_column].to_numpy(), bins=50)
        _axis.set_title(_title + " (after QC)")
    _figure.tight_layout()
    mo.vstack([mo.md("### 8. Post-QC diagnostics, by sample"), _figure])
    return


@app.cell
def _(mo, post_qc, project):
    # --- 9. Registered artifacts and lineage ------------------------------
    _rows = ["| stage | path | sha256 | parent | cells | representation |", "| --- | --- | --- | --- | --- | --- |"]
    for _artifact in project.store.artifacts():
        _rows.append(
            f"| `{_artifact.stage}` | `{_artifact.path}` | `{_artifact.sha256[:12]}` | "
            f"`{(_artifact.parent_sha256 or '—')[:12]}` | "
            f"{_artifact.n_obs if _artifact.n_obs is not None else '—'} | "
            f"`{_artifact.representation}` |"
        )
    _lineage = project.artifacts.lineage_of(post_qc.sha256)
    mo.md(
        "### 9. Artifacts\n\n"
        + "\n".join(_rows)
        + "\n\nLineage of the post-QC artifact: "
        + " → ".join(f"`{_item.stage}`" for _item in reversed(_lineage))
    )
    return


@app.cell
def _(mo, project):
    # --- 10. Provenance summary -------------------------------------------
    _manifest_path = project.write_manifest()
    _decisions = project.store.decisions()
    _references = project.store.references()
    _statistics = project.store.statistics()
    _recent = "\n".join(
        f"- `{_decision.kind}` — {_decision.summary}" for _decision in _decisions[-8:]
    )
    mo.md(
        f"""
        ### 10. Provenance

        `{_manifest_path.relative_to(project.root)}` ·
        {len(_decisions)} decisions · {len(_references)} references ·
        {len(_statistics)} statistical comparisons

        {_recent or "_no decisions recorded yet_"}
        """
    )
    return


@app.cell
def _(mo, project):
    # --- 11. Scientific validation ----------------------------------------
    # The same checks `cellimo check` runs, inline, so a problem is visible
    # while the analysis is still open rather than at review time.
    report = project.check()
    _lines = [finding.format_line() for finding in report.findings]
    _body = "\n".join(_lines) if _lines else "no findings"
    mo.md(
        f"""
        ### 11. Scientific validation

        {"**PASSED**" if report.passed else "**FAILED**"} ·
        {len(report.errors)} error(s) · {len(report.warnings)} warning(s) ·
        {report.checks_run} checks

        ```
        {_body}
        ```
        """
    )
    return (report,)


@app.cell
def _(mo, report):
    mo.accordion(
        {
            f"{finding.code} — {finding.title}": mo.md(
                f"**{finding.severity}** · `{finding.location or 'project'}`\n\n"
                f"{finding.detail}\n\n"
                + (f"**Fix:** {finding.remedy}\n\n" if finding.remedy else "")
                + "\n".join(f"- {reference}" for reference in finding.references)
            )
            for finding in report.findings
        }
    ) if report.findings else mo.md("_Nothing to expand — no findings._")
    return


if __name__ == "__main__":
    app.run()
