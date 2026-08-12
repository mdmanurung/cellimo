"""Shared fixtures.

The synthetic dataset is deliberately realistic in the ways the validator cares
about: several donors, two conditions, two batches, mitochondrial genes, and a
subset of genuinely low-quality cells so that quality control has something to
remove and the exclusion ledger has something to reconcile.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from cellimo.project.project import Project

pytest.importorskip("anndata", reason="the test suite needs anndata")

N_DONORS = 6
CELLS_PER_DONOR = 120
N_GENES = 300
N_MITO_GENES = 15
LOW_QUALITY_PER_DONOR = 10


@pytest.fixture(autouse=True)
def isolated_index_dir(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Never touch the user's real index or data directory during tests."""
    base = tmp_path_factory.mktemp("cellimo-home")
    previous = {
        "CELLIMO_HOME": os.environ.get("CELLIMO_HOME"),
        "CELLIMO_INDEX_DIR": os.environ.get("CELLIMO_INDEX_DIR"),
    }
    os.environ["CELLIMO_HOME"] = str(base)
    os.environ["CELLIMO_INDEX_DIR"] = str(base / "index")
    yield base
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _build_anndata(*, with_counts_in_x: bool = True) -> Any:
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse

    rng = np.random.default_rng(20260811)
    donors: list[str] = []
    samples: list[str] = []
    conditions: list[str] = []
    batches: list[str] = []
    quality: list[str] = []
    for index in range(N_DONORS):
        donors += [f"donor{index:02d}"] * CELLS_PER_DONOR
        samples += [f"sample{index:02d}"] * CELLS_PER_DONOR
        conditions += ["stim" if index % 2 else "ctrl"] * CELLS_PER_DONOR
        batches += [f"batch{index // 3}"] * CELLS_PER_DONOR
        quality += ["low"] * LOW_QUALITY_PER_DONOR + ["ok"] * (
            CELLS_PER_DONOR - LOW_QUALITY_PER_DONOR
        )

    n_cells = N_DONORS * CELLS_PER_DONOR
    counts = rng.poisson(1.5, size=(n_cells, N_GENES)).astype("float32")

    # Low-quality cells: few genes, and a mitochondrial fraction well over any
    # sensible threshold.
    low = np.array([label == "low" for label in quality])
    counts[low, :] = 0
    counts[np.ix_(low, np.arange(N_MITO_GENES))] = rng.poisson(
        30, size=(int(low.sum()), N_MITO_GENES)
    ).astype("float32")
    counts[np.ix_(low, np.arange(N_MITO_GENES, N_MITO_GENES + 5))] = 1.0

    # A handful of genes are detected nowhere, so gene filtering does something.
    counts[:, -12:] = 0

    var = pd.DataFrame(
        index=[
            f"MT-GENE{i}" if i < N_MITO_GENES else f"GENE{i}" for i in range(N_GENES)
        ]
    )
    obs = pd.DataFrame(
        {
            "participant_id": donors,
            "sample_id": samples,
            "condition": conditions,
            "library_batch": batches,
            "timepoint": ["baseline"] * n_cells,
            "quality_truth": quality,
        },
        index=[f"cell{i:05d}" for i in range(n_cells)],
    )
    adata = ad.AnnData(X=sparse.csr_matrix(counts), obs=obs, var=var)
    if not with_counts_in_x:
        adata.X = adata.X.multiply(0.37)  # normalised-looking, non-integer values
    return adata


@pytest.fixture(scope="session")
def synthetic_h5ad(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A multi-donor, multi-condition dataset with raw counts in ``X``."""
    directory = tmp_path_factory.mktemp("synthetic")
    path = directory / "source.h5ad"
    _build_anndata().write_h5ad(path)
    return path


@pytest.fixture(scope="session")
def normalized_h5ad(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same dataset with non-integer values and no counts layer."""
    directory = tmp_path_factory.mktemp("synthetic-normalized")
    path = directory / "normalized.h5ad"
    _build_anndata(with_counts_in_x=False).write_h5ad(path)
    return path


@pytest.fixture
def project(tmp_path: Path, synthetic_h5ad: Path) -> Project:
    """A freshly initialised project around the synthetic dataset."""
    root = tmp_path / "project"
    root.mkdir()
    data = root / "data"
    data.mkdir()
    local = data / "source.h5ad"
    local.write_bytes(synthetic_h5ad.read_bytes())
    return Project.init(root, local, profile="scanpy", name="test-project")


@pytest.fixture
def fixture_index(tmp_path: Path) -> Path:
    """A tiny lexical index exercising both reference namespaces."""
    root = tmp_path / "index"
    root.mkdir()
    payload: dict[str, Any] = {
        "meta": {
            "name": "cellimo-test-fixture",
            "version": "1",
            "note": "fixture index for tests",
        },
        "workflows": [
            {
                "notebook_id": "scverse_scanpy_pbmc3k_qc",
                "title": "PBMC3k quality control",
                "summary": (
                    "Filter cells by gene count and mitochondrial fraction, "
                    "stratified per sample, before normalisation."
                ),
                "source_repository": "scverse/scanpy",
                "source_path": "docs/tutorials/pbmc3k.ipynb",
                "url": "https://github.com/scverse/scanpy",
                "package": "scanpy",
                "package_version": "1.10.0",
                "organization": "scverse",
                "license": "BSD-3-Clause",
                "sections": [
                    {
                        "section_id": "0",
                        "kind": "markdown",
                        "heading": "Quality control",
                        "content": "Remove cells with few genes and high mitochondrial content.",
                        "order": 0,
                    },
                    {
                        "section_id": "1",
                        "kind": "code",
                        "heading": "Quality control",
                        "content": "sc.pp.filter_cells(adata, min_genes=200)",
                        "order": 1,
                    },
                ],
            },
            {
                "notebook_id": "theislab_pseudobulk_de",
                "title": "Pseudobulk differential expression across donors",
                "summary": (
                    "Aggregate counts per donor and test with a count model, "
                    "avoiding pseudoreplication across cells."
                ),
                "source_repository": "theislab/single-cell-best-practices",
                "source_path": "jupyter-book/conditions/differential_gene_expression.ipynb",
                "url": "https://github.com/theislab/single-cell-best-practices",
                "package": "decoupler",
                "package_version": "1.6.0",
                "organization": "theislab",
                "license": "MIT",
                "sections": [
                    {
                        "section_id": "0",
                        "kind": "code",
                        "heading": "Pseudobulk",
                        "content": "pdata = dc.get_pseudobulk(adata, sample_col='donor')",
                        "order": 0,
                    }
                ],
            },
        ],
        "documentation": [
            {
                "chunk_id": "scanpy_pp_normalize_total",
                "collection": "scanpy_api",
                "title": "scanpy.pp.normalize_total",
                "summary": "Normalize counts per cell to a target sum.",
                "package": "scanpy",
                "package_version": "1.10.0",
                "organization": "scverse",
                "url": "https://scanpy.readthedocs.io",
                "sections": [
                    {
                        "section_id": "0",
                        "kind": "text",
                        "heading": "normalize_total",
                        "content": (
                            "Normalize counts per cell so every cell has the same "
                            "total count after normalization."
                        ),
                        "order": 0,
                    }
                ],
            }
        ],
    }
    (root / "cellimo-index.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return root
