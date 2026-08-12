#!/usr/bin/env python
"""Score retrieval against the labelled query set, and show its working.

    python tools/eval_retrieval.py                    # score everything
    python tools/eval_retrieval.py --show             # ...and print every hit
    python tools/eval_retrieval.py --only qc-prose    # one query, in detail
    python tools/eval_retrieval.py --cap 1 --cap 2 --cap 3   # compare caps

The point is not the number. The point is the **unlabelled** column: hits that
the query set has no opinion about. Those are where your judgement is needed —
if an unlabelled hit is good, add it to `good`; if it is noise, add it to
`noise`, and the score becomes a little more honest.

Needs an installed index. On a network filesystem ChromaDB's sqlite fails with
"disk I/O error", so put it on local disk::

    export CELLIMO_INDEX_DIR=/tmp/cellimo-index
    cellimo index install --yes
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cellimo.retrieval.base import MissingIndex, open_index  # noqa: E402
from cellimo.retrieval.models import SearchHit  # noqa: E402

QUERIES = REPO / "tests" / "eval_queries.yaml"

GREEN, RED, GREY, BOLD, OFF = "\033[32m", "\033[31m", "\033[90m", "\033[1m", "\033[0m"


@dataclass
class Verdict:
    """One query's outcome."""

    id: str
    query: str
    good: int = 0
    noise: int = 0
    unlabelled: list[str] = field(default_factory=list)
    hits: list[SearchHit] = field(default_factory=list)

    @property
    def precision(self) -> float:
        """Share of judged hits that were good. Unlabelled hits are not judged."""
        judged = self.good + self.noise
        return self.good / judged if judged else 0.0


def _matches(repository: str, prefixes: list[str]) -> bool:
    return any(repository.startswith(prefix) for prefix in prefixes if prefix)


def evaluate(index, spec: dict, top_k: int = 3) -> list[Verdict]:
    global_noise = spec.get("global_noise") or []
    verdicts = []
    for entry in spec["queries"]:
        good = entry.get("good") or []
        noise = (entry.get("noise") or []) + global_noise
        hits = index.search_workflows(entry["query"], top_k=top_k).hits
        verdict = Verdict(id=entry["id"], query=entry["query"], hits=hits)
        for hit in hits:
            repository = hit.source_repository or "(unattributed)"
            if _matches(repository, noise):
                verdict.noise += 1
            elif _matches(repository, good):
                verdict.good += 1
            else:
                verdict.unlabelled.append(repository)
        verdicts.append(verdict)
    return verdicts


def report(verdicts: list[Verdict], *, show: bool, top_k: int) -> None:
    print(f"\n{BOLD}{'query':<16} {'good':>5} {'noise':>6} {'?':>3}  precision{OFF}")
    print("─" * 52)
    for verdict in verdicts:
        colour = GREEN if verdict.precision == 1.0 else RED if verdict.noise else GREY
        bar = f"{verdict.precision:>6.0%}" if (verdict.good + verdict.noise) else "     —"
        print(
            f"{verdict.id:<16} {verdict.good:>5} {verdict.noise:>6} "
            f"{len(verdict.unlabelled):>3}  {colour}{bar}{OFF}"
        )
        if show or verdict.noise:
            for hit in verdict.hits:
                repository = hit.source_repository or "(unattributed)"
                print(f"      {GREY}{hit.score:.3f}  {repository[:36]:<38}"
                      f"{hit.title[:38]}{OFF}")

    judged = sum(v.good + v.noise for v in verdicts)
    good = sum(v.good for v in verdicts)
    unlabelled = sum(len(v.unlabelled) for v in verdicts)
    total = len(verdicts) * top_k
    print("─" * 52)
    print(
        f"{BOLD}precision@{top_k}: {good}/{judged} judged hits good "
        f"({good / judged:.0%})" if judged else "nothing judged"
    )
    print(f"{OFF}{unlabelled}/{total} hits unlabelled — your judgement goes here")

    if unlabelled:
        counts: dict[str, int] = {}
        for verdict in verdicts:
            for repository in verdict.unlabelled:
                counts[repository] = counts.get(repository, 0) + 1
        print(f"\n{BOLD}most frequent unlabelled sources{OFF} "
              f"{GREY}(add to good/noise in tests/eval_queries.yaml){OFF}")
        for repository, count in sorted(counts.items(), key=lambda kv: -kv[1])[:8]:
            print(f"  {count:>2}x  {repository}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="print every hit")
    parser.add_argument("--only", help="a single query id")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument(
        "--cap",
        type=int,
        action="append",
        help="per-repository cap to try; repeat to compare (default: whatever is configured)",
    )
    args = parser.parse_args()

    index = open_index()
    if isinstance(index, MissingIndex):
        print(f"{RED}no index installed{OFF}\n  {index.reason}", file=sys.stderr)
        print("\n  export CELLIMO_INDEX_DIR=/tmp/cellimo-index"
              "\n  cellimo index install --yes", file=sys.stderr)
        return 2

    spec = yaml.safe_load(QUERIES.read_text(encoding="utf-8"))
    if args.only:
        spec["queries"] = [q for q in spec["queries"] if q["id"] == args.only]
        if not spec["queries"]:
            print(f"{RED}no query with id {args.only!r}{OFF}", file=sys.stderr)
            return 2

    if args.cap:
        # Compare caps by patching the default the backends read.
        from cellimo.retrieval import diversify as D

        for cap in args.cap:
            original = D.DEFAULT_PER_REPOSITORY
            D.DEFAULT_PER_REPOSITORY = cap
            print(f"\n{BOLD}══ per_repository = {cap} ══{OFF}")
            report(evaluate(index, spec, args.top_k), show=args.show, top_k=args.top_k)
            D.DEFAULT_PER_REPOSITORY = original
        return 0

    report(evaluate(index, spec, args.top_k), show=args.show, top_k=args.top_k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
