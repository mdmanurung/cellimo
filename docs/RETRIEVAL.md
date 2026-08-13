# Retrieval

The point of retrieval here is to answer "how is this step actually done in
published work?" with a citable source, not with a plausible paraphrase.

```
query → search → relevant sections → design checks → cited code → the agent adapts
```

There is no model in that pipeline. Cellimo ranks, selects by explicit term
overlap, and applies bounded method checks; Codex or Claude decides how the
surviving source applies to the live analysis.

## The MCP server

`cellimo-knowledge` is a stdio MCP server (`cellimo mcp serve`) with exactly
five tools, all of them read-only queries:

| tool | arguments | returns |
| --- | --- | --- |
| `ground` | `query`, `packages`, `modalities`, `top_k=5`, `analysis_mode=auto`, `candidate_code` | cited sections split into `api_usage` and `in_practice`, design findings, and proposed-code preflight |
| `search_workflows` | `query`, `packages`, `modalities`, `top_k=8` | ranked notebooks |
| `search_documentation` | `query`, `packages`, `top_k=8` | ranked API/doc sections |
| `get_reference` | `reference_id`, `section_ids` | the exact source, with a content hash |
| `index_status` | — | what is installed, and what it cannot answer |

It cannot execute Python, start a kernel, read an AnnData matrix, run a Scanpy
operation, train a model, edit a notebook, compute a statistic, or write
anything into your project. A retrieval server that could also run code would be
a second execution path with none of the provenance.

### What "read-only" does and does not mean

It describes the **tool contract** above, exactly. It is not a claim that the
index directory on disk is never written to.

The ChromaDB backend writes to its own internal bookkeeping files — `length.bin`
inside a collection's segment directory, and `chroma.sqlite3` — when it opens a
collection and when it answers a query. That is ChromaDB's behaviour, not
Cellimo's, and it means **the chroma backend needs its index directory to remain
writable** by the process serving it. Mounting the index read-only makes the
backend fail to open; Cellimo degrades to "no usable index" with that reason
rather than crashing, but searches will return nothing.

The lexical backend reads its JSON once at construction and never writes.

Nothing in either backend touches your project, your dataset or your provenance.

The index is opened **once**, when the server object is built. Re-opening it per
call would reload a sentence-transformers model on every query; a test asserts
it is opened exactly once.

### Ground before writing

`ground` is the normal entry point for an analysis cell. It searches a wider
set of notebooks, admits only code sections with concrete overlap in their
code, heading, or nearby prose, and returns at most eight (five by default).
Each returned section keeps its `# cellimo:source` header.

Sources are separated by role rather than assigned a single trust score:
tutorials and vignettes show canonical API usage; paper-companion repositories
show how APIs are used in practice. When project design metadata is available,
`ground` withholds recognised pseudoreplication (`C004`), confirmatory testing
on corrected values (`C006`), and pooled multi-sample QC (`C008`). If nothing
relevant and checked remains, `needs_user_decision` is true. The caller must
stop and ask the user rather than fill the gap from memory.

Grounding has a required two-call cycle because Cellimo cannot inspect a cell
that has not been proposed yet:

1. call `ground(query=...)` and use both source roles to adapt one cell in
   working memory;
2. call `ground(query=..., candidate_code=<exact proposed cell>)` before adding
   it to Marimo.

The second result must say `candidate_reviewed=true` and
`needs_user_decision=false`. It compares custom AnnData plotting against the
corpus call table and the native plotting signatures in the project's recorded
interpreter. A native alternative, a missing corpus table, or an unavailable
signature needed to settle a possible reinvention requires a user decision.
The source header from the selected section stays in the adapted cell.

## Reference identifiers

A reference id has to survive re-indexing, must not depend on a row's position
in a result set, and must resolve back to the exact source. Two namespaces do
that:

```
notebook:<notebook_id>              a whole indexed notebook
chunk:<collection>:<chroma_id>      one indexed chunk
```

`notebook_id` is the key both the notebook store and the summary index use, so
the two agree. `chunk:` ids resolve with a direct `get(ids=[…])`. Query offsets
and ranks are never part of an id.

`get_reference` returns a `content_hash` over the sections it actually returned,
so a provenance record can prove which version of a reference was read.

## Backends

**`chroma`** reads the index inherited from KAI: a ChromaDB instance of workflow
chunks, a second ChromaDB instance of notebook summaries, and a filesystem store
of the notebooks themselves. Needs `pip install 'cellimo[retrieval]'`.

**`lexical`** reads a single `cellimo-index.json` and scores with BM25, using
nothing outside the standard library. It backs the test suite — all five MCP
tools are exercised without a 345 MB download or a PyTorch install —
and is a reasonable format for a lab that wants to index its own notebooks.

