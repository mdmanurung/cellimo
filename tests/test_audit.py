"""The AnnData audit, against the object shapes real datasets actually have.

The rest of the suite audits one well-formed fixture. These are the awkward
cases: no ``X`` at all, counts only in a layer, a single sample, obs columns
that look like identifiers, and objects that cannot be read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cellimo.audit import audit_anndata
from cellimo.errors import CellimoError

pytest.importorskip("anndata")


def _write(path: Path, adata: Any) -> Path:
    adata.write_h5ad(path)
    return path


def _frame(n_obs: int, **columns: Any) -> Any:
    import pandas as pd

    return pd.DataFrame(columns, index=[f"cell{i}" for i in range(n_obs)])


@pytest.mark.parametrize("backed", [True, False])
def test_an_anndata_with_no_x_is_audited_not_crashed(tmp_path: Path, backed: bool) -> None:
    """``X`` is optional in AnnData; data can live only in ``layers``.

    In backed mode a missing ``X`` raises a raw ``KeyError`` out of h5py, which
    used to escape ``audit_anndata`` as a traceback on the very first call a
    user makes against a new dataset.
    """
    import anndata as ad
    import numpy as np
    import pandas as pd

    obs = _frame(100, donor_id=["d1"] * 50 + ["d2"] * 50, condition=["a"] * 50 + ["b"] * 50)
    var = pd.DataFrame(index=[f"gene{i}" for i in range(30)])
    adata = ad.AnnData(obs=obs, var=var, shape=(100, 30))
    adata.layers["counts"] = np.random.default_rng(0).poisson(3, size=(100, 30)).astype(
        "float32"
    )
    path = _write(tmp_path / "no_x.h5ad", adata)

    report = audit_anndata(path, backed=backed)

    assert report.n_obs == 100
    assert report.n_vars == 30
    assert report.x_dtype == "absent"
    assert not report.x_is_sparse
    # The counts are still found — in the layer, where they actually are.
    assert report.raw_counts.available
    assert report.raw_counts.location == "layers/counts"
    assert report.raw_counts.layer == "counts"
    assert report.best_candidate("donor") == "donor_id"


def test_counts_in_x_are_found(tmp_path: Path) -> None:
    import anndata as ad
    import numpy as np
    import pandas as pd

    adata = ad.AnnData(
        X=np.random.default_rng(0).poisson(2, size=(40, 10)).astype("float32"),
        obs=_frame(40, sample_id=["s1"] * 20 + ["s2"] * 20),
        var=pd.DataFrame(index=[f"g{i}" for i in range(10)]),
    )
    report = audit_anndata(_write(tmp_path / "counts_x.h5ad", adata), backed=True)
    assert report.raw_counts.available
    assert report.raw_counts.location == "X"
    assert report.raw_counts.integer_like


def test_normalised_values_are_reported_as_not_counts(tmp_path: Path) -> None:
    import anndata as ad
    import numpy as np
    import pandas as pd

    adata = ad.AnnData(
        X=(np.random.default_rng(0).random((40, 10)) * 3.7).astype("float32"),
        obs=_frame(40, sample_id=["s1"] * 40),
        var=pd.DataFrame(index=[f"g{i}" for i in range(10)]),
    )
    report = audit_anndata(_write(tmp_path / "norm.h5ad", adata), backed=True)
    assert not report.raw_counts.available
    assert "not integer-valued" in report.raw_counts.evidence
    assert any("counts" in warning for warning in report.warnings)


def test_a_dotraw_fallback_is_found(tmp_path: Path) -> None:
    import anndata as ad
    import numpy as np
    import pandas as pd

    counts = np.random.default_rng(0).poisson(2, size=(30, 8)).astype("float32")
    adata = ad.AnnData(
        X=counts.copy(),
        obs=_frame(30, donor_id=["d1"] * 30),
        var=pd.DataFrame(index=[f"g{i}" for i in range(8)]),
    )
    adata.raw = adata
    adata.X = adata.X * 0.31  # normalised in place, counts survive in .raw
    report = audit_anndata(_write(tmp_path / "raw.h5ad", adata), backed=True)
    assert report.raw_present
    assert report.raw_counts.available
    assert report.raw_counts.location == "raw/X"


def test_a_missing_file_is_a_clean_error(tmp_path: Path) -> None:
    with pytest.raises(CellimoError, match="does not exist"):
        audit_anndata(tmp_path / "absent.h5ad")


def test_a_non_h5ad_suffix_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "data.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(CellimoError, match=r"expected an \.h5ad"):
        audit_anndata(target)


def test_an_unreadable_h5ad_is_a_clean_error(tmp_path: Path) -> None:
    target = tmp_path / "corrupt.h5ad"
    target.write_bytes(b"this is not HDF5")
    with pytest.raises(CellimoError, match="cannot read"):
        audit_anndata(target)


def test_a_single_sample_object_still_audits(tmp_path: Path) -> None:
    import anndata as ad
    import numpy as np
    import pandas as pd

    adata = ad.AnnData(
        X=np.random.default_rng(0).poisson(2, size=(20, 5)).astype("float32"),
        obs=_frame(20, sample_id=["only"] * 20),
        var=pd.DataFrame(index=[f"g{i}" for i in range(5)]),
    )
    report = audit_anndata(_write(tmp_path / "one.h5ad", adata), backed=True)
    # A constant column cannot define a comparison, so it is not proposed.
    assert report.best_candidate("sample") is None
    assert any("donor or sample" in warning for warning in report.warnings)


def test_a_per_cell_identifier_is_not_proposed_as_a_design_column(tmp_path: Path) -> None:
    import anndata as ad
    import numpy as np
    import pandas as pd

    adata = ad.AnnData(
        X=np.random.default_rng(0).poisson(2, size=(60, 5)).astype("float32"),
        obs=_frame(
            60,
            sample_barcode=[f"barcode{i}" for i in range(60)],
            sample_id=["s1"] * 30 + ["s2"] * 30,
        ),
        var=pd.DataFrame(index=[f"g{i}" for i in range(5)]),
    )
    report = audit_anndata(_write(tmp_path / "ids.h5ad", adata), backed=True)
    proposed = [item.column for item in report.design_candidates.get("sample", [])]
    assert "sample_id" in proposed
    assert "sample_barcode" not in proposed, "a per-cell barcode is not a design column"


def test_layer_names_drop_the_none_anndata_013_emits() -> None:
    """anndata 0.13 reports a layer that is not there.

    `layers.keys()` yields a spurious `None` — alone on an object with no
    layers, and alongside the real names when there are some (reproduced
    against 0.13.2; 0.12.19 does not). It reached `AuditReport.layers:
    list[str]` as a validation error and `name.lower()` in the counts search as
    an AttributeError, so five tests passed on 3.11 and failed on 3.12.
    """
    from cellimo.audit.anndata_audit import _layer_names

    class _Layers(dict):  # type: ignore[type-arg]
        def __iter__(self):
            return iter([None, "counts", None])

    class _Backed:
        layers = _Layers()

    assert _layer_names(_Backed()) == ["counts"]

    class _Empty:
        layers = type("_L", (dict,), {"__iter__": lambda self: iter([None])})()

    assert _layer_names(_Empty()) == []
