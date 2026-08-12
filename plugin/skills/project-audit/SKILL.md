---
name: project-audit
description: >-
  Audit a single-cell dataset and establish the experimental design before any
  analysis. Use when starting a Cellimo project, when the design is unresolved
  or unapproved, when the user asks what is in an .h5ad, or when cellimo check
  reports C001 (no experimental unit) or C003 (raw counts not identified).
allowed-tools: Bash(cellimo *), Read, Skill
---

Two questions must be settled before anything else: **where are the unmodified
counts**, and **what is the biological replicate**. Everything downstream is
uninterpretable without both.

## Run the audit

In the notebook (section 3 of `analysis.py` does this already):

```python
audit = project.audit_anndata(backed=True)
audit.summary_lines()
```

It reads in backed mode and samples the matrix, so a 40 GB object costs seconds.
It writes a report under `provenance/audits/` and registers it as an artifact.

## Read it properly

- `audit.raw_counts` — `available`, `location` (`X`, `layers/counts`, `raw/X`)
  and the evidence. If `available` is false, stop and resolve it. Either the
  counts are somewhere the audit did not look, or the object is already
  normalised. In the second case set, with a reason:

  ```python
  project.config.source = project.config.source.model_copy(update={
      "raw_counts_unavailable_upstream": True,
      "raw_counts_note": "published object; authors discarded counts",
  })
  project.save()
  ```

  That downgrades the check to a warning and makes the limitation travel with
  the project. Count models (DESeq2, edgeR) are then off the table — say so.

- `audit.design_candidates` — proposals, with evidence and confidence. They are
  name-and-cardinality heuristics, not knowledge. Verify each against the actual
  values before proposing it.

- `audit.obs_columns` — cardinality matters. A column with one level cannot
  define a comparison; a column with roughly one level per cell is an
  identifier, not a design factor.

## Propose the design

Ask, do not assume:

- Which column identifies the **donor / participant**? That is usually the
  experimental unit.
- Which identifies the **sample / library**? Several samples may come from one
  donor — then the donor is the unit, not the sample.
- Which is the **condition** being compared, and is it confounded with batch?
  Cross-tabulate before answering. A condition perfectly confounded with batch
  cannot be separated from it by any amount of integration.

```python
project.record_design(
    sample="sample_id", donor="participant_id", condition="condition",
    time="timepoint", batch="library_batch",
)
```

This records a *proposal*. Inferential analysis stays blocked.

## Get it approved

Approval is the user's, not yours:

```python
project.approve_design(approved_by="<the person's name>")
```

Only if the user has explicitly authorised you to proceed unattended:

```python
project.authorize_autonomous("reason the user gave")
project.record_design(..., approve=True, approved_by="autonomous_authorization")
```

Both paths are written to `decisions.jsonl`. Editing an approved design revokes
approval, because the comparison changed and the sign-off no longer applies.

## Report back

State plainly: the shape, where counts live, the design columns with their level
counts, the number of independent units per condition, and any confounding you
found. If a condition has fewer than two donors, say now that no inferential
test will support a claim about it — before anyone spends a day on it.
