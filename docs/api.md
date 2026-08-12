# Python API

The deterministic API the notebook calls. Every function here is pure Python:
none of them invokes a language model, and none of them executes code on your
behalf.

Docstrings are rendered from the source, so what you read here is what the code
says. `automodule` blocks are wrapped in `eval-rst` because the docstrings are
reStructuredText.

## Project

The lifecycle object: initialisation, source immutability, design, stages.

```{eval-rst}
.. automodule:: cellimo.project.project
   :members: Project, StageContext
   :undoc-members:
   :member-order: bysource
```

## Provenance

```{eval-rst}
.. automodule:: cellimo.provenance.store
   :members:
   :undoc-members:
```

```{eval-rst}
.. automodule:: cellimo.provenance.records
   :members:
   :undoc-members:
   :exclude-members: model_config, model_fields, model_computed_fields
```

## Artifacts

```{eval-rst}
.. automodule:: cellimo.artifacts.descriptor
   :members:
   :undoc-members:
   :exclude-members: model_config, model_fields, model_computed_fields
```

```{eval-rst}
.. automodule:: cellimo.artifacts.registry
   :members:
   :undoc-members:
```

## Validation

The engine and the finding model. The checks themselves are documented in
[](VALIDATION.md), which explains the reasoning and cites the literature.

```{eval-rst}
.. automodule:: cellimo.validation.engine
   :members:
   :undoc-members:
   :exclude-members: model_config, model_fields, model_computed_fields
```

## Auditing an AnnData file

```{eval-rst}
.. automodule:: cellimo.audit.anndata_audit
   :members:
   :undoc-members:
   :exclude-members: model_config, model_fields, model_computed_fields
```

## Retrieval

The read-only knowledge index behind the `cellimo-knowledge` MCP server.

```{eval-rst}
.. automodule:: cellimo.retrieval.base
   :members:
   :undoc-members:
```

## Errors

```{eval-rst}
.. automodule:: cellimo.errors
   :members:
   :undoc-members:
   :show-inheritance:
```
