"""``cellimo doctor`` — what is installed, what works, and what does not.

Every check reports one of four states: ``ok``, ``warn``, ``fail`` or ``skip``.
A check that could not run reports ``skip`` with the reason; it never reports
``ok`` by default. That distinction is the whole value of the command — a doctor
that goes green when it could not look is worse than no doctor.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cellimo import __version__
from cellimo.agents import AGENT_NAMES, detect_agents, plugin_registered
from cellimo.environment import capture_environment, detect_environment_manager
from cellimo.errors import CellimoError, ProjectNotFoundError
from cellimo.marimo_pair import pair_status
from cellimo.marimo_runtime import check_notebook, detect_marimo
from cellimo.plugin_manifest import check_manifests
from cellimo.project.project import Project
from cellimo.resources import index_root, plugin_root
from cellimo.retrieval.base import open_index

__all__ = ["Diagnostic", "DiagnosticReport", "run_diagnostics"]

_MIN_FREE_BYTES = 5 * 1024**3


@dataclass(frozen=True)
class Diagnostic:
    """One thing that was checked."""

    name: str
    status: str  # ok | warn | fail | skip
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "data": self.data,
        }

    def format_line(self) -> str:
        symbol = {"ok": "ok  ", "warn": "warn", "fail": "FAIL", "skip": "skip"}[self.status]
        return f"[{symbol}] {self.name}: {self.detail}"


@dataclass
class DiagnosticReport:
    """Everything ``doctor`` found."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    project_root: str | None = None

    @property
    def failures(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.status == "fail"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.status == "warn"]

    def exit_code(self) -> int:
        return 1 if self.failures else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cellimo_version": __version__,
            "project_root": self.project_root,
            "ok": not self.failures,
            "counts": {
                "ok": len([item for item in self.diagnostics if item.status == "ok"]),
                "warn": len(self.warnings),
                "fail": len(self.failures),
                "skip": len([item for item in self.diagnostics if item.status == "skip"]),
            },
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }

    def to_text(self) -> str:
        lines = [f"cellimo {__version__} — diagnostics"]
        if self.project_root:
            lines.append(f"project: {self.project_root}")
        lines.append("")
        lines.extend(item.format_line() for item in self.diagnostics)
        counts = self.to_dict()["counts"]
        lines.append("")
        lines.append(
            f"{counts['ok']} ok, {counts['warn']} warning(s), {counts['fail']} failure(s), "
            f"{counts['skip']} skipped"
        )
        return "\n".join(lines)


def run_diagnostics(
    start: str | Path | None = None, *, check_agents: bool = True
) -> DiagnosticReport:
    """Run every diagnostic. ``check_agents=False`` skips the subprocess calls."""
    report = DiagnosticReport()
    add = report.diagnostics.append

    # The project is located first because it names the *project runtime* — the
    # environment Marimo actually lives in. Cellimo itself is usually installed
    # with `uv tool install`, in an isolated environment that deliberately has no
    # Marimo, so looking only next to this interpreter would report a failure to
    # every correctly-configured user.
    project = _find_project(start)
    interpreter = project.config.environment.interpreter if project else None

    add(_check_python())
    add(_check_cellimo_executable())
    add(_check_plugin_tree())
    add(_check_manifest_sync())
    for diagnostic in _check_agents(enabled=check_agents):
        add(diagnostic)
    add(_check_marimo(interpreter=interpreter, in_project=project is not None))
    for diagnostic in _check_marimo_pair():
        add(diagnostic)
    add(_check_index())

    if project is None:
        add(
            Diagnostic(
                name="project",
                status="skip",
                detail="no cellimo.yaml found here or in any parent directory",
            )
        )
        return report

    report.project_root = str(project.root)
    for diagnostic in _check_project(project):
        add(diagnostic)
    return report


# -- individual checks -----------------------------------------------------


def _check_python() -> Diagnostic:
    version = sys.version_info
    manager = detect_environment_manager()
    detail = (
        f"Python {version.major}.{version.minor}.{version.micro} "
        f"({manager}) at {sys.executable}"
    )
    if (version.major, version.minor) < (3, 11):
        return Diagnostic("python", "fail", detail + " — Cellimo requires 3.11 or newer")
    return Diagnostic(
        "python",
        "ok",
        detail,
        {"version": f"{version.major}.{version.minor}.{version.micro}", "manager": manager},
    )


def _check_cellimo_executable() -> Diagnostic:
    executable = shutil.which("cellimo")
    if executable is None:
        return Diagnostic(
            "cellimo on PATH",
            "fail",
            "the `cellimo` command is not on PATH; the plugin's MCP server is "
            "configured as `cellimo mcp serve` and will not start",
            {"installed_version": __version__},
        )
    return Diagnostic(
        "cellimo on PATH", "ok", f"{executable} (version {__version__})", {"path": executable}
    )


