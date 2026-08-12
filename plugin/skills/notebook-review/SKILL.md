---
name: notebook-review
description: >-
  Review a Cellimo analysis against its recorded provenance and the scientific
  invariants. Use when the user asks to review, audit, sanity-check or critique
  an analysis or notebook, before sharing results, or when preparing a methods
  section from an existing project.
allowed-tools: Bash(cellimo *), Read, Grep, Skill
---

Review the record, not the vibe. Everything below is answerable from files.

## 1. Run the deterministic checks first

```bash
cellimo check --json
```

Do not restate its findings as your own review. It has already found the
structural and mechanical problems; your job is what it cannot see.

## 2. Read the provenance

```
provenance/manifest.json      current state, artifacts, design (step 1 refreshed it)
provenance/artifacts.jsonl    lineage, representations, exclusions
provenance/decisions.jsonl    what was chosen and why
provenance/statistics.jsonl   every comparison and its unit of replication
provenance/references.jsonl   what informed the choices
provenance/environment.json   versions and seed
```

## 3. What the checks cannot see

**Is the objective answerable with this design?** Count donors per condition.
Look for confounding: cross-tabulate condition against batch and against donor.
A condition perfectly confounded with batch cannot be rescued.

**Do the exclusions add up to a defensible dataset?** Read `by_sample` counts.
One sample losing far more than the others is a finding, not a footnote.

**Are the thresholds justified or inherited?** A mitochondrial cut-off of 5%
copied from a PBMC tutorial and applied to kidney tissue is wrong, and `check`
cannot tell.

**Is the annotation honest?** Look for cell types assigned with no supporting
markers recorded, and for the absence of any ambiguous label. A dataset where
every cell got a confident name usually means ambiguity was overwritten.

**Does each claim trace to a statistics record?** Text that says "significantly
upregulated" with nothing in `statistics.jsonl` is unsupported.

**Was the same contrast tested more than once?** Look for repeated tests of one
comparison with different parameters and only the last one reported.

**Are the references real?** Every `reference_id` should resolve through
`get_reference`. A citation that does not resolve is decoration.

## 4. Read the notebook itself

Through marimo-pair, or from disk if no session is running. Look for:

- analysis hidden inside helper functions that return finished objects
- a mutable global `adata` reused across stages, so no stage can be re-run alone
- results computed in a cell whose inputs have since changed
- hard-coded paths outside the project
- silent `try/except` around a scientific step

## 5. Report

Ordered by consequence, not by file position:

1. **Invalidating** — the result does not mean what it says. Pseudoreplication,
   DE on corrected values, confounded comparison, unsupported claim.
2. **Weakening** — the result may hold but cannot be checked. Missing records,
   unjustified thresholds, no effect sizes.
3. **Reproducibility** — someone else could not re-run this. Missing seeds,
   unpinned versions, broken lineage.

For each: what is wrong, where it is recorded (file and record id), and the
smallest change that fixes it. Do not pad the list — three real problems clearly
stated beat fifteen observations.

If the analysis is sound, say that plainly and name the two or three decisions
that carry the most weight, so a reader knows where to concentrate their
scepticism.
