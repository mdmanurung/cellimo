# Cellimo

**Agentic, reproducible single-cell analysis in Marimo.**

Cellimo gives a coding agent — Codex or Claude Code — a project structure,
an artifact lineage, an append-only provenance log and a scientific validator,
so that an analysis it produces can be audited by someone who was not there
when it ran.

It never calls a language model itself. There is exactly one reasoning agent in
the system, and it is the one you are already using.

:::{card} Start here
:link: tutorial
:link-type: doc

**Tutorial: a project that can defend itself** — build a project end to end,
then watch the validator catch a pseudoreplicated comparison. Every cell on
that page ran when this site was built.
:::

## What it actually does

```{list-table}
:header-rows: 1
:widths: 30 70

* -
  -
* - **Records the design**
  - Which column is the donor, which is the sample, which is the condition —
    declared once, approved by a human, and checked against every confirmatory
    analysis that follows.
* - **Hashes every artifact**
  - Content-addressed, with a parent, so lineage is a SHA-256 chain that closes
    on an immutable source dataset.
* - **Logs decisions append-only**
  - JSONL that is never rewritten. A crash costs at most the record being
    written.
* - **Validates the science**
  - 21 checks. Pseudoreplication, differential expression on batch-corrected
    values, unstratified quality control, unidentified raw counts — each keyed
    on the structure of the record, never on what you named your test.
* - **Serves read-only knowledge**
  - An MCP server the agent can search for real analysis workflows. It cannot
    execute Python, start a kernel, or edit your notebook.
```

## The shape of it

Cellimo duplicates none of Marimo's responsibilities. Marimo owns the notebook
and the kernel; marimo-pair owns the connection to it; Cellimo owns the project,
the provenance and the validation — and nothing else.

```{mermaid}
flowchart LR
    A["Codex / Claude Code<br/>(the only reasoning agent)"] -->|writes cells| M[Marimo notebook]
    A -->|searches| K[cellimo-knowledge<br/>MCP · read-only]
    M -->|records| C[Cellimo]
    C --> P[(provenance/<br/>append-only)]
    C --> V{cellimo check}
    V -->|exit 1| A
```

## Install

```bash
pip install cellimo                 # tool runtime: CLI, provenance, validation, MCP
pip install 'cellimo[scanpy]'       # and the scientific project runtime
cellimo install --agents auto       # register the plugin with Codex / Claude Code
cellimo init data/dataset.h5ad
cellimo start
```

The tool runtime is deliberately light — it must install in seconds and never
drag in Scanpy, Torch or CUDA wheels. The scientific stack belongs to the
*project* runtime, which is a different environment on purpose.
See [](ARCHITECTURE.md#two-runtimes).

## Documentation

```{toctree}
:caption: Getting started
:maxdepth: 2

tutorial
MIGRATION
```

```{toctree}
:caption: How it works
:maxdepth: 2

ARCHITECTURE
VALIDATION
SAFETY
MARIMO
PLUGIN
RETRIEVAL
```

```{toctree}
:caption: Reference
:maxdepth: 2

api
IMPLEMENTATION_RECORD
```

## Status

Version {sub-ref}`version`. Alpha: the `scanpy` and `existing` environment
profiles are implemented and tested; no other profile is claimed to work. See
the [changelog](https://github.com/mdmanurung/cellimo/blob/main/CHANGELOG.md).
