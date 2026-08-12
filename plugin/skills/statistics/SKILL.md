---
name: statistics
description: >-
  Donor-aware statistical testing for single-cell data — differential
  expression, differential abundance, effect sizes. Use when the user asks for
  DE, marker significance, condition comparisons, p-values, or when cellimo
  check reports C004 (cells as replicates), C005 (too few replicates), C006 (DE
  on integrated values) or C012 (a declared unit that was never aggregated to).
allowed-tools: Bash(cellimo *), Read, Skill
---

The number of independent units is the number of donors, not the number of
cells. Everything in this skill follows from that.

## Before anything

The design must be approved and `design.experimental_unit` set. Cellimo refuses
to record a confirmatory analysis otherwise, and it is right to: a p-value
without a declared unit of replication is uninterpretable.

Count the units per group first and say the numbers out loud:

```python
adata.obs.groupby(["condition"])["participant_id"].nunique()
```

Two donors per group is the floor for estimating between-donor variance, and it
is a weak floor. One donor per group means no inferential claim is available at
all — report descriptively and say why.

## Differential expression

Default: **pseudobulk per donor per cell type**, then a count model.

```python
pseudobulk = dc.get_pseudobulk(adata, sample_col="participant_id",
                               groups_col="cell_type", layer="counts",
                               mode="sum", min_cells=10, min_counts=1000)
```

Then DESeq2 / edgeR / limma-voom on the aggregated counts, with batch as a
covariate if batches exist. Aggregate **sums of raw counts**, not means of
normalised values.

The alternative is a mixed model with a donor random effect. Both estimate
between-donor variance. Cell-level Wilcoxon between conditions does not, which
is why it reports thousands of "significant" genes that do not replicate
(Squair et al. 2021, doi:10.1038/s41467-021-25960-2; Zimmerman et al. 2021,
doi:10.1038/s41467-021-21038-1).

`sc.tl.rank_genes_groups` between *clusters* is fine — that is marker discovery,
not a condition comparison, and it is exploratory. Record it as
`mode="exploratory"` and the checks will leave it alone.

## Never test on corrected values

Integration alters the expression values themselves. Use the corrected
representation for clustering and embedding; test on counts or log-normalised
expression with batch as a covariate. If you genuinely must, record a
`justification` — the check downgrades to a warning and a reader can judge it.

## Record what you did

```python
project.record_statistics(
    name="stim vs ctrl in CD4 T",
    test="pseudobulk_deseq2",
    mode="confirmatory",
    unit_level="donor",
    n_units={"stim": 5, "ctrl": 4},          # donors, not cells
    n_cells={"stim": 12043, "ctrl": 9982},
    groups=["stim", "ctrl"],
    input_artifact_sha256=post_qc.sha256,
    input_representation="pseudobulk_counts",
    aggregation="pseudobulk",
    covariates=["library_batch"],
    effect_size={"reported": True, "measure": "log2FC", "column": "log2FoldChange"},
    uncertainty={"reported": True, "measure": "adjusted p-value", "column": "padj"},
    seed=project.config.random_seed,
)
```

Three fields decide whether `cellimo check` accepts a confirmatory result, and
none of them is the test's name:

- **`unit_level`** must be `sample` or `donor`, **or** **`aggregation`** must be
  `pseudobulk` / `mixed_model` / `meta_analysis`. Leaving `unit_level` at its
  default (`unknown`) with `aggregation="none"` is an error — the record has to
  *positively state* what the biological replicate was. Calling your test
  something the validator does not recognise will not get you past this.
- **`aggregation`** must say how you reached that unit. Declaring
  `unit_level="donor"` while leaving `aggregation="none"` describes a
  cell-level test wearing a donor-level label: it satisfies C004 and then
  fails C012, which exists for exactly that record. Set it to `pseudobulk`,
  `mixed_model` or `meta_analysis` — whichever you actually did.
- **`n_units`** is donors per group, not cells. Putting cell counts there is the
  single most consequential thing you can get wrong in this whole system; cell
  counts go in `n_cells`.
- **`input_representation`** must not be a corrected representation for a
  confirmatory result, whatever the test is called.

## Differential abundance

Cell-type proportions are compositional: one population going up forces the
others down. Use a compositional method (scCODA, crumblr) or a count model on
per-donor counts with a total-count offset. A chi-squared test on pooled cells
answers a question nobody asked.

## Report

Effect size, its uncertainty, the number of units, and the test — in that order.
A gene list ranked by p-value with no fold changes is not a result. State the
multiple-testing correction and what was corrected over.
