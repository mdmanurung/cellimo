# Changelog

All notable changes to Cellimo are recorded here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versioning is
[semantic](https://semver.org/).

## [0.1.0] — unreleased

First vertical slice. Everything below is implemented and tested; nothing is
listed that is not.

### Added

**Project and provenance**

- `Project` — a transparent Python API: discovery, initialisation, source
  registration, AnnData audit, design declaration and approval, artifact
  registration, decision/reference/statistics recording, environment capture,
  manifest generation, and a `stage()` context manager. No `run_full_pipeline()`.
- `cellimo.yaml` — a validated project configuration covering source, design,
  paths, safety policies, checkpoint policy and random seed, with a
  `SCHEMA_VERSION` that refuses to misread a future format.
- Append-only provenance: `artifacts.jsonl`, `decisions.jsonl`,
  `references.jsonl`, `statistics.jsonl`, `environment.json`, `manifest.json`,
  `runs/`. Record ids are content-derived; atomic writes throughout; a torn
  trailing JSONL line is detected and skipped on read.
- Immutable artifact descriptors with SHA-256 lineage back to the registered
  source, per-exclusion counts including per-sample breakdowns, and explicit
  matrix facts (`representation`, `counts_layer`, `raw_counts_available`).

**Audit**

- `audit_anndata()` — backed read, sampled matrix, raw-counts location with
  evidence, per-column summaries with cardinality, and *proposed* design
  candidates ranked by hint specificity and confidence.

**Validation**

- `cellimo check` — 21 checks over structured provenance (8 structural, 13
  scientific), non-zero exit on errors, `--json` output, `--only` filtering.
- Errors on: no experimental unit, confirmatory analysis before design approval,
  unidentified raw counts, cells registered as biological replicates, a group
  with fewer than two replicates, differential expression on integration-corrected
  values, broken artifact lineage, missing or modified artifacts, and exclusion
  counts that do not reconcile.
- Warnings on: unstratified QC, unjustified integration, missing effect sizes or
  uncertainty, uncaptured environment, unrecorded references.
- Every rule has a *recorded* escape hatch rather than a silencing flag.

**Retrieval**

- `cellimo-knowledge`, a read-only stdio MCP server (`mcp` 2.x) with exactly four
  tools: `search_workflows`, `search_documentation`, `get_reference`,
  `index_status`. No execution, no data access, no notebook editing.
- Stable reference identifiers in two namespaces (`notebook:`, `chunk:`), never
  derived from result position.
- Two backends: `chroma` (KAI's published index) and `lexical` (stdlib BM25 over
  a JSON index, used by the tests).
- `cellimo index install` — checksum-verified, resumable download with the
  archive's wrapping directory stripped, zip-slip rejection, and an explicit
  confirmation prompt.
- Honest reporting of what the published index cannot answer: no documentation
  collections, no modality field, an unreliable package field.

**Marimo**

- A valid `analysis.py` template with eleven sections, `mo.ui` controls for
  every design field, run-button gates before expensive stages, and inline
  validation. Copied verbatim into projects — no string substitution — so the
  shipped file is the tested file.
- Marimo detection with a ≥ 0.23.8 floor, `marimo check` integration, and server
  discovery through Marimo's own registry.

**marimo-pair**

- Vendored unmodified at tag `v0.0.18` (`0c486ee7ee4cd54622e0d062badddab429f435b1`),
  Apache-2.0, with per-file SHA-256s verified by `doctor` and by tests.

**Plugin**

- One canonical `plugin/plugin.toml` generating `.claude-plugin/plugin.json`,
  `.claude-plugin/marketplace.json`, `.codex-plugin/plugin.json` and `.mcp.json`,
  with drift caught by a test and by `doctor`.
- Five skills: `cellimo` (router), `project-audit`, `quality-control`,
  `statistics`, `notebook-review`.

**CLI**

- `install`, `init`, `start`, `doctor`, `check`, `index status|install|update`,
  `mcp serve`, `sessions`.
- `install` shells out to each agent's own plugin commands, so it cannot clobber
  existing user configuration, and supports `--dry-run`.
- `doctor` distinguishes `ok` / `warn` / `fail` / `skip`; a check that could not
  run never reports `ok`.

**Safety**

- Registered source data cannot be written through Cellimo's APIs, including via
  symlinks and hard links (device/inode comparison).
- Project-output path traversal is rejected on fully resolved paths.
- Artifacts are immutable once registered.
- Network actions and plugin installation are never silent.

### Fixed (found by installing it for real, and by the second-pass audit)

Installing Cellimo as a `uv tool` — the documented path — exposed a cluster of
defects around the two-runtime split. All four were live:

- **`doctor` reported Marimo as a hard failure** for every correctly-configured
  user, because it searched only next to its own interpreter. It now searches the
  project runtime first, and treats a missing Marimo outside a project as a
  warning rather than a failure.
- **`init` recorded the tool interpreter as the project runtime.** It now detects
  it (explicit `--python`, then `VIRTUAL_ENV`, then a project `.venv`, then the
  current interpreter) and prints what it chose.
- **`provenance/environment.json` recorded the wrong environment** — the tool's
  dependencies rather than the scientific stack that produced the results.
  `capture_environment` now queries the project interpreter in a subprocess.
- **Virtualenv paths were resolved through their symlinks**, which silently
  substituted the base interpreter — an environment with none of the project's
  packages. Interpreter paths are now made absolute without following symlinks,
  which also fixes environment-manager detection reporting `system` for a uv venv.
- **`matplotlib` was missing from the tracked package list**, so `doctor` warned
  that it was absent from environments that had it. A test now asserts every
  profile requirement is tracked.

The adversarial second pass found six more, each reproduced before being fixed:

- **`is_source=True` skipped the containment check**, so a single public call
  could hash any readable file on the machine — an SSH key, another user's
  dataset — into the project's append-only provenance ledger. It now has to be
  the project's actual registered source.
- **`open_index` caught only `ImportError`.** A ChromaDB index on a read-only
  mount crashed `cellimo mcp serve` at startup with an unhandled traceback
  instead of degrading to "no usable index". Any backend-construction failure now
  degrades cleanly, and says that ChromaDB needs a writable index directory.
- **`get_reference` had no size cap**, so a multi-megabyte notebook cell was
  returned whole in one tool result. Sections are now bounded per-section and
  per-reference, always with `truncated`/`omitted_chars` set and the omission
  stated in the reference's note — and the content hash covers what was actually
  returned.
- **`StageContext.output()` validated the path before creating its parent
  directories but not after**, leaving a window in which a new path component
  could become a symlink out of the project. It now re-validates after `mkdir`.
- **Malformed paths raised raw `ValueError`/`OSError`** (embedded NUL byte,
  over-long component) instead of the typed `PathSafetyError` the rest of the
  path surface raises.
- **"Read-only" was imprecise.** ChromaDB writes to its own bookkeeping files
  when opening a collection and answering a query. The claim is now stated
  exactly — it is a statement about the tool contract (no execution, no data
  access, no project writes), not about the index directory's bytes.

And two defects in the validator itself, both of which let a genuinely flawed
analysis pass with exit 0:

- **The replication rules were escapable by renaming your test.** `C004` and
  `C006` matched substrings of the free-text `test` field, so a confirmatory
  `kruskal_wallis` computed per-cell on batch-corrected values passed cleanly.
  Both now key on structure instead: a confirmatory analysis must *positively
  declare* `sample`/`donor` as its unit or aggregate in a replicate-aware way,
  and no confirmatory statistic may consume a corrected representation whatever
  it is called. The test name only sharpens the message.
- **Byte-identical artifacts at different stages were silently dropped.**
  `append_artifact` deduplicated on content hash alone, so a stage whose output
  happened to equal an earlier one — a normalisation that was a no-op, a
  re-export — returned a normal-looking descriptor that was never written, and
  no check could see the missing stage. Identity is now
  `(stage, path, sha256, parent)`.
- `C006` also fired on honestly-labelled *exploratory* work; ranking markers
  between clusters found on an integrated embedding is routine, and it no longer
  errors there.
- **An agent could sign off on its own design.** `approved_by =
  "autonomous_authorization"` is now cross-checked against a recorded
  authorisation decision from the user, and reported as a warning even when
  genuine.
- Smaller: the MCP server reported an empty version in its handshake; the Codex
  `interface` block was thinner than every real Codex plugin on the machine;
  `agents.py` guessed at `~/.claude` / `~/.codex` paths it never read; `doctor`
  now says when an editable install has pinned the plugin tree to a checkout
  that would break the agents' registration if moved.

A black-box pass against the built wheel found five more:

- **`cellimo init --force` reset the configuration while leaving provenance
  intact**, producing a project whose `cellimo.yaml` said the design was
  unresolved next to a `statistics.jsonl` entry that could only have been written
  while it was approved. Re-initialisation now preserves the design, the
  policies, the checkpoint policy and the seed, and prints what it kept.
- **`audit_anndata` crashed on a valid AnnData with no `X`** — a supported state
  when the data lives in `layers` — with a raw `KeyError` out of h5py. It now
  audits such objects correctly (finding counts in the layer), and any other
  failure while inspecting an object is reported as "cannot audit <path>: …"
  rather than as a traceback.
- **`cellimo init` raised a bare `PermissionError`** when the target's parent was
  not writable. `OSError` is now a clean CLI message.
- **`cellimo check --only TYPO` ran zero checks and reported success.** Unknown
  check codes are now rejected, listing the registered ones.
- **`cellimo check` said nothing at all when `analysis.py` was missing** and
  exited 0. It now reports it and exits non-zero.

The ChromaDB backend — the one that serves the real published index — had no
test coverage at all. It now has a suite that builds a real ChromaDB index and
drives the reader and the MCP server against it.

A **third** pass — adversarial verification of the fixes themselves, on the
repaired tree — found 17 more, including two that re-opened holes the second
pass had supposedly closed:

- **The corrected-values rule could be walked past by relabelling the record.**
  `C006` read the statistics record's self-reported `input_representation` and
  never compared it against the input artifact's own hash-pinned
  `representation`. Register an artifact as `integrated_expression`, then claim
  `lognorm` in the analysis that consumes it, and the check passed. The
  artifact's record now wins; a laundered claim is an error naming both values.
- **An agent could still fully self-approve a design.** The previous fix only
  cross-checked the literal string `"autonomous_authorization"`; any other value
  — including the API's own default `approved_by="user"` — sailed through. Worse,
  `record_design` *overwrote* the caller's declared actor with `"user"` whenever
  an approval was present, so the decision log actively asserted a human had
  approved it. The actor is now recorded as declared (defaulting to `agent`), and
  C002 reports an approval whose actor is not a user.
- **The justification escape hatch cost nothing:** `"."` or `"-"` downgraded a
  hard error to a warning and the run passed. A justification now has to be a
  sentence, and common non-answers are rejected by name.
- **`cellimo check` called a notebook "a valid Marimo notebook" when Python
  could not parse it** — `marimo check` validates the cell graph it managed to
  read, not the whole file. Every notebook is now `ast.parse`d first.
- **The CLI's own entry point turned three abort paths into tracebacks.**
  `SystemExit("a message")` met `int(exc.code)` and raised `ValueError`; only
  `CliRunner` (which the whole suite used) hid it.
- **An unreadable index directory crashed `cellimo mcp serve` at startup**, because
  `detect_backend` ran outside the guarded block.
- **`get_reference` was still unbounded in two dimensions**: section *count* (a
  100,000-cell notebook returned 100,000 placeholder objects) and the lexical
  backend's `summary` (a 10 MB summary came back whole, 21 MB over the wire).
- **The environment snapshot fell back to the tool runtime silently.** It now
  records `requested_interpreter` and `queried_interpreter`, and S007 reports the
  mismatch.
- **Skipping the audit was safer than doing it**: C003 warned rather than errored
  when nothing had been audited at all. It now errors once confirmatory results
  exist.
- Smaller: `init --force` claimed it "kept design" even when the old config was
  unparsable and it kept nothing; `check --json`'s `passed` excluded notebook
  validity while the exit code included it (there is now an `ok` field that
  means what the exit code means); a uv-venv was detected by substring-matching
  the whole `pyvenv.cfg`; `.pixi` environments were not recognised;
  `check_notebook` discarded the stderr explaining why `marimo check` failed; a
  non-integer `order` in third-party index data raised a raw `ValueError`; and
  colliding cell `order` values produced duplicate section ids.

A second mutation pass then reverted all 18 fixes one at a time. **Fifteen were
killed by the suite; three survived** — meaning those regression tests were
decorative, and have been replaced:

- **`capture_environment`'s subprocess path was never exercised.** The test
  passed `sys.executable`, which the implementation's own guard
  (`str(target) != sys.executable`) routes straight back to the in-process
  branch — so the test provably could not reach the code it named. It now drives
  a stub interpreter that answers with sentinel values found nowhere in the test
  process.
- **The TOCTOU re-validation had a test with the right name and no race.** It
  called `output()` once and checked the directory existed, which cannot tell one
  validation call from two. It now plants a symlink out of the project during
  `mkdir`, in exactly the window the second check exists to close.
- **The no-`X` AnnData guard had no test at all** — there was no `tests/test_audit.py`,
  and the shared fixture never omits `X`. There is now a suite covering no-`X`,
  counts-in-a-layer, counts-in-`.raw`, normalised values, single-sample objects,
  per-cell identifiers, and unreadable files.

Each replacement was itself verified by re-running the mutation it is supposed
to catch.

An earlier mutation pass gutted seven guards in an isolated copy of the source
(`assert_writable`, `hash_file`, `atomic_write_bytes`, `parse_reference_id`,
C004, and both halves of S008) and re-ran the suite each time. **All seven were
caught**, and the suite passes in reversed order with no flakiness. Three
weaknesses in the *tests* were fixed:

- **`test_vendor_tampering_is_detected` mutated a real repository file**,
  protected only by `try`/`finally`. A killed process would have left
  `plugin/skills/marimo-pair/SKILL.md` permanently reporting tampering on a
  checkout nobody had touched. It now works on a throwaway copy with
  `plugin_root()` redirected, and gained sibling tests for added and deleted
  vendored files.
- **A source-guard test grepped for the string `assert_writable`** instead of
  exercising it — it survived replacing the guard with a no-op. It now drives all
  three write routes against a registered source and asserts each refuses.
- **The check-registry test only proved a code was registered**, which a check
  whose body returned `[]` would satisfy. Its docstring now says so, and it is
  paired with the per-code behavioural tests that actually catch that.

### Fixed (inherited from KAI)

- The retrieval-index downloader extracted one directory level too deep, so its
  own verification always failed and every run re-downloaded 345 MB. Cellimo
  strips the archive's wrapping directory.
- `ChromaDbManager.get_tool_status()` read a field its dataclass does not
  declare. Cellimo computes status from the collection registry instead.

### Known limitations

- Only the `scanpy` and `existing` profiles are implemented.
- `search_documentation` has no data behind it in the published index; it says
  so rather than returning nothing silently.
- `packages` and `modalities` filters are best-effort against that index, and
  every result that used them declares it.
- Arbitrary Python written into the notebook is **not** sandboxed. No container
  isolation is implemented or claimed.
- `cellimo start` runs Marimo with `--no-token` so the session is discoverable;
  the port is unauthenticated and defaults to loopback.
- Trajectory, spatial, multimodal and R workflows are out of scope.

### Attribution

Derived from [KAI](https://github.com/davidfischerlab/kai) (Apache-2.0). The
retrieval index is KAI's, published on Zenodo under GPL-3.0-or-later and
downloaded rather than redistributed. See `THIRD_PARTY_NOTICES.md`.
