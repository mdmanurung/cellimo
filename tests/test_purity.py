"""Architectural constraints, enforced as tests.

Three claims this project makes are only true if nothing quietly violates them:
there is no internal LLM, the tool runtime stays light, and only the vendored
marimo-pair skill touches Marimo's private agent API. Each is checked here
against the source, not asserted in a README.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "cellimo"

#: Modules that would mean Cellimo is calling a model itself, or reaching for
#: the superseded KAI orchestration.
FORBIDDEN_IMPORTS = (
    "openai",
    "anthropic",
    "ollama",
    "litellm",
    "langchain",
    "llama_index",
    "transformers.pipelines",
    "kai.core",
    "kai.agent",
    "vscode",
)

#: Environment variables a user would have to set if there were an internal LLM.
FORBIDDEN_ENVIRONMENT = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OLLAMA_HOST",
    "KAI_API_KEY",
)


def _python_files() -> list[Path]:
    return [
        path
        for path in SOURCE_ROOT.rglob("*.py")
        # The notebook template is user-facing scientific code, not library code.
        if "templates" not in path.parts
    ]


def _library_files() -> list[Path]:
    return _python_files()


@pytest.mark.parametrize("module", FORBIDDEN_IMPORTS)
def test_no_llm_or_vscode_imports(module: str) -> None:
    offenders = [
        path
        for path in _library_files()
        if f"import {module}" in path.read_text(encoding="utf-8")
        or f"from {module}" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"{module} imported by {[str(p) for p in offenders]}"


@pytest.mark.parametrize("variable", FORBIDDEN_ENVIRONMENT)
def test_no_llm_api_key_is_read(variable: str) -> None:
    offenders = [
        path for path in _library_files() if variable in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"{variable} referenced by {[str(p) for p in offenders]}"


def test_library_code_never_imports_marimo_private_api() -> None:
    """`marimo._code_mode` belongs to the vendored skill and to nothing else."""
    offenders = []
    for path in SOURCE_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "_code_mode" not in text:
            continue
        # A prose mention in a docstring is fine; an import is not.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) and "_code_mode" in stripped:
                offenders.append(f"{path}: {stripped}")
    assert not offenders, offenders


def test_generated_notebook_never_imports_marimo_private_api() -> None:
    template = SOURCE_ROOT / "templates" / "analysis.py"
    assert "_code_mode" not in template.read_text(encoding="utf-8")


def test_tool_runtime_does_not_import_the_scientific_stack() -> None:
    """Importing the CLI, the MCP server and validation must stay light.

    Run in a subprocess so imports performed by the test session itself (the
    fixtures use anndata) cannot mask a real dependency.
    """
    code = (
        "import sys;"
        "import cellimo, cellimo.cli.main, cellimo.mcp.server, cellimo.validation,"
        " cellimo.diagnostics, cellimo.retrieval;"
        "heavy=[m for m in ('scanpy','anndata','torch','squidpy','scvi','chromadb',"
        "'sentence_transformers','matplotlib') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "", f"tool runtime imported: {result.stdout.strip()}"


def test_scientific_imports_are_deferred_to_call_time() -> None:
    """Optional dependencies are imported inside functions, not at module scope."""
    audit_source = (SOURCE_ROOT / "audit" / "anndata_audit.py").read_text(encoding="utf-8")
    module_level = [
        line
        for line in audit_source.splitlines()
        if line.startswith(("import ", "from ")) and "anndata" in line
    ]
    assert not module_level, module_level


def test_missing_anndata_produces_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    from cellimo.audit import anndata_audit
    from cellimo.errors import CellimoError

    real_import = builtins.__import__

    def refuse(name: str, *args: object, **kwargs: object) -> object:
        if name == "anndata":
            raise ImportError("no anndata here")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", refuse)
    with pytest.raises(CellimoError, match="requires anndata"):
        anndata_audit._require_anndata()


def test_mcp_server_exposes_no_execution_surface() -> None:
    source = (SOURCE_ROOT / "mcp" / "server.py").read_text(encoding="utf-8")
    for dangerous in ("subprocess", "os.system", "exec(", "eval(", "compile("):
        assert dangerous not in source, dangerous


def test_every_write_path_goes_through_the_source_guard(tmp_path: Path) -> None:
    """Behavioural, not textual: gut the guard and this must fail.

    An earlier version of this test grepped the source for the string
    ``assert_writable``, which a stub implementation that checked nothing would
    have satisfied perfectly.
    """
    pytest.importorskip("anndata")
    from cellimo.errors import SourceImmutabilityError
    from cellimo.project.project import Project

    root = tmp_path / "guarded"
    root.mkdir()
    source = root / "source.h5ad"
    source.write_bytes(b"pretend dataset")
    project = Project.init(root, source, name="guarded", scaffold=False)
    relative = source.relative_to(root)

    # Every public route to a write must refuse the registered source.
    with pytest.raises(SourceImmutabilityError):
        project.assert_writable(relative)
    with pytest.raises(SourceImmutabilityError):
        project.register_artifact(relative, stage="post_qc")
    with pytest.raises(SourceImmutabilityError), project.stage("post_qc") as stage:
        stage.output(relative)

    # And the source is still exactly what it was.
    assert source.read_bytes() == b"pretend dataset"
