"""Measuring what the field calls.

Pure text and `ast` — no index needed for the extraction itself, which is the
point of keeping this module free of anything scientific.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from cellimo.corpus import (
    CorpusUsage,
    build_usage,
    calls_in_source,
    load_usage,
    save_usage,
)

# -- extraction ------------------------------------------------------------


def test_dotted_calls_are_found() -> None:
    found = calls_in_source(
        "sc.pp.neighbors(adata)\n"
        "sc.pl.umap(adata, color='leiden')\n"
        "fig, ax = plt.subplots()\n"
    )
    assert found == {"sc.pp.neighbors", "sc.pl.umap", "plt.subplots"}


def test_bare_calls_are_ignored() -> None:
    """An unqualified name says nothing about which library was reached for."""
    assert calls_in_source("print(adata)\nlen(genes)\nsorted(names)") == set()


def test_a_call_on_a_temporary_has_no_name_to_count() -> None:
    """`adatas[0].obs.groupby(...)` is not evidence about any library.

    Counting it under some guessed name would inflate the table with noise.
    """
    assert calls_in_source("adatas[0].obs.groupby('sample').sum()") == set()


def test_a_cell_that_is_not_python_yields_nothing_rather_than_raising() -> None:
    """7.4% of the real corpus is R magics and shell escapes."""
    assert calls_in_source("%%R\nlibrary(Seurat)\nDimPlot(obj)") == set()
    assert calls_in_source("!wget https://example.com/data.h5ad") == set()


def test_calls_are_found_inside_nesting() -> None:
    found = calls_in_source(
        "for sample in samples:\n"
        "    with open(path) as handle:\n"
        "        sc.pp.filter_cells(adata, min_genes=200)\n"
    )
    assert "sc.pp.filter_cells" in found


# -- counting --------------------------------------------------------------


def _index(tmp_path: Path, notebooks: dict[str, list[str]]) -> Path:
    """A minimal index in the real archive's shape."""
    base = tmp_path / "notebook_summaries" / "notebooks" / "org" / "repo"
    base.mkdir(parents=True)
    for name, cells in notebooks.items():
        (base / f"{name}.json").write_text(
            json.dumps(
                {
                    "notebook_id": name,
                    "cells": [
                        {"cell_type": "code", "content": source, "order": i}
                        for i, source in enumerate(cells)
                    ],
                }
            ),
            encoding="utf-8",
        )
    return tmp_path


def test_one_notebook_calling_something_ten_times_counts_once(tmp_path: Path) -> None:
    """Notebooks, not calls.

    A notebook that plots in a loop would otherwise outvote fifty that plot
    once, and the question being asked is how many published analyses reached
    for this.
    """
    root = _index(
        tmp_path,
        {
            "loopy": ["for s in samples:\n    sc.pl.umap(adata)"] * 5,
            "plain": ["sc.pl.umap(adata)"],
        },
    )
    usage = build_usage(root)
    assert usage.count("sc.pl.umap") == 2
    assert usage.notebooks_scanned == 2


def test_cells_that_are_not_python_are_counted_and_reported(tmp_path: Path) -> None:
    """A silent skip lets the denominator drift without anyone noticing."""
    root = _index(
        tmp_path,
        {"mixed": ["sc.pl.umap(adata)", "%%R\nlibrary(Seurat)", "import scanpy as sc"]},
    )
    usage = build_usage(root)
    assert usage.code_cells == 3
    assert usage.unparsed_cells == 1, "only the R cell is not Python"
    assert 0.3 < usage.unparsed_share < 0.4


def test_an_import_only_cell_is_not_a_parse_failure(tmp_path: Path) -> None:
    """Otherwise the failure rate measures cell style, not non-Python content."""
    root = _index(tmp_path, {"nb": ["import scanpy as sc\nimport numpy as np"]})
    usage = build_usage(root)
    assert usage.unparsed_cells == 0


def test_most_used_narrows_to_a_namespace(tmp_path: Path) -> None:
    root = _index(
        tmp_path,
        {
            "a": ["sc.pl.umap(adata)\nplt.show()"],
            "b": ["sc.pl.umap(adata)\nsc.pl.violin(adata)"],
            "c": ["plt.subplots()"],
        },
    )
    usage = build_usage(root)
    assert usage.most_used("sc.pl.", limit=5) == [("sc.pl.umap", 2), ("sc.pl.violin", 1)]


def test_a_missing_notebook_store_is_empty_not_an_error(tmp_path: Path) -> None:
    usage = build_usage(tmp_path)
    assert usage.notebooks_scanned == 0
    assert usage.unparsed_share == 0.0


def test_usage_survives_a_round_trip(tmp_path: Path) -> None:
    usage = CorpusUsage(
        notebooks_by_call={"sc.pl.umap": 759}, notebooks_scanned=2845, code_cells=82963
    )
    save_usage(usage, tmp_path)
    assert load_usage(tmp_path) == usage


def test_an_unmeasured_index_loads_as_none(tmp_path: Path) -> None:
    assert load_usage(tmp_path) is None


def test_install_builds_the_call_table_for_a_notebook_store(tmp_path: Path) -> None:
    """Grounding must not depend on a developer having run a private script."""
    from cellimo.retrieval.install import install_from_archive

    archive = tmp_path / "index.zip"
    payload = {
        "notebook_id": "demo",
        "cells": [
            {"cell_type": "code", "content": "sc.pl.violin(adata)", "order": 0}
        ],
    }
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(
            "retrieval/notebook_summaries/notebooks/org/repo/demo.json",
            json.dumps(payload),
        )

    destination = install_from_archive(archive, destination=tmp_path / "installed")
    usage = load_usage(destination)
    assert usage is not None
    assert usage.notebooks_scanned == 1
    assert usage.count("sc.pl.violin") == 1
