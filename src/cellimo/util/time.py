"""Timestamp helpers.

All Cellimo records use timezone-aware UTC ISO-8601 strings so provenance
written on different machines sorts and compares correctly.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["utc_now_iso"]


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