def _check_plugin_tree() -> Diagnostic:
    try:
        root = plugin_root()
    except CellimoError as exc:
        return Diagnostic("plugin tree", "fail", str(exc))
    skills = sorted(
        path.parent.name for path in (root / "skills").glob("*/SKILL.md")
    )
    if not skills:
        return Diagnostic("plugin tree", "fail", f"no skills found under {root / 'skills'}")

    # An editable install resolves the plugin tree to the source checkout. That
    # is convenient — edits take effect immediately — but the agents registered
    # this absolute path as a marketplace, so moving or deleting the checkout
    # breaks their registration silently. Say so once, here.
    editable = root.name == "plugin"
    detail = f"{root} — {len(skills)} skills: {', '.join(skills)}"
    if editable:
        detail += (
            " (editable install: the agents registered this exact path as a "
            "marketplace, so re-run `cellimo install` if you move the checkout)"
        )
    return Diagnostic(
        "plugin tree",
        "warn" if editable else "ok",
        detail,
        {"root": str(root), "skills": skills, "editable_checkout": editable},
    )


def _check_manifest_sync() -> Diagnostic:
    try:
        problems = check_manifests()
    except (CellimoError, OSError, KeyError) as exc:
        return Diagnostic("plugin manifests", "fail", f"could not verify: {exc}")
    if problems:
        return Diagnostic(
            "plugin manifests", "fail", "; ".join(problems), {"problems": problems}
        )
    return Diagnostic(
        "plugin manifests", "ok", "Claude and Codex manifests match plugin.toml"
    )


def _check_agents(*, enabled: bool) -> list[Diagnostic]:
    if not enabled:
        return [
            Diagnostic(
                f"agent: {name}", "skip", "agent detection disabled for this run"
            )
            for name in AGENT_NAMES
        ]
    results: list[Diagnostic] = []
    installations = detect_agents()
    if not any(installation.detected for installation in installations):
        results.append(
            Diagnostic(
                "agents",
                "fail",
                "neither Codex nor Claude Code was found on PATH; Cellimo has no "
                "reasoning agent without one of them",
            )
        )
    for installation in installations:
        if not installation.detected:
            results.append(
                Diagnostic(
                    f"agent: {installation.name}",
                    "warn",
                    installation.note or "not installed",
                )
            )
            continue
        registered, detail = plugin_registered(installation)
        results.append(
            Diagnostic(
                f"agent: {installation.name}",
                "ok" if registered else "warn",
                (
                    f"{installation.version or 'version unknown'} — plugin {detail}"
                    if registered
                    else f"{installation.version or 'version unknown'} — {detail}; "
                    f"run `cellimo install --agents {installation.name}`"
                ),
                {
                    "executable": installation.executable,
                    "version": installation.version,
                    "plugin_registered": registered,
                },
            )
        )
    return results


def _check_marimo(
    *, interpreter: str | None = None, in_project: bool = False
) -> Diagnostic:
    """Report Marimo, searching the project runtime before the tool runtime.

    Outside a project, a missing Marimo is a warning: nothing is broken yet, and
    the project runtime does not exist to look in. Inside one, it is a failure —
    ``cellimo start`` cannot work.
    """
    status = detect_marimo(interpreter)
    if not status.installed:
        return Diagnostic(
            "marimo",
            "fail" if in_project else "warn",
            status.note
            + (
                ""
                if in_project
                else " (checked this interpreter and PATH; no project runtime to check yet)"
            ),
            status.to_dict(),
        )
    if not status.compatible:
        return Diagnostic("marimo", "fail", status.note, status.to_dict())
    return Diagnostic(
        "marimo", "ok", f"{status.version} at {status.executable}", status.to_dict()
    )


def _check_marimo_pair() -> list[Diagnostic]:
    status = pair_status()
    results: list[Diagnostic] = []
    if not status.vendored:
        results.append(
            Diagnostic("marimo-pair", "fail", "no vendoring record found in the plugin")
        )
        return results
    vendor = status.vendor
    assert vendor is not None
    if status.intact:
        results.append(
            Diagnostic(
                "marimo-pair",
                "ok",
                f"{vendor.pinned_tag} ({vendor.pinned_commit[:12]}), {vendor.license}, "
                f"unmodified",
                vendor.to_dict(),
            )
        )
    else:
        results.append(
            Diagnostic(
                "marimo-pair",
                "fail",
                "the vendored copy does not match its recorded hashes: "
                + "; ".join(status.problems[:5]),
                {"problems": status.problems},
            )
        )
    live = status.live_servers
    if live:
        results.append(
            Diagnostic(
                "marimo session",
                "ok",
                f"{len(live)} discoverable session(s): "
                + ", ".join(server.base_url or server.server_id for server in live),
                {"servers": [server.to_dict() for server in live]},
            )
        )
    else:
        stale = len(status.servers) - len(live)
        results.append(
            Diagnostic(
                "marimo session",
                "warn",
                (
                    "no discoverable Marimo session. Start one with `cellimo start`; "
                    "only servers started with --no-token register themselves"
                    + (f" ({stale} stale registry entr(y/ies) found)" if stale else "")
                ),
                {"stale_entries": stale},
            )
        )
    return results


