# Roadmap — seamless, grounded single-cell analysis

The goal: you work in a live Marimo notebook and ask for the next scientific
objective in plain language. Codex or Claude Code carries the project and
kernel state across steps; grounded cells appear, run, show their outputs and
retain the published sources they were adapted from. You inspect the result,
make the decisions that belong to you, and continue in the same thread.

“Seamless” names the user experience, not an opaque pipeline. The method stays
visible in the notebook, and the boundaries between the agent, Marimo,
marimo-pair, retrieval and Cellimo remain explicit and testable.

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

## The original gap, now closed

Nothing connected retrieval to `create_cell`.

The scientific skills now require the two-call `ground` cycle before every
analysis cell, the source header survives into Marimo, and S009 audits citations
per cell. `ground` also checks C004/C006/C008 before writing and preflights the
exact proposed code for native-plot reinvention.

The held-out Kang benchmark now measures the outcome. Its first frozen result is
44.4% call precision and recall with leakage blocked; the dominant error is API
drift in the eligible corpus. That makes corpus freshness and package docs the
next concrete depth problem rather than an assumed one.

---

## v0.2 — grounded cells in the live loop (complete)

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
- **Delete the manual citation ledger** — source headers travel with cells, so
  the agent does not translate every adapted section into a separate reference
  entry by hand.

**Done when** "take me through QC on these 8 donors" produces cells in your
notebook that ran, cite real published notebooks, and `cellimo check` names the
ones that don't.

That is the goal, reached. Everything after is depth.

## v0.3 — make continuation resilient

Keep the analysis moving when reality differs from the first proposal, without
leaving the notebook or guessing past an error.

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

1. **Error recovery.** Until a failing cell is re-grounded from its real
   traceback, the continuous experience can still collapse into manual repair.
2. **Search quality.** Irrelevant evidence makes every downstream step feel
   heavier and less trustworthy.
3. **Corpus staleness.** KAI is a 2025-09 snapshot; Scanpy's API moves.
4. **ChromaDB on network filesystems.** Its SQLite index fails with "disk I/O
   error" on NFS — anyone on an HPC cluster hits this. `CELLIMO_INDEX_DIR` is
   the escape hatch and should be documented loudly. `install.py` should use
   `certifi` so downloads work behind a corporate CA.

## What moves into the background

The 0.1.0 provenance ledger, the wider check catalogue and the recording API
remain implemented, but they are supporting infrastructure rather than the
lead story. The agent uses them while the user stays with the live notebook and
the scientific result. The Marimo/marimo-pair boundary is unchanged and remains
load-bearing; the Kang vignette now presents the experience from question to
result while keeping those safeguards visible.
