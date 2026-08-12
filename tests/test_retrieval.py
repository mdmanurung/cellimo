"""Retrieval: stable identifiers, backend selection and the lexical index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cellimo.errors import IndexNotFoundError, ReferenceNotFoundError
from cellimo.retrieval.base import MissingIndex, detect_backend, open_index
from cellimo.retrieval.ids import (
    chunk_reference_id,
    notebook_reference_id,
    parse_reference_id,
)
from cellimo.retrieval.lexical_index import LexicalKnowledgeIndex, tokenize

# -- identifiers -----------------------------------------------------------


def test_notebook_ids_round_trip() -> None:
    identifier = notebook_reference_id("scverse_scanpy_pbmc3k_qc")
    parsed = parse_reference_id(identifier)
    assert parsed.kind == "notebook"
    assert parsed.identifier == "scverse_scanpy_pbmc3k_qc"


def test_chunk_ids_round_trip() -> None:
    identifier = chunk_reference_id("scverse_scanpy_workflows", "chunk_42_0")
    parsed = parse_reference_id(identifier)
    assert parsed.kind == "chunk"
    assert parsed.collection == "scverse_scanpy_workflows"
    assert parsed.identifier == "chunk_42_0"


@pytest.mark.parametrize("bad", ["", "no-colon", "unknown:thing", "chunk:only-one-part"])
def test_malformed_ids_are_rejected(bad: str) -> None:
    with pytest.raises(ReferenceNotFoundError):
        parse_reference_id(bad)


def test_ids_do_not_depend_on_result_order() -> None:
    # The same notebook always gets the same id, whatever rank it came back at.
    assert notebook_reference_id("abc") == notebook_reference_id("abc")


def test_tokenizer_keeps_identifiers_whole() -> None:
    assert "rank_genes_groups" in tokenize("call sc.tl.rank_genes_groups now")


# -- backend selection -----------------------------------------------------


def test_missing_directory_yields_a_missing_index(tmp_path: Path) -> None:
    index = open_index(tmp_path / "absent")
    assert isinstance(index, MissingIndex)
    status = index.status()
    assert not status.installed
    assert "cellimo index install" in status.note


def test_an_unrecognised_directory_is_reported_honestly(tmp_path: Path) -> None:
    (tmp_path / "random.txt").write_text("hello", encoding="utf-8")
    status = open_index(tmp_path).status()
    assert not status.installed
    assert "does not look like a Cellimo index" in status.note


def test_missing_index_search_returns_an_explanation(tmp_path: Path) -> None:
    index = open_index(tmp_path / "absent")
    result = index.search_workflows("quality control")
    assert result.hits == []
    assert result.note
    with pytest.raises(IndexNotFoundError):
        index.get_reference("notebook:whatever")


def test_backend_detection(tmp_path: Path, fixture_index: Path) -> None:
    assert detect_backend(fixture_index) == "lexical"
    nested = tmp_path / "kai"
    (nested / "retrieval" / "chromadb").mkdir(parents=True)
    assert detect_backend(nested) == "chroma-nested"
    flat = tmp_path / "flat"
    (flat / "notebook_summaries").mkdir(parents=True)
    assert detect_backend(flat) == "chroma"


# -- the lexical backend ---------------------------------------------------


def test_search_workflows_ranks_the_relevant_notebook_first(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    result = index.search_workflows("mitochondrial fraction quality control", top_k=5)
    assert result.hits
    assert result.hits[0].reference_id == "notebook:scverse_scanpy_pbmc3k_qc"
    assert result.hits[0].score > 0


def test_search_is_deterministic(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    first = index.search_workflows("pseudobulk donors", top_k=5)
    second = index.search_workflows("pseudobulk donors", top_k=5)
    assert [hit.reference_id for hit in first.hits] == [
        hit.reference_id for hit in second.hits
    ]


def test_package_filter_is_exact_and_declared(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    result = index.search_workflows("quality control", packages=["scanpy"])
    assert "packages" in result.exact_filters
    assert all(hit.package == "scanpy" for hit in result.hits)


def test_modality_filter_is_declared_approximate(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    result = index.search_workflows("quality control", modalities=["rna"])
    assert "modalities" in result.approximate_filters
    assert "best-effort" in result.note


def test_top_k_is_respected(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    assert len(index.search_workflows("cells genes counts", top_k=1).hits) <= 1


def test_search_documentation_finds_api_sections(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    result = index.search_documentation("normalize counts per cell")
    assert result.hits
    assert result.hits[0].reference_id.startswith("chunk:scanpy_api:")


def test_get_reference_returns_exact_sections(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    reference = index.get_reference("notebook:scverse_scanpy_pbmc3k_qc", ["1"])
    assert [section.section_id for section in reference.sections] == ["1"]
    assert "filter_cells" in reference.sections[0].content
    assert reference.content_hash


def test_get_reference_hash_covers_only_requested_sections(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    whole = index.get_reference("notebook:scverse_scanpy_pbmc3k_qc")
    part = index.get_reference("notebook:scverse_scanpy_pbmc3k_qc", ["1"])
    assert whole.content_hash != part.content_hash


def test_get_reference_rejects_an_unknown_id(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    with pytest.raises(ReferenceNotFoundError):
        index.get_reference("notebook:does_not_exist")


def test_get_reference_reports_available_sections(fixture_index: Path) -> None:
    index = LexicalKnowledgeIndex(fixture_index)
    with pytest.raises(ReferenceNotFoundError, match="available"):
        index.get_reference("notebook:scverse_scanpy_pbmc3k_qc", ["99"])


def test_a_broken_chroma_index_degrades_instead_of_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only or corrupt index must not take the MCP server down at startup."""
    root = tmp_path / "index"
    (root / "chromadb").mkdir(parents=True)

    def explode(path: Path) -> None:
        raise RuntimeError("attempt to write a readonly database")

    monkeypatch.setattr(
        "cellimo.retrieval.chroma_index.ChromaKnowledgeIndex", explode, raising=True
    )
    index = open_index(root)
    assert isinstance(index, MissingIndex)
    status = index.status()
    assert not status.installed
    assert "could not be opened" in status.note
    assert "writable" in status.note
    # And every tool still answers.
    assert index.search_workflows("anything").hits == []


