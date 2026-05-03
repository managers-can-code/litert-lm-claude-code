# Phase 0 Discovery — Consolidated Findings

**Date:** 2026-04-30
**Owner:** ram
**Status:** Phase 0 complete; awaiting owner sign-off on revised design (`design.md` v0.3)

This document consolidates findings from four parallel discovery subagents (Discovery-A through D) plus assembles the Phase 0 deliverables required to start Phase 2 implementation.

---

## TL;DR — three findings that revise the plan

1. **LiteRT-LM already has a `serve` subcommand.** [`python/litert_lm_cli/serve.py`](https://github.com/google-ai-edge/LiteRT-LM/blob/main/python/litert_lm_cli/serve.py) (15 KB, alpha) ships `GeminiHandler` and `OpenAIHandler` over stdlib `http.server`. The integration is *one new handler* (`AnthropicHandler`) following the same pattern, registered as a third `--api` choice. PR shrinks to ~3 files.
2. **No new dependencies needed.** Maintainers explicitly chose stdlib `http.server` — no FastAPI, no aiohttp. We mirror. This sidesteps the "dependency policy is undocumented" risk entirely.
3. **`CONTRIBUTING.md` says contributions aren't open yet.** Owner reported "maintainers contacted, CLA signed," so this is OK, but the PR description must reference the prior conversation. Recommend opening a GitHub Issue first to declare intent and link the conversation.

The result: v1 effort drops from ~3 days of implementation to ~1 day, and **tool use can ship in v1** (existing handlers already do tool translation against the LiteRT-LM `Conversation` API — we mirror the pattern). T1 from the execution plan flips: tool use is no longer a v1.5 deferral.

---

## Discovery-A — Claude Code wire protocol

**Source:** [code.claude.com/docs/en/llm-gateway](https://code.claude.com/docs/en/llm-gateway), [code.claude.com/docs/en/env-vars](https://code.claude.com/docs/en/env-vars), [code.claude.com/docs/en/headless](https://code.claude.com/docs/en/headless), Claude Code v2.1.123.

### Environment variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_BASE_URL` | API endpoint root (default `https://api.anthropic.com/v1`). Accepts any HTTP/HTTPS URL. |
| `ANTHROPIC_AUTH_TOKEN` | Bearer token via `Authorization: Bearer <token>` (used with gateways). |
| `ANTHROPIC_API_KEY` | Sent via `X-Api-Key` (direct Anthropic). |
| `ANTHROPIC_DEFAULT_OPUS_MODEL` / `_SONNET_MODEL` / `_HAIKU_MODEL` | Pin specific model versions. |
| `ANTHROPIC_CUSTOM_HEADERS` | JSON object of extra headers. |
| `ANTHROPIC_BEDROCK_SERVICE_TIER` | Bedrock-specific. |

### Endpoints Claude Code hits

- `POST /v1/messages` — required.
- `POST /v1/messages/count_tokens` — optional. **Note:** clients that pre-flight `count_tokens` can see a 404 cascade that degrades subsequent requests; we must implement at least a stub.
- `GET /v1/models` — optional.

### Headers sent

- `Authorization: Bearer <token>` if `ANTHROPIC_AUTH_TOKEN` set
- `X-Api-Key: <key>` if `ANTHROPIC_API_KEY` set
- `Content-Type: application/json`
- `anthropic-beta` only when beta features in use
- **No fixed `anthropic-version` header** — different from raw Anthropic API

### Headless mode — confirmed available

```
claude -p "your task" --bare --output-format json --allowedTools "Read,Edit,Bash"
```

`--bare` skips local hooks/skills/plugins/MCP — the right setting for CI. This is what Agent B's e2e harness will use.

### Notable quirk

When `ANTHROPIC_BASE_URL` is non-Anthropic, **tool search is disabled by default**. Means: Claude Code won't try to discover server-managed tool catalogs. Our server still receives `tools` in request bodies normally; this only affects server-side tool-discovery flow (which we don't implement anyway).

---

## Discovery-B — LiteRT-LM source + Python bindings

**Source:** [github.com/google-ai-edge/LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM).

### Python bindings — present and stable

- Package: [`python/litert_lm/`](https://github.com/google-ai-edge/LiteRT-LM/tree/main/python/litert_lm)
- Public API ([`__init__.py`](https://raw.githubusercontent.com/google-ai-edge/LiteRT-LM/main/python/litert_lm/__init__.py)): `Engine`, `Conversation`, `Session`, `Backend`, `SamplerConfig`, `Tool`, `ToolEventHandler`, `Responses`, `Benchmark`, `tool_from_function`.
- Implementation: ctypes FFI ([`_ffi.py`](https://github.com/google-ai-edge/LiteRT-LM/blob/main/python/litert_lm/_ffi.py)) over the `litert_lm` C API.
- README badge: "Python ✅ Stable."

### Key API surface for our integration

- `Engine(model_path, backend=Backend.CPU, max_num_tokens=None, cache_dir="")` — context manager.
- `Engine.create_conversation(...).send_message({"role":"user","content":"…"})` — single-shot, returns dict.
- `Conversation.send_message_async(message)` — yields chunk dicts (synchronous iterator backed by `queue.Queue` + C stream callback). **This is the streaming path our SSE encoder consumes.**
- `Conversation.cancel_process()` / `Session.cancel_process()` — cancellation.

### Existing serve subcommand (THE KEY FINDING)

[`python/litert_lm_cli/serve.py`](https://raw.githubusercontent.com/google-ai-edge/LiteRT-LM/main/python/litert_lm_cli/serve.py) — 15 KB, marked alpha:

- Built on stdlib `http.server.HTTPServer` (deliberately no framework).
- Handlers: `GeminiHandler` for `POST /v1beta/models/{id}:generateContent` + `:streamGenerateContent` (SSE), `OpenAIHandler` for `POST /v1/responses`.
- CLI flags: `--host` (default `localhost`), `--port` (default `9379`), `--api {gemini,openai}` (default `gemini`), `--verbose`.
- Engine global singleton `_current_engine` keyed by `model_id`.
- `_ProxyTool` returns tool calls to the client when `automatic_tool_calling=False`.
- Tests already exist: `serve_test.py`, `serve_openai_integration_test.py`, `serve_gemini_integration_test.py`.

### Repo conventions

- **Build:** Bazel-primary (`WORKSPACE`, `BUILD`, `.bazelrc`, `.bazelversion`), CMake-secondary. Distribution via `uv tool install litert-lm`.
- **Test framework:** `absltest` + `parameterized`. NOT pytest.
- **Test layout:** co-located `*_test.py` siblings.
- **Code style:** No `pyproject.toml`, no ruff/black/mypy config in tree. Inline `# pylint: disable=` directives. Google Python style.
- **Deps:** `requirements.txt` pins ~70 packages; new deps must be added with exact pins. **Maintainers chose stdlib `http.server` deliberately — adding FastAPI would be a fight.**
- **Apache-2.0 file headers required:** `# Copyright 2026 The ODML Authors. / Licensed under the Apache License, Version 2.0`.

### CONTRIBUTING.md status — flag

[`CONTRIBUTING.md`](https://raw.githubusercontent.com/google-ai-edge/LiteRT-LM/main/CONTRIBUTING.md) is 5 lines and says the repo is "not currently ready for code contributions." Owner reported maintainers contacted + CLA signed, so we proceed, but the PR must reference the prior conversation. Recommend a GitHub Issue first.

### Recommendation: extend the existing `serve` subcommand in-tree

Add inside `python/litert_lm_cli/`:

- `serve_anthropic.py` — `AnthropicHandler` mirroring `GeminiHandler` / `OpenAIHandler`.
- `serve_anthropic_test.py` and `serve_anthropic_integration_test.py` — `absltest`.
- One-line edit to `serve.py`: extend `--api` Click choice to `{gemini, openai, anthropic}`.
- BUILD additions in `python/litert_lm_cli/BUILD`.

**Fallback if rejected:** companion repo `litert-lm-anthropic-server` depending on `litert-lm` from PyPI.

---

## Discovery-C — Local-LLM bridge integration patterns

### What a generic local-LLM bridge looks like

- Exposes `POST /v1/messages` directly (Anthropic Messages API). No translation needed in Claude Code.
- Pass-through model names. User sets `claude --model <local-tag>`. For tools that hard-code Anthropic names, an alias/copy mechanism can map a local tag to a familiar Anthropic name.
- Native tool use (no grammar shim) — relies on the underlying model's native tool support.
- Anthropic-spec SSE byte-for-byte (a stream converter tracks block lifecycle).
- `ANTHROPIC_AUTH_TOKEN=<anything>` required-but-ignored. Localhost-only by default.
- Happy path: start the local server, pull/load a model, `export ANTHROPIC_BASE_URL=http://localhost:<port>`, `export ANTHROPIC_AUTH_TOKEN=<anything>`, `claude --model <tag>`.

### Engineering issues we address

- **`count_tokens` 404 cascade degrades the server** — we implement a stub.
- **Tool-call arguments not streamed incrementally** → 255 s Claude Code timeout. We stream them.
- **No ping events during long generations** → some clients time out. We emit pings.
- **Tool-bloat** with ~259 Claude Code tool definitions ([claude-code#25857](https://github.com/anthropics/claude-code/issues/25857)) — model-side problem, document but don't try to fix.
- **Context length**: local-LLM bridges typically recommend ≥32K context for agentic use. Defaults are often 4K. Document our minimum.

### What we adopt / what we skip

| Decision | Adopted | Skipped / improved | Reason |
|---|---|---|---|
| `/v1/messages` direct | Yes | | No Claude Code translation needed |
| Localhost-only default | Yes | | Same security posture |
| `ANTHROPIC_AUTH_TOKEN` accepted-but-ignored | Yes | | SDK requires *some* value |
| Anthropic-spec SSE event names | Yes | | Claude Code parses literally |
| Pass-through model names | Yes | | Predictable, matches the standard local-LLM bridge UX |
| `cp`/alias mechanism | | Skip v1; document `--model` | Avoid duplicating CLI scope already provided by the underlying tooling |
| Native tool-use translation | Yes | | Required for Claude Code agent loop |
| Stream tool-call arguments | | **Improve** — incremental | Avoid 255 s timeout |
| `ping` events during gaps | | **Improve** — emit periodically | Cheap insurance |
| `count_tokens` endpoint | | **Improve** — return rough estimate | Avoid 404 cascade degradation |
| Standalone `launch` wrapper | | Skip | Env var setup is sufficient |

---

## Discovery-D — Anthropic SSE spec + golden fixtures

**Sources:** [Simon Willison TIL with verbatim Anthropic capture](https://til.simonwillison.net/llms/streaming-llm-apis), [docs.anthropic.com/en/api/messages-streaming](https://docs.anthropic.com/en/api/messages-streaming) (search excerpts; direct fetch blocked), [docs.anthropic.com/en/api/errors](https://docs.anthropic.com/en/api/errors).

### Wire format

- `event: <name>\ndata: <json>\n\n` (LF, not CRLF).
- Both `event:` and `data:` lines on every event.
- `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache`.
- No `retry:` hints, no SSE comments observed in real traffic.

### Event ordering

```
message_start
  (per content block:
    content_block_start
    content_block_delta+
    content_block_stop)
message_delta
message_stop
```

`ping` events can appear anywhere. `error` events appear mid-stream as `event: error\ndata: {...}` — HTTP 200 by then, so the error is in-band.

### Stop reasons

`end_turn`, `max_tokens`, `stop_sequence`, `tool_use` (also `pause_turn`, `refusal` documented but not in our v1 scope).

### Captured fixtures

Four text fixtures saved under `outputs/fixtures/`:

- `anthropic-sse-stream-1-simple.txt` — "what is 2+2?" → "4." Single text block, end_turn.
- `anthropic-sse-stream-2-multi-turn.txt` — Multi-turn follow-up, single text block, end_turn.
- `anthropic-sse-stream-3-cancel.txt` — Truncated mid-content_block_delta. No closing brace/quote/`\n\n`.
- `anthropic-sse-stream-4-tool-use.txt` — Text block + tool_use block with `input_json_delta` partial JSON. stop_reason: tool_use.

These are the byte-equal test oracle for Agent A's SSE encoder unit tests and Agent B's diff-tests.

### Ambiguity flags

- The spec is documented across multiple pages, some of which the agent could not directly fetch (cited via search excerpts + verbatim live capture). Fixtures match the live-captured shape; should be re-validated against a real API response when an API key is available.
- `ping` cadence is not specified — we emit one between `content_block_start` and the first delta to match the captured reference, plus one every ~10 s during long content_block_deltas.

---

## Phase 0 deliverables checklist

- [x] `discovery.md` (this file)
- [x] `fixtures/anthropic-sse-stream-1-simple.txt`
- [x] `fixtures/anthropic-sse-stream-2-multi-turn.txt`
- [x] `fixtures/anthropic-sse-stream-3-cancel.txt`
- [x] `fixtures/anthropic-sse-stream-4-tool-use.txt`
- [x] CLA + maintainer signal — confirmed by owner (CLA signed, maintainers contacted, no specific placement guidance)
- [x] Static perf threshold table — see `design.md` (T3 in execution plan)
- [ ] Owner sign-off on revised design (`design.md` v0.3)
- [ ] Tool-use spike — **superseded** by Discovery-B finding that existing `serve.py` already implements tool routing; we mirror the pattern in Phase 2 commit 1 instead of running a separate spike.

*End of discovery.md.*
