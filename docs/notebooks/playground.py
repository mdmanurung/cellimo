import marimo

app = marimo.App()


@app.cell(hide_code=True)
def _():
    import marimo as mo
    import numpy as np
    return mo, np


@app.cell(hide_code=True)
def _(np):
    # A stand-in for one sample's cells: most are fine, some are debris.
    rng = np.random.default_rng(20260812)
    genes_per_cell = np.concatenate([
        rng.normal(900, 250, 850).clip(20, None),
        rng.normal(60, 30, 150).clip(5, None),
    ]).astype(int)
    return (genes_per_cell,)


@app.cell(hide_code=True)
def _(mo):
    min_genes = mo.ui.slider(
        10, 400, value=200, step=10, label="min genes per cell", show_value=True
    )
    min_genes
    return (min_genes,)


@app.cell(hide_code=True)
def _(genes_per_cell, min_genes, mo):
    kept = int((genes_per_cell >= min_genes.value).sum())
    total = len(genes_per_cell)
    mo.md(
        f"""
        Threshold **{min_genes.value}** keeps **{kept} / {total}** cells
        ({100 * kept / total:.1f}%), excluding **{total - kept}**.

        Cellimo records that count *per sample*. A threshold chosen on pooled
        cells from every donor can look reasonable while quietly removing most
        of one donor — which is what check `C008` exists to catch.
        """
    )
    return
