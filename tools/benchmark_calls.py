#!/usr/bin/env python
"""Score one held-out published notebook against a grounded candidate.

Example::

    python tools/benchmark_calls.py \
      benchmarks/kang_scgen.yaml \
      benchmarks/kang_scgen_candidate.py \
      benchmarks/kang_scgen_grounding.json

The installed notebook store is read from ``CELLIMO_INDEX_DIR``.  The command
returns non-zero when the full dataset-level denylist was not applied, even if
the function-call score itself is high.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cellimo.benchmark import load_benchmark_spec, run_call_benchmark  # noqa: E402
from cellimo.resources import index_root  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("grounding_trace", type=Path)
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = args.index or index_root()
    spec = load_benchmark_spec(args.spec)
    result = run_call_benchmark(root, spec, args.candidate, args.grounding_trace)
    if args.json:
        print(json.dumps(result.model_dump(mode="json"), indent=2))
    else:
        score = result.score
        status = (
            "leakage blocked"
            if result.leakage_blocked
            else "INVALID: leakage not blocked"
        )
        print(f"{result.benchmark_id}: {status}")
        print(
            f"calls: precision={score.precision:.1%} "
            f"recall={score.recall:.1%} f1={score.f1:.1%}"
        )
        print(
            f"matched={len(score.matched_calls)} missing={len(score.missing_calls)} "
            f"extra={len(score.extra_calls)}"
        )
        print(
            f"dataset exclusions={len(result.leakage.excluded_reference_ids)} "
            f"notebooks scanned={result.leakage.notebooks_scanned}"
        )
        print(
            f"citations resolved={result.citations_resolved} "
            f"candidate review={result.candidate_review_passed}"
        )
        if score.missing_calls:
            print("missing: " + ", ".join(score.missing_calls))
        if score.extra_calls:
            print("extra: " + ", ".join(score.extra_calls))
        if result.leaked_reference_ids:
            print("leaked: " + ", ".join(result.leaked_reference_ids))
    return 0 if result.leakage_blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
