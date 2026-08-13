# Cellimo

**Seamless, grounded single-cell analysis in a live Marimo notebook.**

Tell Codex or Claude Code what you want to learn from a single-cell dataset and
watch the analysis take shape in the notebook you already have open. The agent
attaches to the live kernel, grounds one scientific step at a time in published
workflows, writes and runs the cell, reads the result, and continues from the
state you can see.

Seamless does not mean hidden. The code, plots, tables, decisions, references
and checks stay together in `analysis.py`; the experimental design is settled
before any p-value is computed; and every artifact carries its lineage back to
the source data.

Cellimo is not an agent. It never calls a language model, has no provider
configuration and needs no API key. The agent is Codex or Claude Code; Marimo
owns the notebook and the kernel; Cellimo owns the project, the provenance and
the checks.

---

## Quick start

> **0.1.0 is not published to PyPI.** Install it from a checkout or a built
> wheel; `uv tool install cellimo` is the intended command once it is released.

```bash
git clone https://github.com/mdmanurung/cellimo && cd cellimo
uv tool install .                  # or: uv tool install dist/cellimo-0.1.0-py3-none-any.whl

cellimo install --agents auto
cellimo start data/dataset.h5ad --profile scanpy
```

That leaves you with a live, discoverable Marimo notebook around your dataset.
Under the hood, Cellimo detects Codex and/or Claude Code, registers the plugin
and read-only knowledge server, creates the project, generates `analysis.py`,
and starts the session the agent will use.

Then ask the agent, in its own window:

> Pair with my marimo notebook and take me through quality control.

Continue in the same conversation and notebook:

> Show me which samples drive that threshold, then continue to donor-aware
> differential expression.

You never configure an LLM provider, Ollama, a VS Code extension, a Jupyter
kernel service, a notebook-execution MCP, kernel identifiers, or MCP JSON.

## One continuous analysis loop

For each scientific objective, the agent:

1. inspects the recorded project and the actual live notebook state;
2. retrieves the few published workflow cells that matter;
3. adapts and preflights one visible cell against your data and design;
4. creates and runs it in Marimo, then reads the real output or traceback;
5. records the parameters, decisions, references and resulting artifacts; and
6. continues from that state when you ask the next question.

If a cell fails, the traceback becomes the input to the next grounded fix. If
the session is interrupted, the next request starts by refreshing the manifest
and reading the notebook rather than reconstructing the analysis from chat.
The flow pauses when a scientific choice belongs to you — approving the
experimental design, resolving ambiguous metadata or accepting an exception —
and then resumes in the same place.

## Architecture

```
Codex or Claude Code            ← the only reasoning agent
        |
        v
Cellimo router + focused skills
        |
        +-- marimo-pair                 inspect live state, run scratchpad code,
        |                               create/edit/run durable cells
        |
        +-- cellimo-knowledge (MCP)     ground, search_workflows,
        |                               search_documentation, get_reference,
        |                               index_status  — read-only
        |
        +-- cellimo (Python library)    project config, AnnData audit, design,
                                        artifacts + lineage, provenance,
                                        validation, Marimo UI helpers
```

**Marimo** owns the notebook file, the Python kernel, reactive execution, plots,
tables, controls and durable cells. **marimo-pair** owns everything that talks
to the running kernel. **Cellimo** owns none of that and duplicates none of it.

## Installation

```bash
uv tool install .                  # the tool runtime: CLI, MCP, provenance, checks
```

The tool runtime is deliberately light — Click, pydantic, PyYAML, AnyIO and the
MCP SDK. Scanpy, Torch and friends are *not* in it. The scientific stack belongs
to the **project runtime**, which is whatever environment runs your notebook:

```bash
pip install 'cellimo[scanpy]'      # marimo + anndata + scanpy + the usual
pip install 'cellimo[retrieval]'   # chromadb + sentence-transformers, for the index
pip install 'cellimo[scvi]'        # scvi-tools
pip install 'cellimo[spatial]'     # squidpy
pip install 'cellimo[multimodal]'  # mudata
```

