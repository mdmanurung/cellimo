# Validation

`cellimo check` answers one question: **could this project support the claims
being made from it?**

Scientific rules are predicates over structured provenance; they do not infer
intent from notebook prose or function names. One structural rule, S009, reads
Marimo cell boundaries and citation comments to measure grounding compliance.
It does not execute the notebook.

```bash
cellimo check              # human-readable
cellimo check --json       # structured findings
cellimo check --only C004,C006
```

Exit status is `1` when any finding is an error, `0` otherwise. The generated
notebook runs the same checks inline in section 11, so problems surface while
the analysis is still open.

## Severities

| severity | meaning |
| --- | --- |
| **error** | the result is not interpretable as stated. Exits non-zero. |
| **warning** | the result may hold, but a reader has to take something on trust. |
| **info** | context. |

## Every check

### Structural

| code | rule |
| --- | --- |
| `S001` | Registered source data is present and unchanged |
| `S002` | Project directories exist |
| `S003` | Registered artifacts exist on disk |
| `S004` | Artifact lineage closes on the registered source |
| `S005` | Exclusion counts reconcile |
| `S006` | Cited references are recorded |
| `S007` | Environment was captured |
| `S008` | Artifact hashes match their files |
| `S009` | Scientific notebook cells carry resolvable grounding citations |

`S004` walks `parent_sha256` backwards from every artifact and fails when the
chain does not terminate at the registered source, contains an unregistered
parent, or contains a cycle. `S005` checks that `n_before - n_removed ==
n_remaining` for every exclusion, and that per-sample counts sum to the total.
`S008` verifies artifact content, cheaply. Registration records the file's size
and its nanosecond modification time; a file whose size and mtime are both
unchanged demonstrably has not been written since, and is not re-read. Only files
that fail that test are hashed, and only up to 256 MiB — above which the finding
becomes a warning telling you how to verify it yourself. This matters because
section 11 of the generated notebook runs `project.check()` reactively: without
the fast path, ordinary notebook interaction would re-hash every checkpoint. The
comparison is exact, not tolerance-based, so a same-size overwrite in the same
second is still caught. It is a correctness aid, not a security control —
whoever can write the file can also set its mtime.

`S009` recognises Marimo `@app.cell` functions, scopes each
`# cellimo:source` header to the cell containing it, and warns when a clearly
scientific cell has none. UI, markdown, import-only, and pure Cellimo
bookkeeping cells are excluded. Existing headers are resolved against the
installed index and their section hashes checked; malformed, missing, unknown,
or drifted citations remain visible as warnings. This measures whether the
mandatory grounding loop happened. It does not prove that an adaptation was a
good scientific choice.

### Scientific

| code | rule |
| --- | --- |
| `C001` | The experimental unit is declared |
| `C002` | Confirmatory analysis followed design approval |
| `C003` | Unmodified counts are identified |
| `C004` | Cells are not treated as biological replicates |
| `C005` | Confirmatory groups have replication |
| `C006` | Differential expression does not use corrected values |
| `C007` | Effect sizes and uncertainty are reported |
| `C008` | Quality control is stratified by sample |
| `C009` | Integration was justified, not reflexive |
| `C010` | Filtering stages record their exclusions |
| `C011` | Confirmatory analysis names its input artifact |
| `C012` | A declared replicate unit was actually aggregated to |
| `C013` | Analyses cite the references that informed them |

## The rules that matter most

### C004 — cells are not biological replicates

Fires when a **confirmatory** statistics record fails to *positively declare* a
unit of replication: `unit_level` is not `sample` or `donor`, **and**
`aggregation` is not `pseudobulk`, `mixed_model` or `meta_analysis`.

Note what it does **not** key on: the test's name. An earlier version matched
substrings like `wilcoxon` against the free-text `test` field, which meant a
confirmatory `kruskal_wallis` — or anything called `my_comparison` — computed per
cell on batch-corrected values passed with exit 0. A rule you escape by renaming
a string is not a rule. The test name now only sharpens the message.

Cells from one donor are not independent observations. Treating them as such
does not estimate between-donor variance, so the p-values are computed against
the wrong null and the false-discovery rate is inflated — often dramatically
(Squair et al. 2021; Zimmerman et al. 2021; Murphy & Skene 2022).

*Not* triggered by exploratory work. `sc.tl.rank_genes_groups` between clusters
is marker discovery, not a condition comparison; record it as
`mode="exploratory"` and this rule does not apply. That exemption is deliberate:
a rule that fires on routine marker discovery would be switched off within a
week.

