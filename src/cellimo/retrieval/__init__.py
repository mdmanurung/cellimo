"""Read-only retrieval over the inherited KAI knowledge index.

    user or agent query
        -> semantic and/or lexical search
        -> ranked summaries and exact source sections
        -> Codex or Claude selects and applies them

There is no model in this loop. Cellimo ranks and returns; the agent decides.
"""

from __future__ import annotations

from cellimo.retrieval.base import KnowledgeIndex, MissingIndex, detect_backend, open_index
from cellimo.retrieval.ids import (
    chunk_reference_id,
    notebook_reference_id,
    parse_reference_id,
)
from cellimo.retrieval.models import (
    IndexStatus,
    Reference,
    ReferenceSection,
    SearchHit,
    SearchResult,
)

__all__ = [
    "IndexStatus",
    "KnowledgeIndex",
    "MissingIndex",
    "Reference",
    "ReferenceSection",
    "SearchHit",
    "SearchResult",
    "chunk_reference_id",
    "detect_backend",
    "notebook_reference_id",
    "open_index",
    "parse_reference_id",
]
