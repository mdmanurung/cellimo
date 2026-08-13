"""Held-out call scoring and its leakage guard."""

from __future__ import annotations

import json
from pathlib import Path

from cellimo.benchmark import (
    BenchmarkSpec,
    GroundingTrace,
    GroundingTraceEntry,
    build_leakage_manifest,
    canonical_calls,
    exclusion_digest,
    run_call_benchmark,
    score_calls,
)
from cellimo.retrieval.citations import section_sha
from cellimo.util.hashing import hash_bytes


def _write_notebook(root: Path, notebook_id: str, cells: list[str]) -> None:
    path = root / "notebook_summaries" / "notebooks" / "org" / "repo"
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{notebook_id}.json").write_text(
        json.dumps(
            {
                "notebook_id": notebook_id,
                "cells": [
                    {"cell_type": "code", "content": source, "order": order}
                    for order, source in enumerate(cells)
                ],
            }
        ),
        encoding="utf-8",
    )


def _benchmark_files(tmp_path: Path) -> tuple[Path, BenchmarkSpec, Path, Path]:
    index = tmp_path / "index"
    _write_notebook(
        index,
        "expert_kang",
        [
            "import pertpy as pt\ndata = pt.dt.kang_2018()",
            "model = pt.tl.SCGEN(data)\nmodel.train()\nmodel.predict()",
        ],
    )
    _write_notebook(index, "kang_peer", ["adata = read_kang18()"])
    _write_notebook(index, "eligible", ["model = pt.tl.SCGEN(adata)"])

    spec = BenchmarkSpec(
        id="kang-scgen",
        task="Predict a held-out perturbation response with scGen",
        dataset="Kang 2018",
        dataset_aliases=["kang_2018", "kang18"],
        expert_reference_id="notebook:expert_kang",
        package_roots=["pertpy"],
    )
    candidate = tmp_path / "candidate.py"
    eligible_source = "model = pt.tl.SCGEN(adata)"
    candidate.write_text(
        "# cellimo:source notebook:eligible section=0 "
        f"sha={section_sha(eligible_source)}\n"
        "import pertpy as pt\n"
        "model = pt.tl.SCGEN(adata)\n"
        "model.train()\n",
        encoding="utf-8",
    )
    manifest = build_leakage_manifest(
        index,
        dataset=spec.dataset,
        aliases=spec.dataset_aliases,
        expert_reference_id=spec.expert_reference_id,
    )
    trace = GroundingTrace(
        benchmark_id=spec.id,
        entries=[
            GroundingTraceEntry(
                query=spec.task,
                exclusion_digest=exclusion_digest(manifest.excluded_reference_ids),
                selected_reference_ids=["notebook:eligible"],
                candidate_reviewed=True,
                candidate_sha256=hash_bytes(candidate.read_bytes()),
            )
        ],
    )
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(trace.model_dump_json(indent=2), encoding="utf-8")
    return index, spec, candidate, trace_path


def test_canonical_calls_expand_import_and_model_aliases() -> None:
    calls = canonical_calls(
        [
            "import pertpy as pt\nimport scanpy as sc",
            "model = pt.tl.SCGEN(adata)\nmodel.train()\nsc.pp.neighbors(adata)",
        ],
        package_roots=["pertpy", "scanpy"],
    )

    assert calls == {
        "pertpy.tl.SCGEN",
        "pertpy.tl.SCGEN.train",
        "scanpy.pp.neighbors",
    }


def test_call_score_uses_unique_extracted_calls() -> None:
    result = score_calls(
        {"scanpy.pp.neighbors", "scanpy.tl.umap"},
        {"scanpy.pp.neighbors", "scanpy.pl.umap"},
    )

    assert result.matched_calls == ["scanpy.pp.neighbors"]
    assert result.precision == 0.5
    assert result.recall == 0.5
    assert result.f1 == 0.5


def test_manifest_excludes_every_dataset_match(tmp_path: Path) -> None:
    index, spec, _, _ = _benchmark_files(tmp_path)

    manifest = build_leakage_manifest(
        index,
        dataset=spec.dataset,
        aliases=spec.dataset_aliases,
        expert_reference_id=spec.expert_reference_id,
    )

    assert manifest.notebooks_scanned == 3
    assert manifest.excluded_reference_ids == [
        "notebook:expert_kang",
        "notebook:kang_peer",
    ]


def test_benchmark_scores_only_after_the_full_denylist_was_applied(
    tmp_path: Path,
) -> None:
    index, spec, candidate, trace = _benchmark_files(tmp_path)

    result = run_call_benchmark(index, spec, candidate, trace)

    assert result.leakage_blocked
    assert result.citations_grounded
    assert result.citations_resolved
    assert result.candidate_review_passed
    assert result.leaked_reference_ids == []
    assert result.score.matched_calls == [
        "pertpy.tl.SCGEN",
        "pertpy.tl.SCGEN.train",
    ]
    assert result.score.precision == 1.0
    assert result.score.recall == 0.5


def test_incomplete_grounding_denylist_invalidates_the_result(tmp_path: Path) -> None:
    index, spec, candidate, trace_path = _benchmark_files(tmp_path)
    trace = GroundingTrace.model_validate_json(trace_path.read_text(encoding="utf-8"))
    incomplete = trace.model_copy(
        update={
            "entries": [
                trace.entries[0].model_copy(
                    update={"exclusion_digest": exclusion_digest(["notebook:expert_kang"])}
                )
            ]
        }
    )
    trace_path.write_text(incomplete.model_dump_json(indent=2), encoding="utf-8")

    result = run_call_benchmark(index, spec, candidate, trace_path)

    assert not result.exclusions_applied
    assert not result.leakage_blocked


def test_candidate_change_after_preflight_invalidates_the_result(tmp_path: Path) -> None:
    index, spec, candidate, trace_path = _benchmark_files(tmp_path)
    candidate.write_text(
        candidate.read_text(encoding="utf-8") + "# changed after review\n",
        encoding="utf-8",
    )

    result = run_call_benchmark(index, spec, candidate, trace_path)

    assert not result.candidate_review_passed
    assert not result.leakage_blocked


def test_a_dataset_derived_citation_invalidates_the_result(tmp_path: Path) -> None:
    index, spec, candidate, trace_path = _benchmark_files(tmp_path)
    candidate.write_text(
        "# cellimo:source notebook:kang_peer section=0 "
        f"sha={section_sha('adata = read_kang18()')}\n"
        "import pertpy as pt\npt.tl.SCGEN(adata)\n",
        encoding="utf-8",
    )
    trace = GroundingTrace.model_validate_json(trace_path.read_text(encoding="utf-8"))
    changed = trace.model_copy(
        update={
            "entries": [
                trace.entries[0].model_copy(
                    update={"selected_reference_ids": ["notebook:kang_peer"]}
                )
            ]
        }
    )
    trace_path.write_text(changed.model_dump_json(indent=2), encoding="utf-8")

    result = run_call_benchmark(index, spec, candidate, trace_path)

    assert result.leaked_reference_ids == ["notebook:kang_peer"]
    assert not result.leakage_blocked
