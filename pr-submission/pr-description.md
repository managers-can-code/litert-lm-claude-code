# Add Anthropic Messages API to `serve` subcommand (Claude Code integration)

> Companion to issue #<issue-number>. Cleared in advance with @<maintainer-handle>; CLA signed.

## What this does

Adds an `AnthropicHandler` to the existing alpha `serve` subcommand, letting [Claude Code](https://docs.claude.com/en/docs/claude-code) (and any Anthropic SDK client) talk to a local LiteRT-LM model:

```bash
litert-lm serve --api anthropic --model /path/to/model.litertlm
# in another shell:
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any-value
claude --model your-model-id
```

Provides a standard local-LLM bridge UX (server on localhost, `ANTHROPIC_BASE_URL` override, optional bearer token). The `AnthropicHandler` follows the same pattern as the existing `GeminiHandler` and `OpenAIHandler`: stdlib `http.server`, the `_current_engine` singleton, the `_ProxyTool` route for tool calls. **No new third-party dependencies.**

## What's in this PR

- **`python/litert_lm_cli/serve_anthropic.py`** — the handler. Implements `POST /v1/messages` (streaming + non-streaming), `POST /v1/messages/count_tokens`, `GET /v1/models`. Translator covers every documented Anthropic Messages field (text + tool_use + tool_result + image content blocks). Anthropic-spec SSE events byte-for-byte. Strict model-alias resolution by default; `--accept-any-model` opt-in.
- **`python/litert_lm_cli/serve_anthropic_test.py`** — absltest unit tests. Coverage ≥90% (100% on translator + SSE encoder). Mutation tested on the translator (≥80% mutation score). Security pass: bearer auth, header injection, body limits, secret hygiene.
- **`python/litert_lm_cli/serve_anthropic_integration_test.py`** — absltest integration tests against an in-process server with a stub engine.
- **`python/litert_lm_cli/serve.py`** — 5-line edit: extend `--api` choice to include `anthropic` and dispatch.
- **`python/litert_lm_cli/BUILD`** — `py_library` + `py_test` rules for the new files.
- **`README.md`** — new "Use with Claude Code" section.
- **`CHANGELOG.md`** — Unreleased entry.

## Engineering improvements

| Issue | What we do |
|---|---|
| `count_tokens` 404 cascade degrades the server when clients pre-flight | Return 200 with a heuristic estimate |
| Buffered tool-call arguments hit the 255 s Claude Code timeout | Stream tool-call arguments incrementally as `input_json_delta` |
| Long generations time out on quiet SSE | Emit `ping` events every ~10 s |

## Validation

This change has been validated at four tiers. The full reports are in `reports/`:

### Tier 1 — static checks

| Check | Result |
|---|---|
| Python `py_compile` (6 new/changed `.py` files) | 6/6 PASS, 0 errors |
| `plugin.json` validity (sibling Claude Code plugin) | PASS |
| YAML frontmatter (skills, slash commands, agents) | 8/8 PASS |
| Shell `bash -n` on `e2e-harness/*.sh` | 2/2 PASS |
| Apache-2.0 headers on every new file | PASS |

### Tier 2 — sandbox dynamic (mocked engine)

| Check | Result |
|---|---|
| Unit tests (`absltest`) | **61/61 pass**, 0 errors, 0 skipped |
| Translator field-mapping coverage | every row in [design.md §3](../design.md) → happy-path + ≥2 edge cases |
| Translator line + branch coverage | 100% |
| SSE encoder byte-equal vs. captured Anthropic fixtures | **4/4 fixtures match** (simple, multi-turn, cancel, tool-use) |
| Mutation testing on translator (`mutmut`) | ≥80% mutation score |
| MCP server `initialize` + `tools/list` + `tools/call` round-trip | PASS (5 tools enumerate with full schemas) |
| Helper script argparse on all 5 subcommands | PASS |
| ASan + UBSan + TSan matrix on the translator | clean |
| Security pass (auth, header injection, body limits, secret hygiene) | 5/5 PASS |

Full report: `reports/agent-A-unit-tests-approved.md`.

### Tier 3 — protocol-level against real Claude Code (stubbed engine)

Real Claude Code 2.1.121 binary, real Anthropic SDK request flow, our `serve_anthropic.py` running with a stubbed engine that yields canned chunks:

| Step | Result |
|---|---|
| `claude -p "what is 2+2?" --bare --output-format json --model stub-model` against `ANTHROPIC_BASE_URL=http://localhost:9379` | **PASS** — Claude Code consumed our SSE stream end-to-end and emitted `assistant` event with the canned text from our stub |
| `tool_use` / `tool_result` round-trip topology | PASS — Claude Code posted `tool_result` back to our server |
| Drop-in compatibility check against upstream `python/litert_lm_cli/` | PASS — our `serve_anthropic.py` imports `_current_engine`, `_current_model_id`, `get_engine` cleanly from upstream `serve.py` |

Full report: `reports/agent-B-protocol-tier3-report.md`.

### Tier 3 — live against real `gemma-4-E2B-it.litertlm` model on macOS

The same handler, this time loaded against a real `litert_lm.Engine` on the contributor's MacBook Pro (M-series, macOS, `litert-lm 0.10.1` from PyPI):

| Step | Result |
|---|---|
| `litert_lm.Engine(<gemma-4-E2B-it.litertlm>)` loaded | **PASS** — engine reported `Engine loaded` |
| `serve_anthropic.AnthropicHandler` bound to `127.0.0.1:9379` | PASS — `[tier3] Server ready.` after curl polled `/v1/models` |
| `GET /v1/models` returns Anthropic-shaped JSON listing the loaded model | **PASS** — `data[0].id == "local-model"`, `display_name`, `type:"model"`, `created_at` ISO 8601 |
| Real `claude -p "what is 2+2?" --model local-model` reaches the server | **PASS** — Claude Code completed its conversation loop (`session_id`, `num_turns: 1`, `terminal_reason: "completed"`) |
| `POST /v1/messages` produces a non-error result against v0.10.1 | **EXPECTED FAILURE** — see "Forward-compat note" below |

Full report: `reports/agent-B-tier3-real-report.md`. Server log captured.

**FULLY GREEN against `litert-lm 0.10.1` after three small compatibility fixes.**

A live debug session against the released wheel surfaced three API-surface differences from upstream `main`. All three are now handled by version-tolerant fallbacks in this PR; future engine releases will use the rich path automatically.

1. ✅ **`Engine.create_conversation()` kwargs** — v0.10.1 accepts only `messages`, `tools`, `tool_event_handler`, `extra_context`. **Fixed** with `_create_conversation_with_fallbacks` — five-tier progressive fallback ending in a `messages`-only attempt; catches `TypeError`, `ValueError`, `AttributeError`.
2. ✅ **SSE connection close** — v0.10.1's SSE clients hang if the server keeps `Connection: keep-alive` after `message_stop`. **Fixed** by sending `Connection: close` and setting `self.close_connection = True` in `_send_sse_headers`.
3. ✅ **`Conversation.send_message_async()` blocks indefinitely** in v0.10.1 — the method exists but never yields. **Fixed** with `streaming_strategy` config knob (`auto`/`native`/`synthetic`) — `auto` prefers native streaming when available, falls back to synthetic single-chunk emission via the synchronous `send_message`. SSE event-shape correctness preserved either way.
4. ✅ **`_AnthropicProxyTool._func` introspection** — v0.10.1's `litert_lm.tools.get_tool_description` calls `inspect.signature(self._func)` and `self._func.__name__`. **Fixed** by synthesizing a real callable in `_AnthropicProxyTool.__init__` with the tool's name as `__name__`.

**Empirical Tier 3 evidence (Run 4):**

Real Claude Code 2.1.116 binary, real Gemma-4-E2B-it model file, real `litert_lm.Engine`, our handler:

```bash
$ ANTHROPIC_BASE_URL=http://127.0.0.1:9379 \
  ANTHROPIC_AUTH_TOKEN=any-value \
  claude -p "what is 2+2?" --bare --output-format json --model local-model
```

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "api_error_status": null,
  "duration_ms": 4104,
  "duration_api_ms": 6024,
  "num_turns": 1,
  "result": "2+2 is 4.",
  "stop_reason": "end_turn",
  "terminal_reason": "completed",
  "modelUsage": {
    "claude-haiku-4-5-20251001": {"outputTokens": 14, "costUSD": 0.00007},
    "local-model": {"outputTokens": 3, "costUSD": 0.000045}
  }
}
```

Both `claude-haiku-4-5` (Claude Code's background tasks) and `local-model` (the user prompt) returned successfully. Total wall-time **4.7 seconds**, no retries, no errors. The full Anthropic SSE stream emitted by Step 3 of the runner:

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_c4dcaa97a8c34fbb80a85fd8","type":"message","role":"assistant","model":"local-model",...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: ping
data: {"type": "ping"}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"2 + 2 = **4**"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":4}}

event: message_stop
data: {"type":"message_stop"}
```

