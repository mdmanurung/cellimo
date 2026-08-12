"""``cellimo.yaml`` loading, validation and project discovery."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cellimo.config import (
    CellimoConfig,
    DesignSection,
    PoliciesSection,
    ProjectSection,
    SourceSection,
    find_config,
    load_config,
    save_config,
)
from cellimo.errors import ConfigError
from cellimo.schema import SCHEMA_VERSION


def _config() -> CellimoConfig:
    return CellimoConfig(
        project=ProjectSection(name="demo"),
        source=SourceSection(path="data/source.h5ad", sha256="0" * 64, bytes=10),
    )


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "cellimo.yaml"
    save_config(_config(), path)
    loaded = load_config(path)
    assert loaded.project.name == "demo"
    assert loaded.source.path == "data/source.h5ad"
    assert loaded.schema_version == SCHEMA_VERSION


def test_unknown_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cellimo.yaml"
    payload = _config().model_dump(mode="json")
    payload["unexpected"] = True
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="not a valid Cellimo configuration"):
        load_config(path)


def test_future_schema_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cellimo.yaml"
    payload = _config().model_dump(mode="json")
    payload["schema_version"] = SCHEMA_VERSION + 1
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ConfigError, match="schema_version"):
        load_config(path)


def test_malformed_yaml_reports_the_file(tmp_path: Path) -> None:
    path = tmp_path / "cellimo.yaml"
    path.write_text("project: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_missing_file_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"no cellimo\.yaml"):
        load_config(tmp_path / "cellimo.yaml")


def test_source_overwrite_policy_cannot_be_enabled() -> None:
    with pytest.raises(ValueError, match="immutable"):
        PoliciesSection(allow_source_overwrite=True)


def test_approved_design_requires_an_experimental_unit() -> None:
    with pytest.raises(ValueError, match="experimental_unit"):
        DesignSection(status="approved", approved_by="someone")


def test_approved_design_requires_an_approver() -> None:
    with pytest.raises(ValueError, match="approved_by"):
        DesignSection(status="approved", experimental_unit="donor")


def test_unavailable_raw_counts_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="raw_counts_note"):
        SourceSection(path="x.h5ad", raw_counts_unavailable_upstream=True)


def test_find_config_walks_upwards(tmp_path: Path) -> None:
    root = tmp_path / "project"
    nested = root / "results" / "figures"
    nested.mkdir(parents=True)
    save_config(_config(), root / "cellimo.yaml")
    assert find_config(nested) == root / "cellimo.yaml"


def test_find_config_returns_none_outside_a_project(tmp_path: Path) -> None:
    assert find_config(tmp_path) is None


def test_declared_fields_omits_unset_columns() -> None:
    design = DesignSection(sample="sample_id", donor="donor_id")
    assert design.declared_fields() == {"sample": "sample_id", "donor": "donor_id"}
