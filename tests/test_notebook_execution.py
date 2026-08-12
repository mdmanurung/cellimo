"""Execute the generated notebook for real, with the gates pressed.

`marimo check` proves the notebook parses. This proves it *works*: the audit
runs, the design is recorded and approved, quality control filters cells and
registers an artifact, the diagnostics plot, provenance is written, and the
inline validation passes.

Skipped when the project runtime (scanpy, matplotlib) is not installed, because
that stack deliberately is not part of the tool runtime.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

import pytest

from cellimo.project.project import Project

pytest.importorskip("marimo")
pytest.importorskip("scanpy", reason="the notebook's QC cell uses scanpy")
pytest.importorskip("matplotlib", reason="the notebook's diagnostics cell plots")

pytestmark = [pytest.mark.slow, pytest.mark.needs_marimo]


class _Pressed:
    """Stands in for a pressed ``mo.ui.run_button``."""

    value = True

    def __format__(self, spec: str) -> str:
        return "[button]"


def _run_notebook(project: Project) -> dict[str, Any]:
    """Run ``analysis.py`` headlessly with every gate satisfied."""
    import marimo

    original_button = marimo.ui.run_button
    original_dir = marimo.notebook_dir
    marimo.ui.run_button = lambda *args, **kwargs: _Pressed()  # type: ignore[assignment]
    marimo.notebook_dir = lambda: pathlib.Path(project.root)  # type: ignore[assignment]
    try:
        spec = importlib.util.spec_from_file_location(
            "cellimo_test_analysis", project.notebook_path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _outputs, definitions = module.app.run()
        return dict(definitions)
    finally:
        marimo.ui.run_button = original_button  # type: ignore[assignment]
        marimo.notebook_dir = original_dir  # type: ignore[assignment]


def test_the_generated_notebook_runs_end_to_end(project: Project) -> None:
    definitions = _run_notebook(project)

    audit = definitions["audit"]
    assert audit.raw_counts.available
    assert audit.n_obs == 720

    design = definitions["design"]
    assert design.is_approved()
    assert design.experimental_unit == "participant_id"

    post_qc = definitions["post_qc"]
    assert post_qc.stage == "post_qc"
    assert post_qc.representation == "raw_counts"
    assert post_qc.counts_layer == "counts"
    # The fixture plants low-quality cells; QC must actually remove them.
    assert post_qc.n_obs is not None and post_qc.n_obs < audit.n_obs
    assert post_qc.exclusions
    assert post_qc.exclusions[0].by_sample
    assert post_qc.exclusions[0].stratified_by == "sample_id"

    report = definitions["report"]
    assert report.passed, report.to_text()
    assert not report.errors

    # And the artifact really exists, with lineage back to the source.
    reloaded = Project.open(project.root)
    chain = reloaded.artifacts.lineage_of(post_qc.sha256)
    assert [item.stage for item in chain] == ["post_qc", "source"]
    assert (project.root / post_qc.path).is_file()


def test_running_the_notebook_leaves_the_source_untouched(project: Project) -> None:
    before = project.source_path.read_bytes()
    _run_notebook(project)
    assert project.source_path.read_bytes() == before
    ok, message = Project.open(project.root).verify_source()
    assert ok, message


def test_running_the_notebook_does_not_rewrite_the_notebook(project: Project) -> None:
    before = project.notebook_path.read_bytes()
    _run_notebook(project)
    assert project.notebook_path.read_bytes() == before
