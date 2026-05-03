# Testing both modes — peer tester workflow

This guide walks you from a clean machine to verified-working in **both modes**:

- **Mode 1: Server proxy** — Claude Code talks ONLY to the local model.
- **Mode 2: Subagent delegation** — Cloud Claude stays primary; delegates routine work to the local model.

Each mode has its own verification checklist with what to expect at every step. Total time end-to-end: ~15 minutes including the model download.

---

## Prerequisites — one-time setup (~10 min)

These steps are shared by both modes.

### 1. Install the integration

```bash
# Clone the working LiteRT-LM fork (with AnthropicHandler pre-applied)
gh repo clone managers-can-code/LiteRT-LM
cd LiteRT-LM
git checkout feat/serve-anthropic-api

# Install the patched LiteRT-LM CLI
uv tool install --from . litert-lm
```

**Verify:**

```bash
litert-lm serve --help | grep -A1 "\-\-api"
# expected: type=click.Choice... that includes "anthropic"
```

### 2. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
claude --version
# expected: 2.1.x or newer
```

### 3. Install the companion plugin (needed for both modes)

Claude Code plugins must be loaded either via a marketplace or via the `--plugin-dir` flag. Plain copy + `claude plugin enable` does not work.

```bash
# Clone the companion repo
gh repo clone managers-can-code/litert-lm-claude-code
cd litert-lm-claude-code
```

Then choose one of:

**Quick (one session, no install):**

```bash
claude --plugin-dir "$(pwd)/plugin/litert-lm"
```

The slash commands and MCP server are wired in for that session only.

**Permanent (recommended) — via a local marketplace:**

```bash
# marketplace.json MUST live in .claude-plugin/marketplace.json at the marketplace root.
mkdir -p ~/.claude/plugins/marketplace/.claude-plugin
cp -R plugin/litert-lm ~/.claude/plugins/marketplace/litert-lm

cat > ~/.claude/plugins/marketplace/.claude-plugin/marketplace.json <<'JSON'
{
  "name": "Local Plugins",
  "owner": {"name": "You"},
  "plugins": [
    {"name": "litert-lm", "source": "./litert-lm"}
  ]
}
JSON

# Register the marketplace and install the plugin:
claude plugin marketplace add ~/.claude/plugins/marketplace
claude plugin install "litert-lm@Local Plugins"
```

**Verify:** restart Claude Code, type `/` and confirm `/litert-lm-start`, `/litert-lm-stop`, etc. appear.

### 4. Pre-cache a model

```bash
litert-lm run --from-huggingface-repo=litert-community/Gemma-4-E2B-it \
  gemma-4-E2B-it.litertlm \
  --prompt "say hi"
# expected: a short response from the model
# the .litertlm file is now cached at:
#   ~/.cache/huggingface/hub/models--litert-community--gemma-4-E2B-it-litert-lm/snapshots/<sha>/gemma-4-E2B-it.litertlm
```

### 5. Start the server (used by BOTH modes)

```bash
MODEL_PATH=$(find ~/.cache/huggingface -name "*.litertlm" | head -1)
echo "Model: $MODEL_PATH"

# Inside Claude Code, use the slash command:
/litert-lm-start $MODEL_PATH

# OR from a terminal:
litert-lm serve --api anthropic --model "$MODEL_PATH" &