### C012 — the declared unit is the unit that was computed

C004 accepts a record the moment it names `sample` or `donor` as its
`unit_level`. Naming a unit is not the same as computing at it: a record can
declare `unit_level="donor"` and still record `aggregation="none"`, which is a
cell-level test wearing a donor-level label. C012 warns on exactly that, and on
nothing else.

The two checks **partition** the confirmatory records — C004 owns those that
declare no unit, C012 owns those that declare one without aggregating to it —
so no record is ever reported by both. An earlier C012 keyed on the test name
and was a strict subset of C004, restating every C004 error as a weaker
warning about the same record.

Silenced by a substantive `justification`, on the same terms as C004: a
single character, "n/a" or "see notebook" will not do it.

*Escape hatch*: a non-empty `justification` downgrades it to a warning. Single-
donor perturbation screens, where the well is genuinely the experimental unit,
belong here.

### C006 — confirmatory statistics on corrected values

Fires when a **confirmatory** statistics record has `input_representation` in
`{integrated_expression, integrated_embedding}` — whatever the test is called.

Integration alters the expression values themselves. Use the corrected
representation for clustering and embedding; test on counts or log-normalised
values with batch as a covariate. *Escape hatch*: a recorded `justification`
downgrades it to a warning.

Exploratory work is exempt, deliberately: clusters are found on the integrated
embedding — that is what integration is for — and ranking markers between those
clusters is routine. Firing there would make the rule noise, and a noisy rule
gets switched off.

### C003 — unmodified counts

Fires when neither the audit nor any registered AnnData artifact reports
`raw_counts_available` or a `counts_layer`.

*Escape hatch*: `source.raw_counts_unavailable_upstream = true` with a
`raw_counts_note` explaining why. That downgrades the rule to a warning — nothing
was done wrong locally when a published object arrives already normalised — and
makes the limitation travel with the project.

### C001 / C002 — the design gate

`C001` errors when confirmatory analyses exist with no
`design.experimental_unit`, and warns otherwise. `C002` errors when confirmatory
analyses exist and `design.status != "approved"`.

`C002` also cross-checks the *autonomous* path. A design approved with
`approved_by = "autonomous_authorization"` must be backed by a recorded
`authorization` decision from the user **and** by
`policies.autonomous_authorization`; without both, it is an error, and even with
both it is reported as a warning so a reviewer sees that no human looked at the
specific columns chosen.

**What this cannot do**, stated plainly: nothing stops an agent from calling
`project.authorize_autonomous(...)` itself and then approving its own design. No
library-level check can distinguish that from a user who asked for it — the call
comes from the same process either way. What the check buys you is that the
attempt is *recorded and surfaced* rather than invisible. If that matters to you,
read `provenance/decisions.jsonl` before trusting an approval.

`Project.record_statistics(mode="confirmatory")` refuses outright when the
design is not approved, so the unusable result is normally never produced. The
checks exist for provenance written by other means.

### C008 — stratified quality control

Warns (not errors) when a cell exclusion has no `by_sample` breakdown and no
`stratified_by`, while a sample or donor column is declared. Computing MAD
thresholds on a pooled mixture of samples conflates batch coverage differences
with cell quality (OSCA). *Escape hatch*: `pooling_justification`.

This is a warning because single-sample studies and genuinely uniform processing
make a pooled threshold defensible, and because `sc-best-practices.org`'s own
worked example uses a global threshold.

## What is *not* checked

These invariants are real, but not expressible as predicates over structured
provenance. They live in the skills as guidance, and are listed here so nobody
mistakes a passing `cellimo check` for their satisfaction:

- **preserve ambiguous cell-type labels** — no structured field distinguishes an
  honest "unclear" from a confident label that happens to be wrong;
- **checkpoint before destructive or expensive stages** — lineage records what
  was checkpointed, but "expensive" is not a machine-decidable property;
- **exploration and confirmation properly separated in time** — the `mode` field
  records the claim, but nothing proves a contrast was chosen before the data
  were seen;
- **thresholds appropriate to the tissue** — a 5% mitochondrial cut-off is right
  for PBMCs and wrong for heart.

## Adding a check

```python
from cellimo.validation.engine import Finding, ValidationContext, register

@register("C014", "Short statement of the rule")
def check_something(context: ValidationContext) -> list[Finding]:
    ...
```

A check receives everything already loaded from disk — artifacts, decisions,
references, statistics, environment — and returns findings. It must not read the
notebook source, and it must have an escape hatch that is *recorded* rather than
a flag that silences it.
