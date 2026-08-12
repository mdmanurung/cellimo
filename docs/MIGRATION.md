# Migration from KAI

Cellimo is derived from **KAI** (<https://github.com/davidfischerlab/kai>,
Apache-2.0, "Copyright 2025 David Fischer"), read at commit
`a1a702482fcc64a8856e4a8853e1b84a4ef74e9d`.

KAI got two things right that are worth inheriting: an index of published
single-cell workflows is the right substrate for agentic analysis, and the hard
part is scientific discipline rather than code generation. Cellimo keeps both,
and changes almost everything else.

## What changed, and why

| KAI | Cellimo | Why |
| --- | --- | --- |
| `KaiAgent` + `LLMInterface` + `LLMPool` | Codex or Claude Code | One reasoning agent, the user's. No second model to configure, pay for, or keep current. |
| Model-provider adapters, Ollama | none | An analysis tool that ships its own LLM stack ages badly and asks for an API key that has nothing to do with the science. |
| `WorkflowOrchestrator` | the router skill | Orchestration written as a prompt is inspectable and editable by the user. |
| LLM intent classification, TODO generation, code generation, error recovery | the agent does all four | These are exactly what a coding agent is already good at. |
| VS Code extension + `VSCodeCommunicator` + custom message protocol | Marimo + marimo-pair | Removes an editor dependency, a bespoke protocol and an execution queue. Marimo's reactivity also removes stale results. |
| Custom async notebook-execution queue | Marimo's kernel | Not our problem to solve. |
| Jupyter notebooks | Marimo notebooks | Plain Python files, a real dataflow graph, no hidden execution order. |
| LLM-selected reference workflows | ranked results, the agent chooses | Retrieval that summarises with a model loses the exact source, which is the only thing worth citing. |
| — | artifact lineage, provenance, `cellimo check` | The part that was missing. |

## What was ported

Only the retrieval subsystem, and only its deterministic parts:

| Cellimo | KAI origin |
| --- | --- |
| `cellimo/retrieval/chroma_index.py` | `chromadb_manager.py`, `notebook_storage.py`, `summary_search.py` |
| `cellimo/retrieval/ids.py` | KAI's `notebook_id` and chunk-id construction |
| `cellimo/retrieval/install.py` | `scripts/download_retrieval_data.py` |
| `cellimo/retrieval/models.py` | KAI's chunk and summary metadata vocabulary |

The ported code imports nothing from `kai.core`, no LLM layer, no orchestrator
and no VS Code class — there is nothing to import, because none of it exists in
this repository.

Two upstream defects are fixed rather than reproduced:

1. `download_retrieval_data.py` extracted the archive without stripping its
   leading `retrieval/` component, so files landed one level below where the
   readers look. Its own verification step therefore failed on every successful
   download, and `check_existing_data` never found an installed index, so it
   re-downloaded 345 MB each run. Cellimo strips the prefix.
2. `ChromaDbManager.get_tool_status()` reads `tool_kb.last_used`, a field the
   `ToolKnowledgeBase` dataclass does not declare; calling it raises
   `AttributeError`. Cellimo computes status from the collection registry and
   the collection list instead.

## Why there is no `legacy/` directory

The brief asked for the old implementation to be preserved in a marked legacy
area. This repository had **no KAI history** — it was empty at first commit — so
there was nothing to `git mv`. Copying 23,000 lines of superseded LLM and VS
Code orchestration into a new project would have added dead weight and a second
importable package to shadow the first, which the same brief warns against.

KAI remains available, unmodified, at its own repository. `docs/IMPLEMENTATION_RECORD.md`
records this decision and its rationale.

## Concept mapping

| KAI concept | Cellimo equivalent |
| --- | --- |
| `~/.kai_agent/retrieval/` | `~/.local/share/cellimo/index/` (or `$CELLIMO_INDEX_DIR`) |
| `ChromaDbManager.search()` | `search_workflows` / `search_documentation` (structured hits, not a joined string) |
| `NotebookSelector` (LLM-driven) | `get_reference` — the agent selects |
| `WorkflowSummaryRag` | the summary-index path inside `ChromaKnowledgeIndex` |
| `notebook_id` | `notebook:<notebook_id>` reference id |
| chunk id | `chunk:<collection>:<chroma_id>` reference id |
| `kai/config/settings.py` (mkdir on import) | `cellimo.yaml` per project; no import-time side effects |
| VS Code chat panel | the agent's own interface |
| the agent's internal TODO list | `provenance/decisions.jsonl` |

## The index

Unchanged. Cellimo reads KAI's published index directly:

- Zenodo record 17660667, DOI 10.5281/zenodo.17660667
- `kai_retrieval_251121.zip`, md5 `f8c9fb9d4f258fb4add0228109cf2d14`
- GPL-3.0-or-later (data), distinct from KAI's Apache-2.0 code

`cellimo index install` downloads it on request. Cellimo does not redistribute
it. Its real contents and its gaps are documented in [RETRIEVAL.md](RETRIEVAL.md).

## If you used KAI

There is no automated migration path, because there is no shared project format
— KAI did not have one. To move an analysis:

1. `cellimo init your-dataset.h5ad --profile scanpy`
2. Copy the analysis into `analysis.py` through marimo-pair, one stage at a time.
3. Register each stage output with `project.stage(...)` as you go, so lineage is
   built rather than reconstructed.
4. Record the design and approve it before re-running any statistical test.
5. `cellimo check`.

Expect the check to fail the first time on a real analysis. That is the point.

## Attribution

Please cite KAI when you use the retrieval index. Cellimo's `THIRD_PARTY_NOTICES.md`
records the exact provenance of every inherited component.
