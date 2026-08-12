"""Detecting Codex and Claude Code, and registering the Cellimo plugin with them.

Both CLIs manage their own configuration and both expose real plugin commands:

    claude plugin marketplace add <path> && claude plugin install cellimo@cellimo
    codex  plugin marketplace add <path> && codex  plugin add     cellimo@cellimo

Cellimo shells out to those rather than editing ``~/.claude/settings.json`` or
``~/.codex/config.toml`` itself. That is the whole reason installation cannot
clobber a user's existing configuration: Cellimo never writes those files.

Nothing here runs without being asked. ``cellimo install`` prints every command
before running it, and ``--dry-run`` prints them without running anything.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "AGENT_NAMES",
    "MARKETPLACE_NAME",
    "PLUGIN_NAME",
    "AgentInstallation",
    "InstallResult",
    "InstallStep",
    "detect_agent",
    "detect_agents",
    "install_plugin",
]

AGENT_NAMES: tuple[str, ...] = ("claude", "codex")
MARKETPLACE_NAME = "cellimo"
PLUGIN_NAME = "cellimo"

_VERSION_TIMEOUT = 20
_COMMAND_TIMEOUT = 300


@dataclass(frozen=True)
class AgentInstallation:
    """What was found for one agent CLI."""

    name: str
    executable: str | None = None
    version: str = ""
    supports_plugins: bool = False
    note: str = ""

    @property
    def detected(self) -> bool:
        return self.executable is not None


@dataclass(frozen=True)
class InstallStep:
    """One command that was (or would be) run."""

    command: list[str]
    ran: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def as_text(self) -> str:
        return " ".join(self.command)


@dataclass
class InstallResult:
    """The outcome of registering the plugin with one agent."""

    agent: str
    steps: list[InstallStep] = field(default_factory=list)
    installed: bool = False
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "installed": self.installed,
            "skipped_reason": self.skipped_reason,
            "steps": [
                {
                    "command": step.command,
                    "ran": step.ran,
                    "exit_code": step.exit_code,
                    "stdout": step.stdout[-2000:],
                    "stderr": step.stderr[-2000:],
                }
                for step in self.steps
            ],
        }


def _run(command: list[str], *, timeout: int = _COMMAND_TIMEOUT) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, "", f"{command[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(command)}: timed out after {timeout}s"
    return completed.returncode, completed.stdout, completed.stderr


def detect_agent(name: str) -> AgentInstallation:
    """Look for one agent CLI and ask it for its version and plugin support."""
    if name not in AGENT_NAMES:
        raise ValueError(f"unknown agent {name!r}; expected one of {list(AGENT_NAMES)}")
    # Deliberately no guess at where the agent keeps its configuration: Cellimo
    # never reads or writes it, and recording an assumed ~/.claude or ~/.codex
    # would be an unverified claim about someone else's layout.
    executable = shutil.which(name)
    if executable is None:
        return AgentInstallation(name=name, note=f"{name} is not on PATH")
    code, stdout, stderr = _run([executable, "--version"], timeout=_VERSION_TIMEOUT)
    version = (stdout or stderr).strip().splitlines()[0] if (stdout or stderr) else ""
    plugin_code, _, _ = _run([executable, "plugin", "--help"], timeout=_VERSION_TIMEOUT)
    return AgentInstallation(
        name=name,
        executable=executable,
        version=version if code == 0 else "",
        supports_plugins=plugin_code == 0,
        note="" if plugin_code == 0 else f"{name} has no `plugin` subcommand",
    )


def detect_agents(names: tuple[str, ...] = AGENT_NAMES) -> list[AgentInstallation]:
    """Detect every requested agent CLI."""
    return [detect_agent(name) for name in names]


def plugin_registered(installation: AgentInstallation) -> tuple[bool, str]:
    """Ask the agent CLI whether the Cellimo plugin is installed.

    Returns ``(registered, detail)``. A CLI that cannot answer is reported as
    not-registered with the reason, never as registered-by-assumption.
    """
    if not installation.detected or installation.executable is None:
        return False, installation.note or f"{installation.name} not found"
    if not installation.supports_plugins:
        return False, installation.note or f"{installation.name} has no plugin support"
    code, stdout, stderr = _run([installation.executable, "plugin", "list"], timeout=60)
    if code != 0:
        detail = (stderr or stdout).strip()[:200]
        return False, f"`{installation.name} plugin list` failed: {detail}"
    listed = PLUGIN_NAME in stdout
    return listed, "installed" if listed else f"{PLUGIN_NAME} is not in the plugin list"


def _install_commands(agent: str, executable: str, plugin_root: Path) -> list[list[str]]:
    """The exact commands used to register the plugin with ``agent``.

    The two CLIs differ only in the verb for installing: Claude Code uses
    ``plugin install``, Codex uses ``plugin add``.
    """
    marketplace = [executable, "plugin", "marketplace", "add", str(plugin_root)]
    selector = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
    if agent == "claude":
        return [marketplace, [executable, "plugin", "install", selector]]
    return [marketplace, [executable, "plugin", "add", selector]]


def install_plugin(
    installation: AgentInstallation,
    plugin_root: Path,
    *,
    dry_run: bool = False,
) -> InstallResult:
    """Register the Cellimo plugin with one agent.

    Re-running is safe: adding a marketplace that is already configured is
    reported by the agent CLI and treated as non-fatal, and the install step is
    still attempted so an upgraded plugin is picked up.
    """
    result = InstallResult(agent=installation.name)
    if not installation.detected or installation.executable is None:
        result.skipped_reason = installation.note or f"{installation.name} not found"
        return result
    if not installation.supports_plugins:
        result.skipped_reason = (
            installation.note or f"{installation.name} does not support plugins"
        )
        return result
    if not (plugin_root / "plugin.toml").is_file():
        result.skipped_reason = f"{plugin_root} is not a Cellimo plugin tree"
        return result

    commands = _install_commands(installation.name, installation.executable, plugin_root)
    for index, command in enumerate(commands):
        if dry_run:
            result.steps.append(InstallStep(command=command, ran=False))
            continue
        code, stdout, stderr = _run(command)
        result.steps.append(
            InstallStep(
                command=command,
                ran=True,
                exit_code=code,
                stdout=stdout,
                stderr=stderr,
            )
        )
        is_marketplace_step = index == 0
        already_present = "already" in (stdout + stderr).lower()
        if code != 0 and not (is_marketplace_step and already_present):
            return result
    result.installed = not dry_run
    return result