Cellimo respects an existing Conda, Mamba, Pixi, uv or virtualenv environment.
`--profile existing` adds nothing but Marimo and Cellimo itself.

Since the tool and the notebook usually run in *different* interpreters, `init`
records which Python the project uses — an explicit `--python`, else an activated
`VIRTUAL_ENV`, else a `.venv` in the project, else the current interpreter:

```bash
cellimo init data/dataset.h5ad --python ~/envs/analysis/bin/python
```

`doctor`, `check`, `start` and the environment snapshot all query that
interpreter, not Cellimo's own.

## Using it with Claude Code

```bash
cellimo install --agents claude
```

runs, and prints, exactly:

```
claude plugin marketplace add <plugin directory>
claude plugin install cellimo@cellimo
```

Cellimo never edits `~/.claude/settings.json`; Claude Code's own commands manage
its own configuration, so an existing setup cannot be clobbered.

The plugin brings five skills (`cellimo` router, `project-audit`,
`quality-control`, `statistics`, `notebook-review`), a pinned copy of
`marimo-pair`, and the `cellimo-knowledge` MCP server.

## Using it with Codex

```bash
cellimo install --agents codex
```

runs:

```
codex plugin marketplace add <plugin directory>
codex plugin add cellimo@cellimo
```

The same skill tree serves both platforms. `plugin/plugin.toml` is the single
source of truth; `.claude-plugin/` and `.codex-plugin/` metadata is generated
from it, and a test fails if the two ever disagree.

## The live notebook

`cellimo start` runs:

```
marimo edit analysis.py --host 127.0.0.1 --no-token
```

The agent attaches through marimo-pair and works against the kernel state you
see. Scratchpad experiments stay temporary; cells become durable only when the
agent deliberately adds them to `analysis.py`. The session is discoverable
through Marimo's own registry and binds to loopback because anyone who can
reach it can drive the kernel. See [docs/MARIMO.md](docs/MARIMO.md) and
[docs/SAFETY.md](docs/SAFETY.md).

The generated `analysis.py` has eleven sections: project setup, header, dataset
audit, design declaration, analysis plan, QC configuration, QC execution gate,
QC diagnostics, artifacts and lineage, provenance summary, scientific
validation. Expensive stages sit behind `mo.ui.run_button` so nothing runs by
accident, and inferential analysis is blocked until the design is approved.

There is no hidden pipeline or separate agent transcript to translate into a
method later. What the notebook shows is what ran, and it is also where the
next step appears.

## Project structure

```
project/
├── analysis.py            the Marimo notebook — the method section
├── cellimo.yaml           source, design, paths, policies, seed
├── pyproject.toml         the project runtime's dependencies
├── data/
├── artifacts/             stage outputs, immutable once registered
├── results/{figures,tables,models,report}/
└── provenance/
    ├── manifest.json      rolled up from the logs below
    ├── artifacts.jsonl    lineage, representations, exclusions
    ├── decisions.jsonl    what was chosen and why
    ├── references.jsonl   what informed it
    ├── statistics.jsonl   every comparison and its unit of replication
    ├── environment.json   versions and seed
    └── runs/
```

The `.jsonl` files are append-only; `manifest.json` and `cellimo.yaml` are
written atomically and can be rebuilt from them.

## The Python API

Transparent by design — no `run_full_pipeline()`:

```python
from cellimo import Project

project = Project.open()

audit = project.audit_anndata("data/source.h5ad", backed=True)

project.record_design(
    sample="sample_id",
    donor="participant_id",
    condition="condition",
    time="timepoint",
    batch="library_batch",
)
project.approve_design(approved_by="your name")

with project.stage("post_qc", params={"min_genes": 200}) as stage:
    filtered.write_h5ad(stage.output("artifacts/post_qc.h5ad"))
    stage.add_exclusion("low gene count", n_before=n0, n_removed=n0 - n1,
                        n_remaining=n1, by_sample=per_sample,
                        stratified_by="sample_id")
    stage.set_matrix_facts(representation="raw_counts", counts_layer="counts")
```

