# Implementation record — Cellimo 0.1.0

What was actually built, what was actually verified, and where the brief and
reality diverged. Written during implementation, not after it.

Date: 2026-08-11. Repository: `github.com/mdmanurung/cellimo` (empty at start —
zero commits, no files).

---

## 1. Naming

The prompt pack's placeholders were resolved to match this repository rather
than the pack's suggested default (`Cellwright`):

| Placeholder | Value |
| --- | --- |
| product name | Cellimo |
| Python distribution | `cellimo` |
| import package | `cellimo` |
| CLI executable | `cellimo` |
| plugin ID | `cellimo` |
| retrieval MCP server | `cellimo-knowledge` |
| project config | `cellimo.yaml` |

## 2. Relationship to KAI

KAI (`github.com/davidfischerlab/kai`, Apache-2.0, HEAD `a1a7024` at the time of
writing) was **not vendored**. The repository had no KAI history to `git mv`, so
copying ~23k LOC of superseded LLM and VS Code orchestration into a new project
would have added dead weight and two importable packages to shadow each other —
the exact outcome §3 of the brief warns against.

Instead: KAI was cloned to a scratch directory, read, and its **retrieval
subsystem** was ported into `src/cellimo/retrieval/` with the Apache-2.0 notice
preserved in file headers and in `THIRD_PARTY_NOTICES.md`. Everything else in
KAI — `KaiAgent`, `LLMInterface`, `LLMPool`, `WorkflowOrchestrator`,
`VSCodeCommunicator`, the VS Code extension, the async notebook-execution queue
— is **absent by construction**, not disabled behind a flag.

There is therefore no `legacy/` directory. `docs/MIGRATION.md` records the
mapping from KAI concepts to Cellimo ones.

## 3. Upstream verdicts (verified, not assumed)

Each of these was checked by cloning, installing or running the thing, not by
reading the brief.

### marimo-pair — REAL

- `github.com/marimo-team/marimo-pair`, Apache-2.0, "Copyright 2026 Marimo Team".
- Pinned to tag **`v0.0.18`** = commit `0c486ee7ee4cd54622e0d062badddab429f435b1`.
  (`main` HEAD was `6cecaff464479eaa2b9714572243da707c261d22`, ahead of the last
  release; a tag is pinned rather than a moving branch.)
- Ships no Python. It is two bash scripts (`discover-servers.sh`,
  `execute-code.sh`) plus Markdown skill documentation and three plugin
  manifests (`.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json`,
  `.agents/plugins/marketplace.json`).
- Server discovery reads `${XDG_STATE_HOME:-$HOME/.local/state}/marimo/servers/*.json`.
  Only servers started with `--no-token` self-register there. Execution posts to
  `POST {base}/api/kernel/execute` with a `Marimo-Session-Id` header and reads an
  SSE stream.
- It drives `marimo._code_mode`, a **private** marimo API whose own docstring
  says "Internal, agent-only API… May change or be removed without notice".

Consequence for Cellimo: the pinned skill is **vendored unmodified** under
`plugin/skills/marimo-pair/`, with origin, commit and licence recorded. Cellimo
library code never imports `marimo._code_mode`, and the generated notebook never
imports it either. See `docs/MARIMO.md`.

### Codex plugins — REAL

Codex CLI **0.147.0** is installed on this machine and has a real plugin system
(`codex plugin list` enumerates installed plugins; `.codex-plugin/plugin.json`
is the manifest). So the "one source tree, two platforms" goal in §7 of the
brief is achievable rather than degraded — `plugin/` carries both
`.claude-plugin/` and `.codex-plugin/` metadata generated from one canonical
`plugin.toml`, and a test asserts they stay consistent.

### marimo — 0.23.16

- `marimo._code_mode` exists from 0.21.1, but the surface marimo-pair documents
  (`ctx.packages.add/remove`, `cell.status`, `cell.errors`, `cells.find/grep`)
  needs **≥ 0.23.8**. Cellimo pins `marimo>=0.23.8` and `cellimo doctor` checks it.
- `marimo check FILE` and `marimo check --format json FILE` validate a notebook
  and exit 1 on breaking issues. This is what validates the generated
  `analysis.py`, in tests and in `cellimo doctor`.
- `marimo edit` defaults to `--token` ON, which prevents marimo-pair's
  auto-discovery. `cellimo start` therefore runs `marimo edit --no-token --host
  127.0.0.1`, which is what makes the session discoverable — and is documented as
  a deliberate trade-off in `docs/SAFETY.md`.

  **This was verified empirically rather than taken from marimo-pair's script
  comments**, because the whole security trade-off rests on it. Running
  `marimo edit --no-token --headless -p 2799` wrote
  `~/.local/state/marimo/servers/127.0.0.1_2799.json`
  (`{"server_id": "127.0.0.1:2799", "pid": …, "version": "0.23.16"}`) and
  `cellimo sessions` listed it as live. Running the same notebook with
  `--token --token-password …` produced a server that answered
  `GET /health` with `{"status":"healthy"}` but wrote **no** registry file, and
  `cellimo sessions` found nothing. The claim holds in both directions:
  `--no-token` is necessary for discovery, not merely convenient.

### MCP Python SDK — 2.0.0, a breaking rewrite

`mcp==2.0.0` (2026-07-28) **removed `mcp.server.fastmcp` entirely**;
`from mcp.server.fastmcp import FastMCP` raises `ModuleNotFoundError`. The class
is now `MCPServer`:

```python
from mcp.server.mcpserver import MCPServer   # or: from mcp.server import MCPServer
```

Tests drive the server in-process with the new unified client
(`async with Client(server) as client:`), so the four tools are exercised without
spawning a subprocess. Cellimo pins `mcp>=2.0,<3`.

## 4. Frozen schema

`src/cellimo/schema.py` is the single source of the field vocabulary. Four
things read these names — the provenance writer, `cellimo check`, the MCP
payloads and the generated notebook — so they are defined once and imported
everywhere. `SCHEMA_VERSION = 1`; a project written by a different schema version
is rejected with a message rather than misread.

Load-bearing fields:

| Field | Where | Why it exists |
| --- | --- | --- |
| `design.experimental_unit` | `cellimo.yaml` | the obs column that is the biological replicate |
| `design.status` | `cellimo.yaml` | `unresolved` → `proposed` → `approved`; gates inference |
| `artifact.representation` | `artifacts.jsonl` | what the matrix values *are* (`raw_counts`, `lognorm`, `integrated_expression`, …) |
| `artifact.raw_counts_available` / `counts_layer` | `artifacts.jsonl` | makes "preserve raw counts" checkable |
| `artifact.parent_sha256` | `artifacts.jsonl` | lineage back to the source |
| `artifact.exclusions[].by_sample` | `artifacts.jsonl` | makes "stratify QC by sample" checkable |
| `statistics.mode` | `statistics.jsonl` | `exploratory` vs `confirmatory`; only the latter is held to the replication rules |
| `statistics.unit_level` | `statistics.jsonl` | `cell` here on a confirmatory test *is* pseudoreplication |
| `statistics.n_units` | `statistics.jsonl` | independent units per group; cell counts go in `n_cells` |
| `statistics.input_representation` | `statistics.jsonl` | batch-corrected input to DE without `justification` is an error |

Every scientific check is a predicate over these fields. No check greps source
code or free text.

## 5. Deliberate deviations from the brief

1. **No `legacy/` directory** — see §2. The repository had nothing to preserve.
2. **`mcp` is a core dependency, not an extra.** `cellimo mcp serve` is a
   documented command and the plugin's `.mcp.json` points at it, so the server
   has to work in the tool runtime. `chromadb`/`sentence-transformers` remain in
   the `retrieval` extra, and the MCP server degrades to a clear
   "index not installed" response without them.
3. **The retrieval index is not downloaded during install or tests.** `cellimo
   index install` exists and is tested against a local fixture; pulling the real
   Zenodo archive is an explicit, user-initiated network action.
4. **`marimo._code_mode` is only ever reached through the vendored marimo-pair
   skill's bash scripts.** No Python in this repository imports it, and a test
   enforces that.

## 6. Second-pass audit

An adversarial review ran eight independent lenses over the implementation —
LLM/legacy dependencies, MCP read-onlyness, path safety, the scientific checks,
documentation claims, test quality, plugin packaging, and clean-environment
runtime behaviour — each required to reproduce a defect before reporting it.

It found **26 real defects**, all fixed and all with regression tests. The two
that mattered most:

1. **The replication rules were escapable by renaming your test.** `C004` and
   `C006` matched substrings of the free-text `test` field, so a confirmatory
   `kruskal_wallis` computed per-cell on batch-corrected values passed with exit
   0. Both now key on structure: a confirmatory analysis must positively declare
   `sample`/`donor` as its unit or aggregate in a replicate-aware way, and no
   confirmatory statistic may consume a corrected representation whatever it is
   called.

2. **`append_artifact` deduplicated on content hash alone**, so a stage whose
   output happened to be byte-identical to an earlier artifact returned a
   normal-looking descriptor that was never persisted — an invisible hole in the
   lineage. Identity is now `(stage, path, sha256, parent)`.

Installing the tool for real — `uv tool install`, then registering with both
agents — exposed a further cluster where the two-runtime split was simply wrong:
`doctor` reported Marimo as a hard failure, `init` recorded the tool interpreter
as the project runtime, `environment.json` captured the tool's dependencies
instead of the scientific stack, and virtualenv paths were resolved through their
symlinks onto the base interpreter. See the changelog for the full list.

A mutation pass gutted seven guards in an isolated copy and re-ran the suite:
**all seven were caught**, and the suite passes in reversed order with no
flakiness. Three non-diagnostic tests were replaced with behavioural ones, and a
test that mutated a real repository file now works on a copy.

The audit also confirmed, by reproduction rather than by reading: no LLM or
VS Code dependency anywhere; the tool-runtime wheel installs with no scientific
packages; reference ids are content-derived; source immutability holds against
symlinks, hard links, traversal, case variants and a 50-deep `..` chain; atomic
writes survive a simulated mid-write crash; and 16 concurrent processes appending
to one JSONL log produced no corruption.

## 7. What is not claimed

- No container isolation. Arbitrary Python written by the agent into the
  notebook is **not** sandboxed by Cellimo. The library-level guarantees
  (immutable source, path containment, recorded writes) bind Cellimo's own APIs
  only. `docs/SAFETY.md` says this in the same words.
- Only the `scanpy` and `existing` profiles are implemented and tested.
- Trajectory, spatial, multimodal and R workflows are out of scope for 0.1.0.
