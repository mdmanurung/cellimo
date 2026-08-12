"""Types returned by retrieval, and by the MCP tools that wrap it.

These are the shapes the agent sees. Every hit carries a stable
``reference_id`` that can be passed straight back to ``get_reference`` and
recorded in ``provenance/references.jsonl``, so a claim in the notebook can be
traced to the exact section that motivated it.

Portions of the field vocabulary derive from KAI
(https://github.com/davidfischerlab/kai), Apache-2.0. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MAX_REFERENCE_CHARS",
    "MAX_SECTION_CHARS",
    "IndexStatus",
    "Reference",
    "ReferenceSection",
    "SearchHit",
    "SearchResult",
    "bound_sections",
]


class SearchHit(BaseModel):
    """One ranked search result."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Stable, resolvable identifier — ``notebook:<id>`` or ``chunk:<coll>:<id>``.
    reference_id: str
    title: str = ""
    #: Compact excerpt or summary. Never the whole notebook.
    summary: str = ""
    source_repository: str = ""
    source_path: str = ""
    url: str = ""
    package: str = ""
    package_version: str = ""
    organization: str = ""
    #: Higher is more relevant. Derived from cosine distance for vector
    #: backends and from token overlap for the lexical backend; comparable
    #: within one result set, not across backends.
    score: float = 0.0
    #: Section identifiers that ``get_reference`` accepts for this reference.
    section_ids: list[str] = Field(default_factory=list)
    content_hash: str = ""
    chunk_level: str = ""


class SearchResult(BaseModel):
    """A ranked list plus the honest story of how it was produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    backend: str = ""
    #: Filters that were applied exactly, versus approximated. The KAI index has
    #: no modality field and an unreliable package field, so saying which
    #: filtering was best-effort is the difference between a usable result and a
    #: misleading one.
    exact_filters: list[str] = Field(default_factory=list)
    approximate_filters: list[str] = Field(default_factory=list)
    note: str = ""
    total_candidates: int = 0


#: Per-section and whole-reference character budgets. Published notebooks
#: contain multi-megabyte cells; returning one whole would fill an agent's
#: context with a single tool result. Truncation is always declared.
MAX_SECTION_CHARS = 20_000
MAX_REFERENCE_CHARS = 60_000
#: Cap on how many section objects come back. Bounding characters alone still
#: let a 100,000-cell notebook return 100,000 empty placeholder objects — tens
#: of megabytes of JSON structure with no content in it.
MAX_SECTIONS = 200
#: Summaries are a preview, not the material. Both backends cap them.
MAX_SUMMARY_CHARS = 4_000


class ReferenceSection(BaseModel):
    """One addressable part of a reference — a notebook cell or a doc section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section_id: str
    kind: str = "code"  # code | markdown | text
    heading: str = ""
    content: str = ""
    order: int = 0
    #: True when ``content`` was cut short. Never silently: an agent that sees
    #: this knows to ask for a narrower ``section_ids`` list.
    truncated: bool = False
    omitted_chars: int = 0


def bound_sections(
    sections: list[ReferenceSection],
    *,
    per_section: int = MAX_SECTION_CHARS,
    total: int = MAX_REFERENCE_CHARS,
    max_sections: int = MAX_SECTIONS,
) -> tuple[list[ReferenceSection], int]:
    """Trim sections to a readable budget. Returns ``(sections, omitted_chars)``.

    Three bounds, because any one of them alone can be defeated: characters per
    section, characters overall, and *number of sections* — a notebook with
    100,000 cells would otherwise return 100,000 objects whose content is empty
    but whose structure is not.

    Sections are kept in order and trimmed individually, so a reference always
    begins where the caller asked it to rather than stopping part-way through
    with no explanation.
    """
    bounded: list[ReferenceSection] = []
    remaining = total
    omitted = 0
    dropped = sections[max_sections:]
    if dropped:
        omitted += sum(len(section.content) for section in dropped)
    for section in sections[:max_sections]:
        if remaining <= 0:
            omitted += len(section.content)
            bounded.append(
                section.model_copy(
                    update={
                        "content": "",
                        "truncated": True,
                        "omitted_chars": len(section.content),
                    }
                )
            )
            continue
        budget = min(per_section, remaining)
        if len(section.content) <= budget:
            remaining -= len(section.content)
            bounded.append(section)
            continue
        cut = len(section.content) - budget
        omitted += cut
        # ``budget`` is ``min(per_section, remaining)``, so this reaches exactly
        # zero when the whole-reference budget is what bound this section.
        remaining -= budget
        bounded.append(
            section.model_copy(
                update={
                    "content": section.content[:budget],
                    "truncated": True,
                    "omitted_chars": cut,
                }
            )
        )
    return bounded, omitted


class Reference(BaseModel):
    """The exact source material behind a search hit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_id: str
    title: str = ""
    source_repository: str = ""
    source_path: str = ""
    url: str = ""
    package: str = ""
    package_version: str = ""
    organization: str = ""
    summary: str = ""
    sections: list[ReferenceSection] = Field(default_factory=list)
    #: SHA-256 of the returned content, so a provenance record can prove which
    #: version of a reference was read.
    content_hash: str = ""
    license: str = ""
    note: str = ""


class IndexStatus(BaseModel):
    """What is actually installed, in the words of the files on disk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    installed: bool = False
    backend: str = ""
    path: str = ""
    version: str = ""
    workflow_collections: int = 0
    documentation_collections: int = 0
    notebooks: int = 0
    documents: int = 0
    embedding_model: str = ""
    organizations: list[str] = Field(default_factory=list)
    #: Capabilities that are present in the code but have no data behind them in
    #: the installed index. Reported rather than silently returning nothing.
    unavailable: list[str] = Field(default_factory=list)
    note: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
