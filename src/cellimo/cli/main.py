"""The ``cellimo`` command line.

    cellimo install --agents auto
    cellimo init data/dataset.h5ad --profile scanpy
    cellimo start
    cellimo doctor --json
    cellimo check
    cellimo index status
    cellimo mcp serve

Every command that touches the network or another tool's configuration prints
what it is about to do before doing it, and every one of them can be inspected
with ``--dry-run`` or ``--json`` first.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from cellimo import __version__
from cellimo.agents import AGENT_NAMES, detect_agent, install_plugin
from cellimo.diagnostics import run_diagnostics
from cellimo.errors import CellimoError
from cellimo.marimo_runtime import check_notebook, detect_marimo, discover_servers, edit_command
from cellimo.project.project import Project
from cellimo.resources import index_root, plugin_root
from cellimo.retrieval.base import open_index
from cellimo.retrieval.install import DEFAULT_SOURCE, INDEX_SOURCES, install_index
from cellimo.schema import PROFILES

__all__ = ["cli", "main"]


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="cellimo")
def cli() -> None:
    """Agentic, reproducible single-cell analysis in Marimo.

    Cellimo never calls a language model. Codex or Claude Code is the agent;
    Marimo owns the notebook and the kernel; this tool owns the project,
    provenance, artifact lineage and scientific validation.
    """


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--agents",
    default="auto",
    help="auto | codex | claude | codex,claude — which agents to register with",
)
@click.option("--dry-run", is_flag=True, help="print the commands without running them")
def install(agents: str, dry_run: bool) -> None:
    """Register the Cellimo plugin with Codex and/or Claude Code.

    Registration runs each agent's own plugin commands, so this never edits
    ``~/.claude/settings.json`` or ``~/.codex/config.toml`` directly and cannot
    clobber existing configuration.
    """
    requested = _resolve_agents(agents)
    root = plugin_root()
    click.echo(f"plugin tree: {root}")

    any_installed = False
    for name in requested:
        installation = detect_agent(name)
        if not installation.detected:
            click.echo(f"  {name}: not found on PATH — skipping")
            continue
        click.echo(f"  {name}: {installation.version or 'version unknown'}")
        result = install_plugin(installation, root, dry_run=dry_run)
        if result.skipped_reason:
            click.echo(f"    skipped: {result.skipped_reason}")
            continue
        for step in result.steps:
            marker = "would run" if dry_run else "ran"
            click.echo(f"    {marker}: {step.as_text()}")
            if step.ran and not step.ok:
                message = (step.stderr or step.stdout).strip()[:400]
                click.echo(f"      exit {step.exit_code}: {message}")
        if result.installed:
            any_installed = True
            click.echo(f"    installed cellimo@cellimo for {name}")

    if dry_run:
        click.echo("\nDry run — nothing was changed.")
        return
    if not any_installed:
        click.echo(
            "\nNo agent was registered. Install Codex or Claude Code, or pass "
            "--agents explicitly.",
            err=True,
        )
        raise SystemExit(1)
    click.echo("\nNext: cellimo init DATASET.h5ad --profile scanpy")


def _resolve_agents(value: str) -> list[str]:
    if value == "auto":
        return list(AGENT_NAMES)
    names = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [name for name in names if name not in AGENT_NAMES]
    if unknown:
        raise click.BadParameter(
            f"unknown agent(s) {', '.join(unknown)}; expected {', '.join(AGENT_NAMES)}"
        )
    return names


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("dataset", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--profile",
    type=click.Choice(list(PROFILES)),
    default="scanpy",
    help="scanpy: install a standard stack. existing: adopt the current environment.",
)
@click.option("--dir", "directory", type=click.Path(path_type=Path), default=None,
              help="project directory (default: the current directory)")
@click.option("--name", default=None, help="project name (default: the directory name)")
@click.option(
    "--seed",
    type=int,
    default=None,
    help="random seed recorded in provenance (kept from the existing project on --force)",
)
@click.option("--force", is_flag=True, help="reinitialise an existing project in place")
@click.option(
    "--python",
    "python_path",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "the interpreter that will run the notebook. Defaults to an activated "
        "virtualenv, then a .venv in the project, then this interpreter."
    ),
)
def init(
    dataset: Path,
    profile: str,
    directory: Path | None,
    name: str | None,
    seed: int | None,
    force: bool,
    python_path: Path | None,
) -> None:
    """Create a project around DATASET and generate the Marimo notebook.

    The dataset is hashed where it lies and registered as immutable. It is never
    copied, moved or written to.
    """
    root = Path(directory) if directory is not None else Path.cwd()
    project = Project.init(
        root,
        dataset,
        profile=profile,
        name=name,
        random_seed=seed,
        cellimo_version=__version__,
        exist_ok=force,
        interpreter=python_path,
    )
    environment = project.config.environment
    if force:
        design = project.config.design
        if design.status == "unresolved" and not design.declared_fields():
            # Either there was nothing to keep, or the old config could not be
            # parsed. Claiming otherwise would be worse than saying nothing.
            click.echo(
                "reinitialised in place with a fresh configuration; provenance was "
                "not touched — review provenance/ for records that predate this"
            )
        else:
            click.echo(
                f"reinitialised in place — kept design (status {design.status}), "
                f"policies, checkpoint policy and seed {project.config.random_seed}; "
                f"provenance was not touched"
            )
    click.echo(f"project:  {project.root}")
    click.echo(f"source:   {project.source_path} ({project.config.source.sha256[:12]})")
    click.echo(f"notebook: {project.notebook_path.relative_to(project.root)}")
    click.echo(f"config:   {project.config_path.relative_to(project.root)}")
    click.echo(
        f"runtime:  {environment.interpreter}"
        + (f" (Python {environment.python})" if environment.python else " (version unknown)")
    )

    check = check_notebook(
        project.notebook_path, interpreter=project.config.environment.interpreter
    )
    if check.ran and not check.ok:
        click.echo(
            f"warning: the generated notebook did not pass `marimo check`: "
            f"{check.issues or check.note}",
            err=True,
        )
    elif not check.ran:
        click.echo(f"note: {check.note}")

    click.echo("\nNext: cellimo start")


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("dataset", type=click.Path(exists=True, dir_okay=False, path_type=Path),
                required=False)
@click.option("--profile", type=click.Choice(list(PROFILES)), default="scanpy",
              help="profile used when DATASET is given and no project exists yet")
@click.option("--port", type=int, default=None, help="port for the Marimo server")
@click.option("--host", default="127.0.0.1", help="interface to bind (default: loopback)")
@click.option("--headless", is_flag=True, help="do not open a browser")
@click.option("--print-command", is_flag=True, help="print the marimo command and exit")
def start(
    dataset: Path | None,
    profile: str,
    port: int | None,
    host: str,
    headless: bool,
    print_command: bool,
) -> None:
    """Start Marimo on the project notebook, discoverable by marimo-pair.

    The server runs with ``--no-token``: only untokenised servers register
    themselves in Marimo's server registry, which is how the agent finds the
    session. That means anyone who can reach the port can drive the kernel, so
    the default bind address is loopback.
    """
    try:
        project = Project.open()
    except CellimoError:
        if dataset is None:
            raise SystemExit(
                "no Cellimo project here. Run `cellimo init DATASET.h5ad`, or pass a "
                "dataset: `cellimo start DATASET.h5ad`."
            ) from None
        project = Project.init(
            Path.cwd(), dataset, profile=profile, cellimo_version=__version__
        )
        click.echo(f"initialised project at {project.root}")

    # Marimo lives in the project runtime, not in Cellimo's own environment.
    marimo = detect_marimo(project.config.environment.interpreter)
    if not marimo.installed:
        raise SystemExit(f"cannot start: {marimo.note}")
    if not marimo.compatible:
        raise SystemExit(f"cannot start: {marimo.note}")

    command = edit_command(
        project.notebook_path,
        executable=marimo.executable,
        host=host,
        port=port,
        headless=headless,
        token=False,
    )
    if print_command:
        click.echo(" ".join(command))
        return

    click.echo(f"project:  {project.root}")
    click.echo(f"notebook: {project.notebook_path.name}")
    click.echo(f"running:  {' '.join(command)}")
    click.echo(
        "\nThe session registers itself so Codex or Claude can attach through "
        "marimo-pair. Ask the agent to 'pair with my marimo notebook'.\n"
        "Stop the server with Ctrl-C."
    )
    try:
        completed = subprocess.run(command, check=False)
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    raise SystemExit(completed.returncode)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="emit structured diagnostics")
@click.option("--no-agents", is_flag=True, help="skip agent detection subprocesses")
def doctor(as_json: bool, no_agents: bool) -> None:
    """Check the installation, the agents, Marimo, the index and the project."""
    report = run_diagnostics(check_agents=not no_agents)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        click.echo(report.to_text())
    raise SystemExit(report.exit_code())


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("path", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--json", "as_json", is_flag=True, help="emit structured findings")
@click.option("--only", default="", help="comma-separated check codes to run")
def check(path: Path | None, as_json: bool, only: str) -> None:
    """Validate a project structurally and scientifically. Exits 1 on errors.

    PATH may be a project directory or a notebook inside one. When it is a
    notebook, ``marimo check`` runs on it as well.
    """
    target = Path(path) if path is not None else Path.cwd()
    notebook = target if target.is_file() and target.suffix == ".py" else None
    project = Project.open(target)
    # `manifest.json` is derived state that only three of nine mutating paths
    # refresh, so a session interrupted after recording statistics leaves it
    # stale — which is exactly the state an agent resuming work reads it in,
    # since both the `cellimo` and `notebook-review` skills point at the file.
    # Refreshing per mutation was measured at 12x the cost of a bare append and
    # is quadratic over a session, because every rebuild re-reads every log.
    # Doing it here instead costs one JSON write on a command that has already
    # read all four logs, and this is the inspect step the agent runs first.
    manifest_error = ""
    try:
        project.write_manifest()
    except OSError as exc:
        # A project that cannot be written is still a project that can be
        # checked. Say so rather than turning a read-only directory into a
        # crash, and rather than staying silent about a stale manifest.
        manifest_error = f"{type(exc).__name__}: {exc}"
    codes = [item.strip() for item in only.split(",") if item.strip()] or None
    if codes:
        from cellimo.validation.engine import run_checks

        try:
            report = run_checks(project, only=codes)
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="--only") from exc
    else:
        report = project.check()

    notebook_result = None
    notebook_missing = False
    notebook_path = notebook or project.notebook_path
    if notebook_path.exists():
        notebook_result = check_notebook(
            notebook_path, interpreter=project.config.environment.interpreter
        )
    else:
        # Silence here would read as "the notebook is fine".
        notebook_missing = True

    if as_json:
        payload = report.model_dump(mode="json")
        payload["counts"] = report.counts()
        # `passed` is the provenance verdict; `ok` is what the exit code means.
        payload["passed"] = report.passed
        payload["ok"] = not _failed(report, notebook_result, notebook_missing)
        payload["manifest_refreshed"] = not manifest_error
        if manifest_error:
            payload["manifest_error"] = manifest_error
        payload["notebook"] = (
            notebook_result.to_dict()
            if notebook_result
            else {
                "path": str(notebook_path),
                "ok": False,
                "ran": False,
                "issues": [],
                "note": "the notebook is missing",
            }
            if notebook_missing
            else None
        )
        click.echo(json.dumps(payload, indent=2))
    else:
        click.echo(report.to_text())
        if manifest_error:
            click.echo(f"\nmanifest: not refreshed — {manifest_error}", err=True)
        if notebook_missing:
            click.echo(
                f"\nnotebook: {notebook_path} is missing — regenerate it with "
                f"`cellimo init DATASET --force`"
            )
        if notebook_result is not None:
            if not notebook_result.ran:
                click.echo(f"\nnotebook: not validated — {notebook_result.note}")
            elif notebook_result.ok:
                click.echo(f"\nnotebook: {notebook_path.name} is a valid Marimo notebook")
            else:
                click.echo(
                    f"\nnotebook: {notebook_path.name} has "
                    f"{len(notebook_result.issues)} issue(s)"
                )

    raise SystemExit(1 if _failed(report, notebook_result, notebook_missing) else 0)



def _failed(report: Any, notebook: Any, notebook_missing: bool) -> bool:
    """What `cellimo check` calls a failure.

    Written once: the JSON payload's `ok` field and the exit code have to agree,
    and they did not when this predicate was spelled out twice.
    """
    return (
        not report.passed
        or notebook_missing
        or (notebook is not None and notebook.ran and not notebook.ok)
    )


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


@cli.group()
def index() -> None:
    """Manage the retrieval index behind the cellimo-knowledge MCP server."""


@index.command("status")
@click.option("--json", "as_json", is_flag=True, help="emit structured status")
def index_status(as_json: bool) -> None:
    """Report what index is installed and what it cannot answer."""
    status = open_index().status()
    if as_json:
        click.echo(json.dumps(status.model_dump(mode="json"), indent=2))
        return
    if not status.installed:
        click.echo(f"no index installed at {index_root()}")
        click.echo(status.note)
        click.echo("\nInstall one with: cellimo index install")
        return
    click.echo(f"backend:      {status.backend}")
    click.echo(f"path:         {status.path}")
    click.echo(f"workflows:    {status.workflow_collections} collection(s)")
    click.echo(f"documentation:{status.documentation_collections} collection(s)")
    click.echo(f"notebooks:    {status.notebooks}")
    click.echo(f"documents:    {status.documents}")
    click.echo(f"embeddings:   {status.embedding_model}")
    if status.organizations:
        click.echo(f"organisations:{len(status.organizations)}")
    for item in status.unavailable:
        click.echo(f"unavailable:  {item}")
    if status.note:
        click.echo(f"note:         {status.note}")


@index.command("install")
@click.option("--source", default=DEFAULT_SOURCE, help=f"index build (default: {DEFAULT_SOURCE})")
@click.option("--force", is_flag=True, help="replace an already-installed index")
@click.option("--from-archive", type=click.Path(exists=True, path_type=Path), default=None,
              help="install from a local archive instead of downloading")
@click.option("--yes", is_flag=True, help="do not ask before downloading")
def index_install(source: str, force: bool, from_archive: Path | None, yes: bool) -> None:
    """Download and install a retrieval index. Always an explicit action."""
    from cellimo.retrieval.install import install_from_archive

    destination = index_root()
    if from_archive is not None:
        target = install_from_archive(from_archive, destination=destination, force=force)
        click.echo(f"installed {from_archive} to {target}")
        return

    if source not in INDEX_SOURCES:
        raise SystemExit(f"unknown index {source!r}; available: {sorted(INDEX_SOURCES)}")
    build = INDEX_SOURCES[source]
    click.echo(f"index:    {build.name} ({build.version})")
    click.echo(f"url:      {build.url}")
    click.echo(f"download: {build.bytes / 1e6:.0f} MB compressed, ~840 MB unpacked")
    click.echo(f"licence:  {build.license}")
    click.echo(f"cite:     {build.citation}")
    click.echo(f"target:   {destination}")
    if not yes and not click.confirm("\nDownload it now?", default=False):
        click.echo("Not downloading.")
        raise SystemExit(1)

    state = {"last": -1}

    def _progress(done: int, total: int) -> None:
        if not total:
            return
        percent = int(done * 100 / total)
        if percent != state["last"] and percent % 5 == 0:
            state["last"] = percent
            click.echo(f"  {percent:3d}%", nl=False)
            click.echo("\r", nl=False)

    target = install_index(source, destination=destination, force=force, progress=_progress)
    click.echo(f"\ninstalled to {target}")
    status = open_index().status()
    click.echo(
        f"{status.workflow_collections} workflow collection(s), {status.notebooks} notebook(s)"
    )


@index.command("update")
@click.option("--source", default=DEFAULT_SOURCE, help="index build to install")
@click.option("--yes", is_flag=True, help="do not ask before downloading")
def index_update(source: str, yes: bool) -> None:
    """Replace the installed index with a fresh download."""
    context = click.get_current_context()
    context.invoke(index_install, source=source, force=True, from_archive=None, yes=yes)


# ---------------------------------------------------------------------------
# mcp
# ---------------------------------------------------------------------------


@cli.group("mcp")
def mcp_group() -> None:
    """The read-only cellimo-knowledge MCP server."""


@mcp_group.command("serve")
@click.option("--index-path", type=click.Path(path_type=Path), default=None,
              help="override the index location")
def mcp_serve(index_path: Path | None) -> None:
    """Run the server on stdio. This is what the plugin's .mcp.json invokes."""
    from cellimo.mcp.server import serve

    serve(str(index_path) if index_path else None)


