---
jupytext:
  formats: md:myst
  text_representation:
    extension: .md
    format_name: myst
kernelspec:
  display_name: Python 3
  language: python
  name: python3
---

# Tutorial: a project that can defend itself

Every code cell on this page **ran when the site was built**. The numbers,
hashes and validator output below are what Cellimo actually produced, not what
someone typed into a document and hoped stayed true. If a cell raises, the
documentation build fails.

:::{admonition} This page is a Jupyter notebook. Your analysis will not be.
:class: important

Cellimo generates a **Marimo** notebook — `analysis.py`, a plain Python file
with no hidden state and no execution order to get wrong. MyST-NB cannot
execute a Marimo notebook, so this tutorial drives the same Python API from a
Jupyter kernel instead, and shows the generated notebook as source.

Everything you see here is the API your `analysis.py` cells will call. What
changes in real use is the surrounding runtime: Marimo owns the kernel, and
Codex or Claude Code writes the cells. Cellimo is the same either way, and it
never calls a language model itself.
:::

## What we are going to build

A project whose provenance is complete enough that `cellimo check` will pass —
and, more to the point, one where the checks would have *caught* us if we had
cut a corner.

```{code-cell} ipython3
import tempfile
from pathlib import Path

# A throwaway root, so building these docs twice does not leave a project
# behind or trip over the one from last time.
root = Path(tempfile.mkdtemp(prefix="cellimo-tutorial-"))
(root / "data").mkdir()
print(root)
```

## 1. A dataset with real replication structure

The scientific checks are about *biological replication*, so a dataset with one
donor cannot demonstrate them. Six donors, three per condition, 120 cells each —
and some deliberately terrible cells for quality control to find.

```{code-cell} ipython3
import anndata as ad
import numpy as np
import pandas as pd
from scipy import sparse

N_DONORS, CELLS, GENES, MITO, JUNK = 6, 120, 300, 12, 20

rng = np.random.default_rng(20260812)
obs = pd.DataFrame({
    "participant_id": np.repeat([f"donor{i:02d}" for i in range(N_DONORS)], CELLS),
    "sample_id": np.repeat([f"sample{i:02d}" for i in range(N_DONORS)], CELLS),
    "condition": np.repeat(["stim" if i % 2 else "ctrl" for i in range(N_DONORS)], CELLS),
    "library_batch": np.repeat([f"batch{i // 3}" for i in range(N_DONORS)], CELLS),
})
obs.index = [f"cell{i:05d}" for i in range(len(obs))]

counts = rng.poisson(1.5, size=(len(obs), GENES)).astype("float32")
# The first JUNK cells of each donor are debris: almost no genes, and a
# mitochondrial fraction no threshold would forgive.
junk = np.zeros(len(obs), dtype=bool)
junk[np.concatenate([np.arange(i * CELLS, i * CELLS + JUNK) for i in range(N_DONORS)])] = True
counts[junk, :] = 0
counts[np.ix_(junk, np.arange(MITO))] = rng.poisson(30, size=(junk.sum(), MITO))

var = pd.DataFrame(index=[f"MT-GENE{i}" if i < MITO else f"GENE{i}" for i in range(GENES)])
adata = ad.AnnData(sparse.csr_matrix(counts), obs=obs, var=var)

source = root / "data" / "source.h5ad"
adata.write_h5ad(source)
print(adata)
```

## 2. Initialise the project

The dataset is hashed **where it lies**. Cellimo never copies, moves or writes
to it — the source is the root of every lineage chain, and it is immutable
through every API on this page.

```{code-cell} ipython3
from cellimo.project.project import Project

project = Project.init(root, source, profile="scanpy", name="tutorial")

print("source sha256 :", project.config.source.sha256[:16], "…")
print("notebook      :", project.notebook_path.name)
for entry in sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file()):
    print("   ", entry)
```

That `analysis.py` is the Marimo notebook. Here is how it opens — ordinary,
readable Python, not a serialised cell graph:

```{code-cell} ipython3
print("\n".join(project.notebook_path.read_text().splitlines()[:22]))
```

## 3. Audit before assuming

Before anything is analysed, Cellimo reads the file and reports what is
*actually* in it. This is the step that catches a "raw counts" object that was
normalised upstream three months ago.

