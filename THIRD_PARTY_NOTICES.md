# Third-party notices

Cellimo is licensed under Apache-2.0. It incorporates and depends on the work
below. Where a component is bundled, the exact version is pinned and recorded.

---

## KAI — ported source

- **Project**: KAI, an agentic system for single-cell analysis in Jupyter
  notebooks
- **Source**: <https://github.com/davidfischerlab/kai>
- **Commit read**: `a1a702482fcc64a8856e4a8853e1b84a4ef74e9d` (HEAD, 2026-08-11)
- **Licence**: Apache-2.0, "Copyright 2025 David Fischer"
- **Relationship**: Cellimo is derived from KAI. No KAI source is vendored;
  its retrieval subsystem was **ported** into Cellimo.

Modules whose design and on-disk contract come from KAI:

| Cellimo module | KAI origin |
| --- | --- |
| `cellimo/retrieval/chroma_index.py` | `kai/retrieval/snippets/storage/chromadb_manager.py`, `kai/retrieval/workflow_summaries/{notebook_storage,summary_search}.py` |
| `cellimo/retrieval/ids.py` | KAI's `notebook_id` and chunk-id construction |
| `cellimo/retrieval/install.py` | `scripts/download_retrieval_data.py` |
| `cellimo/retrieval/models.py` | KAI's chunk and summary metadata vocabulary |

What was deliberately **not** carried over: `KaiAgent`, `LLMInterface`,
`LLMPool`, model-provider adapters, `WorkflowOrchestrator`, LLM-based intent
classification, LLM TODO/code generation, LLM error recovery, the VS Code
extension, `VSCodeCommunicator`, and the asynchronous notebook-execution queue.
Cellimo contains no internal language model of any kind.

Two upstream defects were found while porting and are fixed in Cellimo rather
than reproduced: the downloader extracted one directory level too deep (which
made its own verification always fail), and `ChromaDbManager.get_tool_status()`
references a field the `ToolKnowledgeBase` dataclass does not declare.

## KAI retrieval database — downloaded, not redistributed

- **Artifact**: `kai_retrieval_251121.zip` (345,602,911 bytes,
  md5 `f8c9fb9d4f258fb4add0228109cf2d14`)
- **Source**: Zenodo record 17660667,
  DOI [10.5281/zenodo.17660667](https://doi.org/10.5281/zenodo.17660667)
- **Licence**: **GPL-3.0-or-later** — this is *data*, licensed separately from
  KAI's Apache-2.0 source
- **Relationship**: Cellimo does **not** redistribute this index. `cellimo index
  install` downloads it, on request, to the user's own machine. That is why the
  index is an explicit, confirmed action rather than part of installation.

The index also contains a `licenses/` directory of per-repository licence files
for the notebooks it indexes. Upstream states that collection is not guaranteed
to be complete; `get_reference` repeats that caveat on every reference it
returns.

## marimo-pair — vendored, unmodified

- **Project**: marimo-pair, the agent↔Marimo pairing protocol
- **Source**: <https://github.com/marimo-team/marimo-pair>
- **Version**: tag **`v0.0.18`**, commit
  `0c486ee7ee4cd54622e0d062badddab429f435b1`
- **Licence**: Apache-2.0, "Copyright 2026 Marimo Team"
  (`plugin/vendor/marimo-pair/LICENSE`)
- **Vendored to**: `plugin/skills/marimo-pair/` (in an installed wheel:
  `cellimo/_plugin/skills/marimo-pair/`)

Files copied verbatim from upstream `skills/marimo-pair/`:

```
SKILL.md
reference/execution-context.md
reference/finding-marimo.md
reference/gotchas.md
reference/notebook-improvements.md
reference/rich-representations.md
scripts/discover-servers.sh
scripts/execute-code.sh
```

Not copied: `skills/retro-marimo-pair/`, which is a feedback workflow for the
marimo team rather than part of the pairing mechanism.

A SHA-256 of every vendored file is recorded in
`plugin/vendor/marimo-pair.json`. `cellimo doctor` verifies them and reports any
modification, and a test fails if the copy diverges — the claim "unmodified
v0.0.18" is checked, not asserted.

The vendored skill requires **marimo ≥ 0.23.8**: the API surface it documents
(`ctx.packages.add/remove`, cell `status`/`errors`, `cells.find/grep`) does not
exist in earlier releases, despite its own docs citing 0.21.1 as the floor.
`cellimo doctor` checks the installed version against 0.23.8.

## Marimo

- **Project**: Marimo, a reactive Python notebook
- **Source**: <https://github.com/marimo-team/marimo>
- **Licence**: Apache-2.0
- **Relationship**: a runtime dependency of the project runtime. Cellimo does
  not bundle it.

`marimo._code_mode` is a private, explicitly unstable API ("Internal, agent-only
API. Not part of marimo's public API. No versioning guarantees. May change or be
removed without notice."). Only the vendored marimo-pair scripts use it; no
Cellimo module and no generated notebook cell imports it, and a test enforces
that.

## Model Context Protocol Python SDK

- **Package**: `mcp` ≥ 2.0, < 3
- **Source**: <https://github.com/modelcontextprotocol/python-sdk>
- **Licence**: MIT
- **Note**: `mcp` 2.0.0 removed `mcp.server.fastmcp`; the high-level server
  class is now `MCPServer`. Cellimo targets the 2.x API.

## Other runtime dependencies

| Package | Licence |
| --- | --- |
| pydantic | MIT |
| click | BSD-3-Clause |
| PyYAML | MIT |
| platformdirs | MIT |
| packaging | Apache-2.0 / BSD-2-Clause |

Optional extras (`chromadb`, `sentence-transformers`, `anndata`, `scanpy`,
`numpy`, `pandas`, `scipy`, `scikit-learn`, `leidenalg`, `igraph`, `matplotlib`,
`scvi-tools`, `squidpy`, `mudata`) carry their own licences and are installed by
the user into the project runtime.

## Scientific literature

The validation rules cite, and are argued from:

- Squair et al. 2021, *Nat Commun* 12:5692, doi:10.1038/s41467-021-25960-2
- Zimmerman, Espeland & Langefeld 2021, *Nat Commun* 12:738,
  doi:10.1038/s41467-021-21038-1
- Murphy & Skene 2022, *Nat Commun* 13:7851, doi:10.1038/s41467-022-35519-4
- Heumos, Schaar, Lance et al. 2023, *Nat Rev Genet* 24:550–572,
  doi:10.1038/s41576-023-00586-w
- OSCA, "Quality control redux", <https://bioconductor.org/books/>
- Seurat integration vignette, <https://satijalab.org/seurat/>
