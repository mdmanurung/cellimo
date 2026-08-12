---
name: cellimo
description: >-
  Router for single-cell analysis in a Cellimo project. Use when the user asks
  to analyse, QC, integrate, annotate, or test scRNA-seq / single-cell data, to
  work on an .h5ad file, to start or continue a Cellimo project, to review a
  Marimo analysis notebook, or when an analysis step failed and needs recovery.
allowed-tools: Bash(cellimo *), Read, Glob, Grep, Skill
---

You are the reasoning agent. Cellimo is deterministic tooling around you: it
owns project structure, provenance, artifact lineage and scientific validation,
and exposes a read-only retrieval server. Marimo owns the notebook and the
kernel. marimo-pair is how you touch the live session.

Never edit `analysis.py` on disk while a session is running — the kernel writes
that file from its own state and will overwrite you. All notebook changes go
through the `marimo-pair` skill.

## 1. Orient before doing anything

```bash
cellimo doctor            # agents, marimo, marimo-pair, index, project
cellimo check --json      # what the current project already records
```

If `doctor` reports no project, ask for the `.h5ad` and run
`cellimo init DATASET.h5ad --profile scanpy`. If it reports no Marimo session,
tell the user to run `cellimo start` — do not start servers behind their back.

Then load the `marimo-pair` skill and attach to the session. Everything below
assumes you can run code in the user's kernel.

## 2. Classify the request

| The user wants | Load |
| --- | --- |
| to start on a new dataset | `project-audit` |
| to continue where they left off | `cellimo check --json` first — it refreshes `provenance/manifest.json`, which is otherwise as old as the last registered artifact — then read the manifest, then the stage's skill |
| quality control, filtering, doublets | `quality-control` |
| differential expression, abundance, any p-value | `statistics` |
| a critique of what has been done | `notebook-review` |
| to understand a result | explain from provenance; do not re-run anything |
| a failed cell fixed | read the traceback in the live session, fix the smallest thing, re-run that cell |

Load exactly one scientific skill. If the request spans several, do the first
and say what you are leaving for later.

## 3. The cycle

Repeat, one objective at a time:

1. **Inspect** — current project state (`cellimo check --json`, *then*
   `provenance/manifest.json`: check regenerates it, and reading it first
   can show you a session that ended before the work you are resuming) and
   live notebook state (marimo-pair).
2. **Define one scientific objective.** Say it out loud in a sentence. If you
   cannot, you are about to write code without knowing why.
3. **Retrieve** — `search_workflows` for how this step is really done, then
   `get_reference` for the exact cells. Do not work from the summary.
4. **Write a bounded notebook section** — one stage, visible code, no hidden
   helper that does five things.
5. **Execute** through marimo-pair and read the actual output.
6. **Record** — parameters, decisions and references, through the Cellimo API in
   the notebook (`project.record_decision(...)`, `project.record_reference(...)`,
   `project.stage(...)`).
7. **Continue, revise, or stop.** Stop and ask when the next step depends on
   something only the user knows.

## 4. Scientific invariants

These are not style preferences. `cellimo check` enforces the checkable ones and
will fail the project.

- **Establish the biological replicate before any inferential test.** The
  experimental unit is a donor or a sample, never a cell. It goes in
  `design.experimental_unit` and the design must be approved.
- **Cells from one donor are not independent.** Confirmatory differential
  expression is pseudobulk per donor, or a mixed model with a donor random
  effect. Cell-level Wilcoxon between conditions is a false-discovery machine
  (Squair et al. 2021; Zimmerman et al. 2021). Every confirmatory record must
  state `unit_level="donor"`/`"sample"` or a replicate-aware `aggregation`;
  "unknown" is refused, and no test name exempts you.
- **Preserve and document raw counts.** Keep them in `layers['counts']` before
  normalising, and record where they are.
- **Stratify QC by sample** and record every exclusion with counts, per sample.
- **Do not integrate just because batches exist.** Show the batch effect first;
  record the diagnostic.
- **Never feed integration-corrected expression to differential expression.**
  Corrected values are for clustering and embedding. Test on counts or
  log-normalised values with batch as a covariate.
- **Separate exploration from confirmation.** Mark exploratory work
  `mode="exploratory"`; only lock a contrast once and then test it.
- **Report effect sizes and uncertainty**, not just p-values.
- **Preserve ambiguous labels.** "unclear" is a finding; forcing every cell into
  a named type is a fabrication.
- **Checkpoint before expensive or destructive stages.**
- **Record parameters, package versions, seeds and references** as you go, not
  at the end.

## 5. Recording, concretely

Inside a notebook cell:

```python
with project.stage("post_qc", summary="Sample-stratified QC",
                   params={"min_genes": 200, "max_pct_mt": 15}) as stage:
    filtered.write_h5ad(stage.output("artifacts/post_qc.h5ad"))
    stage.add_exclusion("low gene count", n_before=n0, n_removed=n0 - n1,
                        n_remaining=n1, by_sample=per_sample,
                        stratified_by="sample_id")
    stage.set_matrix_facts(representation="raw_counts", counts_layer="counts",
                           n_obs=n1, n_vars=g1)
```

Then `cellimo check`. If it reports an error, fix the analysis — not the record.

## 6. What you must not do

- Do not write to the registered source dataset. Cellimo refuses; so should you.
- Do not hide analysis in a helper that returns a finished object. The notebook
  is the method section.
- Do not carry one mutable global `adata` across stages. Each stage reads its
  parent artifact and writes a new one.
- Do not claim a result the provenance does not support.