```{code-cell} ipython3
audit = project.audit_anndata(backed=True)

print(f"{audit.n_obs} cells x {audit.n_vars} genes")
print("raw counts   :", audit.raw_counts.available, "in", audit.raw_counts.location)
print("donor guess  :", audit.best_candidate("donor"))
print("sample guess :", audit.best_candidate("sample"))
```

The guesses are *proposals*. Cellimo does not act on them.

## 4. Declare the experimental unit — and have a human approve it

This is the gate the whole design turns on. A confirmatory p-value without a
declared unit of replication is uninterpretable, so Cellimo refuses to record
one until a design exists and someone approved it.

```{code-cell} ipython3
design = project.record_design(
    sample="sample_id",
    donor="participant_id",
    condition="condition",
    batch="library_batch",
)
print("status           :", design.status)
print("experimental unit:", design.experimental_unit)
```

Watch what happens if we try to run inference now:

```{code-cell} ipython3
from cellimo.errors import DesignError

try:
    project.record_statistics(
        name="stim vs ctrl", test="pseudobulk_deseq2", mode="confirmatory"
    )
except DesignError as exc:
    print("refused:", exc)
```

```{code-cell} ipython3
approved = project.approve_design(approved_by="the analyst", actor="user")
print(approved.status, "by", approved.approved_by)
```

:::{note}
`actor="user"` records who *made the call*. It defaults to `"agent"`, because
nothing in a library can verify that a human was present — and check `C002`
reports an approval whose actor was not a user. Cellimo reports what was
recorded; it does not pretend to know more.
:::

## 5. Quality control as an ordinary, visible transformation

The filtering below is plain NumPy. Cellimo does not run it, wrap it, or hide
it — it records what came out, so `stage()` brackets code you can read.

```{code-cell} ipython3
dense = np.asarray(adata.X.todense())
genes_per_cell = (dense > 0).sum(axis=1)
mito = np.array([n.startswith("MT-") for n in adata.var_names])
pct_mito = 100 * dense[:, mito].sum(axis=1) / np.maximum(dense.sum(axis=1), 1)

keep = (genes_per_cell >= 50) & (pct_mito <= 20)
labels = adata.obs["sample_id"].to_numpy()
removed = {s: int(((labels == s) & ~keep).sum()) for s in sorted(set(labels))}

adata.layers["counts"] = adata.X.copy()
filtered = adata[keep].copy()
filtered = filtered[:, (np.asarray(filtered.X.todense()) > 0).sum(axis=0) >= 3].copy()

print(f"{adata.n_obs} -> {filtered.n_obs} cells, {adata.n_vars} -> {filtered.n_vars} genes")
print("removed per sample:", removed)
```

Now record it. Note `by_sample` and `stratified_by`: exclusions counted *per
sample* are what lets check `C008` tell stratified quality control from a
threshold computed across a pooled mixture of donors.

```{code-cell} ipython3
with project.stage(
    "post_qc",
    summary="Sample-stratified quality control",
    parent_sha256=project.config.source.sha256,
    params={"min_genes": 50, "max_pct_mt": 20, "min_cells": 3},
) as stage:
    filtered.write_h5ad(stage.output("artifacts/post_qc.h5ad"))
    stage.add_exclusion(
        "low gene count or high mitochondrial fraction",
        axis="obs",
        n_before=int(adata.n_obs),
        n_removed=int(adata.n_obs) - int(filtered.n_obs),
        n_remaining=int(filtered.n_obs),
        by_sample=removed,
        stratified_by="sample_id",
        criteria={"min_genes": 50, "max_pct_mt": 20},
    )
    stage.add_exclusion(
        "genes detected in fewer than 3 cells",
        axis="var",
        n_before=int(adata.n_vars),
        n_removed=int(adata.n_vars) - int(filtered.n_vars),
        n_remaining=int(filtered.n_vars),
    )
    stage.set_matrix_facts(
        representation="raw_counts",
        counts_layer="counts",
        raw_counts_available=True,
        n_obs=int(filtered.n_obs),
        n_vars=int(filtered.n_vars),
        obs_keys=list(filtered.obs.columns),
        layers=list(filtered.layers.keys()),
    )

post_qc = stage.descriptor
print("stage :", post_qc.stage)
print("sha256:", post_qc.sha256[:16], "…")
print("parent:", post_qc.parent_sha256[:16], "… (the source)")
```

