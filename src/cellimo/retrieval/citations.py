"""Where a cell's code came from, carried in the cell itself.

A grounded analysis is one whose code was adapted from work that ran, rather
than recalled by a language model. That claim is only worth anything if it can
be checked, so the claim travels with the code::

    # cellimo:source notebook:theislab_scib_pbmc section=12 sha=a1de8044c91f
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], inplace=True)

Three properties this format is chosen for, in order of how much they matter:

*Nobody types it.* ``get_reference`` emits the header; the agent keeps it while
adapting the code. There is no recording call, which is the whole correction
this version of Cellimo is making.

*The notebook does not depend on Cellimo.* It is a comment. ``analysis.py``
still runs with cellimo uninstalled — Cellimo is authoring scaffolding, not a
runtime dependency.

*An uncited cell is visible as uncited.* That is the signal. A cell with no
header was written from memory, and the reader can see which ones those are
instead of having to trust the whole notebook equally.

The ``sha`` covers **one section's content**, never the concatenation of
whatever was requested alongside it. Hashing the returned blob would make the
same cell hash differently depending on the call that produced it, which is
exactly the kind of guarantee that looks real and is not.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Iterator
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cellimo.errors import CellimoError
from cellimo.retrieval.base import KnowledgeIndex
from cellimo.retrieval.models import Reference
from cellimo.util.hashing import hash_bytes, short_hash

__all__ = [
    "CITATION_SHA_LENGTH",
    "Citation",
    "CitationState",
    "CitationStatus",
    "CitedAnalysisCell",
    "analysis_cells",
    "attach_headers",
    "format_header",
    "malformed_headers",
    "parse",
    "resolve",
    "section_sha",
]

#: Long enough that a collision is not a practical concern, short enough to sit
#: in a comment without pushing the code off the line.
CITATION_SHA_LENGTH = 12

_HEADER = re.compile(
    r"^\s*#\s*cellimo:source\s+(?P<reference_id>\S+)"
    r"\s+section=(?P<section_id>\S+)"
    rf"\s+sha=(?P<sha>[0-9a-fA-F]{{{CITATION_SHA_LENGTH}}})\s*$"
)

_DATA_NAMES = re.compile(
    r"^_?(?:adata|adatas|anndata|mdata|mudata|sdata|spatialdata|filtered|pseudobulk)(?:_|$)",
    re.IGNORECASE,
)
_SCIENTIFIC_MODULES = frozenset(
    {
        "anndata",
        "cellrank",
        "decoupler",
        "matplotlib",
        "mudata",
        "numpy",
        "pandas",
        "pertpy",
        "pydeseq2",
        "scanpy",
        "scipy",
        "scvi",
        "seaborn",
        "squidpy",
        "statsmodels",
    }
)
_SCIENTIFIC_METHODS = frozenset(
    {
        "boxplot",
        "describe",
        "fit",
        "groupby",
        "heatmap",
        "hist",
        "mean",
        "median",
        "plot",
        "quantile",
        "scatter",
        "sum",
        "transform",
        "value_counts",
        "violinplot",
        "write_h5ad",
        "write_zarr",
    }
)
_ANNDATA_ATTRIBUTES = frozenset({"X", "layers", "obs", "obsm", "obsp", "raw", "var", "varm"})


def section_sha(content: str) -> str:
    """The hash a citation carries for one section's content."""
    return short_hash(hash_bytes(content.encode("utf-8")), CITATION_SHA_LENGTH)


def format_header(reference_id: str, section_id: str, content: str) -> str:
    """The comment line that makes a cell's origin checkable."""
    return (
        f"# cellimo:source {reference_id} section={section_id} "
        f"sha={section_sha(content)}"
    )


def attach_headers(reference: Reference) -> Reference:
    """Prefix every code section with the header that makes it checkable.

    Markdown and prose sections are left alone — a citation belongs on code the
    agent is about to adapt, not on the narration around it.

    The hash is taken before the header is added, so a section hashes the same
    whether or not it was fetched with provenance. Anything else would make the
    sha depend on how the code was retrieved rather than on what it says.
    """
    sections = [
        section
        if section.kind != "code" or not section.content
        else section.model_copy(
            update={
                "content": (
                    format_header(
                        reference.reference_id, section.section_id, section.content
                    )
                    + "\n"
                    + section.content
                )
            }
        )
        for section in reference.sections
    ]
    return reference.model_copy(update={"sections": sections})


class Citation(BaseModel):
    """One claim that a piece of code came from somewhere."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_id: str
    section_id: str
    sha: str
    #: 1-indexed line in the notebook, so a report can point at it.
    line: int = 0


class CitationState(StrEnum):
    """What checking a citation against the index found.

    ``DRIFTED`` is deliberately distinct from ``RESOLVED``: the source still
    exists but no longer says what it said when the code was adapted, which is
    a different thing from a citation that was always wrong.
    """

    RESOLVED = "resolved"
    UNKNOWN_REFERENCE = "unknown_reference"
    UNKNOWN_SECTION = "unknown_section"
    DRIFTED = "drifted"


class CitationStatus(BaseModel):
    """A citation and what became of it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    citation: Citation
    state: CitationState
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state is CitationState.RESOLVED


