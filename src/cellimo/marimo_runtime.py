"""Talking to Marimo — version checks, notebook validation, server discovery.

Marimo owns the notebook, the kernel and reactive execution. Cellimo only needs
to know three things: is a compatible Marimo installed, is the generated
notebook valid, and is a session running that the agent can attach to.

Everything here shells out to the ``marimo`` CLI or reads its server registry.
Nothing imports ``marimo._code_mode``: that private API belongs to the vendored
marimo-pair skill and to nothing else.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from packaging.version import InvalidVersion, Version

from cellimo.resources import marimo_server_registry_dirs

__all__ = [
    "MARIMO_MIN_VERSION",
    "MarimoServer",
    "MarimoStatus",
    "NotebookCheck",
    "check_notebook",
    "detect_marimo",
    "discover_servers",
    "edit_command",
]

#: The floor is set by what marimo-pair's skill actually uses: ``ctx.packages``
#: and cell ``status``/``errors`` only exist from 0.23.8 onward.
MARIMO_MIN_VERSION = "0.23.8"

_TIMEOUT = 60


@dataclass(frozen=True)
class MarimoStatus:
    """What was found when looking for Marimo."""

    installed: bool = False
    executable: str | None = None
    version: str = ""
    compatible: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "executable": self.executable,
            "version": self.version,
            "minimum": MARIMO_MIN_VERSION,
            "compatible": self.compatible,
            "note": self.note,
        }


@dataclass(frozen=True)
class MarimoServer:
    """One running Marimo server, as recorded in its own registry."""

    server_id: str
    pid: int | None
    host: str
    port: int | None
    base_url: str
    version: str
    started_at: str
    registry_file: str
    alive: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "server_id": self.server_id,
            "pid": self.pid,
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "version": self.version,
            "started_at": self.started_at,
            "registry_file": self.registry_file,
            "alive": self.alive,
        }


@dataclass(frozen=True)
class NotebookCheck:
    """Result of running ``marimo check`` on a notebook."""

    path: str
    ok: bool
    ran: bool = True
    issues: list[dict[str, object]] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "ok": self.ok,
            "ran": self.ran,
            "issues": self.issues,
            "note": self.note,
        }


def _marimo_executable(interpreter: str | Path | None = None) -> str | None:
    """Find Marimo, preferring the *project* runtime over the tool runtime.

    Cellimo is normally installed with ``uv tool install``, which puts it in its
    own isolated environment — one that deliberately does not contain Marimo.
    Looking only next to ``sys.executable`` would therefore report "marimo is not
    installed" to every such user even when their project environment has it.

    Search order: the interpreter the project recorded at ``cellimo init``, then
    this interpreter, then ``PATH``.
    """
    import sys

    candidates = [
        Path(interpreter).parent / "marimo" if interpreter else None,
        Path(sys.executable).parent / "marimo",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("marimo")


def _run(command: list[str], *, timeout: int = _TIMEOUT) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except FileNotFoundError:
        return 127, "", f"{command[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(command)}: timed out after {timeout}s"
    return completed.returncode, completed.stdout, completed.stderr


def detect_marimo(interpreter: str | Path | None = None) -> MarimoStatus:
    """Find Marimo and decide whether its version is usable.

    ``interpreter`` is the project runtime's Python, recorded at ``cellimo
    init``. Pass it whenever a project is known: Marimo lives there, not in the
    tool runtime.
    """
    executable = _marimo_executable(interpreter)
    if executable is None:
        return MarimoStatus(
            note=(
                "marimo was not found in the project runtime, this interpreter, or "
                "on PATH; install it into the environment that runs the notebook "
                "(`pip install 'cellimo[scanpy]'`, or `pip install marimo`)"
            )
        )
    code, stdout, stderr = _run([executable, "--version"])
    raw = (stdout or stderr).strip()
    version = raw.split()[-1] if raw else ""
    if code != 0 or not version:
        return MarimoStatus(
            installed=True,
            executable=executable,
            note=f"`marimo --version` failed: {(stderr or stdout).strip()[:200]}",
        )
    try:
        compatible = Version(version) >= Version(MARIMO_MIN_VERSION)
        note = (
            ""
            if compatible
            else (
                f"marimo {version} is older than {MARIMO_MIN_VERSION}; the marimo-pair "
                f"skill uses APIs that do not exist in it"
            )
        )
    except InvalidVersion:
        compatible = False
        note = f"could not parse marimo version {version!r}"
    return MarimoStatus(
        installed=True,
        executable=executable,
        version=version,
        compatible=compatible,
        note=note,
    )


def check_notebook(path: str | Path, *, interpreter: str | Path | None = None) -> NotebookCheck:
    """Validate a notebook with ``marimo check --format json``.

    Returns ``ran=False`` when Marimo is not installed, so callers can report
    "not checked" rather than pretending the notebook passed.
    """
    target = Path(path)
    if not target.exists():
        return NotebookCheck(path=str(target), ok=False, note="notebook does not exist")

    # Parse it as Python first. `marimo check` reports a file with a trailing
    # syntax error as a clean notebook — it validates the cell graph it managed
    # to read, not the whole file — and "valid Marimo notebook" is a false claim
    # about a file the interpreter cannot even import. This costs microseconds
    # and needs nothing installed.
    try:
        ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
    except SyntaxError as exc:
        return NotebookCheck(
            path=str(target),
            ok=False,
            issues=[
                {
                    "type": "syntax-error",
                    "message": str(exc.msg),
                    "line": exc.lineno or 0,
                    "severity": "breaking",
                }
            ],
            note=f"the notebook is not valid Python: {exc.msg} (line {exc.lineno})",
        )
    except (OSError, UnicodeDecodeError) as exc:
        return NotebookCheck(
            path=str(target), ok=False, note=f"cannot read the notebook: {exc}"
        )

    executable = _marimo_executable(interpreter)
    if executable is None:
        return NotebookCheck(
            path=str(target),
            ok=False,
            ran=False,
            note=(
                "the notebook parses as Python, but marimo is not installed so its "
                "cell graph was not validated"
            ),
        )
    code, stdout, stderr = _run([executable, "check", "--format", "json", str(target)])
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        # `marimo check` failed before producing JSON. Its stderr is the only
        # explanation there is, so it must not be dropped.
        message = (stderr or stdout).strip()
        return NotebookCheck(
            path=str(target),
            ok=code == 0,
            note=message[:500] or f"`marimo check` exited {code} with no output",
        )
    issues = payload.get("issues") or []
    return NotebookCheck(path=str(target), ok=code == 0 and not issues, issues=issues)


def discover_servers() -> list[MarimoServer]:
    """Read Marimo's server registry, the same one marimo-pair discovers through.

    Only servers started with ``--no-token`` register themselves. A stale entry
    whose process is gone is reported with ``alive=False`` rather than dropped,
    so ``doctor`` can explain why nothing is reachable.
    """
    servers: list[MarimoServer] = []
    for directory in marimo_server_registry_dirs():
        if not directory.is_dir():
            continue
        for entry in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            pid = payload.get("pid")
            servers.append(
                MarimoServer(
                    server_id=str(payload.get("server_id", entry.stem)),
                    pid=int(pid) if isinstance(pid, int) else None,
                    host=str(payload.get("host", "")),
                    port=payload.get("port"),
                    base_url=str(payload.get("base_url", "")),
                    version=str(payload.get("version", "")),
                    started_at=str(payload.get("started_at", "")),
                    registry_file=str(entry),
                    alive=_pid_alive(pid) if isinstance(pid, int) else False,
                )
            )
    return servers


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def edit_command(
    notebook: str | Path,
    *,
    executable: str | None = None,
    host: str = "127.0.0.1",
    port: int | None = None,
    headless: bool = False,
    token: bool = False,
    sandbox: bool = False,
) -> list[str]:
    """Build the ``marimo edit`` command line used by ``cellimo start``.

    ``token=False`` (the default) is what makes the session discoverable: only
    servers started with ``--no-token`` write themselves into Marimo's registry,
    which is how marimo-pair finds them. The trade-off — anyone who can reach the
    port can drive the kernel — is why the host defaults to loopback.
    """
    command = [executable or _marimo_executable() or "marimo", "edit", str(notebook)]
    command += ["--host", host]
    if port is not None:
        command += ["-p", str(port)]
    command.append("--no-token" if not token else "--token")
    if headless:
        command.append("--headless")
    if sandbox:
        command.append("--sandbox")
    return command
