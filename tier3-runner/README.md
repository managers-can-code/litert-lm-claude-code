# Tier 3 Real-Hardware Runner

Runs the real-model verification on your machine in one command.

## What it does

1. Boots `outputs/pr/python/litert_lm_cli/serve_anthropic.py` against your installed `litert_lm` Python package and the `.litertlm` model file you point it at.
2. Hits `/v1/models`, non-streaming `/v1/messages`, and streaming `/v1/messages` directly via curl.
3. Runs `claude -p "what is 2+2?"` with `ANTHROPIC_BASE_URL` pointing at our server. This is the real-model equivalent of the protocol test that already passed against a stub.
4. Runs a tool-use scenario (`claude -p "use Read to read /etc/hostname"` with `--allowedTools Read`).
5. Writes everything to `outputs/tier3-real-report.md`.
6. Cleans up the launcher process.

Wall time: model load (~30-60 s) + scenarios (~1-2 min) = **~3-5 min total**.

## Prereqs

The script preflight-checks all of these:

- `python3` with `litert_lm` importable (you installed the CLI — that ships the Python package).
- `claude` CLI (Claude Code 2.x). If missing: `npm install -g @anthropic-ai/claude-code`.
- `curl`.
- A `.litertlm` model file you can pass as the first argument.
- Optional: `jq` for prettier JSON output (script falls back to `python -m json.tool`).

## Run

```bash
bash /Users/ramiyengar/Library/Application\ Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/tier3-runner/run-tier3.sh \
  ~/Downloads/gemma-4-E2B-it.litertlm
```

Adjust the model path to wherever you saved the file.

If you saved it under `~/Downloads/` with the exact filename, use:

```bash
bash run-tier3.sh ~/Downloads/gemma-4-E2B-it.litertlm
```

(after `cd` into the `tier3-runner/` directory.)

## What success looks like

- Step 1 returns a valid `/v1/models` JSON listing `local-model`.
- Step 2 returns a non-streaming Anthropic Messages JSON with a non-empty assistant text.
- Step 3 returns a stream of `event: message_start ... event: message_stop` SSE events, with the assistant's text reconstructable from the `text_delta` chunks.
- **Step 4 is the moneyshot**: Claude Code returns a JSON envelope where the assistant message is non-empty. This proves the full path — Claude Code ↔ HTTP/SSE ↔ our handler ↔ real `litert_lm.Engine` ↔ your real model — works end-to-end.
- Step 5 either round-trips a tool call or surfaces a clean error if your model doesn't natively support tool calling. Either is acceptable; if your model can't do tool use, that's a model property, not an integration bug.

## What failure looks like (and what each means)

| Symptom | Likely cause | Action |
|---|---|---|
| `cannot import litert_lm` in preflight | Package not installed | `uv tool install litert-lm` or `pip install litert-lm` |
| `Engine() failed: ...` in launcher log | Bad model file / unsupported format | Verify the `.litertlm` file isn't corrupted; check LiteRT-LM compatibility |
| Server didn't become ready in 90s | Slow model load OR launcher crashed | Check `tier3-server.log` (path printed) for tracebacks |
| Step 2 returns 500 `api_error` | Engine threw during inference | Server log will have the traceback |
| Step 4 Claude Code returns "I encountered an error" | Most likely a stream-format mismatch from the real engine yielding chunks in an unexpected shape | Server log will show the chunk shape; report it back |
| Step 5 returns `tool_use` content correctly but Claude Code reports it as malformed | The model's tool-call JSON didn't match its declared `input_schema` | Model-side issue; not blocking for v1 |

## After the run

Open `outputs/tier3-real-report.md`. Drop the report path back into the chat with me and I'll synthesise the final confidence verdict.

## Why we set `accept_any_model = True` in the launcher

Claude Code makes background requests with `claude-3-5-haiku-*`-flavored model names (probably for context compaction / summarization tasks) in addition to the user-specified `--model`. Under our default strict mode, those would 404 with `not_found_error`, degrading the Claude Code session. The launcher flips this to permissive specifically for the test. Whether to flip the production default is a separate decision — see the open question at the end of the chat.
