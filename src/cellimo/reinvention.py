"""Did a native function already do this?

The failure this exists for, in the user's words: *the worst is when Claude
invents a function from scratch where there is actually a field standard
method — or when there is a function native to the package, especially to
visualize results, but Claude decides to make a custom plot.*

Real, published example from the corpus::

    plt.scatter(ad.obsm['X_tsne'][:, 0], ad.obsm['X_tsne'][:, 1],
                s=3, color=colors[ad.obs['clusters']])

That is ``sc.pl.tsne(ad, color='clusters')``, hand-rolled.

**Precision is the whole problem.** A third of published plotting is
matplotlib-only, and most of it is legitimate — someone plotting a DataFrame,
or building a figure no native function offers. A check that fires on those
gets switched off, and then it does not matter how good the rest is. Measured
over the corpus: of 443 matplotlib-only drawing cells, only **80 (18%) touch an
AnnData at all**. The other 82% are plotting arrays and frames, and none of
them are reinvention.

So three conditions must hold before anything is said, and the last two are
what keep it quiet:

1. the cell draws something,
2. it calls no native plotting function,
3. **it is working on an AnnData** — otherwise the native functions do not
   apply and there is nothing to have used instead.

Then a suggestion is only offered if the native function **could have expressed
it** — checked against the real signature from the project runtime, not against
a hard-coded list. That is the user's own limit: hand-written code is right
whenever the native one cannot do the job.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from cellimo.corpus import CorpusUsage, calls_in_source

__all__ = [
    "NATIVE_PLOT_NAMESPACES",
    "Reinvention",
    "native_signatures",
    "review_source",
]

#: Namespaces whose presence means the author already used the native path.
NATIVE_PLOT_NAMESPACES = ("sc.pl.", "sq.pl.", "pt.pl.", "dc.pl.")

#: Calls that put marks on a figure. Styling (`plt.grid`, `plt.close`) is
#: excluded on purpose: it decorates a native plot far more often than it
#: replaces one, and counting it would fire on almost every figure in the
#: corpus.
_DRAWING = frozenset(
    {
        "plt.plot", "plt.scatter", "plt.bar", "plt.barh", "plt.hist", "plt.hist2d",
        "plt.boxplot", "plt.violinplot", "plt.imshow", "plt.pcolormesh",
        "plt.contourf", "plt.stackplot", "plt.pie",
        "sns.violinplot", "sns.boxplot", "sns.scatterplot", "sns.histplot",
        "sns.barplot", "sns.heatmap", "sns.stripplot", "sns.distplot",
        "sns.swarmplot", "sns.lineplot", "sns.kdeplot", "sns.clustermap",
        "sns.catplot", "sns.displot", "sns.relplot", "sns.jointplot",
    }
)

#: The same marks, reached through an axes object rather than through pyplot —
#: `ax.boxplot(...)`, `axes[0].scatter(...)`. This is the *idiomatic* matplotlib
#: form, and matching only the `plt.` names missed the hand-rolled boxplot in
#: this project's own tutorial, which is the case the check exists for.
_DRAWING_METHODS = frozenset(
    {
        "plot", "scatter", "bar", "barh", "hist", "hist2d", "boxplot",
        "violinplot", "imshow", "pcolormesh", "contourf", "stackplot", "pie",
    }
)

#: Plots of a low-dimensional embedding. They need coordinates in ``.obsm``, so
#: they are not candidates for a cell that never reads any.
_EMBEDDING_PLOTS = frozenset(
    {
        "sc.pl.umap", "sc.pl.tsne", "sc.pl.pca", "sc.pl.embedding",
        "sc.pl.draw_graph", "sc.pl.diffmap", "sc.pl.spatial", "sc.pl.pca_scatter",
    }
)

#: Attribute access that only an AnnData has, matched on a word boundary rather
#: than by substring. The receiver is not named: the corpus calls the object
#: `ad`, `adata`, `andata`, `a`, and worse. The boundary matters — `data=adata.obs`
#: is the standard seaborn idiom and an earlier substring form (`.obs[`, `.obs.`)
#: missed it, while `\bobs\b` alone would match unrelated words.
_ANNDATA_ATTRIBUTE = re.compile(r"\.(?:obs|var|obsm|varm|obsp|layers|raw)\b")


class Reinvention(BaseModel):
    """One place a native function would have done the job."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: What the code did instead, e.g. ``plt.scatter``.
    wrote: str
    #: Native candidates, most-used in the corpus first.
    candidates: list[str] = Field(default_factory=list)
    detail: str = ""

    def format_line(self) -> str:
        options = ", ".join(self.candidates) or "a native function"
        return f"{self.wrote} on an AnnData; {options} does this"


