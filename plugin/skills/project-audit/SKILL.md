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

## Run the deterministic audit

Section 3 of `analysis.py` already calls Cellimo's own project API:

```python
audit = project.audit_anndata(backed=True)
audit.summary_lines()
```

This is Cellimo bookkeeping rather than a corpus-derived scientific method. It
reads in backed mode, samples the matrix, writes a report under
`provenance/audits/`, and registers it as an artifact. It does not need a source
header. Do not replace it with custom HDF5 inspection.

## Ground every scientific inspection cell

Any additional cell that cross-tabulates, summarises, or plots the dataset is a
scientific code cell. Before creating each one:

1. Call `ground` for one objective with concrete column names and likely native
   APIs. Example query shape: `AnnData experimental design donor sample
   condition batch cross tabulation pandas.crosstab confounding` with
   `analysis_mode="exploratory"`.
2. If `needs_user_decision` is true, stop and show the user the note and relevant
   findings. Do not invent an inspection because no applicable source survived.
3. Read both `api_usage` and `in_practice`, then adapt one bounded cell in
   working memory. Preserve every applicable `# cellimo:source ... section=...
   sha=...` line exactly.
4. Call `ground` again with the same objective and the exact proposed cell as
   `candidate_code`. Require `candidate_reviewed=true` and
   `needs_user_decision=false` before creating it.
5. Escalate any native-function disagreement or unavailable required check to
   the user. The agent cannot waive it.
6. Only then use marimo-pair `create_cell` and `run_cell`; inspect the status
   and output before drawing a design conclusion.

One grounding result applies to one scientific cell. Pure calls to
`record_design`, `approve_design`, or other Cellimo provenance APIs are
bookkeeping; a cell that also analyses data is not exempt.

## Read the audit properly

- `audit.raw_counts` reports availability, location (`X`, `layers/counts`, or
  `raw/X`), and evidence. If unavailable, stop and resolve whether counts exist
  elsewhere or were discarded upstream. In the latter case, record the
  limitation explicitly:

  ```python
  project.config.source = project.config.source.model_copy(update={
      "raw_counts_unavailable_upstream": True,
      "raw_counts_note": "published object; authors discarded counts",
  })
  project.save()
  ```

  Count models are then unavailable; say that plainly.

- `audit.design_candidates` are heuristic proposals based on names and
  cardinalities. Verify their actual values before proposing them.

- `audit.obs_columns` exposes cardinality. A one-level column cannot define a
  comparison; a near-cell-unique column is an identifier, not a design factor.

## Propose the design

Ask rather than assume:

- Which column identifies the donor or participant? That is usually the
  experimental unit.
- Which identifies the sample or library? Several samples may come from one
  donor, so sample is not automatically the unit.
- Which condition is being compared, and is it confounded with batch? Ground
  and run the cross-tabulation before answering. Integration cannot identify a
  contrast that is perfectly confounded.

```python
project.record_design(
    sample="sample_id", donor="participant_id", condition="condition",
    time="timepoint", batch="library_batch",
)
```

This records a proposal. Inferential analysis remains blocked.

## Get user approval

Approval belongs to the user:

```python
project.approve_design(approved_by="<the person's name>")
```

Only explicit unattended authorisation permits:

```python
project.authorize_autonomous("reason the user gave")
project.record_design(..., approve=True, approved_by="autonomous_authorization")
```

Both routes are written to `decisions.jsonl`. Editing an approved design revokes
approval because the comparison changed.

## Report back

State the shape, raw-count location, design columns with level counts, number of
independent units per condition, and any confounding. If a condition has fewer
than two donors, say immediately that no inferential test can support a claim
about it.
