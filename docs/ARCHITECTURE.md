# Architecture

Cellimo exists because three capable systems already exist and none of them
should be rebuilt: a reasoning agent, a reactive notebook, and an index of
published analyses. Cellimo connects them into one continuous analysis
experience and supplies the discipline that makes the result resumable,
inspectable and defensible.

## Who owns what

```
Codex or Claude Code                      the only reasoning agent
        │
        ▼
Cellimo router + focused skills           what to do next, and what never to do
        │
        ├── marimo-pair ─────────────►    the live Marimo kernel
        │                                 (inspect state, run scratchpad code,
        │                                  create/edit/run durable cells)
        │
        ├── cellimo-knowledge (MCP) ──►   the retrieval index (read-only)
        │
        └── cellimo (Python library) ─►   the project on disk
                                          (config, audit, artifacts, lineage,
                                           provenance, validation)
```

| System | Owns |
| --- | --- |
| **Codex / Claude Code** | reasoning, planning, writing code, judging output |
| **Marimo** | the notebook file, the kernel, reactive execution, plots, tables, UI controls, durable cells |
| **marimo-pair** | all communication with the running kernel |
| **Cellimo** | project structure, the immutable source, artifacts and lineage, provenance, scientific validation, read-only retrieval |

Cellimo duplicates none of Marimo's or marimo-pair's responsibilities. It has no
kernel, no execution queue, no notebook editor and no message protocol.

From the user's perspective those boundaries form one loop: ask for an
objective, inspect the cell and result that appear in Marimo, make any necessary
scientific decision, and continue. Underneath, the agent reads live state,
grounds and preflights the cell, marimo-pair runs it, and Cellimo records and
validates the outcome. The experience is seamless because state passes through
the loop; the implementation stays explicit so no hidden pipeline has to be
trusted.

## One agent, no exceptions

There is exactly one model in the loop, and it is the user's. Cellimo has:

- no LLM client, provider adapter or model pool;
- no API key, no `OPENAI_API_KEY`, no Ollama;
- no LLM-based intent classification, code generation or error recovery.

This is enforced, not asserted: `tests/test_purity.py` fails if any module
imports `openai`, `anthropic`, `ollama`, `litellm`, `langchain` or
`llama_index`, or references an LLM API-key variable.

The consequence for retrieval is structural. The pipeline is:

```
query → cited sections → agent adapts in memory → candidate preflight → notebook cell
```

There is no model ranking, filtering or summarising in between. `ground`
composes `search_workflows` and `get_reference`, selects sections by literal
evidence, and applies explicit design rules. A second call checks the exact
proposed cell for native-function reinvention before the agent creates it.

## Two runtimes

**The tool runtime** is what `uv tool install cellimo` puts on your PATH: the
CLI, the MCP server, retrieval, provenance and validation. Its dependencies are
Click, pydantic, PyYAML, platformdirs, packaging, AnyIO and the MCP SDK. It
installs in seconds and pulls no CUDA wheels.

**The project runtime** is whatever environment runs the notebook: Marimo,
AnnData, Scanpy, and whatever else the analysis needs. It is described by the
project's own `pyproject.toml`.

Because these are usually *different interpreters*, the project runtime's Python
is recorded in `cellimo.yaml` at `init` and used from then on:

```yaml
environment:
  profile: scanpy
  python: "3.11"
  interpreter: /path/to/analysis/.venv/bin/python
  manager: uv
```

Detection order: an explicit `cellimo init --python …`, then an activated
`VIRTUAL_ENV`, then a `.venv` inside the project, then the current interpreter
(which is the right answer when Cellimo was pip-installed into the analysis
environment itself).

Everything that asks a question about the scientific stack asks *that*
interpreter: `doctor`'s Marimo and package checks, `marimo check` on the
notebook, `cellimo start`, and `provenance/environment.json`. Capturing the
environment in-process instead would record the tool's own dependencies —
pydantic, click, mcp — and none of the packages that produced the results, which
is worse than recording nothing because it looks complete.

The path is deliberately **not** resolved through symlinks. A virtualenv is
identified by the path you invoke; following `bin/python` lands on the base
interpreter, which has none of the project's packages.

The separation is testable, and is tested: importing `cellimo`, `cellimo.cli`,
`cellimo.mcp`, `cellimo.validation`, `cellimo.diagnostics` and
`cellimo.retrieval` in a subprocess must leave `sys.modules` free of `scanpy`,
`anndata`, `torch`, `squidpy`, `chromadb`, `sentence_transformers` and
`matplotlib`. Optional imports happen inside the functions that need them.

## Artifacts, not one mutating object

The default single-cell pattern — one `adata` mutated in place from cell to cell
— makes a notebook unreplayable: no cell can be re-run alone, and nothing records
what the matrix contained at any point.

Cellimo uses explicit stages instead:

```
source → audit → post_qc → normalized → integrated → annotated → statistics
```

Each stage reads its parent artifact and writes a new one. An artifact is
immutable once registered and is described by a frozen descriptor: stage, path,
SHA-256, parent SHA-256, parameters, and what the matrix actually contains
(`representation`, `counts_layer`, `raw_counts_available`, shapes, exclusions).

Lineage is a graph over SHA-256 values. `cellimo check` fails when a chain does
not terminate at the registered source.

Inside a cell, keep AnnData objects private (`_adata`, or a local in a function).
Marimo makes underscore-prefixed names cell-private, so a mutating global cannot
leak across stages by accident.

## Provenance

`cellimo.yaml` is the declared state *now*: source, design, paths, policies,
seed. `provenance/` is the append-only history:

| file | contents |
| --- | --- |
| `artifacts.jsonl` | one immutable descriptor per registered file |
| `decisions.jsonl` | one record per analytical choice, with rationale and references |
| `references.jsonl` | one record per consulted reference, with content hash |
| `statistics.jsonl` | one record per comparison, with its unit of replication |
| `environment.json` | interpreter, platform, package versions, seed |
| `manifest.json` | rolled up from the above; rebuilt by `cellimo check` and by `init`, `record_design` and `register_artifact` |
| `runs/` | one file per Cellimo command invocation |

Record identifiers are content-derived, so writing the same record twice yields
the same id rather than a duplicate. `manifest.json` and `cellimo.yaml` are
written atomically; the `.jsonl` files are append-only and a torn trailing line
is detected and skipped on read.

## The frozen vocabulary

Four things read the same field names: the provenance writer, `cellimo check`,
the MCP payloads and the generated notebook. If they drifted, the validator
would silently pass on nothing. So the vocabulary lives in exactly one module,
`cellimo/schema.py`, with a `SCHEMA_VERSION`; a project written by a different
version is rejected with a message rather than misread.

## The design gate

`design.status` moves `unresolved → proposed → approved`. The agent may propose;
only a human, or a recorded autonomous authorisation, approves. Until then
`Project.record_statistics(mode="confirmatory")` refuses, and the notebook says
so in its header.

Editing an approved design revokes approval: the comparison changed, so the
sign-off no longer applies.

## What Cellimo will not do

- Run a scientific pipeline for you. There is no `run_full_pipeline()`, by
  design — the notebook is the method section.
- Edit a notebook file while a Marimo session is running. The kernel writes that
  file from its own state; anything else is overwritten.
- Execute code through the MCP server. Retrieval reads; marimo-pair runs.
- Claim isolation it does not implement. See [SAFETY.md](SAFETY.md).
