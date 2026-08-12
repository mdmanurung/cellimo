# The plugin — one tree, two platforms

Codex and Claude Code both have real plugin systems, and both accept the same
shape: a directory with a manifest, a `skills/` tree and an `.mcp.json`. Cellimo
ships one such directory and generates each platform's metadata from a single
canonical definition.

## Layout

```
plugin/
├── plugin.toml                     ← the source of truth; edit this
├── .claude-plugin/
│   ├── plugin.json                 ← generated
│   └── marketplace.json            ← generated
├── .codex-plugin/
│   └── plugin.json                 ← generated
├── .mcp.json                       ← generated
├── skills/
│   ├── cellimo/SKILL.md            router
│   ├── project-audit/SKILL.md
│   ├── quality-control/SKILL.md
│   ├── statistics/SKILL.md
│   ├── notebook-review/SKILL.md
│   └── marimo-pair/                vendored, pinned, unmodified
└── vendor/
    ├── marimo-pair.json            origin, tag, commit, licence, file hashes
    └── marimo-pair/LICENSE
```

Both platforms require that only `plugin.json` lives inside the
`.claude-plugin/` and `.codex-plugin/` directories; `skills/` and `.mcp.json`
stay at the plugin root. A test asserts this.

In an installed wheel the same tree is at `cellimo/_plugin/`, which is what
`cellimo install` points the agents at.

## Generating the manifests

```bash
python -m cellimo.plugin_manifest --write    # regenerate
python -m cellimo.plugin_manifest --check    # fail if they have drifted
```

`--check` runs in the test suite (`tests/test_plugin.py`) and in `cellimo
doctor`. Editing a generated JSON file directly makes the build fail, which is
the intended pressure: there is one place to change the plugin's identity.

The consistency the tests enforce is not that the two manifests are identical —
they cannot be, the platforms differ — but that they agree on name, version,
description, licence, repository, the skills path and the MCP config path.

## Installation

```bash
cellimo install --agents auto        # both, if present
cellimo install --agents claude
cellimo install --agents codex
cellimo install --dry-run            # print the commands, change nothing
```

The commands run are exactly:

```
claude plugin marketplace add <plugin root>
claude plugin install cellimo@cellimo

codex plugin marketplace add <plugin root>
codex plugin add cellimo@cellimo
```

Cellimo shells out to each agent's own CLI rather than editing
`~/.claude/settings.json` or `~/.codex/config.toml`. That is why installation
cannot clobber existing user configuration: Cellimo never writes those files.

Re-running is safe. A marketplace that is already configured is reported by the
agent and treated as non-fatal, and the install step still runs so an upgraded
plugin is picked up.

## The MCP server

`.mcp.json`:

```json
{
  "mcpServers": {
    "cellimo-knowledge": {
      "command": "cellimo",
      "args": ["mcp", "serve"]
    }
  }
}
```

A bare `cellimo` command, not `${CLAUDE_PLUGIN_ROOT}/bin/...`. That variable is
a Claude Code feature and this same file has to work under Codex, so the command
must not depend on it. The cost is that `cellimo` has to be on PATH —
`uv tool install cellimo` puts it there, and `cellimo doctor` fails loudly with
exactly this explanation when it is not.

## Skills

| skill | when it loads |
| --- | --- |
| `cellimo` | router: any single-cell analysis request |
| `project-audit` | starting a dataset; design unresolved; `C001`/`C003` |
| `quality-control` | filtering, thresholds, doublets; `C008`/`C010` |
| `statistics` | any p-value; `C004`/`C005`/`C006`/`C012` |
| `notebook-review` | reviewing or auditing an analysis |
| `marimo-pair` | vendored: driving the live session |

The router loads exactly one scientific skill per request and says what it is
leaving for later. Skills reference the validator's codes directly, so a failing
check points at the skill that explains it.

Cellimo's own skills never mention `marimo._code_mode` — a test enforces that.
Only the vendored skill touches the kernel.

## Vendored marimo-pair

Pinned at tag `v0.0.18`, commit `0c486ee7ee4cd54622e0d062badddab429f435b1`,
Apache-2.0. Every file's SHA-256 is recorded in `plugin/vendor/marimo-pair.json`,
and:

- `cellimo doctor` verifies them and reports any modification;
- `tests/test_plugin.py` fails if the copy diverges, and separately proves the
  detector works by tampering with a file and restoring it.

Upgrading means replacing the tree, re-running the hash recorder, and updating
the tag and commit in the vendoring record and in `THIRD_PARTY_NOTICES.md`. See
[MIGRATION.md](MIGRATION.md) for why it is vendored rather than depended upon.

## Skill authoring

`SKILL.md` frontmatter:

```yaml
---
name: kebab-case-matching-the-directory
description: >-
  What it does and, more importantly, when to load it. Trigger words matter:
  this is what the agent matches against.
allowed-tools: Bash(cellimo *), Read, Skill
---
```

`tests/test_plugin.py` validates the frontmatter of every skill: `name` must be
kebab-case and match its directory, and `description` must be present.