def test_get_reference_bounds_a_huge_section(tmp_path: Path) -> None:
    """A multi-megabyte notebook cell must not be returned whole."""
    from cellimo.retrieval.models import MAX_SECTION_CHARS

    root = tmp_path / "big-index"
    root.mkdir()
    payload = {
        "meta": {"version": "1"},
        "workflows": [
            {
                "notebook_id": "huge_nb",
                "title": "Huge",
                "summary": "a notebook with an enormous cell",
                "source_repository": "demo/huge",
                "package": "scanpy",
                "sections": [
                    {"section_id": "0", "content": "x" * (MAX_SECTION_CHARS * 3)}
                ],
            }
        ],
        "documentation": [],
    }
    (root / "cellimo-index.json").write_text(json.dumps(payload), encoding="utf-8")

    reference = LexicalKnowledgeIndex(root).get_reference("notebook:huge_nb")
    section = reference.sections[0]
    assert len(section.content) <= MAX_SECTION_CHARS
    assert section.truncated
    assert section.omitted_chars == MAX_SECTION_CHARS * 2
    # The truncation is declared, not silent.
    assert "omitted" in reference.note
    assert "section_ids" in reference.note


def test_bound_sections_keeps_small_content_untouched() -> None:
    from cellimo.retrieval.models import ReferenceSection, bound_sections

    sections = [ReferenceSection(section_id="0", content="small")]
    bounded, omitted = bound_sections(sections)
    assert bounded == sections
    assert omitted == 0
    assert not bounded[0].truncated


def test_bound_sections_respects_the_whole_reference_budget() -> None:
    from cellimo.retrieval.models import (
        MAX_REFERENCE_CHARS,
        MAX_SECTION_CHARS,
        ReferenceSection,
        bound_sections,
    )

    sections = [
        ReferenceSection(section_id=str(index), content="y" * MAX_SECTION_CHARS)
        for index in range(10)
    ]
    bounded, omitted = bound_sections(sections)
    total = sum(len(section.content) for section in bounded)
    assert total <= MAX_REFERENCE_CHARS
    assert omitted > 0
    # Every section is still present, in order, so the caller can see what was cut.
    assert [section.section_id for section in bounded] == [str(i) for i in range(10)]
    assert any(section.truncated for section in bounded)


def test_content_hash_covers_what_was_actually_returned(tmp_path: Path) -> None:
    from cellimo.retrieval.models import MAX_SECTION_CHARS
    from cellimo.util.hashing import hash_bytes

    root = tmp_path / "index"
    root.mkdir()
    content = "z" * (MAX_SECTION_CHARS * 2)
    (root / "cellimo-index.json").write_text(
        json.dumps(
            {
                "meta": {"version": "1"},
                "workflows": [
                    {
                        "notebook_id": "nb",
                        "title": "t",
                        "summary": "s",
                        "sections": [{"section_id": "0", "content": content}],
                    }
                ],
                "documentation": [],
            }
        ),
        encoding="utf-8",
    )
    reference = LexicalKnowledgeIndex(root).get_reference("notebook:nb")
    returned = "\n\n".join(section.content for section in reference.sections)
    assert reference.content_hash == hash_bytes(returned.encode("utf-8"))
    assert reference.content_hash != hash_bytes(content.encode("utf-8"))


