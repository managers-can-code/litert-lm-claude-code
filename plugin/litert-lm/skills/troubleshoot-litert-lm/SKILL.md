---
name: troubleshoot-litert-lm
description: Diagnose problems with the local LiteRT-LM Anthropic-compatible server when Claude Code can't talk to it, the server is slow, returns errors, or won't start. Use this skill when the user reports any LiteRT-LM serve failure or Claude-Code-to-LiteRT-LM connection issue.
when_to_use: Trigger phrases include "litert-lm not working", "claude code can't connect to litert-lm", "litert-lm slow", "litert-lm error", "litert-lm serve failed", "connection refused litert-lm", "anthropic api error local model", "claude --model not found".
allowed-tools: Bash Read
---

# Troubleshoot LiteRT-LM with Claude Code

Diagnose and fix the seven most common failure modes when running Claude Code
against a local `litert-lm serve --api anthropic` server. Run the diagnostic
flow first, then jump to the matching fix.

## Diagnostic flow (run in this order)

1. **Probe the server health endpoint:**

   ```text
   /litert-lm-status
   ```

   If this prints a model id, the server is alive; the problem is on the
   Claude Code side.
   If this prints a connection error, the server is the problem.

2. **Check what process owns port 9379:**

   ```bash
   lsof -nP -iTCP:9379 -sTCP:LISTEN || echo "nothing listening on 9379"
   ```

3. **Tail the last lines of the server log:**

   ```bash
   tail -n 50 ~/.litert-lm/server.log
   ```

4. **Check the env vars Claude Code is using:**

   ```bash
   echo "ANTHROPIC_BASE_URL=$ANTHROPIC_BASE_URL"
   echo "ANTHROPIC_AUTH_TOKEN=${ANTHROPIC_AUTH_TOKEN:+set}"
   ```

5. **Note the model id the server reports:** the value Claude Code uses must
   match exactly. Run `/litert-lm-status` to see it.

## Fixes by symptom

### Connection refused

The server isn't running, or it crashed.

- Run `/litert-lm-status`. If it says the server is down, restart with
  `/litert-lm-start <model-path>`.
- If `/litert-lm-start` exits with "model file not found", verify the model
  path. Use an absolute path, no `~` shell expansion.
- If `/litert-lm-start` exits with "port already in use", another process is
  squatting on 9379. See "Port conflicts" below.
- If the server starts but immediately dies, run `tail -n 100
  ~/.litert-lm/server.log` and look for `failed to load model`. Usually the
  model file is corrupt or built for the wrong runtime; redownload from
  <https://ai.google.dev/edge/litert-lm/models>.

### `not_found_error` for an unknown model

Claude Code sent a model id the server doesn't recognize.

- Run `/litert-lm-status` to see the id the server actually loaded.
- Re-run with `claude --model <that-exact-id>`. The id is the basename of
  the model file without the `.litertlm` suffix, lowercased.
- Or ask Claude to use the `litert-lm` MCP server's `litert_lm_list_models`
  tool: it returns whatever the server reports.

### `overloaded_error`

The server's concurrency limit is reached. Default is small (intentional) so
laptops don't OOM.

- Wait 5 to 10 seconds and retry the prompt.
- If you see this constantly with single-user use, you may have a hung
  request. `/litert-lm-stop` then `/litert-lm-start` clears it.

### Slow inference (first token > 30s)

First-token latency includes model warmup. After that, throughput depends on
the model size and your hardware.

- Confirm the model is actually loaded into the accelerator (Metal on
  macOS, GPU on Linux). Tail the log for `using backend:` lines.
- Try a smaller model: Gemma 3n E2B is the smallest viable option for most
  laptops. Larger models (E4B, 7B+) need a real GPU.
- Disable verbose logging if it's set: `--vlog_level 0`.
- If first-token is fine but per-token is slow, the model is just too big
  for the hardware. There is no silver bullet. Switch models.

### Roughly four-minute SSE timeout

Claude Code's HTTP client times out long-running streamed completions. With
LiteRT-LM, the server sends keepalive pings, so this should not happen for
normal completions, but big context + slow hardware can push past it.

- Confirm the server log shows the request was still being processed when
  the client gave up.
- Use a smaller `max_tokens` in the prompt, or a faster model.
- If it's reproducible, file an issue against LiteRT-LM with the timing
  from `~/.litert-lm/server.log`.

### Port conflicts

Default port 9379 is occupied by something else.

- Find the process: `lsof -nP -iTCP:9379 -sTCP:LISTEN`.
- Kill it if it's stale, or pick a different port:
  `/litert-lm-start <model> --port 9380`.
- Update the env var: `export ANTHROPIC_BASE_URL=http://localhost:9380`.
  The old shell session won't see the change automatically.

### Model file missing

`/litert-lm-start` reports "model file not found".

- Verify the path with `ls -l <path>`.
- The path must be absolute. `~` is not expanded by the helper script.
- If the file looks fine but the server still fails to load it, the file
  may be partially downloaded. Check the size against the upstream listing
  at <https://ai.google.dev/edge/litert-lm/models>.

## Escalation: invoke the litert-lm-debug subagent

If the symptom doesn't match anything above, hand off to the deep-dive
subagent:

> "Use the litert-lm-debug subagent to investigate."

It tails the log, probes endpoints, identifies the LiteRT-LM version, and
writes a structured diagnosis report.

## What this skill does NOT do

- Modify the user's shell rc files without permission.
- Kill processes the user didn't ask to kill.
- Download model files. Always have the user click through the model
  catalog so they accept the relevant license.
