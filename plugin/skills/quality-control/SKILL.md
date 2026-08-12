---
name: quality-control
description: >-
  Sample-stratified quality control for single-cell data with every exclusion
  recorded. Use when filtering cells or genes, setting QC thresholds, handling
  mitochondrial fraction or doublets, or when cellimo check reports C008
  (unstratified QC) or C010 (exclusions not recorded).
allowed-tools: Bash(cellimo *), Read, Skill
---

Quality control removes data. Every removal is a scientific claim that those
cells were not measuring what you wanted, so every removal is recorded with its
reason, its counts, and its per-sample breakdown.

## Look before thresholding

Plot the three distributions **per sample**, not pooled:

- genes per cell
- counts per cell
- percentage of mitochondrial counts

Pooling hides the thing you are looking for. A sample sequenced half as deeply
as the others has a legitimately lower count distribution; a global threshold
deletes most of it and keeps the rest, which is a batch effect you created
yourself.

## Set thresholds within samples

```python
for sample in adata.obs[sample_key].unique():
    mask = (adata.obs[sample_key] == sample).to_numpy()
    values = np.log1p(adata.obs.loc[mask, "total_counts"].to_numpy())
    median = np.median(values)
    mad = np.median(np.abs(values - median)) or 1e-9
    outlier = np.abs(values - median) > 5 * mad
```

A fixed floor (`min_genes`, `max_pct_mt`) plus a within-sample MAD rule is a
defensible default. Tissue changes the mitochondrial cut-off: heart and kidney
cells legitimately carry far more mitochondrial RNA than PBMCs. If you deviate
from the default, say why and record it.

If there is only one sample, a global threshold is correct — record
`pooling_justification` so the check knows it was a choice, not an oversight.

## Preserve the counts first

```python
if "counts" not in adata.layers:
    adata.layers["counts"] = adata.X.copy()
```

Do this before anything touches `X`. Normalisation in place with no counts layer
is unrecoverable.

## Record the exclusions

```python
with project.stage("post_qc", summary="Sample-stratified QC",
                   params={"min_genes": 200, "max_pct_mt": 15, "mad": 5}) as stage:
    filtered.write_h5ad(stage.output("artifacts/post_qc.h5ad"))
    stage.add_exclusion(
        "low gene count / high mitochondrial fraction / within-sample outlier",
        axis="obs", n_before=n0, n_removed=n0 - n1, n_remaining=n1,
        by_sample={sample: int(count) for sample, count in removed.items()},
        stratified_by=sample_key,
        criteria={"min_genes": 200, "max_pct_mt": 15, "mad_threshold": 5},
    )
    stage.add_exclusion("genes in fewer than 3 cells", axis="var",
                        n_before=g0, n_removed=g0 - g1, n_remaining=g1)
    stage.set_matrix_facts(representation="raw_counts", counts_layer="counts",
                           n_obs=n1, n_vars=g1)
```

`n_before - n_removed` must equal `n_remaining`; the validator checks it. Use the
numbers the object actually reports, not arithmetic you did in your head.

## Then look at what you removed

Report the per-sample loss. A sample that lost 60% of its cells while the others
lost 5% is not a QC success — it is a failed library, and dropping it entirely
may be the honest call. Say so and let the user decide.

Doublet detection (scDblFinder, scrublet, DoubletFinder) is a separate stage,
run **per sample** because doublet rate scales with loading density. Record the
expected rate and the observed rate.

## Stop conditions

- If QC removes more than half the cells, stop and explain before continuing.
- If a condition group drops below two donors after QC, say immediately that
  inferential comparison of that group is no longer supportable.