def _check_index() -> Diagnostic:
    index = open_index()
    status = index.status()
    if not status.installed:
        return Diagnostic(
            "retrieval index",
            "warn",
            status.note or f"no index installed at {index_root()}",
            status.model_dump(mode="json"),
        )
    detail = (
        f"{status.backend} backend at {status.path}: "
        f"{status.workflow_collections} workflow collection(s), "
        f"{status.notebooks} notebook(s)"
    )
    if status.unavailable:
        detail += f" — unavailable: {'; '.join(status.unavailable)}"
    return Diagnostic(
        "retrieval index",
        "warn" if status.unavailable else "ok",
        detail,
        status.model_dump(mode="json"),
    )


def _find_project(start: str | Path | None) -> Project | None:
    try:
        return Project.open(start)
    except (ProjectNotFoundError, CellimoError):
        return None


def _check_project(project: Project) -> list[Diagnostic]:
    results: list[Diagnostic] = []

    ok, message = project.verify_source()
    results.append(
        Diagnostic("source integrity", "ok" if ok else "fail", message)
    )

    source = project.source_path
    readable = source.is_file() and os.access(source, os.R_OK)
    results.append(
        Diagnostic(
            "source readable",
            "ok" if readable else "fail",
            f"{source}" if readable else f"{source} is not readable",
        )
    )
    if source.exists():
        writable_source = os.access(source, os.W_OK)
        results.append(
            Diagnostic(
                "source immutability",
                "warn" if writable_source else "ok",
                (
                    f"{source} is writable by this user — Cellimo's APIs refuse to "
                    f"write it, but nothing stops arbitrary code in the notebook. "
                    f"Consider `chmod a-w`."
                    if writable_source
                    else f"{source} is read-only on disk"
                ),
            )
        )

    outputs = project.path("artifacts")
    writable = os.access(outputs, os.W_OK)
    results.append(
        Diagnostic(
            "outputs writable",
            "ok" if writable else "fail",
            f"{outputs}" if writable else f"{outputs} is not writable",
        )
    )

    usage = shutil.disk_usage(project.root)
    results.append(
        Diagnostic(
            "disk space",
            "ok" if usage.free >= _MIN_FREE_BYTES else "warn",
            f"{usage.free / 1e9:.1f} GB free at {project.root}",
            {"free_bytes": usage.free, "total_bytes": usage.total},
        )
    )

    notebook = project.notebook_path
    if not notebook.exists():
        results.append(
            Diagnostic("notebook", "fail", f"{notebook} is missing")
        )
    else:
        check = check_notebook(
            notebook, interpreter=project.config.environment.interpreter
        )
        if not check.ran:
            results.append(Diagnostic("notebook", "skip", check.note, check.to_dict()))
        elif check.ok:
            results.append(
                Diagnostic("notebook", "ok", f"{notebook.name} is a valid Marimo notebook")
            )
        else:
            results.append(
                Diagnostic(
                    "notebook",
                    "fail",
                    f"{notebook.name}: "
                    + (
                        "; ".join(
                            str(issue.get("message", issue)) for issue in check.issues[:3]
                        )
                        or check.note
                    ),
                    check.to_dict(),
                )
            )

    # Packages are looked up in the *project* runtime. Checking this process
    # would report the tool environment's contents, which never contains Scanpy.
    profile = project.config.environment.profile
    interpreter = project.config.environment.interpreter
    expected = _profile_packages(profile)
    # Ask for the profile's packages explicitly, so this check can never look
    # for something the snapshot did not query.
    snapshot = capture_environment(
        interpreter=interpreter or None, extra_packages=expected
    )
    missing = [name for name in expected if name not in snapshot.packages]
    results.append(
        Diagnostic(
            "project packages",
            "warn" if missing else "ok",
            (
                f"profile {profile!r}: {', '.join(missing)} missing from "
                f"{snapshot.python_executable} — install them into the project runtime"
                if missing
                else f"profile {profile!r}: all expected packages present in "
                f"{snapshot.python_executable}"
            ),
            {
                "profile": profile,
                "missing": missing,
                "interpreter": snapshot.python_executable,
                "found": {
                    name: snapshot.packages[name]
                    for name in expected
                    if name in snapshot.packages
                },
            },
        )
    )
    return results


def _profile_packages(profile: str) -> tuple[str, ...]:
    if profile == "scanpy":
        return ("marimo", "anndata", "scanpy", "numpy", "pandas", "matplotlib")
    return ("marimo",)
