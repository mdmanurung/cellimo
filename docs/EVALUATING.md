# Evaluating Cellimo

A testing ground for judging whether retrieval is good enough to build on, and
for telling me where it is not.

## Setup, once

```bash
export CELLIMO_INDEX_DIR=/tmp/cellimo-index   # NOT a network filesystem, see below
cellimo index install --yes                   # 345 MB from Zenodo, ~840 MB unpacked
cellimo index status --json
```

:::{warning}
**ChromaDB cannot open its index on NFS.** Its sqlite store fails with
`disk I/O error` on network filesystems, which is most HPC home directories.
Point `CELLIMO_INDEX_DIR` at local disk. If `install` fails with
`CERTIFICATE_VERIFY_FAILED`, download with `curl` and use `--from-archive`.
:::

Expect `installed: true`, `notebooks: 2845`, `documents: 100999`, and a note
that `search_documentation` returns nothing — the published archive has no
documentation collections. That is a known gap, not a fault.

## The scorecard

```bash
python tools/eval_retrieval.py            # score every query
python tools/eval_retrieval.py --show     # ...and print every hit
python tools/eval_retrieval.py --only qc-prose --show
python tools/eval_retrieval.py --cap 1 --cap 2 --cap 3   # compare caps
```

Three columns matter, and the last one most:

| | |
| --- | --- |
| **good** | a hit from a repository the query set calls a right answer |
| **noise** | a hit from one it calls wrong |
| **?** | a hit it has **no opinion about** — this is where your judgement goes |

A query with `0 good, 0 noise, 3 ?` is not a passing query. It is an unjudged
one, and the score quietly excludes it.

## What I actually need from you

Edit `tests/eval_queries.yaml`. Three things, in order of value:

**1. Is `lueckenlab/masterpraktikum_fibrosis_atlas` noise?** It appears in five
of twelve queries. I have called it global noise on the grounds that it is a
teaching-course repository, duplicate-heavy and not peer-reviewed. It is the
single biggest lever on the score, and if you would actually read it I am
wrong.

**2. Re-label the `?` column.** Run with `--show`, look at what came back, and
move each unlabelled repository into `good` or `noise`. The run prints the most
frequent unlabelled sources at the end for exactly this.

**3. Fix the queries I guessed at.** `spatial` and `multiome` currently score
nothing at all — every hit unlabelled — because I did not know the corpus well
enough to say what a right answer looks like. Four entries are marked `TODO`.

Feedback in any form is useful, including "this query is not one I would ever
type."

## Where it stands

`precision@3 = 68%` over 41 judged hits, with 13 of 54 unlabelled. Known
failures, all visible in the scorecard:

- **`qc-prose` scores 0%.** A prose query for quality control returns
  `theislab/mapqc` (atlas-mapping QC, a different thing) and still reaches
  `dpeerlab/MitoEJ-paper-analysis` — a mitochondrial *copy-number* paper that
  matches on the word "mitochondrial".
- **`qc-symbols` scores 33% and does better in practice.** Querying with the
  scanpy functions the agent is about to write beats querying with English.
  That belongs in the skills, not in ranking.
- **A checkpoint notebook can still rank.** Duplicates are collapsed only when
  both copies appear in the same result set. 28 of the 82 checkpoint notebooks
  in the corpus have no original, so dropping them outright would lose the only
  copy — which is why they are not simply filtered.

## Trying it by hand

The `cellimo-knowledge` MCP server is read-only and gives you four tools:
`search_workflows`, `search_documentation`, `get_reference`, `index_status`. Ask
your agent to search for something you know well and judge the answers — that is
the fastest way to form an opinion the scorecard cannot give you.

A retrieved code section now arrives with its origin attached:

```python
# cellimo:source notebook:epigen_macrophage_regulation_rna_01 section=1 sha=e0e09b49a416
sc.pp.filter_cells(adata, min_genes=200)
```

That header is what makes a cell's provenance checkable later. Whether it is
*useful* — whether the cell it points at is one you would actually adapt — is
the question the scorecard is trying to answer.