The source really is immutable — not by convention, but because the write guard
refuses:

```{code-cell} ipython3
from cellimo.errors import SourceImmutabilityError

try:
    project.assert_writable("data/source.h5ad")
except SourceImmutabilityError as exc:
    print("refused:", exc)
```

:::{warning}
That guard covers writes **through Cellimo's APIs**. Arbitrary Python typed
into a notebook cell can still open the file and overwrite it. See
[](SAFETY.md) for what is and is not enforced — the honest boundary matters
more than a reassuring one.
:::

## 6. A confirmatory analysis that respects the replication structure

Six donors, not 720 cells. `aggregation="pseudobulk"` and `unit_level="donor"`
are what checks `C004` and `C012` read.

```{code-cell} ipython3
project.record_reference(
    reference_id="notebook:scverse_scanpy_pbmc3k_qc",
    title="PBMC3k quality control",
    source="scverse/scanpy",
    package="scanpy",
    used_for="quality-control thresholds",
    stage="post_qc",
)

project.record_statistics(
    name="stim vs ctrl, pseudobulk",
    test="pseudobulk_deseq2",
    mode="confirmatory",
    unit_level="donor",
    n_units={"stim": 3, "ctrl": 3},
    n_cells={"stim": 300, "ctrl": 300},
    groups=["stim", "ctrl"],
    input_artifact_sha256=post_qc.sha256,
    input_representation="pseudobulk_counts",
    aggregation="pseudobulk",
    covariates=["library_batch"],
    effect_size={"reported": True, "measure": "log2FC", "column": "log2FoldChange"},
    uncertainty={"reported": True, "measure": "adjusted p-value", "column": "padj"},
)
project.capture_environment()
print("recorded")
```

## 7. Validate

```{code-cell} ipython3
report = project.check()
print(report.to_text())
```

## 8. What the record looks like

Lineage is a hash chain that closes on the source:

```{code-cell} ipython3
for item in project.artifacts.lineage_of(post_qc.sha256):
    print(f"  {item.stage:10} {item.sha256[:12]}…  {item.path}")
```

```{code-cell} ipython3
import json

manifest = json.loads(project.write_manifest().read_text())
print(json.dumps(
    {k: manifest[k] for k in ("project_name", "counts", "latest_by_stage")},
    indent=2,
))
```

## The part that matters: it catches you

A validator that only ever passes tells you nothing. Here is the same project
with one thing changed — the same comparison run per *cell* instead of per
donor, which is the single most common way single-cell differential expression
goes wrong (Squair et al. 2021):

```{code-cell} ipython3
project.record_statistics(
    name="stim vs ctrl, per cell",
    test="wilcoxon",
    mode="confirmatory",
    unit_level="cell",
    n_units={"stim": 300, "ctrl": 300},
    groups=["stim", "ctrl"],
    input_artifact_sha256=post_qc.sha256,
    input_representation="lognorm",
    aggregation="none",
    effect_size={"reported": True, "measure": "log2FC"},
    uncertainty={"reported": True, "measure": "padj"},
)

report = project.check()
for finding in report.errors:
    print(finding.format_line())
    print("   ", finding.detail)
    for ref in finding.references:
        print("    see:", ref)
print("\nexit code:", report.exit_code())
```

Renaming the test would not have helped — `C004` keys on the *structure* of the
record, never on the test's name. A rule you escape by renaming a string is not
a rule. [](VALIDATION.md) has the full list and the literature behind each one.

```{code-cell} ipython3
:tags: [remove-cell]
import shutil
shutil.rmtree(root, ignore_errors=True)
```

## Where to go next

- [](VALIDATION.md) — every check, what it reads, and the papers behind it
- [](ARCHITECTURE.md) — the two-runtime split and why Cellimo owns none of the kernel
- [](SAFETY.md) — what is enforced, and the limits that are stated rather than implied
- [](MARIMO.md) — how this fits a Marimo session and marimo-pair
