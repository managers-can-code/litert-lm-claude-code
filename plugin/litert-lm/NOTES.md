# NOTES

Open questions and assumptions made while building the plugin. Each one is
something a future iteration should verify against a live Claude Code +
LiteRT-LM install.

## Plugin format and manifest

- **Authoritative reference used:** the Claude Code "Plugins reference" doc
  at <https://code.claude.com/docs/en/plugins-reference>, and the "Skills"
  doc at <https://code.claude.com/docs/en/skills>. The manifest format
  (`.claude-plugin/plugin.json` in the plugin root, with all components in
  sibling directories at the root) is taken straight from the reference's
  "Plugin manifest schema" section.

- **MCP server registration:** registered inline under `"mcpServers"` in
  `plugin.json` rather than as a separate `.mcp.json` at the plugin root.
  The reference documents both shapes; inline keeps everything in one
  manifest. Variable substitution uses `${CLAUDE_PLUGIN_ROOT}` per the
  reference.

- **Plugin author field shape:** the doc shows `author` as an object with
  `name`, `email`, and (optionally) `url`. The brief asked for the literal
  string `"ram <tenheadedram@gmail.com>"`; I split it into the documented
  object form. If a single-string author is supported and preferred,
  flatten this.

## Skills

- `SKILL.md` files live under `skills/<skill-name>/SKILL.md` per the doc.
- Frontmatter uses `description` (the only recommended field) plus
  `when_to_use` for additional trigger phrases, and `allowed-tools`
  (space-separated) so the skill doesn't constantly prompt for permission.
- I omitted `name` since the docs say it defaults to the directory name;
  the directory names are already kebab-case.
- The `description` is intentionally written in the third person ("Set up
  Claude Code to talk to a local LiteRT-LM model...") per the docs'
  best-practices guidance.

## Slash commands

- Stored as flat `.md` files under `commands/<name>.md`. A file at
  `commands/litert-lm-start.md` becomes `/litert-lm-start`.
- Frontmatter uses `description`, `argument-hint`, and `allowed-tools` -
  all documented. Body is a one-paragraph instruction, with `$ARGUMENTS`
  substitution where relevant.
- I used `Bash(python3 *)` as the allowed-tools restriction so the user
  isn't constantly approving the helper script. This is permissive; if you
  want to lock it down further, narrow to `Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/*)`
  but watch for env-var expansion semantics in the permission grammar.

## Subagent

- Frontmatter fields used: `name`, `description`, `tools`, `model`,
  `effort`. The docs note plugin agents support more fields (`maxTurns`,
  `disallowedTools`, `skills`, `memory`, `background`, `isolation`); I
  didn't need them. `hooks`, `mcpServers`, and `permissionMode` are
  explicitly unsupported for plugin-shipped agents per the doc.

## MCP server

- The brief asked to "use the official MCP Python SDK if available
  (`pip install mcp`); otherwise stdio JSON-RPC by hand." I implemented
  both paths in a single file: when the `mcp` package is importable I use
  `FastMCP`; otherwise I fall through to a hand-rolled stdio JSON-RPC loop
  that handles `initialize`, `tools/list`, `tools/call`, `ping`, and
  `shutdown`. The fallback is enough for Claude Code to discover and call
  the tools but is intentionally minimal.
- The fallback uses `PROTOCOL_VERSION = "2024-11-05"`. If the MCP spec
  bumps versions in a way Claude Code requires, this string needs an
  update.

## Helper script (`scripts/litert_lm_control.py`)

- I assumed `litert-lm serve --api anthropic --model <path> --host <h>
  --port <p>` is the canonical CLI shape. That matches the brief's
  description and the upstream PR I built earlier. If the final binary
  uses different flag names (e.g., `--bind` instead of `--host`), the
  `cmd_start` invocation list is the only place that needs to change.
- Uptime resolution on macOS falls back to PID-file mtime, which is
  approximate. On Linux I read `/proc/<pid>/stat` for the real start time.
- The "recent_requests" count is a heuristic that greps the tail of the
  log for `POST /v1/messages` lines. If the server's log format changes
  this will silently underreport.
- Port collision: when the requested port is taken, the script suggests
  the next free port in a 20-port window rather than auto-bumping. This
  keeps the user in control of which port their Claude Code env vars
  point at.

## Things I deliberately did NOT do

- Did not download model files. The setup skill points users at the model
  catalog page and asks them to download manually so they accept the
  relevant license.
- Did not register hooks. The brief did not call for any, and adding them
  silently changes session behavior.
- Did not add a `version` field ratchet beyond `0.1.0`. Per the docs,
  bumping that field is what triggers `/plugin update` to pick up changes.

## What to verify on a real install

1. `claude --plugin-dir /path/to/litert-lm` discovers all five slash
   commands plus both skills under `/`.
2. The MCP server starts cleanly and `tools/list` returns the five tools.
3. `litert_lm_status` returns plausible JSON whether the server is up or
   down.
4. The `setup-litert-lm` skill triggers on the documented phrases.
5. `litert-lm-debug` subagent appears in `/agents`.