The scientific transformation stays in the notebook, visible. Cellimo records
what it did.

## Grounding inside the flow

Before a scientific cell appears, the agent uses `cellimo-knowledge`, a
read-only MCP server over an index of published single-cell analysis notebooks
inherited from KAI. The grounding cycle selects relevant source cells, keeps
their citations attached to the adapted code, and preflights the exact proposed
cell before Marimo runs it:

| tool | returns |
| --- | --- |
| `ground(query, packages, modalities, top_k, analysis_mode, exclude_reference_ids, candidate_code)` | cited sections plus design and proposed-code checks before a cell is created; exact exclusions support held-out evaluation |
| `search_workflows(query, packages, modalities, top_k)` | ranked notebooks with stable reference ids |
| `search_documentation(query, packages, top_k)` | ranked API/documentation sections |
| `get_reference(reference_id, section_ids)` | the exact cells, with a content hash |
| `index_status()` | what is installed, and what it cannot answer |

It cannot execute Python, start a kernel, read your data, train anything, edit
the notebook, or write into your project. `ground` deterministically selects
relevant source cells and withholds recognised design errors; the agent decides
how to adapt what remains. (That is the tool contract. The ChromaDB backend
does write to its own internal index files while querying, so its index
directory must stay writable — see [docs/RETRIEVAL.md](docs/RETRIEVAL.md).)

```bash
cellimo index status
cellimo index install      # 345 MB download, asks first
```

The published index contains **workflows only** — no documentation collections —
and has no modality field. `index_status` says so, and `search_documentation`
returns an empty result with an explanation rather than pretending. See
[docs/RETRIEVAL.md](docs/RETRIEVAL.md).

## Safeguards inside the flow

`cellimo check` validates a project structurally and scientifically. Scientific
claims are checked against structured provenance; S009 additionally audits
Marimo cell boundaries and `# cellimo:source` comments so ungrounded code is
visible. It exits non-zero on errors.

It refuses, as errors:

- a confirmatory analysis with no declared experimental unit;
- a confirmatory analysis before the design is approved;
- unmodified counts not identified anywhere in the project;
- **cells registered as the biological replicate** (pseudoreplication);
- a compared group with fewer than two biological replicates;
- **integration-corrected values used as differential-expression input**;
- artifact lineage that does not close on the registered source;
- artifacts that changed after registration, or exclusion counts that do not add up.

(Content verification has a size limit: artifacts above 256 MiB whose size or
modification time changed are reported as a *warning* with the expected hash and
the command to check it yourself, rather than re-read on every `check`. See
[docs/VALIDATION.md](docs/VALIDATION.md).)

It warns about unstratified QC, unjustified integration, missing effect sizes,
missing references, uncited scientific cells and an uncaptured environment.
Every scientific exception has an explicit escape hatch that must be *recorded*
— a stated justification downgrades an error to a warning, so defensible unusual
work is possible and visible.

```bash
cellimo check            # human-readable
cellimo check --json     # structured findings
```

See [docs/VALIDATION.md](docs/VALIDATION.md) for the full rule list.

## Safety, and its limits

Cellimo's own APIs enforce that registered source data cannot be overwritten or
deleted (including through symlinks and hard links), that project-output paths
cannot escape the project root, that artifacts are immutable once registered,
and that every managed write is recorded. Package installation and network
access are never silent.

**Arbitrary Python written by the agent into the notebook is not sandboxed by
any of this.** A cell that calls `os.remove` will remove the file. There is no
container isolation, and Cellimo does not claim any. Make the source read-only
(`chmod a-w`) if that matters to you — `cellimo doctor` checks and tells you.

See [docs/SAFETY.md](docs/SAFETY.md).

## Command reference