# Verify it's running:
curl -s http://localhost:9379/v1/models | python3 -m json.tool
# expected: JSON with `data: [{"id": "local-model", ...}]`
```

---

## Mode 1 verification — Server-proxy

In this mode, Claude Code's entire traffic goes to the local model. Cloud Claude is bypassed.

### Setup

```bash
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any-value-the-server-ignores-it
```

### Test 1 — single-turn chat

```bash
claude -p "what is 2+2?" --bare --output-format json --model local-model
```

**Expected:**

```json
{
  "type": "result",
  "is_error": false,
  "result": "2 + 2 = 4.",
  "stop_reason": "end_turn",
  "duration_ms": <less than 5000>,
  "modelUsage": {
    "local-model": {"outputTokens": <small number>, "costUSD": ...}
  }
}
```

`is_error: false`, response from the local model in ~2-5 seconds. `modelUsage` shows ONLY `local-model` (no `claude-haiku-*` entry — confirming nothing went to cloud).

### Test 2 — interactive session

```bash
claude --model local-model
# type a few prompts
```

Each response visibly streams from your local model. Network monitor (e.g., `lsof -i :443 | grep claude`) shows no traffic to `api.anthropic.com`.

### Test 3 — automated reproduction (optional)

```bash
cd <litert-lm-claude-code-checkout>
bash tier3-runner/run-tier3-auto.command
# writes outputs/tier3-real-report.md with full transcript
```

### Tear down

```bash
unset ANTHROPIC_BASE_URL
unset ANTHROPIC_AUTH_TOKEN
# server keeps running for Mode 2; stop it with /litert-lm-stop when done
```

---

## Mode 2 verification — Subagent delegation

In this mode, you talk to **cloud Claude** as normal. The local model is invoked only when cloud Claude delegates a routine sub-task.

### Setup

**Make sure `ANTHROPIC_BASE_URL` is NOT set** (Mode 2 needs the cloud connection):

```bash
unset ANTHROPIC_BASE_URL
unset ANTHROPIC_AUTH_TOKEN
```

The server from step 5 is still running. The plugin from step 3 is active.

### Test 1 — explicit delegation

Open Claude Code and type:

```
> Use the local model to generate a one-line doc string for this Python function:
>
>     def merge_sort(arr):
>         if len(arr) <= 1: return arr
>         mid = len(arr) // 2
>         return merge(merge_sort(arr[:mid]), merge_sort(arr[mid:]))
```

**Expected:**

- Cloud Claude responds quickly (it's reasoning on cloud).
- It internally invokes `Task(subagent_type="litert-lm-local", prompt="...")`.
- The subagent calls the `litert_lm_generate` MCP tool.
- The local model produces the doc string.
- Cloud Claude integrates the result into its response.

You'll see a transcript entry indicating the subagent ran. The doc string itself is what your local model produced.

**Verification check:** `litert-lm` server log shows a `POST /v1/messages` entry timestamped during the test. Cloud Claude shows a tool-use trace including the `litert_lm_generate` call.

### Test 2 — delegation with refusal

```
> Use the local model to architect a microservices migration plan for our
> 200k-line monolith.
```

**Expected:**

The `litert-lm-local` subagent should refuse this kind of task per its system prompt (multi-file reasoning, complex architecture). Cloud Claude either handles it directly or surfaces the refusal explanation to you.

### Test 3 — delegation for privacy

```
> Paraphrase the following internal email using the local model — do not
> send the original text to the cloud:
>
>     [your email text here]
```

**Expected:**

Cloud Claude formulates a delegation prompt that includes the sensitive text and routes it through the subagent. The original text only crosses the wire to your local server (`localhost:9379`); the cloud only sees Claude's orchestration metadata, not the email content itself.

**Verification check:** monitor outbound traffic during the test. The local server log shows the email text in its request body. Outbound traffic to `api.anthropic.com` does NOT include the email content (only the orchestration prompt mentioning that delegation should occur).

> **Note on privacy semantics.** Cloud Claude must reason about the task to decide what to delegate, so it sees a description of what you want done. To avoid the cloud seeing sensitive content at all, use Mode 1 (server proxy) instead — that's the right tool for hard-privacy cases. Mode 2 protects the *bulk inference* from the cloud, not the *task description*.

---

## Switching between modes

```bash
# Use Mode 1 (server proxy):
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any
claude

# Switch to Mode 2 (subagent delegation):
unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN
claude
```

The server and plugin are shared. Only the env vars change.

---

## Common failures and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `--api: anthropic` not in `--help` output | Wrong LiteRT-LM clone | Verify you're on `feat/serve-anthropic-api` branch of the fork |
| `litert-lm-local` subagent not found | Plugin not loaded | Re-run with `claude --plugin-dir <path>/plugin/litert-lm`, OR install via local marketplace (see step 3); plain `claude plugin enable` won't work |
| `claude plugin enable litert-lm` says "Plugin not found in any editable settings scope" | Plugin must be registered via a marketplace first | Use one of the install methods in step 3 (`--plugin-dir` for one-shot, or the local-marketplace setup for permanent) |
| `litert_lm_generate: server is not running` | Server stopped | `/litert-lm-start <model-path>` |
| Mode 2 test shows requests in `api.anthropic.com` traffic only (no local hit) | Cloud Claude didn't delegate | Phrase your prompt to explicitly request local delegation: "use the local model to ..." |
| Mode 1 test shows requests to `api.anthropic.com` despite `ANTHROPIC_BASE_URL` set | Env var not exported in the shell that ran `claude` | `echo $ANTHROPIC_BASE_URL` to confirm; re-export and rerun `claude` |
| Long generations time out at ~4 minutes | Quiet SSE during long generation | Server emits `ping` events every 10 s — if it still happens, reduce `max_tokens` or split the task |

---

## What success looks like, side-by-side

After completing both modes, you should have:

| Artifact | Mode 1 | Mode 2 |
|---|---|---|
| Real model output reaching Claude Code | ✓ | ✓ (via subagent) |
| `is_error: false` in the JSON envelope | ✓ | ✓ |
| `modelUsage` shows local-model | ✓ (only) | ✓ (alongside `claude-haiku-*` for orchestration) |
| Verifiable in server log | ✓ | ✓ |
| Works offline | ✓ | ✗ (cloud Claude needed) |
| Cloud token cost incurred | $0 | small (orchestration only) |

If both work, the integration is verified end-to-end on your machine.
