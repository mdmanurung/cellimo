"""The marimo-pair integration boundary.

Cellimo does not implement notebook control. marimo-pair already does it, so a
pinned, unmodified copy of its skill is vendored into ``plugin/skills/marimo-pair``
and shipped with the plugin. This module is the boundary around that copy: it
reports what is vendored, verifies the copy has not been edited, and answers
"is there a live session to attach to".

The private API marimo-pair drives (``marimo._code_mode``) is imported by its
bash scripts inside the Marimo kernel, never by Cellimo. Nothing in this package
imports it, and a test enforces that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cellimo.errors import CellimoError
from cellimo.marimo_runtime import MarimoServer, discover_servers
from cellimo.resources import plugin_root, vendored_marimo_pair_root
from cellimo.util.hashing import hash_file

__all__ = [
    "PairStatus",
    "VendorInfo",
    "pair_status",
    "vendor_info",
    "verify_vendored_copy",
]


@dataclass(frozen=True)
class VendorInfo:
    """Where the vendored skill came from."""

    name: str
    upstream_repository: str
    pinned_tag: str
    pinned_commit: str
    license: str
    copyright: str
    path: Path
    requires: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "upstream_repository": self.upstream_repository,
            "pinned_tag": self.pinned_tag,
            "pinned_commit": self.pinned_commit,
            "license": self.license,
            "copyright": self.copyright,
            "path": str(self.path),
            "requires": self.requires,
            "file_count": len(self.files),
        }


@dataclass(frozen=True)
class PairStatus:
    """Whether the agent can actually pair with a notebook right now."""

    vendored: bool
    intact: bool
    problems: list[str] = field(default_factory=list)
    servers: list[MarimoServer] = field(default_factory=list)
    vendor: VendorInfo | None = None

    @property
    def live_servers(self) -> list[MarimoServer]:
        return [server for server in self.servers if server.alive]

    def to_dict(self) -> dict[str, object]:
        return {
            "vendored": self.vendored,
            "intact": self.intact,
            "problems": self.problems,
            "servers": [server.to_dict() for server in self.servers],
            "live_servers": len(self.live_servers),
            "vendor": self.vendor.to_dict() if self.vendor else None,
        }


def _manifest_path() -> Path:
    return plugin_root() / "vendor" / "marimo-pair.json"


def vendor_info() -> VendorInfo:
    """Read the vendoring record written when the skill was copied in."""
    path = _manifest_path()
    if not path.is_file():
        raise CellimoError(f"vendoring record {path} is missing")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return VendorInfo(
        name=payload.get("name", "marimo-pair"),
        upstream_repository=payload.get("upstream_repository", ""),
        pinned_tag=payload.get("pinned_tag", ""),
        pinned_commit=payload.get("pinned_commit", ""),
        license=payload.get("license", ""),
        copyright=payload.get("copyright", ""),
        path=vendored_marimo_pair_root(),
        requires=payload.get("requires", {}),
        files=payload.get("files", {}),
    )


def verify_vendored_copy() -> list[str]:
    """Check the vendored skill against its recorded hashes.

    An edited vendored file is a licensing and a correctness problem: the record
    says "unmodified copy of v0.0.18" and it has to stay true.
    """
    try:
        info = vendor_info()
    except CellimoError as exc:
        return [str(exc)]
    problems: list[str] = []
    root = info.path
    if not root.is_dir():
        return [f"vendored marimo-pair skill is missing from {root}"]
    for relative, expected in info.files.items():
        candidate = root / relative
        if not candidate.is_file():
            problems.append(f"vendored file {relative} is missing")
            continue
        actual = hash_file(candidate)
        if actual != expected:
            problems.append(
                f"vendored file {relative} was modified "
                f"(expected {expected[:12]}, found {actual[:12]})"
            )
    present = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    for extra in sorted(present - set(info.files)):
        problems.append(f"vendored tree has an unrecorded file: {extra}")
    return problems


def pair_status() -> PairStatus:
    """Summarise pairing readiness for ``cellimo doctor``."""
    try:
        info: VendorInfo | None = vendor_info()
    except CellimoError:
        info = None
    problems = verify_vendored_copy()
    return PairStatus(
        vendored=info is not None,
        intact=not problems,
        problems=problems,
        servers=discover_servers(),
        vendor=info,
    )