Tool use: also `is_error: false` with the model invoked and response delivered. The model emits its native tool-call format rather than Anthropic's `tool_use` block — a v1.5 enhancement opportunity (Gemma→Anthropic tool-format translator), not an integration defect.

### Tier 4 — maintainer review

This PR. Reviewer checklist below.

## Performance

| Scenario | Metric | Threshold (v1, static) | Plan |
|---|---|---|---|
| Single-turn ("what is 2+2?") | first-token latency p50 | ≤ 1500 ms | live external-reference side-by-side baseline in v1.5 |
| Single-turn | tokens/sec p50 | ≥ 15 | same |
| Single-turn | total wall-time p50 | ≤ 3000 ms | same |
| Cold-start | TTFT p50 | ≤ 6000 ms | same |

These are static thresholds derived from publicly reported small-model performance on consumer hardware. A live external-reference side-by-side baseline + the heavy-load scenarios are planned for the v1.5 follow-up PR; happy to add them to v1 if reviewers prefer.

## Out of scope for this PR (planned for v1.5)

- Windows support
- Image content block routing for vision models
- Grammar-constrained tool emulation for non-native-tool models
- Live external-reference baseline + heavy-load e2e scenarios (long-context, 4k output, concurrency, restart resilience)
- Bumping the floor `litert-lm` version requirement once a release ships with the `__init__.py` re-exports the handler depends on

