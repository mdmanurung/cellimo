# Cellimo

**Seamless, grounded single-cell analysis in a live Marimo notebook.**

Ask Codex or Claude Code a scientific question and watch the answer take shape
where you work: one grounded cell at a time, executed against the live kernel,
with the plot or table immediately available for the next question. Cellimo
connects the notebook, published workflows, project state, provenance and
scientific checks so you do not have to carry context between separate tools.

The analysis remains inspectable all the way through. Code, output, decisions,
references and artifact lineage accumulate together in `analysis.py`, and the
flow pauses when a scientific choice needs the analyst rather than guessing.

It never calls a language model itself. There is exactly one reasoning agent in
the system, and it is the one you are already using.

:::{card} Start here
:link: tutorial
:link-type: doc

**Vignette: seamless analysis, visible from question to result** — a real
published experiment (Kang et al. 2018: eight donors, control vs interferon-β)
taken end to end in one continuous thread, ending with the validator refusing
the same comparison run per cell.
:::

## One continuous analysis loop

```{mermaid}
flowchart LR
    U["Your next scientific objective"] --> A["Agent reads project<br/>and live notebook state"]
    A --> G["Ground and preflight<br/>one visible cell"]
    G --> M["Marimo creates,<br/>runs and displays it"]
    M --> U
    M --> C["Cellimo records lineage,<br/>decisions and checks"]
    C --> A
```

You can start with “take me through quality control”, inspect the per-sample
distributions that appear, ask why a threshold was chosen, and continue to the
next stage without exporting a result or restating the dataset. A failed cell
feeds its real traceback into the next grounded fix. A resumed session begins
from the manifest and notebook state on disk, not from a model's memory of the
conversation.

## What keeps the flow trustworthy

```{list-table}
:header-rows: 1
:widths: 30 70

* -
  -
* - **Records the design**
  - Which column is the donor, which is the sample, which is the condition —
    declared once, approved by a human, and checked against every confirmatory
    analysis that follows.
* - **Grounds each scientific cell**
  - Published workflow sections are selected before code is written; the exact
    adapted cell is preflighted and keeps its source header in the notebook.
* - **Hashes every artifact**
  - Content-addressed, with a parent, so lineage is a SHA-256 chain that closes
    on an immutable source dataset.
* - **Logs decisions append-only**
  - JSONL that is never rewritten. A crash costs at most the record being
    written.
* - **Validates the science**
  - 22 checks. Scientific rules key on structured records, never on what a test
    was named; one structural rule also makes uncited Marimo analysis cells
    visible.
* - **Serves read-only knowledge**
  - An MCP server the agent can search for real analysis workflows. It cannot
    execute Python, start a kernel, or edit your notebook.
```

## Clear ownership, one experience

Cellimo duplicates none of Marimo's responsibilities. Marimo owns the notebook
and the kernel; marimo-pair owns the connection to it; Cellimo owns the project,
the provenance, grounding and validation — and nothing else. Those boundaries
let the user experience remain continuous without hiding where code ran or who
made a decision.

```{mermaid}
flowchart LR
    A["Codex / Claude Code<br/>(the only reasoning agent)"] -->|creates and runs cells| M[Marimo notebook]
    A -->|grounds proposed cells| K[cellimo-knowledge<br/>MCP · read-only]
    M -->|records as it goes| C[Cellimo]
    C --> P[(provenance/<br/>append-only)]
    C --> V{cellimo check}
    V -->|finding to resolve| A
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
playground
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
EVALUATING
ROADMAP
IMPLEMENTATION_RECORD
```

## Status

Version {sub-ref}`version`. Alpha: the `scanpy` and `existing` environment
profiles are implemented and tested; no other profile is claimed to work. See
the [changelog](https://github.com/mdmanurung/cellimo/blob/main/CHANGELOG.md).
