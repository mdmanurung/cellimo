"""Citation headers: emitting them, reading them back, and checking them.

No index required for most of this — parsing is a pure function over text, which
is the point. Resolution is exercised against a stub that answers like a
backend, so the four outcomes can be produced deliberately rather than hoped for.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from cellimo.errors import ReferenceNotFoundError
from cellimo.retrieval import citations as C
from cellimo.retrieval.models import Reference, ReferenceSection

CODE = "sc.pp.filter_cells(adata, min_genes=200)"


def _reference(reference_id: str = "notebook:x", content: str = CODE) -> Reference:
    return Reference(
        reference_id=reference_id,
        sections=[
            ReferenceSection(section_id="0", kind="markdown", content="Prose."),
            ReferenceSection(section_id="1", kind="code", content=content),
        ],
    )


class _StubIndex:
    """Answers `get_reference` like a backend, and records how often it was asked."""

    backend = "stub"

    def __init__(self, reference: Reference | None) -> None:
        self._reference = reference
        self.calls = 0

    def get_reference(
        self,
        reference_id: str,
        section_ids: Sequence[str] | None = None,
        *,
        with_provenance: bool = True,
    ) -> Reference:
        self.calls += 1
        if self._reference is None:
            raise ReferenceNotFoundError(f"{reference_id} is not here")
        return (
            C.attach_headers(self._reference) if with_provenance else self._reference
        )


# -- the header ------------------------------------------------------------


def test_a_code_section_comes_back_carrying_its_origin() -> None:
    attached = C.attach_headers(_reference())
    code = attached.sections[1].content
    assert code.startswith("# cellimo:source notebook:x section=1 sha=")
    assert code.endswith(CODE), "the code itself must be untouched"


def test_prose_is_not_annotated() -> None:
    """A citation belongs on code the agent adapts, not on the narration."""
    attached = C.attach_headers(_reference())
    assert attached.sections[0].content == "Prose."


def test_the_sha_covers_the_section_alone_not_what_was_requested_with_it() -> None:
    """The bug this format exists to avoid.

    Hashing the concatenation of everything returned would make one cell hash
    differently depending on the call that produced it — a guarantee that looks
    real and is not.
    """
    alone = Reference(
        reference_id="notebook:x",
        sections=[ReferenceSection(section_id="1", kind="code", content=CODE)],
    )
    together = _reference()  # same section, alongside a markdown one
    assert (
        C.parse(C.attach_headers(alone).sections[0].content)[0].sha
        == C.parse(C.attach_headers(together).sections[1].content)[0].sha
    )


# -- parsing ---------------------------------------------------------------


def test_headers_are_found_with_their_line_numbers() -> None:
    source = "\n".join(
        [
            "import scanpy as sc",
            "",
            "# cellimo:source notebook:a section=3 sha=abc123def456",
            "sc.pp.normalize_total(adata)",
            "    # cellimo:source notebook:b section=12 sha=0badc0ffee11",
            "    sc.pp.log1p(adata)",
        ]
    )
    found = C.parse(source)
    assert [(c.reference_id, c.section_id, c.line) for c in found] == [
        ("notebook:a", "3", 3),
        ("notebook:b", "12", 5),
    ]


@pytest.mark.parametrize(
    "line",
    [
        "# cellimo:source notebook:a section=3",  # no sha
        "# cellimo:source notebook:a sha=abc123",  # no section
        "# cellimo source notebook:a section=3 sha=abc123",  # not the marker
        "sc.pp.log1p(adata)  # cellimo:source notebook:a section=3 sha=abc1",
    ],
)
def test_a_malformed_or_trailing_header_is_not_a_citation(line: str) -> None:
    """Better to see an uncited cell than to accept a half-parsed claim."""
    assert C.parse(line) == []


def test_a_notebook_with_no_headers_yields_nothing() -> None:
    assert C.parse("sc.pp.log1p(adata)\nsc.tl.leiden(adata)") == []


# -- resolution ------------------------------------------------------------


def test_a_citation_matching_its_source_resolves() -> None:
    index = _StubIndex(_reference())
    cited = C.parse(C.attach_headers(_reference()).sections[1].content)
    (status,) = C.resolve(cited, index)  # type: ignore[arg-type]
    assert status.state is C.CitationState.RESOLVED
    assert status.ok


def test_a_source_that_changed_since_adaptation_reports_drift() -> None:
    """Distinct from 'wrong': the source exists, it just no longer says this."""
    cited = C.parse(C.attach_headers(_reference()).sections[1].content)
    moved = _StubIndex(_reference(content="sc.pp.filter_cells(adata, min_genes=500)"))
    (status,) = C.resolve(cited, moved)  # type: ignore[arg-type]
    assert status.state is C.CitationState.DRIFTED
    assert not status.ok
    assert "changed after this code was adapted" in status.detail


def test_a_citation_to_nothing_is_reported_not_raised() -> None:
    """One bad citation must not stop the rest of the notebook being checked."""
    cited = C.parse(C.attach_headers(_reference()).sections[1].content)
    (status,) = C.resolve(cited, _StubIndex(None))  # type: ignore[arg-type]
    assert status.state is C.CitationState.UNKNOWN_REFERENCE


def test_a_citation_to_a_section_that_does_not_exist() -> None:
    cited = [C.Citation(reference_id="notebook:x", section_id="99", sha="abc123abc123")]
    (status,) = C.resolve(cited, _StubIndex(_reference()))  # type: ignore[arg-type]
    assert status.state is C.CitationState.UNKNOWN_SECTION
    assert "99" in status.detail


def test_one_lookup_per_reference_however_many_cells_cite_it() -> None:
    """Eight cells from one published analysis should cost one lookup."""
    sha = C.section_sha(CODE)
    cited = [
        C.Citation(reference_id="notebook:x", section_id="1", sha=sha, line=n)
        for n in range(8)
    ]
    index = _StubIndex(_reference())
    statuses = C.resolve(cited, index)  # type: ignore[arg-type]
    assert all(status.ok for status in statuses)
    assert index.calls == 1
