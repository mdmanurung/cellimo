"""Narrowing a ranked result set — the properties that matter, without an index.

The behaviour these pin down was measured against the real archive: one
repository supplying 13 of 25 candidates for a quality-control query, and 79
checkpoint-suffixed notebooks that are near-duplicates of notebooks already in
the corpus.
"""

from __future__ import annotations

import pytest

from cellimo.retrieval.diversify import base_notebook_id, diversify
from cellimo.retrieval.models import SearchHit


def _hit(reference_id: str, repository: str, score: float = 0.5) -> SearchHit:
    return SearchHit(
        reference_id=reference_id, source_repository=repository, score=score
    )


@pytest.mark.parametrize(
    ("reference_id", "expected"),
    [
        ("notebook:x_processing_checkpoint", "notebook:x_processing"),
        ("notebook:x_processing-checkpoint", "notebook:x_processing"),
        ("notebook:x_processing_copy1", "notebook:x_processing"),
        ("notebook:x_processing_copy", "notebook:x_processing"),
        # Repeated markers: a copy of a checkpoint.
        ("notebook:x_processing_checkpoint_copy2", "notebook:x_processing"),
        # A notebook that is genuinely about checkpointing keeps its name.
        ("notebook:model_checkpointing_guide", "notebook:model_checkpointing_guide"),
    ],
)
def test_duplicate_suffixes_collapse_to_one_identity(
    reference_id: str, expected: str
) -> None:
    assert base_notebook_id(reference_id) == expected


def test_checkpoints_are_dropped_and_the_next_distinct_hit_is_promoted() -> None:
    """Dropping must promote, not shorten — which is why the caller over-fetches."""
    hits = [
        _hit("notebook:a", "org/one", 0.9),
        _hit("notebook:a_checkpoint", "org/one", 0.89),
        _hit("notebook:b", "org/two", 0.8),
        _hit("notebook:c", "org/three", 0.7),
    ]
    kept, note = diversify(hits, top_k=3)
    assert [hit.reference_id for hit in kept] == [
        "notebook:a",
        "notebook:b",
        "notebook:c",
    ]
    assert "duplicate" in note


def test_one_repository_cannot_take_every_slot() -> None:
    """The measured failure: 13 of 25 candidates from a single repository."""
    hits = [_hit(f"notebook:flood{i}", "org/flood", 0.9) for i in range(10)]
    hits += [_hit("notebook:relevant", "org/other", 0.5)]
    kept, note = diversify(hits, top_k=5, per_repository=2)
    assert [hit.source_repository for hit in kept] == ["org/flood"] * 2 + ["org/other"]
    assert "org/other" in {hit.source_repository for hit in kept}
    assert "per repository" in note


def test_nothing_is_dropped_silently() -> None:
    hits = [_hit(f"notebook:n{i}", "org/one") for i in range(6)]
    kept, note = diversify(hits, top_k=6, per_repository=2)
    assert len(kept) == 2
    assert note, "dropping four hits without saying so is worse than not filtering"


def test_an_unattributed_hit_is_not_treated_as_a_crowd() -> None:
    """A blank repository is missing evidence, not evidence of a shared source."""
    hits = [_hit(f"notebook:n{i}", "") for i in range(4)]
    kept, note = diversify(hits, top_k=4, per_repository=1)
    assert len(kept) == 4
    assert note == ""


def test_a_clean_result_set_is_returned_unchanged() -> None:
    hits = [_hit(f"notebook:n{i}", f"org/{i}") for i in range(4)]
    kept, note = diversify(hits, top_k=4)
    assert kept == hits
    assert note == ""


def test_ranking_order_is_never_rearranged() -> None:
    """This decides which candidates survive; it does not re-score them."""
    hits = [
        _hit("notebook:a", "org/one", 0.9),
        _hit("notebook:b", "org/two", 0.5),
        _hit("notebook:c", "org/three", 0.1),
    ]
    kept, _ = diversify(hits, top_k=3)
    assert [hit.score for hit in kept] == [0.9, 0.5, 0.1]
