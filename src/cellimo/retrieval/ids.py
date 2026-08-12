"""Stable reference identifiers.

A reference id must survive re-indexing, must not depend on the position of a
row in a query result, and must be resolvable back to the exact source. Two
namespaces satisfy that against the inherited KAI index:

``notebook:<notebook_id>``
    A whole indexed notebook. ``notebook_id`` is the filesystem key KAI derives
    from ``{org}_{repo}_{stem}``, lowercased with non-word characters replaced —
    the same string used by both the notebook store and the summary index, so
    the two agree.

``chunk:<collection>:<chroma_id>``
    One indexed chunk of a workflow. Resolvable with a direct ``get(ids=[…])``
    against the named collection.

Row indices, query offsets and result ranks are never part of an id.
"""

from __future__ import annotations

from dataclasses import dataclass

from cellimo.errors import ReferenceNotFoundError

__all__ = [
    "CHUNK_PREFIX",
    "NOTEBOOK_PREFIX",
    "ParsedReference",
    "chunk_reference_id",
    "notebook_reference_id",
    "parse_reference_id",
]

NOTEBOOK_PREFIX = "notebook"
CHUNK_PREFIX = "chunk"


@dataclass(frozen=True)
class ParsedReference:
    """A decoded reference identifier."""

    kind: str
    identifier: str
    collection: str = ""


def notebook_reference_id(notebook_id: str) -> str:
    """Build the reference id for an indexed notebook."""
    if not notebook_id:
        raise ValueError("notebook_id must not be empty")
    return f"{NOTEBOOK_PREFIX}:{notebook_id}"


def chunk_reference_id(collection: str, chroma_id: str) -> str:
    """Build the reference id for one indexed chunk."""
    if not collection or not chroma_id:
        raise ValueError("collection and chroma_id must not be empty")
    return f"{CHUNK_PREFIX}:{collection}:{chroma_id}"


def parse_reference_id(reference_id: str) -> ParsedReference:
    """Decode a reference id, or explain precisely why it is not one."""
    if not reference_id or ":" not in reference_id:
        raise ReferenceNotFoundError(
            f"{reference_id!r} is not a reference id; expected "
            f"'notebook:<id>' or 'chunk:<collection>:<id>'"
        )
    kind, _, remainder = reference_id.partition(":")
    if kind == NOTEBOOK_PREFIX:
        if not remainder:
            raise ReferenceNotFoundError(f"{reference_id!r} has an empty notebook id")
        return ParsedReference(kind=kind, identifier=remainder)
    if kind == CHUNK_PREFIX:
        collection, _, chroma_id = remainder.partition(":")
        if not collection or not chroma_id:
            raise ReferenceNotFoundError(
                f"{reference_id!r} is missing a collection or chunk id; expected "
                f"'chunk:<collection>:<id>'"
            )
        return ParsedReference(kind=kind, identifier=chroma_id, collection=collection)
    raise ReferenceNotFoundError(
        f"{reference_id!r} uses unknown namespace {kind!r}; expected "
        f"{NOTEBOOK_PREFIX!r} or {CHUNK_PREFIX!r}"
    )