def test_an_unreadable_index_directory_degrades_instead_of_crashing(tmp_path: Path) -> None:
    """`is_file()` re-raises PermissionError; that must not kill `cellimo mcp serve`."""
    import os

    if os.geteuid() == 0:  # pragma: no cover - root ignores mode bits
        pytest.skip("root can read anything")
    root = tmp_path / "unreadable"
    root.mkdir()
    (root / "cellimo-index.json").write_text("{}", encoding="utf-8")
    root.chmod(0o000)
    try:
        index = open_index(root)
        assert isinstance(index, MissingIndex)
        assert "could not be read" in index.status().note
        assert index.search_workflows("anything").hits == []
    finally:
        root.chmod(0o755)


def test_get_reference_bounds_the_number_of_sections(tmp_path: Path) -> None:
    """Character budgets alone still let 100,000 placeholder objects through."""
    from cellimo.retrieval.models import MAX_SECTIONS

    root = tmp_path / "many-sections"
    root.mkdir()
    (root / "cellimo-index.json").write_text(
        json.dumps(
            {
                "meta": {"version": "1"},
                "workflows": [
                    {
                        "notebook_id": "many_nb",
                        "title": "Many",
                        "summary": "a notebook with a great many cells",
                        "sections": [
                            {"section_id": str(i), "content": "x" * 10}
                            for i in range(5_000)
                        ],
                    }
                ],
                "documentation": [],
            }
        ),
        encoding="utf-8",
    )
    reference = LexicalKnowledgeIndex(root).get_reference("notebook:many_nb")
    assert len(reference.sections) <= MAX_SECTIONS
    assert "omitted" in reference.note


def test_a_huge_summary_is_bounded(tmp_path: Path) -> None:
    """The lexical backend had no cap on summary at all; chroma always did."""
    from cellimo.retrieval.models import MAX_SUMMARY_CHARS

    root = tmp_path / "huge-summary"
    root.mkdir()
    (root / "cellimo-index.json").write_text(
        json.dumps(
            {
                "meta": {"version": "1"},
                "workflows": [
                    {
                        "notebook_id": "sum_nb",
                        "title": "Summary",
                        "summary": "s" * (MAX_SUMMARY_CHARS * 5),
                        "sections": [{"section_id": "0", "content": "tiny"}],
                    }
                ],
                "documentation": [],
            }
        ),
        encoding="utf-8",
    )
    index = LexicalKnowledgeIndex(root)
    reference = index.get_reference("notebook:sum_nb")
    assert len(reference.summary) < MAX_SUMMARY_CHARS * 2
    hits = index.search_workflows("summary").hits
    assert hits and len(hits[0].summary) < MAX_SUMMARY_CHARS * 2


def test_a_non_integer_section_order_does_not_raise(tmp_path: Path) -> None:
    """Index data is third-party; a bad `order` must degrade, not crash."""
    root = tmp_path / "bad-order"
    root.mkdir()
    (root / "cellimo-index.json").write_text(
        json.dumps(
            {
                "meta": {"version": "1"},
                "workflows": [
                    {
                        "notebook_id": "bad_nb",
                        "title": "Bad order",
                        "summary": "a notebook whose order field is nonsense",
                        "sections": [
                            {"section_id": "0", "content": "a", "order": "not a number"},
                            {"section_id": "1", "content": "b", "order": None},
                        ],
                    }
                ],
                "documentation": [],
            }
        ),
        encoding="utf-8",
    )
    reference = LexicalKnowledgeIndex(root).get_reference("notebook:bad_nb")
    assert [section.order for section in reference.sections] == [0, 1]


def test_status_reports_what_is_indexed(fixture_index: Path) -> None:
    status = LexicalKnowledgeIndex(fixture_index).status()
    assert status.installed
    assert status.backend == "lexical"
    assert status.notebooks == 2
    assert status.documents == 3
    assert status.embedding_model.startswith("none")


def test_status_declares_missing_documentation(tmp_path: Path) -> None:
    root = tmp_path / "index"
    root.mkdir()
    (root / "cellimo-index.json").write_text(
        json.dumps({"meta": {"version": "1"}, "workflows": [], "documentation": []}),
        encoding="utf-8",
    )
    status = LexicalKnowledgeIndex(root).status()
    assert any("search_documentation" in item for item in status.unavailable)


def test_index_is_loaded_once_not_per_query(fixture_index: Path, monkeypatch) -> None:
    """Opening is expensive; querying must not reopen the file."""
    index = LexicalKnowledgeIndex(fixture_index)
    reads = {"count": 0}
    original = Path.read_text

    def counting_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "cellimo-index.json":
            reads["count"] += 1
        return original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    index.search_workflows("quality control")
    index.search_documentation("normalize")
    index.status()
    assert reads["count"] == 0
