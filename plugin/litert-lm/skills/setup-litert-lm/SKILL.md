---
name: setup-litert-lm
description: Set up Claude Code to talk to a local LiteRT-LM model running 'litert-lm serve --api anthropic'. Use this skill whenever the user asks to set up litert-lm, configure litert-lm for Claude Code, use Claude Code with a local model, run Claude Code offline, or point Claude Code at a local server.
when_to_use: Trigger phrases include "set up litert-lm", "use Claude Code with local model", "configure litert-lm for claude code", "litert-lm setup", "run claude code locally", "point claude code at litert-lm", "use claude code offline".
allowed-tools: Bash Read Write Edit
---

# Set up LiteRT-LM with Claude Code

Walk the user from zero to a working `claude --model <local-id>` session
against a local LiteRT-LM server. The user already has both Claude Code and
LiteRT-LM installed in the easy case; this skill verifies that and fills in
gaps when they don't.

Work through the steps below in order. Skip a step if a prior step's check
already proved it is unnecessary, but say so out loud.

## Step 0: confirm we're talking about the right thing

Make sure the user actually wants to use a local model. If they're really
asking "how do I install Claude Code", offer the install one-liner and stop.

The supported model server is **LiteRT-LM** built with the `AnthropicHandler`
PR merged. Without that PR, `litert-lm serve --api anthropic` fails. Tell the
user that explicitly when relevant.

## Step 1: verify Claude Code is installed and recent enough

```bash
claude --version
```

The plugin requires Claude Code >= 2.1.123. If `claude` is missing or older,
print:

```bash
npm install -g @anthropic-ai/claude-code
```

## Step 2: verify LiteRT-LM is installed

```bash
litert-lm --version 2>/dev/null || echo "missing"
litert-lm serve --help 2>&1 | grep -E "anthropic" || echo "anthropic api not built in"
```

If the binary is missing, install it:

```bash
uv tool install litert-lm
# or, without uv:
pipx install litert-lm
```

If the binary is present but `--api anthropic` is not listed, the user has an
older build. Tell them to upgrade to a release that includes the Anthropic
handler PR (or to install from source until that ships).

## Step 3: pick a model

The user needs a `.litertlm` model file on local disk. Recommend Gemma 3n E2B
as the smallest viable starter model:

- name: `gemma-3n-E2B-it-int4.litertlm`
- size: small enough to run on a recent laptop
- catalog: <https://ai.google.dev/edge/litert-lm/models>

If the user already has a model file, ask for the absolute path and confirm
it exists.

## Step 4: start the server

Prefer the slash command this plugin ships:

```text
/litert-lm-start /absolute/path/to/your-model.litertlm
```

That spawns the server in the background, writes the PID to
`~/.litert-lm/server.pid`, and tees the server's output to
`~/.litert-lm/server.log`. The default port is **9379**.

If the user wants to do it by hand:

```bash
litert-lm serve --api anthropic --model /absolute/path/to/your-model.litertlm
```

leave the terminal open while it runs.

After starting, confirm the server is reachable:

```text
/litert-lm-status
```

This pings `http://localhost:9379/v1/models` and prints the loaded model id.
Note the id - the user passes it to `claude --model`.

## Step 5: export the env vars Claude Code needs

LiteRT-LM's Anthropic handler accepts any auth token, but Claude Code still
needs both env vars set so it doesn't try to reach `api.anthropic.com`.

```bash
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any-value
```

`/litert-lm-config` prints these with the right model id pre-filled. Suggest the
user paste them into their shell profile (`~/.zshrc`, `~/.bashrc`) for
persistence.

## Step 6: smoke test

In the same shell where the env vars are exported:

```bash
claude -p "what is 2+2?" --model gemma-3n-e2b
```

Replace the model id with whatever `/litert-lm-status` reported. If the model
answers, the integration is wired up.

If the test fails, do not loop on the same error; pivot to the
`troubleshoot-litert-lm` skill (or tell the user to ask "litert-lm not
working").

## Optional follow-ups

- Suggest `/litert-lm-switch <other-model.litertlm>` if they have multiple
  models and want to compare.
- Suggest `/litert-lm-stop` when they're done so the server doesn't keep using
  GPU/RAM in the background.
- If they want to drop the env vars permanently into their shell rc:

  ```bash
  echo 'export ANTHROPIC_BASE_URL=http://localhost:9379' >> ~/.zshrc
  echo 'export ANTHROPIC_AUTH_TOKEN=any-value' >> ~/.zshrc
  ```

## Output style

Be terse. Print the exact command, run it via Bash where appropriate, and
report the result. Do not paraphrase commands the user can copy-paste.
