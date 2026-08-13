"""The validation engine behind ``cellimo check``.

Every scientific check is a pure function over structured provenance. One
structural check (S009) reads Marimo cell boundaries and citation comments to
measure whether grounding happened; it does not execute the notebook or infer a
scientific claim from prose.

Findings carry a stable code (``S###`` structural, ``C###`` scientific), a
severity, the record they point at, and a remedy. ``cellimo check`` exits
non-zero when any finding is an error.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from cellimo.artifacts.descriptor import ArtifactDescriptor
from cellimo.provenance.records import (
    DecisionRecord,
    EnvironmentRecord,
    ReferenceRecord,
    StatisticsRecord,
)
from cellimo.schema import Severity
from cellimo.util.time import utc_now_iso

if TYPE_CHECKING:  # pragma: no cover
    from cellimo.config import CellimoConfig
    from cellimo.project.project import Project

__all__ = [
    "CHECKS",
    "Check",
    "Finding",
    "ValidationContext",
    "ValidationReport",
    "register",
    "run_checks",
]


class Finding(BaseModel):
    """One thing the validator has to say about a project."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    severity: Severity
    title: str
    detail: str
    #: Which record this points at, e.g. ``statistics:de-stim-vs-ctrl`` or
    #: ``artifact:post_qc:9f2a…``. Empty for project-level findings.
    location: str = ""
    remedy: str = ""
    #: Literature or documentation supporting the rule.
    references: list[str] = Field(default_factory=list)

    def format_line(self) -> str:
        prefix = {"error": "ERROR", "warning": "WARN ", "info": "INFO "}[self.severity]
        where = f" [{self.location}]" if self.location else ""
        return f"{prefix} {self.code}{where}: {self.title}"


class ValidationContext:
    """Everything a check is allowed to look at.

    Built once and passed to every check, so the append-only logs are read from
    disk exactly once per ``cellimo check`` run.
    """

    def __init__(self, project: Project) -> None:
        self.project = project
        self.config: CellimoConfig = project.config
        self.store = project.store
        self.registry = project.artifacts
        self.artifacts: list[ArtifactDescriptor] = self.store.artifacts()
        self.decisions: list[DecisionRecord] = self.store.decisions()
        self.references: list[ReferenceRecord] = self.store.references()
        self.statistics: list[StatisticsRecord] = self.store.statistics()
        self.environment: EnvironmentRecord | None = self.store.environment()
        self.by_sha = {descriptor.sha256: descriptor for descriptor in self.artifacts}

    # -- convenience used by several checks --------------------------------

    @property
    def confirmatory(self) -> list[StatisticsRecord]:
        return [record for record in self.statistics if record.mode == "confirmatory"]

    def artifacts_at(self, stage: str) -> list[ArtifactDescriptor]:
        return [descriptor for descriptor in self.artifacts if descriptor.stage == stage]

    def decisions_for(self, sha256: str) -> list[DecisionRecord]:
        return [record for record in self.decisions if sha256 in record.artifacts]

    def source_descriptor(self) -> ArtifactDescriptor | None:
        candidates = self.artifacts_at("source")
        return candidates[0] if candidates else None


class ValidationReport(BaseModel):
    """The result of one ``cellimo check`` run."""

    model_config = ConfigDict(extra="forbid")

    project: str
    root: str
    checked_at: str = Field(default_factory=utc_now_iso)
    findings: list[Finding] = Field(default_factory=list)
    checks_run: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def exit_code(self) -> int:
        return 1 if self.errors else 0

    def counts(self) -> dict[str, int]:
        return {
            "error": len(self.errors),
            "warning": len(self.warnings),
            "info": len([f for f in self.findings if f.severity == "info"]),
            "checks_run": self.checks_run,
        }

    def to_text(self) -> str:
        if not self.findings:
            return (
                f"cellimo check: {self.checks_run} checks passed for "
                f"{self.project!r} — no findings."
            )
        lines = [f"cellimo check: {self.project!r} ({self.root})", ""]
        for finding in sorted(
            self.findings,
            key=lambda item: ({"error": 0, "warning": 1, "info": 2}[item.severity], item.code),
        ):
            lines.append(finding.format_line())
            lines.append(f"      {finding.detail}")
            if finding.remedy:
                lines.append(f"      fix: {finding.remedy}")
            for reference in finding.references:
                lines.append(f"      see: {reference}")
            lines.append("")
        counts = self.counts()
        lines.append(
            f"{counts['error']} error(s), {counts['warning']} warning(s) "
            f"from {self.checks_run} checks."
        )
        return "\n".join(lines)


class Check:
    """One named validation rule."""

    def __init__(
        self,
        code: str,
        title: str,
        function: Callable[[ValidationContext], Sequence[Finding]],
    ) -> None:
        self.code = code
        self.title = title
        self.function = function

    def __call__(self, context: ValidationContext) -> Sequence[Finding]:
        return self.function(context)


#: All registered checks, in the order they are reported.
CHECKS: list[Check] = []


def register(code: str, title: str) -> Callable[
    [Callable[[ValidationContext], Sequence[Finding]]],
    Callable[[ValidationContext], Sequence[Finding]],
]:
    """Decorator registering a check function under ``code``."""

    def decorator(
        function: Callable[[ValidationContext], Sequence[Finding]],
    ) -> Callable[[ValidationContext], Sequence[Finding]]:
        if any(existing.code == code for existing in CHECKS):
            raise ValueError(f"duplicate check code {code}")
        CHECKS.append(Check(code, title, function))
        return function

    return decorator


def run_checks(project: Project, *, only: Sequence[str] | None = None) -> ValidationReport:
    """Run every registered check against ``project``."""
    from cellimo.validation import checks as _checks  # noqa: F401  (registers checks)

    context = ValidationContext(project)
    if only:
        known = {check.code for check in CHECKS}
        unknown = sorted(set(only) - known)
        if unknown:
            # Running zero checks and reporting success would be the worst
            # possible answer to a typo.
            raise ValueError(
                f"unknown check code(s): {', '.join(unknown)}. "
                f"Registered codes: {', '.join(sorted(known))}"
            )
    selected = [check for check in CHECKS if not only or check.code in set(only)]
    findings: list[Finding] = []
    for check in selected:
        findings.extend(check(context))
    return ValidationReport(
        project=project.config.project.name,
        root=str(project.root),
        findings=findings,
        checks_run=len(selected),
    )
