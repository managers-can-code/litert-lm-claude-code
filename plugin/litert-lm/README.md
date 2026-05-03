# litert-lm Claude Code plugin

Point Claude Code at a local model. The plugin supports **two complementary modes** — pick one or use both.

| Mode | What happens | Use it when |
|---|---|---|
| **Server-proxy mode** | `ANTHROPIC_BASE_URL` points at your local `litert-lm serve --api anthropic`. ALL Claude Code traffic goes to the local model. | You want full offline / privacy / zero per-token cost. Quality drops to the local model's level. |
| **Subagent-delegation mode** | Cloud Claude stays the primary agent. The `litert-lm-local` subagent is invoked via the `Task` tool to run specific routine work locally. | You want to keep cloud Claude's quality on hard tasks but offload doc strings, simple refactors, code summaries, paraphrasing, etc. to local — saves tokens, keeps privacy on the delegated portion. |

Both modes share the same plugin install, slash commands, MCP server, and `.litertlm` model. The difference is only how Claude Code interacts with the local model.

License: Apache-2.0.

---

## Prerequisites

| Thing | Why | How |
| --- | --- | --- |
| Claude Code | the front-end you'll be talking to | `npm install -g @anthropic-ai/claude-code` (>= v2.1.123) |
| LiteRT-LM | the local server | `uv tool install litert-lm` (the build that includes the AnthropicHandler PR) |
| A `.litertlm` model | what runs on your hardware | Download Gemma 3n E2B (smallest viable) from <https://ai.google.dev/edge/litert-lm/models> |
| Python 3.10+ | the helper script and MCP server | usually preinstalled on macOS / Linux |

To check you have the right LiteRT-LM build, run `litert-lm serve --help` and
confirm `--api` lists `anthropic` as a choice.

---

## Install the plugin

Claude Code plugins are loaded either via a **marketplace** (for permanent install) or via the **`--plugin-dir` flag** (for one-session testing). Plain copy-into-cache + `claude plugin enable <name>` does NOT work — Claude Code needs the plugin to be known to a marketplace settings scope first.

### Option A: load for one session (quickest — good for testing)

```bash
claude --plugin-dir /path/to/plugin/litert-lm
```

This loads the plugin into the current session only. The slash commands `/litert-lm-*` and the `litert-lm` MCP server appear immediately. No marketplace setup required.

### Option B: install permanently via a local marketplace (recommended)

Step 1 — set up a tiny local marketplace folder. The marketplace metadata must live in a `.claude-plugin/marketplace.json` file at the marketplace root; the plugin sits beside it:

```bash
mkdir -p ~/.claude/plugins/marketplace/.claude-plugin
cp -R /path/to/plugin/litert-lm ~/.claude/plugins/marketplace/litert-lm

cat > ~/.claude/plugins/marketplace/.claude-plugin/marketplace.json <<'JSON'
{
  "name": "Local Plugins",
  "owner": {"name": "You"},
  "plugins": [
    {"name": "litert-lm", "source": "./litert-lm"}
  ]
}
JSON
```

After this, your marketplace folder looks like:

```
~/.claude/plugins/marketplace/
├── .claude-plugin/
│   └── marketplace.json     ← marketplace metadata
└── litert-lm/
    ├── .claude-plugin/
    │   └── plugin.json      ← plugin metadata (already in the plugin)
    └── ...
```

Step 2 — register the marketplace and install the plugin (run inside Claude Code):

```text
/plugin marketplace add ~/.claude/plugins/marketplace
/plugin install litert-lm@Local Plugins
```

Or do it from the shell:

```bash
claude plugin marketplace add ~/.claude/plugins/marketplace
claude plugin install "litert-lm@Local Plugins"
```

Step 3 — restart Claude Code. The slash commands `/litert-lm-*` appear and the `litert-lm` MCP server is started on session boot.

> If you later distribute this plugin via a public marketplace, users will run `claude plugin install litert-lm@<your-marketplace>` and skip Step 1.

---

## Quick start (the "first 60 seconds" path)

```bash
# 1. Start Claude Code in any directory.
claude

# 2. Inside Claude Code, ask the setup skill to walk you through it.
> set up litert-lm
```

The skill verifies the binary, helps you pick a model, starts the server, and
prints the two `export` lines you need.

If you already know what you want to do, the slash commands also work
directly:

```text
/litert-lm-start /path/to/gemma-3n-E2B-it-int4.litertlm
/litert-lm-status
/litert-lm-config
/litert-lm-stop
```

---

## What ships in the plugin

