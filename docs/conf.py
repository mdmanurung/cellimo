"""Sphinx configuration for the Cellimo documentation site.

The site is built with MyST-NB so that pages can be *executed*, not just
rendered. `tutorial.md` runs the real Cellimo API against a dataset it
generates itself, and `nb_execution_raise_on_error` makes a failure there fail
the build — so the tutorial cannot drift away from the code the way a
hand-transcribed one does.

Build it with::

    make -C docs html          # or: sphinx-build -W -b html docs docs/_build/html

``-W`` is deliberate: a broken cross-reference is a broken document.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "_ext"))

from cellimo import __version__ as _cellimo_version

# -- project -----------------------------------------------------------------

project = "Cellimo"
author = "Cellimo contributors"
copyright = "2026, Cellimo contributors"
release = _cellimo_version
version = _cellimo_version

# -- general -----------------------------------------------------------------

extensions = [
    "myst_nb",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
    "sphinx_design",
    "sphinxcontrib.mermaid",
    "marimo_island",
]

# Intersphinx is deliberately absent. It resolves inventories over the network
# at build time, and with ``-W`` a network hiccup would turn into a failed
# build — for a project whose entire subject is reproducibility, docs that only
# build when the network is up would be an embarrassing default.

exclude_patterns = ["_build", "**.ipynb_checkpoints", "Thumbs.db", ".DS_Store"]
source_suffix = {".md": "myst-nb", ".ipynb": "myst-nb", ".rst": "restructuredtext"}

# -- MyST --------------------------------------------------------------------

myst_enable_extensions = [
    "attrs_inline",
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
    "tasklist",
]
myst_heading_anchors = 3

# -- MyST-NB execution -------------------------------------------------------

#: "cache" executes a page once and reuses the result until its source changes,
#: so an unchanged tutorial does not re-run on every build.
nb_execution_mode = "cache"
nb_execution_timeout = 300
#: The point of executing the docs is to find out when they are wrong.
nb_execution_raise_on_error = True
nb_execution_show_tb = True
#: Long provenance dumps are informative but should not push the prose off the
#: page; the theme makes these scroll rather than truncate.
nb_output_stderr = "show"
nb_merge_streams = True

# -- HTML --------------------------------------------------------------------

html_theme = "sphinx_book_theme"
html_title = f"Cellimo {release}"
html_copy_source = True
html_show_sourcelink = True
html_theme_options = {
    "repository_url": "https://github.com/mdmanurung/cellimo",
    "repository_branch": "main",
    "path_to_docs": "docs",
    "use_repository_button": True,
    "use_source_button": True,
    "use_issues_button": True,
    "use_edit_page_button": True,
    "home_page_in_toc": True,
    "show_toc_level": 2,
    "navigation_with_keys": False,
}

# -- autodoc -----------------------------------------------------------------

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_class_signature = "separated"
#: The scientific stack is an *optional* runtime. Autodoc must not need Scanpy
#: installed to document a module that imports it lazily.
autodoc_mock_imports = ["anndata", "scanpy", "chromadb", "sentence_transformers"]
napoleon_google_docstring = True
napoleon_numpy_docstring = False
