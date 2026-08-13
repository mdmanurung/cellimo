# Held-out function-call benchmarks

The primary outcome benchmark compares the scientific functions selected by a
grounded candidate with those in a published expert notebook. Calls are parsed
from both programs with Python's AST; no expected function list is hand-written.

The first case is Kang 2018 scGen perturbation prediction:

- `kang_scgen.yaml` freezes the task, dataset aliases, evaluation namespaces,
  and held-out expert reference;
- `kang_scgen_candidate.py` is the frozen grounded candidate. It is parsed, not
  executed—the benchmark measures method selection and never trains a model in
  CI;
- `kang_scgen_grounding.json` records the denylist digest for every grounding
  call, the eligible sources used, and the successful proposed-code preflight.

Run it against a writable local copy of the published index:

```bash
python tools/benchmark_calls.py \
  benchmarks/kang_scgen.yaml \
  benchmarks/kang_scgen_candidate.py \
  benchmarks/kang_scgen_grounding.json \
  --index /tmp/cellimo-index
```

Before scoring, Cellimo scans all stored notebook JSON, conservatively excludes
every reference containing a dataset alias, verifies the held-out notebook is
in that denylist, checks every trace entry against the denylist digest, and
resolves every candidate citation. The command exits non-zero if any of those
conditions fails.

Calls are compared exactly after expanding import aliases and tracing named
model instances to their constructors. API drift is therefore visible rather
than silently credited. In the current index, the frozen candidate scores
44.4% precision and 44.4% recall (8 of 18 calls matched). Most disagreement is
the eligible corpus's `Scgen`/`reg_mean_plot` API versus the held-out
notebook's `SCGEN`/`plot_reg_mean_plot`, plus different Scanpy PCA entry points.
This one case is a pipeline baseline, not a general estimate of agent quality.
