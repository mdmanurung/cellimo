"""The ChromaDB backend — the one that serves the real published index.

The lexical backend carries the rest of the suite because it is fast and has no
heavy dependencies, which left the backend that actually runs in production
untested. These tests build a small index in ChromaDB's own format and drive the
real reader against it.

Only the summary-index path is exercised: it uses ChromaDB's bundled default
embedding function, so this needs `chromadb` but not `sentence-transformers`
(and therefore not Torch). The chunk-search path, which does need the
sentence-transformers model KAI built the main index with, is exercised through
`get_reference` on a chunk rather than by re-embedding a query.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cellimo.errors import ReferenceNotFoundError
from cellimo.retrieval.base import detect_backend, open_index
from cellimo.retrieval.chroma_index import EMBEDDING_MODEL, SUMMARY_COLLECTION

chromadb = pytest.importorskip("chromadb", reason="needs the retrieval extra")

pytestmark = [pytest.mark.slow, pytest.mark.needs_retrieval]

NOTEBOOK_ID = "scverse_scanpy_pbmc3k_qc"
REPOSITORY = "scverse/scanpy"


@pytest.fixture(scope="module")
def chroma_index(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A KAI-shaped index: a summary collection plus a filesystem notebook store."""
    root = tmp_path_factory.mktemp("chroma-index")

    summaries = root / "notebook_summaries"
    (summaries / "notebooks" / "scverse" / "scanpy").mkdir(parents=True)
    (summaries / "summaries" / "scverse" / "scanpy").mkdir(parents=True)

    (summaries / "notebooks" / "scverse" / "scanpy" / f"{NOTEBOOK_ID}.json").write_text(
        json.dumps(
            {
                # The real archive nests provenance under "metadata" and gives the
                # payload exactly four top-level keys. The fixture used to put
                # these at the top level — a shape no published index has — which
                # is why every reference came back with a blank repository and
                # title for months without a test noticing.
                "notebook_id": NOTEBOOK_ID,
                "cell_count": 2,
                "metadata": {
                    "title": "PBMC3k quality control",
                    "source_repository": REPOSITORY,
                    "workflow_filename": "pbmc3k.ipynb",
                    "source_path": f"{REPOSITORY}/docs/tutorials/pbmc3k.ipynb",
                },
                "cells": [
                    {
                        "cell_type": "markdown",
                        "section": "Quality control",
                        "content": "Filter cells by gene count and mitochondrial fraction.",
                        "order": 0,
                    },
                    {
                        "cell_type": "code",
                        "section": "Quality control",
                        "content": "sc.pp.filter_cells(adata, min_genes=200)",
                        "order": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (summaries / "summaries" / "scverse" / "scanpy" / f"{NOTEBOOK_ID}.txt").write_text(
        "Quality control of PBMC3k: filtering cells and genes before normalisation.",
        encoding="utf-8",
    )

    client = chromadb.PersistentClient(path=str(summaries / "summary_index"))
    collection = client.get_or_create_collection(
        name=SUMMARY_COLLECTION, metadata={"hnsw:space": "cosine"}
    )
    collection.add(
        ids=[NOTEBOOK_ID],
        documents=[
            "Quality control of PBMC3k: filter cells by detected genes and "
            "mitochondrial fraction, then filter genes."
        ],
        metadatas=[
            {
                "notebook_id": NOTEBOOK_ID,
                "full_notebook_id": f"{REPOSITORY}/docs/tutorials/pbmc3k.ipynb",
                "source_repository": REPOSITORY,
                "title": "PBMC3k quality control",
                "summary_length": 74,
            }
        ],
    )

    (root / "collection_registry.json").write_text(
        json.dumps({"scverse_scanpy_workflows": {"document_count": 42}}), encoding="utf-8"
    )
    return root


def test_the_backend_is_detected(chroma_index: Path) -> None:
    assert detect_backend(chroma_index) == "chroma"


def test_nested_archive_layout_is_detected(tmp_path: Path) -> None:
    """The published archive wraps everything one level deeper."""
    nested = tmp_path / "installed"
    (nested / "retrieval" / "notebook_summaries").mkdir(parents=True)
    assert detect_backend(nested) == "chroma-nested"


def test_open_index_returns_the_chroma_backend(chroma_index: Path) -> None:
    index = open_index(chroma_index)
    assert index.backend == "chroma"


def test_status_reports_the_embedding_model_and_notebooks(chroma_index: Path) -> None:
    status = open_index(chroma_index).status()
    assert status.installed
    assert status.backend == "chroma"
    assert status.notebooks == 1
    assert status.embedding_model == EMBEDDING_MODEL
    assert status.documents == 42  # from collection_registry.json
    # The published index has no documentation collections; say so.
    assert any("search_documentation" in item for item in status.unavailable)


def test_search_workflows_returns_a_stable_notebook_reference(chroma_index: Path) -> None:
    result = open_index(chroma_index).search_workflows("mitochondrial quality control")
    assert result.hits
    hit = result.hits[0]
    assert hit.reference_id == f"notebook:{NOTEBOOK_ID}"
    assert hit.source_repository == REPOSITORY
    assert hit.title == "PBMC3k quality control"
    assert 0.0 <= hit.score <= 1.0
    assert hit.url.startswith("https://github.com/scverse/scanpy")


def test_search_documentation_is_empty_with_an_explanation(chroma_index: Path) -> None:
    result = open_index(chroma_index).search_documentation("normalize counts")
    assert result.hits == []
    assert "workflows only" in result.note


def test_get_reference_reads_the_notebook_store(chroma_index: Path) -> None:
    reference = open_index(chroma_index).get_reference(f"notebook:{NOTEBOOK_ID}")
    assert [section.section_id for section in reference.sections] == ["0", "1"]
    assert "filter_cells" in reference.sections[1].content
    assert reference.summary.startswith("Quality control of PBMC3k")
    assert reference.content_hash
    assert "licences" in reference.note


def test_get_reference_carries_the_provenance_a_citation_needs(
    chroma_index: Path,
) -> None:
    """Repository, title and URL come from the payload's nested metadata block.

    Reading these off the top level instead — where the fixture used to put them
    and no published archive ever does — returned a blank repository and the
    notebook id as the title for all 2,845 notebooks in the real index. Nothing
    caught it, because no test asserted on these fields at all.
    """
    reference = open_index(chroma_index).get_reference(f"notebook:{NOTEBOOK_ID}")
    assert reference.source_repository == REPOSITORY
    assert reference.title == "PBMC3k quality control"
    assert reference.title != NOTEBOOK_ID, "the id is a fallback, not a title"
    assert reference.organization == REPOSITORY.split("/", maxsplit=1)[0]
    assert REPOSITORY in reference.url


def test_get_reference_attaches_the_citation_header(chroma_index: Path) -> None:
    """The production backend must emit headers, not only the lexical one.

    This was committed without the chroma half and nothing noticed, because no
    chroma test asserted on the header. The lexical backend and a stub carried
    the citation tests, so the backend that actually serves the published index
    was the one left unverified.
    """
    from cellimo.retrieval.citations import parse

    reference = open_index(chroma_index).get_reference(f"notebook:{NOTEBOOK_ID}")
    code = next(s for s in reference.sections if s.kind == "code")
    (citation,) = parse(code.content)
    assert citation.reference_id == f"notebook:{NOTEBOOK_ID}"
    assert citation.section_id == code.section_id
    assert code.content.rstrip().endswith("sc.pp.filter_cells(adata, min_genes=200)")

    raw = open_index(chroma_index).get_reference(
        f"notebook:{NOTEBOOK_ID}", with_provenance=False
    )
    assert parse(next(s for s in raw.sections if s.kind == "code").content) == []


def test_get_reference_selects_sections(chroma_index: Path) -> None:
    reference = open_index(chroma_index).get_reference(f"notebook:{NOTEBOOK_ID}", ["1"])
    assert [section.section_id for section in reference.sections] == ["1"]


def test_get_reference_rejects_an_unknown_notebook(chroma_index: Path) -> None:
    with pytest.raises(ReferenceNotFoundError, match="not in the store"):
        open_index(chroma_index).get_reference("notebook:does_not_exist")


def test_get_reference_reports_available_sections(chroma_index: Path) -> None:
    with pytest.raises(ReferenceNotFoundError, match="available"):
        open_index(chroma_index).get_reference(f"notebook:{NOTEBOOK_ID}", ["99"])


def test_notebook_paths_are_mapped_once_at_construction(
    chroma_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-lookup globbing over 3,000 notebooks would be paid on every call."""
    index = open_index(chroma_index)
    calls: list[Any] = []
    original = Path.rglob

    def counting_rglob(self: Path, pattern: str):  # type: ignore[no-untyped-def]
        calls.append((self, pattern))
        return original(self, pattern)

    monkeypatch.setattr(Path, "rglob", counting_rglob)
    index.get_reference(f"notebook:{NOTEBOOK_ID}")
    index.search_workflows("quality control")
    # Only the summary-text lookup may glob, and only when the repository-derived
    # path misses — which it does not here.
    assert not [call for call in calls if call[1] == "*.json"]


def test_the_mcp_server_serves_the_chroma_backend(chroma_index: Path) -> None:
    import asyncio

    from mcp.client import Client

    from cellimo.mcp.server import build_server

    server = build_server(index_path=str(chroma_index))

    async def _drive() -> Any:
        async with Client(server) as client:
            status = (await client.call_tool("index_status", {})).structured_content
            hits = (
                await client.call_tool("search_workflows", {"query": "quality control"})
            ).structured_content
            reference = (
                await client.call_tool(
                    "get_reference", {"reference_id": f"notebook:{NOTEBOOK_ID}"}
                )
            ).structured_content
            return status, hits, reference

    status, hits, reference = asyncio.run(_drive())
    assert status["backend"] == "chroma"
    assert hits["hits"][0]["reference_id"] == f"notebook:{NOTEBOOK_ID}"
    assert reference["sections"]
