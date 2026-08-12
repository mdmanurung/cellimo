"""Project discovery, scaffolding and the public :class:`Project` API."""

from __future__ import annotations

from cellimo.project.project import Project, StageContext
from cellimo.project.scaffold import render_notebook, scaffold_project

__all__ = ["Project", "StageContext", "render_notebook", "scaffold_project"]
