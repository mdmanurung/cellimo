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

## Ground every scientific code cell

Before creating each cell that aggregates, models, tests, summarises, or plots
scientific data:

1. Call `ground` with the exact contrast, experimental unit, input
   representation, intended package, and native API names. For condition DE, a
   useful query shape is `pseudobulk raw counts per donor per cell type
   decoupler.get_pseudobulk DESeq2 condition differential expression`. Use
   `analysis_mode="confirmatory"`; use `"exploratory"` only when the objective
   genuinely is exploratory marker discovery.
2. If `needs_user_decision` is true, stop and show the note and relevant
   findings. A rejected cell-level test, corrected-value test, or absent
   precedent is not permission to devise a nearby method.
3. Compare both `api_usage` and `in_practice`. Use tutorials to settle the
   installed API and paper companions to judge realistic modelling choices.
   Adapt one bounded cell in working memory, retaining every applicable
   `# cellimo:source ... section=... sha=...` line exactly.
4. Call `ground` again with the same query and the exact proposed cell as
   `candidate_code`. Do not create it unless `candidate_reviewed=true` and
   `needs_user_decision=false`.
5. If the preflight reports a native alternative or cannot settle the check,
   ask the user. Do not argue past the gate.
6. Only then use marimo-pair `create_cell` and `run_cell`, and inspect the
   actual status and output. Ground model fitting, result summarisation, and
   plotting as separate cells when they are separate scientific objectives.

One grounding result applies to one cell. Cellimo provenance-only calls are
bookkeeping; a cell that also computes a scientific result is not exempt.

## Settle the design first

The design must be approved and `design.experimental_unit` set before any
confirmatory test. Count the units per group and report them before fitting.
Two donors per group is the floor for estimating between-donor variance, and a
weak floor. One donor per group supports description, not inference.

## Differential expression

Default to pseudobulk per donor per cell type, then a count model. Use a
grounded field-standard aggregation function such as
`decoupler.get_pseudobulk` when it fits; do not rebuild aggregation with loops,
`np.vstack`, or ad hoc group arithmetic. Aggregate sums of raw counts, not means
of normalised values.

Fit DESeq2, edgeR, limma-voom, or another design-appropriate count workflow,
including batch as a covariate when the design supports it. A mixed model with
a donor random effect is the main alternative. Both estimate between-donor
variance; a cell-level Wilcoxon comparison between conditions does not.

`sc.tl.rank_genes_groups` between clusters can be acceptable for exploratory
marker discovery because it is not a condition-level inferential claim. Ground
that cell with `analysis_mode="exploratory"` and record it as exploratory.

## Never test corrected values

Use integrated or batch-corrected representations for neighbourhood structure,
clustering, and embeddings. Test counts or log-normalised expression with batch
represented in the model. A `C006` rejection is escalated to the user; it is
not silently downgraded by changing a label.

## Record what ran

After the grounded test cell succeeds, record the observed unit and cell counts:

```python
project.record_statistics(
    name="stim vs ctrl in CD4 T",
    test="pseudobulk_deseq2",
    mode="confirmatory",
    unit_level="donor",
    n_units={"stim": 5, "ctrl": 4},
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

The decisive fields describe the method rather than naming it:

- `unit_level` is `sample` or `donor`, never a cell count.
- `aggregation` states how the analysis reached that unit: `pseudobulk`,
  `mixed_model`, or `meta_analysis` as actually used.
- `n_units` holds donors or samples per group; `n_cells` holds cells.
- `input_representation` must truthfully name the uncorrected test input.

## Differential abundance

Cell-type proportions are compositional: one population increasing forces the
others down. Ground a compositional method such as crumblr or scCODA, or a
replicate-aware count model with the appropriate offset. A chi-squared test on
pooled cells answers the wrong question.

## Report

Report effect size, uncertainty, number of independent units, and test, in that
order. State the multiple-testing correction and its family. A gene list ranked
only by p-value is not a result.