class CitedAnalysisCell(BaseModel):
    """One Marimo analysis cell and the citations scoped to that cell."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    line: int
    end_line: int
    calls: list[str]
    citations: list[Citation]


def parse(source: str) -> list[Citation]:
    """Every citation header in a notebook, in the order they appear.

    Deliberately token-based rather than AST-based: comments are not part of
    Python's syntax tree, and tokenising distinguishes a real comment from
    documentation that merely shows ``# cellimo:source`` inside a string.
    """
    citations: list[Citation] = []
    for number, line in _comment_tokens(source):
        match = _HEADER.match(line)
        if match is None:
            continue
        citations.append(
            Citation(
                reference_id=match["reference_id"],
                section_id=match["section_id"],
                sha=match["sha"].lower(),
                line=number,
            )
        )
    return citations


def malformed_headers(source: str) -> list[int]:
    """Line numbers that look like citation headers but do not parse."""
    return [
        number
        for number, line in _comment_tokens(source)
        if "cellimo:source" in line and _HEADER.match(line) is None
    ]


def _comment_tokens(source: str) -> Iterator[tuple[int, str]]:
    """Actual Python comments, including those before a later syntax error."""
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                yield token.start[0], token.string
    except (IndentationError, tokenize.TokenError):
        # A malformed notebook can still contain useful headers before the
        # broken token. ``generate_tokens`` yields those before raising.
        return


def analysis_cells(source: str) -> list[CitedAnalysisCell]:
    """Marimo cells that clearly perform scientific computation or plotting.

    This is deliberately structural and conservative. A cell is included when
    it touches a recognisable single-cell object, calls a scientific package
    (including an arbitrary import alias), or uses a common tabulation/modelling
    method. Marimo UI, markdown, imports, and pure Cellimo bookkeeping are left
    alone. Citations are scoped by the function's line range, so a header in one
    cell cannot cover the next.
    """
    tree = ast.parse(source)
    citations = parse(source)
    cells: list[CitedAnalysisCell] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_cell_decorator(item) for item in node.decorator_list):
            continue
        aliases = _scientific_aliases(node)
        if not _is_analysis_cell(node, aliases):
            continue
        end_line = node.end_lineno or node.lineno
        cell_citations = [
            citation
            for citation in citations
            if node.lineno <= citation.line <= end_line
        ]
        cells.append(
            CitedAnalysisCell(
                line=node.lineno,
                end_line=end_line,
                calls=sorted(_cell_calls(node)),
                citations=cell_citations,
            )
        )
    return cells


def _is_cell_decorator(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "cell"
        and isinstance(node.value, ast.Name)
        and node.value.id == "app"
    )


def _scientific_aliases(node: ast.AST) -> set[str]:
    aliases: set[str] = set()
    for item in ast.walk(node):
        if isinstance(item, ast.Import):
            for name in item.names:
                root = name.name.split(".", 1)[0]
                if root in _SCIENTIFIC_MODULES:
                    aliases.add(name.asname or root)
        elif isinstance(item, ast.ImportFrom):
            root = (item.module or "").split(".", 1)[0]
            if root in _SCIENTIFIC_MODULES:
                aliases.update(name.asname or name.name for name in item.names)
    return aliases


def _cell_calls(node: ast.AST) -> set[str]:
    calls: set[str] = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Call):
            continue
        parts: list[str] = []
        current: ast.expr = item.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            calls.add(".".join(reversed(parts)))
        elif isinstance(item.func, ast.Attribute):
            calls.add(item.func.attr)
    return calls


def _is_analysis_cell(node: ast.AST, aliases: set[str]) -> bool:
    for item in ast.walk(node):
        if isinstance(item, ast.Name) and _DATA_NAMES.match(item.id):
            return True
        if isinstance(item, ast.Attribute) and (
            item.attr in _ANNDATA_ATTRIBUTES or item.attr in _SCIENTIFIC_METHODS
        ):
            return True
        if isinstance(item, ast.Call):
            root = item.func
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in aliases:
                return True
    return False


def resolve(
    citations: list[Citation], index: KnowledgeIndex
) -> list[CitationStatus]:
    """Check each citation against the installed index.

    One ``get_reference`` call per distinct reference, not per citation: a
    notebook that adapts eight cells from one published analysis should cost one
    lookup, and the ChromaDB backend is slow enough for that to be worth caring
    about.
    """
    statuses: list[CitationStatus] = []
    sections_by_reference: dict[str, dict[str, str] | None] = {}

    for citation in citations:
        if citation.reference_id not in sections_by_reference:
            sections_by_reference[citation.reference_id] = _load(
                index, citation.reference_id
            )
        sections = sections_by_reference[citation.reference_id]

        if sections is None:
            statuses.append(
                CitationStatus(
                    citation=citation,
                    state=CitationState.UNKNOWN_REFERENCE,
                    detail=(
                        f"{citation.reference_id} is not in the installed index"
                    ),
                )
            )
            continue

        content = sections.get(citation.section_id)
        if content is None:
            statuses.append(
                CitationStatus(
                    citation=citation,
                    state=CitationState.UNKNOWN_SECTION,
                    detail=(
                        f"{citation.reference_id} has no section "
                        f"{citation.section_id!r}"
                    ),
                )
            )
            continue

        actual = section_sha(content)
        if actual != citation.sha:
            statuses.append(
                CitationStatus(
                    citation=citation,
                    state=CitationState.DRIFTED,
                    detail=(
                        f"the source now hashes to {actual}, not {citation.sha}; "
                        f"the cited section changed after this code was adapted"
                    ),
                )
            )
            continue

        statuses.append(
            CitationStatus(citation=citation, state=CitationState.RESOLVED)
        )
    return statuses


def _load(index: KnowledgeIndex, reference_id: str) -> dict[str, str] | None:
    """Section id to content for one reference, or None when it is not there."""
    try:
        # Raw content: the sha covers what the section says, not the header we
        # would have wrapped it in.
        reference = index.get_reference(reference_id, with_provenance=False)
    except CellimoError:
        # Covers a missing index and an unknown reference alike. Either way the
        # citation cannot be confirmed, and saying so beats raising: one bad
        # citation must not stop the rest of the notebook being checked.
        return None
    return {section.section_id: section.content for section in reference.sections}
