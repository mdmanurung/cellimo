# Roadmap — a Marimo-native, grounded Claude for single-cell science

The goal: you work in a Marimo notebook. Claude Code is paired to the live
kernel. You ask for an analysis in plain language and cells appear, run, and
carry the published source they were adapted from.

**Most of that already exists.** The gap is narrower than it looks.

---

## What is already built

**marimo-pair supplies the interface and the hands.** Vendored at
`plugin/vendor/`, unmodified, pinned. It already gives the agent:

- `create_cell` / `edit_cell` on **durable** cells — not a chat panel, the real
  notebook
- a scratchpad scope for exploration that does not pollute the notebook
- `ctx.packages.add()` — dependency management from inside the session
- `ctx.set_ui_value(element, value)` — drives `mo.ui` widgets
- cell status and errors, readable back

**Cellimo supplies the corpus.** Verified against the real 345 MB KAI archive:
2,845 notebooks, 249 collections, ~97% Python cells (630 Python vs 17 R/magic in
a 30-reference sample). `get_reference` returns literal cell source with
repository, title and URL — after the `chroma_index` metadata fix on this
branch, which had been blanking all of it.

**Claude Code is the agent.** Cellimo never invokes a language model, and does
not need to: marimo-pair is the bridge, and the reasoning is the one you already
chose. That founding constraint stands unchanged.

## The gap, stated exactly

Nothing connects retrieval to `create_cell`.

The agent writes cells from memory because no skill tells it to retrieve first —
`quality-control`, `statistics` and `project-audit` mention retrieval **zero**
times between them — and because a retrieved cell has nowhere to record where it
came from once it lands in the notebook.

That is the whole project. Everything below serves it.

---

## v0.2 — ground the cells marimo-pair writes

- **Section selection in `ground(query)`.** The load-bearing piece. Search
  returns no `section_ids` and headings are almost all `'main'`, so
  search-then-fetch hands back 93 undifferentiated cells. Returning the 3–5 that
  matter is what makes the rest feel effortless.
- **Citation survives into the notebook.** `get_reference` prefixes each code
  section with `# cellimo:source <ref> section=<n> sha=<hash>`; the agent keeps
  the header when it adapts the code through `create_cell`. Nobody records
  anything — the citation lives in the cell.
- **`cellimo check` reads `analysis.py`**, resolves every header against the
  index, and reports cells that cite nothing. Plus `C004` and `C006`.
- **Rewrite the three scientific skills** so the loop is:
  `ground` → adapt to the user's columns → `create_cell` → run → read status.
  Three markdown files; the highest-leverage change in this roadmap.
- **Delete the manual ledger** — eleven API calls become zero.

**Done when** "take me through QC on these 8 donors" produces cells in your
notebook that ran, cite real published notebooks, and `cellimo check` names the
ones that don't.

That is the goal, reached. Everything after is depth.

## v0.3 — close the feedback loop

Use the half of marimo-pair the skills currently ignore.

- A grounded cell errors → the agent reads the cell status → **re-retrieves**
  against the error rather than guessing a fix.
- Retrieved code's imports drive `ctx.packages.add()`, so adapting a published
  notebook installs what it needs.
- `ctx.set_ui_value` to wire thresholds to `mo.ui.slider`, so QC cut-offs become
  something you move rather than something you re-prompt for.

## v0.4 — a corpus worth trusting

KAI's snapshot is fixed at 2025-09, has no documentation collections, and search
is noisy.

1. **Reranking.** `"leiden clustering umap"` currently returns an
   electronic-health-record notebook; scores sit in a 0.70–0.79 band undifferentiated.
2. **Package docs** (scanpy, scverse, Seurat) — the wrong-argument class of
   hallucination a notebook corpus cannot fix.
3. **Live GitHub**, behind a quality filter.
4. **Your own past analyses**, so it reuses your conventions.

---

## Risks, in the order they bite

1. **Section selection.** If the agent still receives 93 cells, nothing
   downstream feels seamless. This is the one unproven piece.
2. **Search quality.** Must improve before the experience is trustworthy.
3. **Corpus staleness.** KAI is a 2025-09 snapshot; scanpy's API moves.
4. **ChromaDB on network filesystems.** Its sqlite index fails with "disk I/O
   error" on NFS — anyone on an HPC cluster hits this. `CELLIMO_INDEX_DIR` is
   the escape hatch and should be documented loudly. `install.py` should use
   `certifi` so downloads work behind a corporate CA.

## What gets left behind

The 0.1.0 provenance ledger, nineteen of twenty-one checks, the manual recording
API. The Marimo/marimo-pair boundary is unchanged — it was never the problem,
and it is now the load-bearing part. The Kang tutorial gets rewritten once the
API settles.