| Component | Trigger | Purpose |
| --- | --- | --- |
| `skills/setup-litert-lm` | "set up litert-lm", "use Claude Code with local model" | Guided first-run setup |
| `skills/troubleshoot-litert-lm` | "litert-lm not working", "litert-lm error" | Diagnose and fix common problems |
| `/litert-lm-start` | manual | Spawn `litert-lm serve --api anthropic` in the background |
| `/litert-lm-stop` | manual | Stop the running server |
| `/litert-lm-status` | manual | Probe `/v1/models`, print server uptime + last requests |
| `/litert-lm-switch` | manual | Hot-swap to a different model file |
| `/litert-lm-config` | manual | Print the env vars to point Claude Code at the local server |
| `agents/litert-lm-debug.md` | "the local server is misbehaving" | Subagent that tails logs, probes endpoints, writes a diagnosis report |
| `agents/litert-lm-local.md` | invoked via `Task(subagent_type="litert-lm-local", ...)` | **Subagent-delegation mode.** A wrapper around local inference. The main cloud Claude delegates routine work to it; the subagent runs the prompt against the local model and returns the text. |
| `mcp/litert_lm_mcp.py` | always (registered in `plugin.json`) | MCP tools: `litert_lm_status`, `litert_lm_list_models`, `litert_lm_start`, `litert_lm_stop`, `litert_lm_switch_model`, `litert_lm_generate` (used by the litert-lm-local subagent) |

Everything writes its state to `~/.litert-lm/` (PID file, log file,
`config.json` with the last-used model). Nothing in your home directory is
touched outside that folder.

---

## Mode 1 — Server-proxy (`ANTHROPIC_BASE_URL`)

LiteRT-LM's Anthropic handler accepts any auth token. The two exports you
need:

```bash
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any-value
```

Then either:

```bash
claude -p "what is 2+2?" --model gemma-3n-e2b
```

or open Claude Code interactively. `/litert-lm-config` prints these for you with
the model id substituted in.

In this mode, ALL Claude Code traffic goes to the local model. Cloud Claude is bypassed entirely.

## Mode 2 — Subagent delegation (`Task(subagent_type="litert-lm-local")`)

In this mode, you stay on cloud Claude but selectively delegate routine work to the local model. Cloud Claude does the orchestration (it understands your high-level intent, picks what to delegate, integrates results); the local model does the bulk text generation.

**Setup:** the same as Mode 1 — start the server with `/litert-lm-start <model-path>`. Do NOT export `ANTHROPIC_BASE_URL`. Run Claude Code normally against `api.anthropic.com`.

**How to invoke:** ask cloud Claude to delegate something. The subagent description tells Claude when to use it; just describe the task in natural terms:

```
> Generate doc strings for the public functions in src/handlers.py.
  Delegate to the local model since they're routine.

> Summarize the changes in this PR using the local model so we don't burn
  cloud tokens on it.

> Paraphrase this email using my local model — don't send it to the cloud.
```

Cloud Claude will spawn a `litert-lm-local` subagent via `Task`, the subagent will call the `litert_lm_generate` MCP tool, and the result flows back into your conversation.

**When to use which mode:**

- **Mode 1** when privacy / offline / max cost savings outweigh quality drop. Examples: working on a plane, code under embargo, demo-running through Claude Code's quota.
- **Mode 2** when you want cloud-Claude-quality on hard tasks but want to redirect routine work locally. Examples: high-volume doc-string passes, code summaries during code review, paraphrasing.
- **Both at once** is fine — they share the server. Ad-hoc switch by setting / unsetting `ANTHROPIC_BASE_URL`.

---

## Troubleshooting

Run the troubleshoot skill:

```text
> litert-lm not working
```

Or jump straight to the relevant slash command:

| Symptom | Try |
| --- | --- |
| `Connection refused` | `/litert-lm-status` to check the server is up |
| `not_found_error` for the model id | `/litert-lm-status` to see which model is loaded, then `claude --model <that-id>` |
| Slow first token | normal on first request; the model warms up |
| `overloaded_error` | server is at concurrency limit; wait or restart with `/litert-lm-switch` |
| Port already in use | `/litert-lm-start --port 9380` |

For deeper investigation, ask Claude to invoke the `litert-lm-debug` subagent.

---

## Where files live

| Path | Purpose |
| --- | --- |
| `~/.litert-lm/server.pid` | PID of the running server (if any) |
| `~/.litert-lm/server.log` | Stdout + stderr from the server, tailable |
| `~/.litert-lm/config.json` | Last-used model path, port, host |
| `~/.litert-lm/last-model-id` | Model id resolved from `/v1/models` after startup |

To wipe the plugin's state without uninstalling it, delete `~/.litert-lm/`.

---

## Versioning

Pinned to `0.1.0` in `plugin.json`. Bump that field when you ship updates so
Claude Code picks them up via `/plugin update`. If you instead want
update-on-every-commit semantics for an internal copy, drop the `version`
field from `plugin.json`.

---

## Reporting issues

This plugin lives next to LiteRT-LM. File issues in the LiteRT-LM repo with
the `claude-code-plugin` label.