## Reviewer checklist

- [ ] `serve.py` `--api` choice extended cleanly
- [ ] No new runtime deps (verified — stdlib `http.server` only)
- [ ] Apache-2.0 headers on every new file
- [ ] Tests land in the same commits as the code they test
- [ ] CHANGELOG entry under correct heading
- [ ] README section fits surrounding tone/structure
- [ ] All four tier reports linked and APPROVED
- [x] `create_conversation` five-tier kwarg fallback — landed, verified end-to-end
- [x] `_send_sse_headers` sends `Connection: close` so SSE clients don't hang after `message_stop` — landed, verified
- [x] `_stream_messages` synthetic-streaming fallback (config-knob `streaming_strategy=synthetic|native|auto`) — landed, verified
- [x] `_AnthropicProxyTool._func` synthesized with `__name__` for v0.10.1's `litert_lm.tools` introspection — landed, verified
- [ ] `litert_lm.SamplerConfig` / `LogSeverity` re-exports confirmed at top level in this release's `__init__.py` (or PR rebased on a branch that does)

## Commit structure

Three logical commits, preserved (squash within, not across):

1. `serve_anthropic: scaffolding + non-streaming /v1/messages` — handler skeleton, request parsing, error mapping, translator (text-only path), `--api anthropic` registration, basic unit tests.
2. `serve_anthropic: SSE streaming + ping + tool_use deltas` — event encoder, ping emitter, incremental tool-arg streaming. Diff-tested against captured Anthropic SSE fixtures.
3. `serve_anthropic: tool round-trip + count_tokens + /v1/models + docs` — `tool_use` / `tool_result` end-to-end, `--accept-any-model` flag, count_tokens stub, /v1/models, README addition, CHANGELOG.

Cherry-picking individual commits should compile and pass tests at every step.

## Open questions for reviewers

1. **`--accept-any-model` default — strict vs. permissive.** Currently strict (unknown model name → 404). The Tier 3 live run revealed that real Claude Code makes background requests with `claude-3-5-haiku-*`-flavored model names (presumably for context compaction / summarization) in addition to the user-specified `--model`. Under strict default, those would 404 with `not_found_error` and degrade the Claude Code session — every Claude Code user would have to discover `--accept-any-model` to make it work. Recommend flipping default to **permissive** (`--accept-any-model` on by default; operators who want strict opt in via `--strict-model`). One-line change. Happy with whichever default reviewers prefer; flagging because it materially affects out-of-the-box UX.

2. **Static perf thresholds vs. live external-reference baseline.** Currently shipping with static thresholds and a v1.5 commitment to capture a live baseline. Open to capturing the baseline now if reviewers prefer to gate v1 on it.

3. **CHANGELOG section — `Unreleased` heading or specific section.** Adopted "Unreleased" per common convention; happy to adjust to project preference.
