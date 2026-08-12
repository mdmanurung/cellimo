"""A Sphinx directive that embeds a Marimo notebook as marimo *islands*.

Why this exists rather than ``sphinx-marimo``: that extension shells out to
``marimo export html-wasm`` without ``--execute``, so the reader gets an empty
shell until Pyodide finishes booting in their browser, and the build learns
nothing about whether the notebook still runs.

Islands invert both properties. ``MarimoIslandGenerator.build()`` executes the
notebook here, in the same interpreter that builds the docs, and the *computed*
output travels with the page, so the reader never waits on Pyodide to calculate
it. Pyodide is loaded afterwards, and only to hydrate cells marked reactive.

Be precise about what "embedded" means, because it is easy to oversell: the
output ships as ``<marimo-mime-renderer data-mime=... data-data=...>``, i.e. the
computed value sits in a JSON-escaped HTML *attribute* and the islands script
renders it into the DOM. So it is present without Pyodide, but it is **not**
visible with JavaScript disabled and **not** indexable as prose. The win over a
WASM export is time-to-content, not no-JS readability.

It also makes the build a test: a notebook whose cells raise fails the build,
the same guarantee ``nb_execution_raise_on_error`` gives ``docs/tutorial.md``.
That check is ours, not marimo's — see the stub scan in ``run``.

Usage::

    ```{marimo-island} playground.py
    :display-code:
    ```

Paths are resolved against ``marimo_island_dir`` (default ``notebooks``),
relative to the documentation root.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from docutils import nodes
from docutils.parsers.rst import directives
from sphinx.application import Sphinx
from sphinx.errors import ExtensionError
from sphinx.util.docutils import SphinxDirective

__all__ = ["setup"]

#: Marks a page as having already emitted the island runtime's <head> payload.
_HEAD_EMITTED = "marimo_island_head_emitted"


class MarimoIslandDirective(SphinxDirective):
    """Execute a Marimo notebook at build time and embed its islands."""

    has_content = False
    required_arguments = 1
    option_spec = {  # noqa: RUF012
        "display-code": directives.flag,
        "static": directives.flag,
    }

    def run(self) -> list[nodes.Node]:
        from marimo import MarimoIslandGenerator

        source = (
            Path(self.env.app.srcdir)
            / self.env.config.marimo_island_dir
            / self.arguments[0]
        )
        if not source.is_file():
            raise ExtensionError(f"marimo-island: no such notebook: {source}")

        display_code = "display-code" in self.options
        reactive = "static" not in self.options

        generator = MarimoIslandGenerator.from_file(
            str(source), display_code=display_code
        )
        try:
            asyncio.run(generator.build())
        except Exception as exc:
            raise ExtensionError(
                f"marimo-island: {source.name} failed to execute: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # `build()` does not raise on a failing cell — it calls `run_notebook`,
        # takes the `did_error` flag it returns and does `del did_error`
        # (marimo/_islands/_island_generator.py:478). Without this loop a
        # notebook whose cells all raise would publish a page of error boxes
        # and a green build, which is the opposite of the point.
        broken = [
            stub
            for stub in generator.stubs
            if getattr(stub.output, "channel", None) == "marimo-error"
        ]
        if broken:
            detail = "; ".join(str(stub.output.data)[:200] for stub in broken)
            raise ExtensionError(
                f"marimo-island: {len(broken)} cell(s) in {source.name} failed: {detail}"
            )

        html = ""
        # The runtime's <head> payload is emitted once per page, before the
        # first island on it.
        if not getattr(self.env, _HEAD_EMITTED, set()) & {self.env.docname}:
            emitted = getattr(self.env, _HEAD_EMITTED, set())
            emitted.add(self.env.docname)
            setattr(self.env, _HEAD_EMITTED, emitted)
            html += generator.render_head()

        html += "".join(
            stub.render(
                display_code=display_code,
                display_output=True,
                is_reactive=reactive,
            )
            for stub in generator.stubs
        )
        return [nodes.raw("", html, format="html")]


def _purge(app: Sphinx, env: Any, docname: str) -> None:
    """Let a rebuilt page emit the head payload again."""
    emitted = getattr(env, _HEAD_EMITTED, set())
    emitted.discard(docname)
    setattr(env, _HEAD_EMITTED, emitted)


def setup(app: Sphinx) -> dict[str, Any]:
    app.add_config_value("marimo_island_dir", "notebooks", "env", types=[str])
    app.add_directive("marimo-island", MarimoIslandDirective)
    app.connect("env-purge-doc", _purge)
    return {"version": "0.1", "parallel_read_safe": False}
