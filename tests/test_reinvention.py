"""Catching a native function that should have been used.

Precision is the whole problem, so roughly half of these assert *silence*. A
check that fires on legitimate matplotlib gets switched off, and then nothing
else about it matters.
"""

from __future__ import annotations

from cellimo.corpus import CorpusUsage
from cellimo.reinvention import review_source

# Shaped like the real measurement: sc.pl.umap dominates, so a naive ranking
# would offer it for everything.
USAGE = CorpusUsage(
    notebooks_by_call={
        "sc.pl.umap": 759,
        "sc.pl.violin": 188,
        "sc.pl.dotplot": 185,
        "sc.pl.scatter": 129,
    },
    notebooks_scanned=2845,
)

SIGNATURES = {
    "sc.pl.umap": ["adata", "color", "size"],
    "sc.pl.violin": ["adata", "keys", "groupby", "rotation"],
    "sc.pl.dotplot": ["adata", "var_names", "groupby"],
    "sc.pl.scatter": ["adata", "x", "y", "color"],
}


def _review(source: str, **kwargs):
    return review_source(source, usage=USAGE, signatures=SIGNATURES, **kwargs)


# -- it must fire -----------------------------------------------------------


def test_the_axes_method_form_is_caught() -> None:
    """The regression that matters: this project's own tutorial did exactly this.

    Matching only the `plt.*` names missed it, because idiomatic matplotlib
    draws through an axes object. A check that misses the failure it was built
    for is not a check.
    """
    source = (
        "fig, axes = plt.subplots(1, 2)\n"
        "for ax, metric in ((axes[0], 'n_genes'), (axes[1], 'total_counts')):\n"
        "    ax.boxplot([adata.obs.loc[adata.obs['sample_id'] == s, metric]\n"
        "                for s in samples])\n"
    )
    (finding,) = _review(source)
    assert finding.wrote == "boxplot()"
    assert "sc.pl.violin" in finding.candidates


def test_a_hand_rolled_embedding_plot_is_caught() -> None:
    """Verbatim from the corpus — published code reinventing sc.pl.tsne."""
    source = (
        "plt.scatter(ad.obsm['X_tsne'][:, 0], ad.obsm['X_tsne'][:, 1],\n"
        "            s=3, color=colors[ad.obs['clusters']])"
    )
    (finding,) = _review(source)
    assert finding.wrote == "plt.scatter"


# -- it must stay quiet -----------------------------------------------------


def test_plotting_a_dataframe_is_not_reinvention() -> None:
    """82% of matplotlib-only cells in the corpus are this. None are findings."""
    assert _review("df.groupby('x').mean().plot.bar()\nplt.show()") == []
    assert _review("plt.hist(counts, bins=50)\nplt.xlabel('n')") == []


def test_a_cell_already_using_the_native_function_is_left_alone() -> None:
    source = "sc.pl.violin(adata, ['n_genes'], groupby='sample_id')\nplt.savefig('f.png')"
    assert _review(source) == []


def test_styling_a_native_figure_is_not_drawing() -> None:
    """`plt.grid`/`plt.close` decorate native plots far more often than replace them."""
    source = "plt.rcParams.update({'figure.dpi': 150})\nplt.close('all')\nadata.obs.head()"
    assert _review(source) == []


def test_a_cell_that_draws_nothing_is_not_a_finding() -> None:
    assert _review("adata.obs['sample_id'].value_counts()") == []


# -- the suggestion has to fit ---------------------------------------------


def test_embedding_plots_are_not_offered_without_coordinates() -> None:
    """sc.pl.umap is the most-used call in the corpus.

    Without this it would head every suggestion, including for a cell that
    plots QC metrics and never reads an embedding — which is how a check earns
    a reputation for noise.
    """
    source = "ax.boxplot([adata.obs.loc[adata.obs['sample_id'] == s, 'n_genes'] for s in ss])"
    (finding,) = _review(source)
    assert "sc.pl.umap" not in finding.candidates
    assert finding.candidates[0] == "sc.pl.violin"


def test_embedding_plots_are_offered_when_coordinates_are_read() -> None:
    source = "plt.scatter(adata.obsm['X_umap'][:, 0], adata.obsm['X_umap'][:, 1])"
    (finding,) = _review(source)
    assert "sc.pl.umap" in finding.candidates


def test_a_native_function_that_cannot_group_is_not_offered_to_grouping_code() -> None:
    """The user's own limit: hand-written code is right when the native one cannot do it."""
    source = "sns.violinplot(data=adata.obs, x='sample_id', y='n_genes')\nax.set_title('x')"
    findings = _review("ax.boxplot(adata.obs['n_genes'], groupby='sample_id')")
    assert findings, "grouping code should still get grouping-capable candidates"
    assert all("groupby" in SIGNATURES[c] for c in findings[0].candidates)
    assert _review(source)  # seaborn on .obs is still reinvention


def test_without_signatures_it_still_reports_but_does_not_filter() -> None:
    """The project interpreter may be unreachable; degrade, do not go silent."""
    source = "ax.boxplot([adata.obs.loc[adata.obs['s'] == s, 'n'] for s in ss])"
    findings = review_source(source, usage=USAGE, signatures=None)
    assert findings and findings[0].candidates