# ---------------------------------------------------------------------------
# sessions (small helper, used by the router skill)
# ---------------------------------------------------------------------------


@cli.command("sessions")
@click.option("--json", "as_json", is_flag=True, help="emit structured session data")
def sessions(as_json: bool) -> None:
    """List discoverable Marimo sessions, as marimo-pair sees them."""
    found = discover_servers()
    if as_json:
        click.echo(json.dumps([server.to_dict() for server in found], indent=2))
        return
    if not found:
        click.echo("no Marimo sessions registered. Start one with `cellimo start`.")
        return
    for server in found:
        state = "live" if server.alive else "stale"
        click.echo(f"{state:5s} {server.base_url or server.server_id} (marimo {server.version})")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Turns Cellimo errors into a message and a non-zero status."""
    try:
        cli.main(args=argv, standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.exceptions.Abort:
        click.echo("aborted", err=True)
        return 130
    except CellimoError as exc:
        click.echo(f"cellimo: {exc}", err=True)
        return 1
    except OSError as exc:
        # Permission denied, no space left, read-only filesystem: the user's
        # problem to fix, but they should see a sentence rather than a traceback.
        click.echo(f"cellimo: {exc}", err=True)
        return 1
    except SystemExit as exc:
        # ``SystemExit("a message")`` is a legitimate way to abort, and Python
        # prints the string and exits 1. Coercing it with int() raised
        # ValueError instead, turning every such abort into a traceback.
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        click.echo(str(code), err=True)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
