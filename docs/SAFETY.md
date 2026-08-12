# Safety, and its limits

This document states exactly what Cellimo guarantees and exactly what it does
not. The second half matters more.

## What Cellimo's own APIs enforce

**Registered source data cannot be written.** `Project.assert_writable()` is the
gate every managed write goes through. It resolves the target fully — following
symlinks — and refuses when it is the same file as the registered source, as
determined by device and inode, so a symlink, a hard link or a differently
spelled path is caught too. `policies.allow_source_overwrite` exists so the
refusal is visible in configuration; setting it to `true` is rejected by the
config validator.

**Project outputs cannot escape the project.** Paths are resolved against the
project root and rejected when they land outside it. `../escape.h5ad`, an
absolute path elsewhere, and a symlinked subdirectory pointing out of the tree
all raise `PathSafetyError`.

**Artifacts are immutable once registered.** Re-registering the same path with
different content appends a new descriptor with a new hash; nothing is rewritten
in place. `cellimo check` (`S008`) re-hashes artifacts and fails when one has
changed since registration.

**Every managed write is recorded.** Registering an artifact appends to
`artifacts.jsonl` and writes a decision. There is no unlogged write path through
the API.

**Network access and package installation are never silent.** `cellimo index
install` prints the URL, the size, the licence and the target, and asks before
downloading. `cellimo install` prints every command it is about to run and
supports `--dry-run`. Nothing downloads during `pip install` or during tests.

**Configuration and provenance survive a crash.** `cellimo.yaml`,
`manifest.json` and `environment.json` are written to a temporary file in the
destination directory and moved into place with `os.replace`, so a reader never
sees a half-written file. The `.jsonl` logs are append-only, and a torn trailing
line — the expected outcome of a crash mid-append — is detected and skipped on
read.

## What Cellimo does **not** guarantee

**Arbitrary Python written by the agent into the notebook is not sandboxed.**
This is the important sentence in this document. Cellimo's protections are
library-level: they bind calls to Cellimo's own API. A notebook cell containing

```python
import os
os.remove("data/source.h5ad")
```

will remove the file. So will `shutil.rmtree`, `open(..., "w")`, a subprocess, or
anything else Python can do. Marimo executes what is in the notebook; Cellimo
does not intercept it and does not claim to.

**There is no container isolation.** None is implemented, so none is claimed.

**The Marimo session is unauthenticated by default.** `cellimo start` runs
`marimo edit --no-token`, because only untokenised servers register themselves
in Marimo's server registry, which is how marimo-pair discovers them. Anyone who
can reach that port can execute code in your kernel with your permissions. The
mitigations:

- the bind address defaults to `127.0.0.1` (loopback only);
- `cellimo start --host` exists but should not be pointed at a public interface;
- on a shared machine, other local users can reach loopback ports. If that is
  your threat model, do not use `--no-token`; run `marimo edit --token` yourself
  and give the agent `MARIMO_TOKEN` instead, accepting that auto-discovery will
  not work.

**The retrieval index is third-party content.** It indexes public notebooks from
GitHub. `get_reference` returns their text verbatim. Treat retrieved code as you
would treat any code from the internet: read it before running it.

## Recommended hardening

```bash
chmod a-w data/source.h5ad        # make the source read-only at the filesystem
```

`cellimo doctor` checks this and reports `source immutability` as a warning when
the file is still writable by you, with exactly this suggestion. Filesystem
permissions are the only protection that binds arbitrary notebook code.

Keep the source dataset outside the project directory, on read-only shared
storage, when your institution provides it. Cellimo records an absolute path for
sources outside the project and never copies them.

## What `cellimo doctor` tells you

| diagnostic | why it matters |
| --- | --- |
| `source integrity` | the file still hashes to what was registered |
| `source readable` | the analysis can run at all |
| `source immutability` | whether the filesystem, not just Cellimo, protects it |
| `outputs writable` | artifacts can actually be written |
| `disk space` | a checkpoint will not fail halfway |
| `marimo session` | whether a discoverable session exists, and whether registry entries are stale |
| `marimo-pair` | the vendored copy matches its recorded hashes |
| `notebook` | `analysis.py` still parses as a Marimo notebook |
| `project packages` | the *project runtime* has what the profile expects |
| `marimo` | a compatible Marimo (≥ 0.23.8) exists in the project runtime |
| `plugin tree` / `plugin manifests` | the skills are present and the two platform manifests match `plugin.toml` |
| `agent: claude` / `agent: codex` | each agent is installed and has the plugin registered |
| `cellimo on PATH` | the plugin's MCP server command can actually resolve |
| `retrieval index` | what is installed, and what it cannot answer |

A check that could not run reports `skip` with the reason. It never reports `ok`
by default — a doctor that goes green when it could not look is worse than no
doctor.

## Reporting a problem

Cellimo's guarantees are enforced by tests in `tests/test_purity.py`,
`tests/test_project.py` and `tests/test_util.py`. If you find a path through the
API that writes the source, escapes the project root, or mutates a registered
artifact, that is a bug — please report it with the sequence of calls.