| command | what it does |
| --- | --- |
| `cellimo install --agents auto\|codex\|claude\|codex,claude` | register the plugin with each agent's own CLI |
| `cellimo init DATASET --profile scanpy\|existing` | create a project, register the source, generate `analysis.py` |
| `cellimo start [DATASET]` | start Marimo, discoverable by marimo-pair |
| `cellimo doctor [--json]` | agents, Python, Marimo, marimo-pair, index, project |
| `cellimo check [PATH] [--json]` | structural + scientific validation; non-zero on errors |
| `cellimo index status\|install\|update` | manage the retrieval index |
| `cellimo mcp serve` | run the read-only MCP server on stdio |
| `cellimo sessions` | list discoverable Marimo sessions |

Only the `scanpy` and `existing` profiles are implemented in 0.1.0. No other
profile is offered.

## Attribution

Cellimo is derived from **KAI** (<https://github.com/davidfischerlab/kai>,
Apache-2.0), which pioneered agentic single-cell analysis with a retrieval index
over published notebooks. Cellimo's retrieval layer is a port of KAI's, and the
knowledge index it reads is KAI's, published on Zenodo
(DOI [10.5281/zenodo.17660667](https://doi.org/10.5281/zenodo.17660667)).

Cellimo is a different product: a single reasoning agent instead of an internal
LLM stack, Marimo instead of a VS Code extension, and deterministic validation
instead of model-driven orchestration. See [docs/MIGRATION.md](docs/MIGRATION.md)
and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Development

```bash
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e '.[dev]'

.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m pytest tests/ -q -m 'not slow and not needs_retrieval'   # quick pass
.venv/bin/ruff check src tests
.venv/bin/mypy
.venv/bin/python -m build
```

`python -m cellimo.plugin_manifest --write` regenerates the platform manifests
from `plugin/plugin.toml`; `--check` fails if they have drifted, which is what
the test suite runs.

## Documentation

The site is published at **<https://mdmanurung.github.io/cellimo/>**, rebuilt
from `main` by `.github/workflows/docs.yml`. To build it locally:

```bash
pip install -e '.[docs]'
make -C docs html          # -> docs/_build/html/index.html
```

`docs/tutorial.ipynb` is a vignette of the seamless live-analysis experience,
using a real published dataset — Kang et al. 2018, eight lupus donors, control
vs interferon-β. It follows one plain-language objective from audit to
pseudobulk differential expression, keeps every decision and result visible,
and ends with `cellimo check` immediately refusing the same comparison run per
cell.

Because it downloads real data and runs a full analysis, it is executed
**locally** and its outputs are committed:

```bash
pip install -e '.[docs,docs-data]'   # docs-data adds pertpy, scanpy, leidenalg
make -C docs execute                 # re-runs the tutorial in place
```

`docs-data` is heavy (pertpy pulls jax) and is deliberately kept out of `docs`,
so the published build stays light. `.github/workflows/tutorial-refresh.yml`
re-executes the notebook monthly and reports if its output changed.

Note that the tutorial is a Jupyter notebook while your analysis is a Marimo
one. MyST-NB cannot execute a Marimo notebook, so the tutorial drives the same
Python API from an IPython kernel and shows the generated `analysis.py` as
source.

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — who owns what, and why
- [docs/MARIMO.md](docs/MARIMO.md) — the notebook, marimo-pair, sessions
- [docs/PLUGIN.md](docs/PLUGIN.md) — one tree, two platforms
- [docs/RETRIEVAL.md](docs/RETRIEVAL.md) — the index, its schema and its gaps
- [docs/EVALUATING.md](docs/EVALUATING.md) — leakage-blocked function-call benchmark and retrieval diagnostics
- [docs/VALIDATION.md](docs/VALIDATION.md) — every check, with its rationale
- [docs/SAFETY.md](docs/SAFETY.md) — what is guaranteed and what is not
- [docs/MIGRATION.md](docs/MIGRATION.md) — from KAI
- [docs/IMPLEMENTATION_RECORD.md](docs/IMPLEMENTATION_RECORD.md) — decisions taken during the build

## Licence

Apache-2.0. See [LICENSE](LICENSE).