def _touches_anndata(source: str) -> bool:
    return _ANNDATA_ATTRIBUTE.search(source) is not None


def _drawing_methods(source: str) -> set[str]:
    """Marks made through an axes object: ``ax.boxplot``, ``axes[0].scatter``.

    Matched on the method name alone, because the receiver is often a subscript
    or a loop variable and has no stable name to check. That is loose on its
    own — the AnnData condition is what keeps it honest.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    return {
        f"{node.func.attr}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _DRAWING_METHODS
    }


def _keywords_used(source: str) -> set[str]:
    """Keyword names the cell passes to anything, as a proxy for what it needs."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set()
    return {
        keyword.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg
    }


def review_source(
    source: str,
    *,
    usage: CorpusUsage,
    signatures: Mapping[str, Sequence[str]] | None = None,
    limit: int = 3,
) -> list[Reinvention]:
    """Report native functions the code could have used instead.

    ``signatures`` maps a native call to its parameter names, from the project
    runtime. Without it, candidates are ranked on corpus usage alone and the
    "could it have expressed this" test is skipped — so pass it whenever the
    project interpreter can be reached.
    """
    calls = calls_in_source(source)
    if any(call.startswith(NATIVE_PLOT_NAMESPACES) for call in calls):
        return []
    drawing = sorted(calls & _DRAWING)
    drawing += sorted(_drawing_methods(source) - set(drawing))
    if not drawing or not _touches_anndata(source):
        return []

    needed = _keywords_used(source)
    # An embedding plot needs coordinates. Suggesting sc.pl.umap for a cell that
    # never touches .obsm is how a check earns a reputation for noise — and
    # sc.pl.umap is the most-used function in the corpus, so without this it
    # would head every suggestion regardless of what the cell does.
    embedding_available = ".obsm" in source
    candidates = []
    for call, _ in usage.most_used("sc.pl.", limit=40):
        if call in _EMBEDDING_PLOTS and not embedding_available:
            continue
        if signatures is not None:
            parameters = set(signatures.get(call, ()))
            if not parameters:
                continue
            # Only suggest something that could carry what this code passed.
            # `groupby` is the discriminating one: a cell that groups needs a
            # native function that groups.
            if "groupby" in needed and "groupby" not in parameters:
                continue
        candidates.append(call)
        if len(candidates) == limit:
            break

    if not candidates:
        return []
    return [
        Reinvention(
            wrote=drawing[0],
            candidates=candidates,
            detail=(
                f"this cell draws with {', '.join(drawing)} while working on an "
                f"AnnData, and calls no native plotting function"
            ),
        )
    ]


_REMOTE_SIGNATURES = """\
import inspect, json, sys
module_name, prefix = sys.argv[1], sys.argv[2]
try:
    module = __import__(module_name, fromlist=["pl"])
except Exception:
    print("{}"); raise SystemExit(0)
plotting = getattr(module, "pl", None)
out = {}
for name in dir(plotting or ()):
    if name.startswith("_"):
        continue
    function = getattr(plotting, name, None)
    if not callable(function):
        continue
    try:
        out[prefix + name] = list(inspect.signature(function).parameters)
    except (TypeError, ValueError):
        pass
print(json.dumps(out))
"""


def native_signatures(
    interpreter: str | Path, module: str = "scanpy", prefix: str = "sc.pl."
) -> dict[str, list[str]]:
    """Parameter names of the project runtime's plotting functions.

    Asked of the *project* interpreter, never imported here: the tool runtime
    deliberately has no scanpy, and the answer must describe the environment the
    notebook actually runs in — a function removed in the installed version must
    not be recommended. Follows ``environment._capture_from``.
    """
    try:
        completed = subprocess.run(
            [str(interpreter), "-c", _REMOTE_SIGNATURES, module, prefix],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
