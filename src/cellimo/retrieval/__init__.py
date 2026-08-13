"""Read-only retrieval over the inherited KAI knowledge index.

    user or agent query
        -> semantic and/or lexical search
        -> a few cited, design-checked source sections
        -> Codex or Claude adapts and applies them

There is no model in this loop. Cellimo ranks and returns; the agent decides.
"""

from __future__ import annotations

from cellimo.retrieval.base import KnowledgeIndex, MissingIndex, detect_backend, open_index
from cellimo.retrieval.grounding import (
    GroundedCode,
    GroundingDesign,
    GroundingFinding,
    GroundingResult,
    design_from_project,
    ground,
)
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
    "GroundedCode",
    "GroundingDesign",
    "GroundingFinding",
    "GroundingResult",
    "IndexStatus",
    "KnowledgeIndex",
    "MissingIndex",
    "Reference",
    "ReferenceSection",
    "SearchHit",
    "SearchResult",
    "chunk_reference_id",
    "design_from_project",
    "detect_backend",
    "ground",
    "notebook_reference_id",
    "open_index",
    "parse_reference_id",
]