`open_index()` picks by inspecting the directory, and returns a `MissingIndex`
when nothing is installed. `MissingIndex` answers every tool truthfully rather
than raising: the agent is told the index is absent and how to install it,
instead of seeing an empty result that looks like "no such workflow exists".

## Installing the index

```bash
cellimo index status
cellimo index install         # prints URL, size, licence, target; asks first
cellimo index install --from-archive /path/to/index.zip
cellimo index update
```

Never during `pip install`, never during tests, never as a side effect.
The explicit install also computes the corpus function-call table once; an
older installed index is measured read-only on the first proposed-code
preflight.

The published archive is 345 MB compressed and about 840 MB unpacked. It is
GPL-3.0-or-later **data**, separate from KAI's Apache-2.0 code, which is why
Cellimo downloads it rather than redistributing it.

Download goes to a `.part` file, resumes with a `Range` request if interrupted,
and is moved into place only after its md5 matches the publisher's. A mismatch
keeps the partial file so a retry can resume. Extraction strips the archive's
single wrapping directory, skips `__MACOSX` junk, and rejects any entry that
would land outside the destination.

(KAI's own downloader extracted one level too deep, which made its verification
step fail on every successful download. Cellimo strips the prefix instead.)

## What the published index does and does not contain

Verified against the actual archive, not inferred:

- **249 collections, all workflows.** Every collection is
  `content_type='workflows'`; there are **no documentation collections**.
  `search_documentation` therefore returns an empty result with an explanation,
  and `index_status` lists it under `unavailable`. The index's own bundled
  README claims otherwise; it is stale.
- **100,999 chunks** across three levels: 3,151 document, 19,146 section, 78,702
  code-cell.
- **3,108 notebooks** in the filesystem store, from 15 organisations
  (BayraktarLab, Lotfollahi-lab, MarioniLab, ShalekLab, YosefLab, aertslab,
  bioFAM, dpeerlab, epigen, lueckenlab, mlbio-epfl, saezlab, scverse, teichlab,
  theislab).
- **No modality field anywhere.** The `modalities` argument is best-effort text
  matching, and every result that used it says so in `approximate_filters` and
  `note`.
- **No reliable package field.** The `library`/`tool` metadata is the repository
  name, and KAI's extractor defaults unrecognised repositories to the literal
  string `"python"`. Package filtering matches on the repository, and again
  declares itself approximate.

Saying this in the API rather than in a footnote is deliberate. A filter that
silently does nothing is worse than one that admits it is fuzzy.

## Embeddings

The main index was built with `sentence-transformers/all-MiniLM-L6-v2` passed
explicitly. ChromaDB does not persist which embedding function built a
collection, so it has to be supplied again on every open or the query embeddings
will not match the stored ones. Cellimo passes it; `index_status` reports the
model name.

The summary index was built with ChromaDB's *default* (ONNX) embedding function
instead. Both are 384-dimensional and both derive from all-MiniLM-L6-v2, but via
different runtimes, so their rankings are close rather than identical. Cellimo
opens each with the function it was built with.

Loading the sentence-transformers model is deferred until a query actually needs
the main index, so `index_status` and notebook lookups never pay for it.

## Recording what you used

For adapted code, keep the `# cellimo:source` header returned by `ground` in the
same Marimo cell. `cellimo check` S009 scopes citations to cells, reports
uncited scientific cells, and resolves each header against the installed index.
The append-only reference ledger remains useful for papers, documentation, and
decisions that inform the analysis but are not themselves adapted code:

```python
project.record_reference(
    reference_id="notebook:scverse_scanpy_pbmc3k_qc",
    title="PBMC3k quality control",
    source="scverse/scanpy",
    package="scanpy",
    section_ids=["0", "1"],
    content_hash=reference.content_hash,
    used_for="quality-control thresholds",
    stage="post_qc",
)
```

`cellimo check` warns (`S006`) when a decision cites a reference that is not in
`references.jsonl`, (`S009`) when grounded code has no resolvable cell header,
and (`C013`) when confirmatory analyses exist with no references recorded at
all.

## Building your own lexical index

```json
{
  "meta": {"name": "lab-index", "version": "1"},
  "workflows": [
    {
      "notebook_id": "lab_project_qc",
      "title": "Our QC procedure",
      "summary": "…",
      "source_repository": "lab/project",
      "package": "scanpy",
      "sections": [
        {"section_id": "0", "kind": "code", "heading": "QC",
         "content": "sc.pp.filter_cells(adata, min_genes=200)", "order": 0}
      ]
    }
  ],
  "documentation": []
}
```

Save as `cellimo-index.json` in a directory and point `CELLIMO_INDEX_DIR` at it.
Scoring is deterministic, so the same query gives the same order every time.
