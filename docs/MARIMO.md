# Marimo, marimo-pair and the notebook

## Why Marimo

A Marimo notebook is a plain Python file with a dataflow graph derived from the
code, not a JSON blob with hidden execution order. Cells re-run when their inputs
change, so a stale result is impossible by construction — which is precisely the
failure mode that makes agent-written Jupyter analyses untrustworthy.

For an agentic workflow that matters more than usual: the agent can add a cell,
run it, and know that everything downstream is consistent, without replaying the
whole notebook.

## Version requirement

**marimo ≥ 0.23.8.** The floor is set by what the vendored marimo-pair skill
actually uses: `ctx.packages.add/remove`, cell `status`/`errors`, and
`cells.find/grep` do not exist before 0.23.8, despite marimo-pair's own docs
citing 0.21.1. `cellimo doctor` checks the installed version and fails on an
older one rather than letting the agent hit an `AttributeError` mid-analysis.

## Starting a session

```bash
cellimo start
```

runs:

```
marimo edit analysis.py --host 127.0.0.1 --no-token
```

`--no-token` is load-bearing. Marimo writes one JSON file per running server
into `${XDG_STATE_HOME:-~/.local/state}/marimo/servers/`, and **only servers
started without a token register themselves**. That registry is how marimo-pair
finds your session. Without it, the agent has nothing to attach to.

The trade-off — an unauthenticated kernel on a local port — is discussed in
[SAFETY.md](SAFETY.md). The bind address defaults to loopback for that reason.

`cellimo sessions` lists what marimo-pair would find, including stale entries
whose process has died (a hard kill leaves the file behind).

## How the agent attaches

The vendored marimo-pair skill provides two bash scripts:

- `discover-servers.sh` reads the registry, prunes dead PIDs, probes
  `GET {base}/health`, and prints the reachable servers as JSON;
- `execute-code.sh --url URL [--file PATH] -c CODE` posts to
  `POST {base}/api/kernel/execute` with a `Marimo-Session-Id` header and streams
  back the result.

Code sent this way runs in a **scratchpad**: a shallow copy of the kernel
globals. New top-level bindings are discarded afterwards. Only cells created
through `marimo._code_mode` persist.

That two-level model is the right one for an agent — it can inspect and
experiment freely without mutating the user's notebook, and has to make a
deliberate move to change anything durable.

## The private API boundary

`marimo._code_mode` is how durable cells are created and edited. Its own module
docstring says:

> Internal, agent-only API. Not part of marimo's public API. No versioning
> guarantees. May change or be removed without notice.

Cellimo therefore treats it as belonging to marimo-pair alone:

- no Cellimo module imports it;
- the generated notebook never imports it;
- `tests/test_purity.py` fails if either changes.

If marimo removes or reshapes it, the blast radius is the vendored skill, which
is pinned and replaceable, rather than Cellimo's library code.

## Never edit a live notebook file

While a session is running, the kernel writes `analysis.py` from its own state.
An external edit is either overwritten or produces a file that disagrees with the
running kernel. Every notebook change goes through marimo-pair.

Cellimo enforces the corresponding rule on its own side: `render_notebook`
refuses to overwrite an existing notebook without `force=True`, and no command
writes an existing `analysis.py`. A test asserts that auditing, recording a
design, writing a manifest and running checks all leave the notebook byte-identical.

## The generated notebook

`analysis.py` is copied verbatim from the bundled template — no string
substitution — so the file that ships is exactly the file that CI validates with
`marimo check`. It discovers its own project with `mo.notebook_dir()`, which is
why no substitution is needed.

Eleven sections:

| # | section | what it does |
| --- | --- | --- |
| 1 | project setup | `Project.open()` from the notebook's own directory |
| 2 | project header | source hash, profile, seed, design status |
| 3 | dataset audit | backed read, sampled matrix, counts location, design candidates |
| 4 | design declaration | dropdowns for sample/donor/condition/time/batch/study and the experimental unit |
| 5 | analysis plan | stage table; says plainly what is blocked |
| 6 | QC configuration | thresholds, MAD, mitochondrial prefix |
| 7 | QC execution gate | `mo.ui.run_button` — nothing expensive runs by accident |
| 8 | QC diagnostics | per-sample distributions after filtering |
| 9 | artifacts | registered artifacts and the lineage chain |
| 10 | provenance | manifest, decisions, references, statistics counts |
| 11 | scientific validation | `project.check()` inline, with expandable findings |

Design approval is a button, and inferential analysis stays blocked until it is
pressed (or an autonomous authorisation is recorded).

There is no hidden pipeline. `tests/test_marimo_template.py` asserts every
section is present, that expensive work is gated, and that no
`run_full_pipeline`-shaped function exists.

## Writing cells that Marimo accepts

Marimo derives the dataflow graph from the AST, and enforces:

- **no cycles** between cells;
- **no public name defined in two cells** — one owning cell per name;
- **no wildcard imports**;
- names prefixed with `_` are cell-private and invisible to other cells.

The last rule is genuinely useful here: keeping intermediate AnnData objects as
`_adata` inside a cell prevents a mutating global from leaking across stages,
which is the pattern the artifact model exists to replace.

Validate any notebook with:

```bash
marimo check analysis.py
marimo check --format json analysis.py
```

`cellimo check` runs it too and reports the result alongside the scientific
findings.
