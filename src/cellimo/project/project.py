"""The Cellimo project — the transparent Python API.

A project is a directory containing ``cellimo.yaml``, a Marimo notebook, and a
``provenance/`` trail. This class is the only supported way to mutate that
trail, which is what makes the safety and lineage guarantees enforceable.

Everything here is explicit. There is no ``run_full_pipeline()``: scientific
transformations stay visible in the notebook, and this API records what they
did.

    from cellimo import Project

    project = Project.open()
    audit = project.audit_anndata("data/source.h5ad", backed=True)
    project.record_design(
        sample="sample_id",
        donor="participant_id",
        condition="condition",
        time="timepoint",
        batch="library_batch",
    )
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cellimo.artifacts.descriptor import ArtifactDescriptor, Exclusion
from cellimo.artifacts.registry import ArtifactRegistry
from cellimo.audit.anndata_audit import AuditReport, audit_anndata
from cellimo.config import (
    CONFIG_FILENAME,
    CellimoConfig,
    DesignSection,
    EnvironmentSection,
    ProjectSection,
    SourceSection,
    find_config,
    load_config,
    save_config,
)
from cellimo.environment import (
    capture_environment,
    detect_environment_manager,
    detect_project_interpreter,
    interpreter_version,
)
from cellimo.errors import (
    ArtifactError,
    ConfigError,
    DesignError,
    ProjectExistsError,
    ProjectNotFoundError,
    SourceImmutabilityError,
)
from cellimo.project.scaffold import scaffold_project
from cellimo.provenance.records import (
    DecisionRecord,
    EffectSizeReport,
    ReferenceRecord,
    StatisticsRecord,
    UncertaintyReport,
)
from cellimo.provenance.store import ProvenanceStore
from cellimo.schema import DESIGN_FIELDS, SCHEMA_VERSION
from cellimo.util.atomic import atomic_write_json
from cellimo.util.hashing import hash_file, hash_json, short_hash
from cellimo.util.paths import resolve_in_project, same_file
from cellimo.util.time import utc_now_iso

if TYPE_CHECKING:  # pragma: no cover
    from cellimo.validation.engine import ValidationReport

__all__ = ["Project"]


class Project:
    """One Cellimo project rooted at a directory containing ``cellimo.yaml``."""

    def __init__(self, root: str | Path, config: CellimoConfig) -> None:
        self.root = Path(root).resolve()
        self.config = config
        self.store = ProvenanceStore(self.root / config.paths.provenance)

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path | None = None) -> Project:
        """Discover and load the project containing ``path`` (default: cwd)."""
        config_path = find_config(path)
        if config_path is None:
            start = Path(path or Path.cwd()).resolve()
            raise ProjectNotFoundError(
                f"no {CONFIG_FILENAME} found in {start} or any parent directory; "
                f"run `cellimo init DATASET` to create a project"
            )
        return cls(config_path.parent, load_config(config_path))

    @classmethod
    def init(
        cls,
        root: str | Path,
        dataset: str | Path,
        *,
        profile: str = "scanpy",
        name: str | None = None,
        description: str = "",
        random_seed: int | None = None,
        cellimo_version: str = "",
        exist_ok: bool = False,
        scaffold: bool = True,
        interpreter: str | Path | None = None,
    ) -> Project:
        """Create a new project around ``dataset`` and register it as the source.

        The dataset is never copied or moved: it is hashed where it lies and
        recorded as immutable.
        """
        root_path = Path(root).expanduser().resolve()
        config_path = root_path / CONFIG_FILENAME
        if config_path.exists() and not exist_ok:
            raise ProjectExistsError(
                f"{config_path} already exists; pass --force to reinitialise or "
                f"choose a different directory"
            )

        # Re-initialising in place must not silently unsay what the project
        # already recorded. Provenance is append-only and survives; if the
        # configuration reset alongside it, a project could end up with an
        # approved-design statistics record and a config claiming the design was
        # never approved — a contradiction no check could resolve.
        previous: CellimoConfig | None = None
        if config_path.exists():
            try:
                previous = load_config(config_path)
            except ConfigError:
                previous = None  # unreadable: a fresh config is the repair

        source = Path(dataset).expanduser().resolve()
        if not source.exists():
            raise ConfigError(f"dataset {source} does not exist")
        if not source.is_file():
            raise ConfigError(f"dataset {source} is not a regular file")

        # The notebook runs in the *project* runtime. When Cellimo is installed
        # with `uv tool install` that is a different interpreter from this one,
        # so it is detected rather than assumed.
        project_interpreter = detect_project_interpreter(root_path, interpreter)

        config = CellimoConfig(
            schema_version=SCHEMA_VERSION,
            cellimo_version=cellimo_version,
            project=ProjectSection(
                name=name or root_path.name,
                description=description,
            ),
            source=SourceSection(
                path=cls._source_reference(root_path, source),
                # Filled in from the registration below, which hashes the
                # file as part of its own contract. Hashing here as well
                # would read a large dataset twice for no gain.
                sha256="",
                bytes=source.stat().st_size,
                format=source.suffix.lstrip(".").lower() or "unknown",
            ),
            environment=EnvironmentSection(
                profile=profile,  # type: ignore[arg-type]
                python=interpreter_version(project_interpreter),
                interpreter=project_interpreter,
                manager=detect_environment_manager(project_interpreter),
            ),
            random_seed=random_seed if random_seed is not None else 0,
        )

        if previous is not None:
            config = config.model_copy(
                update={
                    "design": previous.design,
                    "policies": previous.policies,
                    "checkpoint": previous.checkpoint,
                    "random_seed": (
                        random_seed if random_seed is not None else previous.random_seed
                    ),
                    "project": config.project.model_copy(
                        update={"created": previous.project.created}
                    ),
                }
            )

        try:
            root_path.mkdir(parents=True, exist_ok=True)
            save_config(config, config_path)
            scaffold_project(root_path, config, force=False, with_notebook=scaffold)
        except OSError as exc:
            raise ConfigError(
                f"cannot create the project at {root_path}: {exc}"
            ) from exc

        project = cls(root_path, config)
        project.store.ensure_layout()
        registered = project.register_source()
        project.config.source = project.config.source.model_copy(
            update={"sha256": registered.sha256}
        )
        project.save()
        project.capture_environment()
        project.record_decision(
            kind="note",
            summary=f"Project initialised around {source.name}",
            rationale=(
                "Source dataset registered as immutable; all analysis writes go to "
                "artifacts/ and results/."
            ),
            parameters={
                "profile": profile,
                "random_seed": config.random_seed,
                "reinitialised": previous is not None,
            },
            actor="cellimo",
        )
        project.write_manifest()
        return project

    @staticmethod
    def _source_reference(root: Path, source: Path) -> str:
        """Store the source path relative to the project when it lives inside it."""
        try:
            return source.relative_to(root).as_posix()
        except ValueError:
            return str(source)

    # -- locations ---------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def source_path(self) -> Path:
        """Absolute path of the registered source dataset."""
        declared = Path(self.config.source.path).expanduser()
        return declared if declared.is_absolute() else (self.root / declared).resolve()

    @property
    def notebook_path(self) -> Path:
        return self.root / self.config.paths.notebook

    @property
    def artifacts(self) -> ArtifactRegistry:
        return ArtifactRegistry(self.root, self.store, source_path=self.source_path)

    def path(self, key: str) -> Path:
        """Return one of the configured output directories by name."""
        try:
            relative = getattr(self.config.paths, key)
        except AttributeError as exc:
            raise ConfigError(f"unknown project path {key!r}") from exc
        return self.root / relative

    def resolve(self, relative: str | Path, *, what: str = "path") -> Path:
        """Resolve a project-relative path, rejecting anything outside the root."""
        return resolve_in_project(self.root, relative, what=what)

    def save(self) -> Path:
        """Write ``cellimo.yaml`` atomically."""
        return save_config(self.config, self.config_path)

    # -- source ------------------------------------------------------------

    def register_source(self) -> ArtifactDescriptor:
        """Register the configured source dataset as the root of all lineage."""
        source = self.source_path
        if not source.exists():
            raise ConfigError(f"registered source {source} is missing")
        return self.artifacts.register(
            source,
            stage="source",
            kind="anndata",
            is_source=True,
            description="Immutable source dataset",
            params={"declared_path": self.config.source.path},
        )

    def verify_source(self) -> tuple[bool, str]:
        """Check that the source file still hashes to the recorded digest."""
        source = self.source_path
        if not source.exists():
            return False, f"source {source} is missing"
        if not self.config.source.sha256:
            return False, "no source hash was recorded at initialisation"
        current = hash_file(source)
        if current != self.config.source.sha256:
            return False, (
                f"source {source} changed on disk: recorded "
                f"{short_hash(self.config.source.sha256)}, found {short_hash(current)}"
            )
        return True, f"source unchanged ({short_hash(current)})"

    def assert_writable(self, target: str | Path) -> Path:
        """Resolve ``target`` for writing, refusing the source and any escape.

        This is the guard every Cellimo write goes through. It does not, and
        cannot, constrain arbitrary Python run by the agent in the notebook.
        """
        resolved = self.resolve(target, what="output path")
        source = self.source_path
        if source.exists() and resolved.exists() and same_file(resolved, source):
            raise SourceImmutabilityError(
                f"refusing to write {target}: it is the registered source dataset "
                f"({source}), which is immutable"
            )
        if not source.exists() and resolved == source:
            raise SourceImmutabilityError(
                f"refusing to write {target}: it is the registered source path"
            )
        return resolved

    # -- audit -------------------------------------------------------------

    def audit_anndata(
        self,
        path: str | Path | None = None,
        *,
        backed: bool = True,
        register: bool = True,
    ) -> AuditReport:
        """Audit an ``.h5ad`` file and record the report under ``provenance/``.

        Defaults to the registered source. The report is written as JSON and
        registered as an ``audit``-stage artifact so it appears in lineage.
        """
        target = Path(path).expanduser() if path is not None else self.source_path
        if not target.is_absolute():
            target = (self.root / target).resolve()
        report = audit_anndata(target, backed=backed)
        if not register:
            return report

        # The audit file is named by the *content* of its findings, with the
        # timestamp excluded from the fingerprint. Re-auditing an unchanged
        # dataset therefore reuses the same file and the same registration
        # instead of orphaning the previous descriptor with a new mtime.
        payload = report.model_dump(mode="json")
        fingerprint = payload.copy()
        fingerprint.pop("audited_at", None)
        audits_dir = self.store.root / "audits"
        audits_dir.mkdir(parents=True, exist_ok=True)
        report_path = (
            audits_dir
            / f"{short_hash(report.sha256)}-{short_hash(hash_json(fingerprint))}.json"
        )
        if not report_path.exists():
            atomic_write_json(report_path, payload)

        # The audit derives from the file it audited: the registered source when
        # that is what was audited, otherwise whichever artifact it was.
        audited = self.artifacts.store.artifact_by_sha(report.sha256)
        parent = audited.sha256 if audited is not None else self.config.source.sha256
        self.artifacts.register(
            report_path,
            stage="audit",
            kind="audit",
            parent_sha256=parent,
            description=f"AnnData audit of {target.name}",
            params={"backed": backed, "audited_path": str(target)},
            n_obs=report.n_obs,
            n_vars=report.n_vars,
            counts_layer=report.raw_counts.layer,
            raw_counts_available=report.raw_counts.available,
            obs_keys=report.obs_names(),
            layers=report.layers,
            obsm_keys=report.obsm_keys,
            representation="raw_counts" if report.raw_counts.location == "X" else "unknown",
        )
        self.record_decision(
            kind="note",
            stage="audit",
            summary=f"Audited {target.name}",
            rationale="; ".join(report.summary_lines()[:3]),
            parameters={"backed": backed},
            actor="cellimo",
        )
        return report

    # -- design ------------------------------------------------------------

    def record_design(
        self,
        *,
        sample: str | None = None,
        donor: str | None = None,
        condition: str | None = None,
        time: str | None = None,
        batch: str | None = None,
        study: str | None = None,
        experimental_unit: str | None = None,
        notes: str = "",
        actor: str = "agent",
        approve: bool = False,
        approved_by: str | None = None,
    ) -> DesignSection:
        """Declare the experimental design.

        The agent may propose values, which sets ``status`` to ``proposed``.
        Only :meth:`approve_design` (or ``approve=True`` with an explicit
        ``approved_by``) unblocks confirmatory statistics.

        ``experimental_unit`` defaults to the donor column when given, else the
        sample column — but never to a cell-level identifier.
        """
        design = self.config.design
        updates = {
            "sample": sample if sample is not None else design.sample,
            "donor": donor if donor is not None else design.donor,
            "condition": condition if condition is not None else design.condition,
            "time": time if time is not None else design.time,
            "batch": batch if batch is not None else design.batch,
            "study": study if study is not None else design.study,
            "notes": notes or design.notes,
        }
        unit = (
            experimental_unit
            or design.experimental_unit
            or updates["donor"]
            or updates["sample"]
        )
        updates["experimental_unit"] = unit

        if approve:
            if not approved_by:
                raise DesignError(
                    "approving a design requires approved_by (a person, or "
                    "'autonomous_authorization' when the user has authorised it)"
                )
            if not unit:
                raise DesignError(
                    "cannot approve a design without an experimental unit; name "
                    "the obs column that identifies the biological replicate"
                )
            updates["status"] = "approved"
            updates["approved_by"] = approved_by
            updates["approved_at"] = utc_now_iso()
        elif design.status != "approved":
            updates["status"] = "proposed" if any(
                updates[field] for field in DESIGN_FIELDS
            ) else "unresolved"
        else:
            # Editing an approved design revokes approval: the comparison
            # changed, so the human sign-off no longer applies.
            updates["status"] = "proposed"
            updates["approved_by"] = None
            updates["approved_at"] = None

        self.config.design = DesignSection.model_validate({**design.model_dump(), **updates})
        # Log before saving. Either order can be interrupted, but only one is
        # interrupted safely: a decision with no approved config blocks
        # confirmatory analysis, while an approved config with no decision is
        # the state C002 downgrades to a *warning* — so a crash in that window
        # left a project that `cellimo check` passed. Of the nine mutating paths
        # here this is the only one whose missing record any check reads.
        self.record_decision(
            kind="design",
            summary=(
                f"Design {'approved' if approve else 'proposed'}: "
                + ", ".join(
                    f"{key}={value}"
                    for key, value in self.config.design.declared_fields().items()
                )
            )
            or "Design updated",
            rationale=notes,
            parameters={
                **self.config.design.declared_fields(),
                "experimental_unit": self.config.design.experimental_unit,
                "status": self.config.design.status,
                # Recorded so ``cellimo check`` can ask who approved *this*
                # design rather than trusting the free-text approved_by string.
                "approved": bool(approve),
                "approved_by": self.config.design.approved_by,
            },
            # The caller's declared actor is recorded as-is. Inferring
            # actor="user" from the presence of an approval fabricated the one
            # fact that matters: an agent calling approve_design() produced a
            # decision log saying a human had approved it.
            actor=actor,
        )
        self.save()
        self.write_manifest()
        return self.config.design

    def approve_design(
        self,
        *,
        approved_by: str = "user",
        experimental_unit: str | None = None,
        actor: str = "agent",
    ) -> DesignSection:
        """Approve the current design, unblocking confirmatory analysis.

        ``actor`` records who actually made this call. It defaults to ``agent``
        because that is what an unattended call is: nothing in a library can
        verify that a human is present. The notebook's approval button passes
        ``actor="user"``, which is as close to a human act as this can get, and
        ``cellimo check`` reports an approval whose actor is not a user.
        """
        return self.record_design(
            experimental_unit=experimental_unit,
            approve=True,
            approved_by=approved_by,
            actor=actor,
        )

    def authorize_autonomous(self, reason: str) -> None:
        """Record that the user authorised the agent to approve the design itself.

        This lowers a safety bar, so it is written to both the configuration and
        the decision log with the stated reason.
        """
        self.config.policies = self.config.policies.model_copy(
            update={"autonomous_authorization": True}
        )
        self.save()
        self.record_decision(
            kind="authorization",
            summary="Autonomous design approval authorised by the user",
            rationale=reason,
            actor="user",
        )

    # -- recording ---------------------------------------------------------

    def register_artifact(
        self,
        path: str | Path,
        *,
        stage: str,
        kind: str = "anndata",
        parent_sha256: str | None = None,
        exclusions: Sequence[Exclusion | dict[str, Any]] = (),
        **fields: Any,
    ) -> ArtifactDescriptor:
        """Register a produced file, refusing writes to the source."""
        self.assert_writable(path)
        if parent_sha256 is None and stage != "source":
            parent_sha256 = self._infer_parent(stage)
        descriptor = self.artifacts.register(
            path,
            stage=stage,
            kind=kind,
            parent_sha256=parent_sha256,
            exclusions=exclusions,
            **fields,
        )
        self.write_manifest()
        return descriptor

    def _infer_parent(self, stage: str) -> str | None:
        """Use the most recent artifact of the preceding stage as the parent."""
        from cellimo.schema import STAGES  # local import keeps the module list in one place

        index = STAGES.index(stage) if stage in STAGES else -1
        for previous in reversed(STAGES[:index]):
            candidate = self.artifacts.latest(previous)
            if candidate is not None:
                return candidate.sha256
        return None

    def record_decision(
        self,
        *,
        kind: str,
        summary: str,
        stage: str | None = None,
        rationale: str = "",
        parameters: dict[str, Any] | None = None,
        references: Sequence[str] = (),
        artifacts: Sequence[str] = (),
        actor: str = "agent",
    ) -> DecisionRecord:
        """Record one analytical decision."""
        record = DecisionRecord(
            kind=kind,  # type: ignore[arg-type]
            stage=stage,  # type: ignore[arg-type]
            summary=summary,
            rationale=rationale,
            parameters=dict(parameters or {}),
            references=list(references),
            artifacts=list(artifacts),
            actor=actor,
        )
        return self.store.append_decision(record)

    def record_reference(
        self,
        *,
        reference_id: str,
        title: str = "",
        source: str = "",
        url: str = "",
        package: str = "",
        package_version: str = "",
        section_ids: Sequence[str] = (),
        content_hash: str = "",
        retrieval_score: float | None = None,
        query: str = "",
        used_for: str = "",
        stage: str | None = None,
    ) -> ReferenceRecord:
        """Record a reference that informed the analysis."""
        record = ReferenceRecord(
            reference_id=reference_id,
            title=title,
            source=source,
            url=url,
            package=package,
            package_version=package_version,
            section_ids=list(section_ids),
            content_hash=content_hash,
            retrieval_score=retrieval_score,
            query=query,
            used_for=used_for,
            stage=stage,  # type: ignore[arg-type]
        )
        return self.store.append_reference(record)

    def record_statistics(
        self,
        *,
        name: str,
        test: str,
        mode: str = "exploratory",
        experimental_unit: str | None = None,
        unit_level: str = "unknown",
        n_units: dict[str, int] | None = None,
        n_cells: dict[str, int] | None = None,
        groups: Sequence[str] = (),
        input_artifact_sha256: str = "",
        input_representation: str = "unknown",
        aggregation: str = "none",
        covariates: Sequence[str] = (),
        effect_size: dict[str, Any] | EffectSizeReport | None = None,
        uncertainty: dict[str, Any] | UncertaintyReport | None = None,
        justification: str = "",
        seed: int | None = None,
        output_artifact_sha256: str | None = None,
        packages: dict[str, str] | None = None,
    ) -> StatisticsRecord:
        """Record one statistical comparison.

        Confirmatory tests are refused outright unless the design is approved —
        the check exists in ``cellimo check`` as well, but failing here means the
        unusable result is never produced in the first place.
        """
        if (
            mode == "confirmatory"
            and self.config.policies.require_design_approval_for_inference
            and not self.config.design.is_approved()
        ):
            raise DesignError(
                    "cannot record a confirmatory analysis: the experimental "
                    "design is not approved. Declare the design, name the "
                    "biological replicate, and approve it (or record an explicit "
                    "autonomous authorisation) first."
                )
        unit = experimental_unit or self.config.design.experimental_unit
        record = StatisticsRecord(
            name=name,
            test=test,
            mode=mode,  # type: ignore[arg-type]
            experimental_unit=unit,
            unit_level=unit_level,  # type: ignore[arg-type]
            n_units=dict(n_units or {}),
            n_cells=dict(n_cells or {}),
            groups=list(groups),
            input_artifact_sha256=input_artifact_sha256,
            input_representation=input_representation,  # type: ignore[arg-type]
            aggregation=aggregation,
            covariates=list(covariates),
            effect_size=_as_effect(effect_size),
            uncertainty=_as_uncertainty(uncertainty),
            justification=justification,
            seed=seed if seed is not None else self.config.random_seed,
            output_artifact_sha256=output_artifact_sha256,
            packages=dict(packages or {}),
        )
        return self.store.append_statistics(record)

    def capture_environment(self) -> Path:
        """Snapshot the *project* runtime and write ``provenance/environment.json``.

        Called from inside the notebook this is already the right interpreter;
        called from the CLI it is not, so the recorded project interpreter is
        queried instead of this process.
        """
        record = capture_environment(
            cellimo_version=self.config.cellimo_version,
            random_seed=self.config.random_seed,
            interpreter=self.config.environment.interpreter or None,
        )
        return self.store.write_environment(record)

    def write_manifest(self) -> Path:
        """Regenerate ``provenance/manifest.json`` from the append-only logs."""
        manifest = self.store.build_manifest(
            project_name=self.config.project.name,
            cellimo_version=self.config.cellimo_version,
            source={
                "path": self.config.source.path,
                "sha256": self.config.source.sha256,
                "bytes": self.config.source.bytes,
                "immutable": self.config.source.immutable,
            },
            design=self.config.design.model_dump(mode="json"),
        )
        return self.store.write_manifest(manifest)

    # -- stages ------------------------------------------------------------

    @contextmanager
    def stage(
        self,
        name: str,
        *,
        summary: str = "",
        parent_sha256: str | None = None,
        params: dict[str, Any] | None = None,
        references: Sequence[str] = (),
    ) -> Iterator[StageContext]:
        """Bracket one analysis stage, recording what it produced.

        The transformation itself stays in the notebook and stays visible; this
        only records the outcome::

            with project.stage("post_qc", params={"min_genes": 200}) as qc:
                filtered = run_qc(adata)          # ordinary, visible code
                filtered.write_h5ad(qc.output("post_qc.h5ad"))
                qc.add_exclusion("low gene count", n_removed=1200, n_remaining=8800)
                qc.set_matrix_facts(representation="raw_counts", counts_layer="counts")
        """
        context = StageContext(
            project=self,
            stage=name,
            params=dict(params or {}),
            parent_sha256=parent_sha256,
            references=list(references),
            summary=summary,
        )
        yield context
        context.finish()

    # -- validation --------------------------------------------------------

    def check(self) -> ValidationReport:
        """Run structural and scientific validation over this project."""
        from cellimo.validation.engine import run_checks  # local: keeps import graph acyclic

        return run_checks(self)


def _as_effect(value: dict[str, Any] | EffectSizeReport | None) -> EffectSizeReport:
    if value is None:
        return EffectSizeReport()
    if isinstance(value, EffectSizeReport):
        return value
    return EffectSizeReport.model_validate(value)


def _as_uncertainty(value: dict[str, Any] | UncertaintyReport | None) -> UncertaintyReport:
    if value is None:
        return UncertaintyReport()
    if isinstance(value, UncertaintyReport):
        return value
    return UncertaintyReport.model_validate(value)


class StageContext:
    """Handle yielded by :meth:`Project.stage`."""

    def __init__(
        self,
        *,
        project: Project,
        stage: str,
        params: dict[str, Any],
        parent_sha256: str | None,
        references: list[str],
        summary: str,
    ) -> None:
        self.project = project
        self.stage = stage
        self.params = params
        self.parent_sha256 = parent_sha256
        self.references = references
        self.summary = summary or f"Stage {stage}"
        self.exclusions: list[Exclusion] = []
        self._output: Path | None = None
        self._kind = "anndata"
        self._matrix_facts: dict[str, Any] = {}
        self.descriptor: ArtifactDescriptor | None = None

    def output(self, relative: str | Path, *, kind: str = "anndata") -> Path:
        """Reserve and return a safe output path inside the project.

        Validated twice: once before creating the parent directories and once
        after. Between those two moments a not-yet-existing path component could
        be replaced by a symlink pointing out of the project, and the caller
        writes to the returned path trusting this method's promise.
        """
        resolved = self.project.assert_writable(relative)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved = self.project.assert_writable(relative)
        self._output = resolved
        self._kind = kind
        return resolved

    def add_exclusion(
        self,
        reason: str,
        *,
        axis: str = "obs",
        n_before: int = 0,
        n_removed: int = 0,
        n_remaining: int = 0,
        criteria: dict[str, Any] | None = None,
        by_sample: dict[str, int] | None = None,
        stratified_by: str = "",
        pooling_justification: str = "",
    ) -> None:
        """Record one removal of cells or genes, ideally stratified by sample.

        ``n_before`` defaults to ``n_removed + n_remaining``; pass it explicitly
        when the numbers come from the object itself so the validator can
        reconcile them instead of trusting arithmetic it performed.
        """
        self.exclusions.append(
            Exclusion(
                reason=reason,
                axis=axis,
                n_before=n_before or (n_removed + n_remaining),
                n_removed=n_removed,
                n_remaining=n_remaining,
                criteria=dict(criteria or {}),
                by_sample=dict(by_sample or {}),
                stratified_by=stratified_by,
                pooling_justification=pooling_justification,
            )
        )

    def set_matrix_facts(
        self,
        *,
        representation: str = "unknown",
        counts_layer: str | None = None,
        raw_counts_available: bool | None = None,
        n_obs: int | None = None,
        n_vars: int | None = None,
        obs_keys: Sequence[str] = (),
        layers: Sequence[str] = (),
        obsm_keys: Sequence[str] = (),
    ) -> None:
        """State what the produced matrix contains. Read by ``cellimo check``."""
        self._matrix_facts = {
            "representation": representation,
            "counts_layer": counts_layer,
            "raw_counts_available": (
                raw_counts_available
                if raw_counts_available is not None
                else (counts_layer is not None or representation == "raw_counts")
            ),
            "n_obs": n_obs,
            "n_vars": n_vars,
            "obs_keys": list(obs_keys),
            "layers": list(layers),
            "obsm_keys": list(obsm_keys),
        }

    def finish(self) -> ArtifactDescriptor | None:
        """Register the stage output and its decision record."""
        if self._output is None:
            self.project.record_decision(
                kind="note",
                stage=self.stage,  # type: ignore[arg-type]
                summary=self.summary,
                parameters=self.params,
                references=self.references,
            )
            return None
        if not self._output.exists():
            raise ArtifactError(
                f"stage {self.stage!r} reserved {self._output} but nothing was "
                f"written there; write the output before leaving the stage block"
            )
        descriptor = self.project.register_artifact(
            self._output,
            stage=self.stage,
            kind=self._kind,
            parent_sha256=self.parent_sha256,
            description=self.summary,
            params=self.params,
            exclusions=self.exclusions,
            **self._matrix_facts,
        )
        self.project.record_decision(
            kind="filtering" if self.exclusions else "note",
            stage=self.stage,  # type: ignore[arg-type]
            summary=self.summary,
            parameters=self.params,
            references=self.references,
            artifacts=[descriptor.sha256],
        )
        self.descriptor = descriptor
        return descriptor
