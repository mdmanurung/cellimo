"""Narrowing a ranked result set to distinct, useful sources.

Semantic similarity alone ranks badly against this corpus, in two measurable
ways. Both are properties of the archive, not of the query:

*One repository floods the results.* For ``quality control filter cells genes
mitochondrial counts``, 13 of 25 candidates came from a single repository —
``dpeerlab/MitoEJ-paper-analysis``, a mitochondrial *copy-number* paper matched
on the word "mitochondrial". Nothing relevant was reachable in the top five.
Concentration is not uniform: the median top-repository share across ten varied
queries is about 0.22, so this rescues the catastrophic query rather than
lifting every one.

*Jupyter checkpoints are indexed as separate notebooks.* 79 of 2,845 notebook
ids are ``…_checkpoint`` variants and 8 are ``…_copy`` variants. They are **not**
byte-identical to their originals — a checkpoint is a slightly earlier save, so
``content_hash`` differs on every pair examined — which means content comparison
cannot find them and the id suffix must.

Applied before a backend trims to ``top_k``, so dropping a candidate promotes
the next distinct one rather than shortening the answer. That requires the
backend to have over-fetched; see ``chroma_index.OVER_FETCH``.
"""

from __future__ import annotations

import re

from cellimo.retrieval.models import SearchHit

__all__ = ["DEFAULT_PER_REPOSITORY", "base_notebook_id", "diversify"]

#: How many hits one repository may contribute. Two rather than one: a
#: repository that genuinely covers a topic well — scverse/scanpy-tutorials for
#: quality control — should be able to offer an alternative, and capping at one
#: discards a good second answer to make room for a worse first one.
DEFAULT_PER_REPOSITORY = 2

#: Trailing markers Jupyter and file managers add to a copy of a notebook.
_DUPLICATE_SUFFIX = re.compile(r"(?:[-_](?:checkpoint|copy)\d*)+$", re.IGNORECASE)


def base_notebook_id(reference_id: str) -> str:
    """The identity a notebook shares with its checkpoints and copies.

    ``notebook:x_processing_to_adata_checkpoint`` and
    ``notebook:x_processing_to_adata`` collapse to the same value.
    """
    return _DUPLICATE_SUFFIX.sub("", reference_id.strip()).lower()


def diversify(
    hits: list[SearchHit],
    *,
    top_k: int,
    per_repository: int = DEFAULT_PER_REPOSITORY,
) -> tuple[list[SearchHit], str]:
    """Trim ``hits`` to ``top_k``, dropping duplicates and repository floods.

    Returns the kept hits and a note describing what was removed. The note is
    never empty when something was dropped: a filter that silently discards
    results is worse than one that admits what it did.

    Order is preserved. Nothing is re-scored — this only decides which of the
    already-ranked candidates survive.
    """
    if top_k <= 0:
        return [], ""

    kept: list[SearchHit] = []
    seen_notebooks: set[str] = set()
    per_repo: dict[str, int] = {}
    duplicates = 0
    crowded = 0

    for hit in hits:
        identity = base_notebook_id(hit.reference_id)
        if identity in seen_notebooks:
            duplicates += 1
            continue
        repository = hit.source_repository or ""
        # An unattributed hit is not evidence that two hits share a source, so
        # a blank repository is never treated as a crowd.
        if repository and per_repo.get(repository, 0) >= per_repository:
            crowded += 1
            continue
        seen_notebooks.add(identity)
        if repository:
            per_repo[repository] = per_repo.get(repository, 0) + 1
        kept.append(hit)
        if len(kept) == top_k:
            break

    parts = []
    if duplicates:
        parts.append(f"{duplicates} checkpoint/copy duplicate(s)")
    if crowded:
        parts.append(f"{crowded} hit(s) beyond {per_repository} per repository")
    note = f"filtered: {', '.join(parts)}" if parts else ""
    return kept, note
