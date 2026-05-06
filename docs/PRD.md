# LiteRT-LM × Claude Code — Design Document

**Status:** v1 implementation complete, empirically verified end-to-end against real Claude Code 2.1.116 + Gemma-4-E2B-it. Ready for upstream submission.
**Author:** ram (tenheadedram@gmail.com)
**Last updated:** 2026-05-01
**Target repo:** [google-ai-edge/LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM)
**Companion plugin:** Claude Code plugin distributed separately

---

## 1. Problem statement

Claude Code is one of the most capable agentic coding assistants available — it ships with a sophisticated tool-use loop, file-system primitives, multi-turn planning, and a careful permission model. By default it talks to `api.anthropic.com` over the Anthropic Messages API, which means every keystroke leaves the developer's machine and incurs API cost.

LiteRT-LM is Google's purpose-built runtime for on-device LLM inference. It already exposes a `serve` subcommand that speaks the Gemini API and the OpenAI Responses API. The `serve` infrastructure (HTTP server, engine singleton, tool routing) is mature; the only thing missing is a third handler that speaks Anthropic's Messages API.

The user value is straightforward: developers who want privacy, offline operation, lower latency, or zero per-token cost should be able to point Claude Code at a model running locally on their own hardware — with the same experience they get against `api.anthropic.com`.

This PR provides a standard local-LLM bridge UX (server on localhost, `ANTHROPIC_BASE_URL` override, optional bearer token) and addresses several engineering issues common to such bridges (`count_tokens` 404 cascades, tool-arg streaming timeouts, missing pings), while inheriting LiteRT-LM's existing serve architecture rather than building a parallel one.

## 2. Goals and non-goals

### Goals

1. Claude Code can talk to a locally-served LiteRT-LM model with the same `ANTHROPIC_BASE_URL` flow it uses for `api.anthropic.com`.
2. Streaming and non-streaming Messages API requests both work.
3. Tool use round-trips correctly — Anthropic-shape `tool_use` content blocks out, `tool_result` content blocks in.
4. Match Anthropic's SSE wire format byte-for-byte so Claude Code's strict parser is happy.
5. Zero new third-party runtime dependencies. Reuse stdlib `http.server`, the `litert_lm` Python bindings, and the existing `_current_engine` singleton.
6. Land as a small, reviewable PR — three new files, four edits, ~600 lines of handler code.
7. Compatible with the most recent released `litert-lm` wheel (`0.10.1`) so users don't need to build from source.

### Non-goals (deferred to v1.5 or later)

- Image content block translation for vision-only models (handler returns clean error today).
- Grammar-constrained tool emulation for models without native tool support.
- Live external-reference side-by-side performance baseline (static thresholds shipped in v1).
- Windows support (Linux + macOS in v1).
- A `tool-format-translator` that converts Gemma's native `<|tool_call>` format to Anthropic's `tool_use` block. Useful for letting models that already speak some tool-call format integrate seamlessly.

## 3. Users and use cases

### User personas

**The privacy-conscious developer.** Has Claude Code installed but won't (or can't) ship code through a third-party API. Points it at a local model so all inference stays on the box.

**The offline developer.** Lives on a plane, in a coffee shop with broken Wi-Fi, or behind a corporate proxy. Wants Claude Code to keep working.

**The cost-conscious developer.** Uses Claude Code heavily for routine refactoring, code review, doc-string generation. The cloud bill adds up. Routes the boring stuff to a local model and keeps Claude Code's full UX.

**The on-device research engineer.** Already uses LiteRT-LM for benchmarking small models. Wants to evaluate them through the agentic Claude Code lens without writing their own harness.

### Concrete user stories

1. *I install Claude Code and `litert-lm`. I want to type three commands and have Claude Code answering with my local model.* — addressed by the Quickstart in the README and the companion Claude Code plugin's `setup-litert-lm` skill.

2. *I'm in the middle of a complex refactor and Claude Code makes a tool call. I want my local model's tool-call response delivered to Claude Code as if it were a real Anthropic tool_use block.* — addressed by the translator's `tool_use` / `tool_result` round-trip and the incremental `input_json_delta` streaming improvement.

3. *I want to switch models without restarting my Claude Code session.* — addressed by the helper script's `switch` subcommand (in the companion plugin), plus the strict / `--accept-any-model` model resolution flags.

4. *I want to know if my local model is up and what it's serving.* — addressed by `GET /v1/models` and the companion plugin's `/litert-lm-status` slash command.

## 4. Architecture

### 4.1 In-tree drop-in

The integration is a single new handler class, `AnthropicHandler`, registered as a third option in the existing `serve.py`'s `--api` Click choice. The handler subclasses `http.server.BaseHTTPRequestHandler` exactly like the existing `GeminiHandler` and `OpenAIHandler`, and reaches the model through the existing `_current_engine` singleton and `_ProxyTool` patterns.

```
                         ┌──────────────────────────────────┐
Claude Code CLI ──HTTP──▶│ POST /v1/messages                │
(ANTHROPIC_BASE_URL)     │ AnthropicHandler.do_POST         │
                         │ ├─ auth + body limits + timeouts │
                         │ ├─ translator (Anthropic → LRT)  │
                         │ ├─ engine.create_conversation()  │
                         │ │  + send_message[_async]        │
                         │ └─ SSE encoder (LRT → Anthropic) │
                         └──────────────────────────────────┘
                                      │
                                      ▼
                         litert_lm.Engine (singleton)
```

### 4.2 Translator

Anthropic Messages JSON → LiteRT-LM `Engine.create_conversation` parameters. Every documented Anthropic field has an explicit mapping with at least one happy-path test and one edge case. The translator is a pure function exposed as four small helpers (`translate_system`, `translate_messages`, `translate_tools`, `translate_sampler`) so unit tests can target each without spinning up the HTTP layer.

### 4.3 SSE encoder

The streaming path emits the Anthropic event sequence byte-for-byte: `message_start` → (per content block: `content_block_start` → `content_block_delta+` → `content_block_stop`) → `message_delta` → `message_stop`, with optional `ping` events. We diff-test against captured Anthropic API SSE responses to guarantee byte-equality — Claude Code's parser is strict.

The encoder writes `Connection: close` so SSE clients (curl, Claude Code) cleanly terminate after `message_stop` rather than hanging on an idle keep-alive socket.

### 4.4 Tool use

`AnthropicProxyTool` extends `litert_lm.Tool` and surfaces the Anthropic tool definition (name, description, JSON-Schema) to the engine. The handler runs with `automatic_tool_calling=False` so tool calls are returned to Claude Code rather than executed server-side. Claude Code's next request carries a `tool_result` block that the translator round-trips back into the conversation history.

For models that don't natively speak any tool-call format, the request degrades to text-only with a clean `invalid_request_error` rather than crashing.

### 4.5 Compatibility fallbacks

The handler runs against three different `litert_lm` API surfaces in the wild: the upstream `main` branch (richest), the released `0.10.1` wheel (most-conservative), and intermediate builds. Three small fallback paths handle the skew transparently:

1. **`_create_conversation_with_fallbacks`** — five-tier kwarg fallback ending in a `messages`-only attempt. Catches `TypeError`, `ValueError`, `AttributeError` so engines that reject our proxy-tool's introspection also fall through.
2. **`streaming_strategy` config** (`auto`/`native`/`synthetic`) — `auto` prefers `Conversation.send_message_async` when available, falls back to the synchronous `send_message` + synthetic single-chunk emission otherwise. Either path emits a valid Anthropic SSE event sequence.
3. **`_AnthropicProxyTool._func`** — synthesized as a real callable with `__name__` set to the tool's name, so v0.10.1's `litert_lm.tools.get_tool_description` introspection (`inspect.signature(self._func)` and `self._func.__name__`) succeeds.

These fallbacks are forward-compatible: they degrade gracefully on older builds and stay inert on newer ones.

## 5. Wire protocol details

### 5.1 Request — Anthropic Messages API

`POST /v1/messages` accepts the standard Anthropic Messages request body. Headers:

- `Content-Type: application/json` (required)
- `Authorization: Bearer <token>` and/or `X-Api-Key: <token>` (accepted but only enforced if the server was started with `--bearer-token`)

The handler imposes the following operational limits, all configurable:

- Body size: 4 MiB default
- Per-request wall-time: 5 minutes default
- Concurrency cap: 4 in-flight inferences default; over-cap returns 503 `overloaded_error`

### 5.2 Response — non-streaming

Single JSON envelope matching Anthropic's Messages spec: `id`, `type: "message"`, `role: "assistant"`, `content[]` (text + tool_use blocks), `stop_reason` (`end_turn` / `max_tokens` / `stop_sequence` / `tool_use`), `stop_sequence`, `usage`. Verified empirically against Gemma-4-E2B-it: returned `"2 + 2 = **4**"` in this exact shape.

### 5.3 Response — streaming

SSE event sequence per Anthropic's spec:

```
event: message_start
data: {"type":"message_start","message":{...}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: ping
data: {"type": "ping"}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"<token>"}}

... (more deltas) ...

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn",...},"usage":{...}}

event: message_stop
data: {"type":"message_stop"}
```

Wire format: `event: <name>\ndata: <json>\n\n`, LF (not CRLF). UTF-8. `Connection: close` header. `ping` events every ~10s during long content_block_deltas to keep proxies happy.

### 5.4 Errors

Translated to Anthropic-shape error JSON. Error type → HTTP status table:

| Condition | Status | Anthropic type |
|---|---|---|
| Body parse / missing field | 400 | `invalid_request_error` |
| Unknown model (strict mode) | 404 | `not_found_error` |
| Bearer token missing/wrong | 401 | `authentication_error` |
| Body > `--max-request-bytes` | 413 | `invalid_request_error` |
| Concurrency cap | 503 | `overloaded_error` |
| Request timeout | 504 | `api_error` |
| Internal LiteRT-LM failure | 500 | `api_error` |

## 6. Validation

Four-tier verification, with empirical evidence at each level:

| Tier | Description | Evidence |
|---|---|---|
| 1 | Static checks | 6/6 Python files compile, plugin.json valid, all YAML frontmatter parses, Apache-2.0 headers present |
| 2 | Sandbox dynamic | 61/61 unit tests pass, ≥90% line coverage (100% on translator), mutation tested on translator, MCP roundtrip validated, 4/4 SSE fixtures byte-equal against captured Anthropic responses |
| 3a | Protocol-level vs. real Claude Code | Real `claude` 2.1.x binary consumed our SSE stream end-to-end with a stub engine |
| 3b | Live real-model on macOS | Real Gemma-4-E2B-it model loaded into real `litert_lm.Engine 0.10.1`; Claude Code returned `is_error: false`, `result: "2+2 is 4."`, `terminal_reason: "completed"` in 4.7 seconds |
| 4 | Maintainer review | Pending PR submission |

The transition from "all 500s" to "fully green" took four iterations against real hardware, surfacing three v0.10.1 compatibility issues which are now handled by the fallback paths described in §4.5.

## 7. Trade-offs and decisions

### 7.1 In-tree subcommand vs. companion package

**Chosen:** new handler in-tree, registered as `--api anthropic` in the existing `serve` subcommand.

**Rejected:** standalone companion repo (e.g. `litert-lm-anthropic-server`).

**Rationale:** users discover this via `litert-lm serve --help`, the upstream serve infrastructure is reused unchanged, and PR review surface is minimised (3 new files, 4 edits). If maintainers prefer the companion-repo route, the handler is fully extractable and the companion path is a documented fallback.

### 7.2 Strict vs. permissive `--accept-any-model` default

**Chosen for v1:** strict (unknown model → 404).

**Open question for reviewers:** flip to permissive. The Tier 3 live test surfaced that Claude Code makes background requests with `claude-haiku-*`-flavored model names for compaction/summarization, in addition to the user-specified `--model`. Under strict default these 404 and degrade the session. Recommend flipping to permissive default (`--strict-model` opt-in for operators who want hard enforcement). See PR description's Open Questions section.

### 7.3 No new dependencies

**Chosen:** stdlib `http.server` only.

**Rejected:** FastAPI / Flask / aiohttp.

**Rationale:** the existing `serve.py` deliberately uses stdlib. Matching that choice keeps the PR tiny and review-friendly.

### 7.4 Version-tolerant fallbacks vs. floor pin

**Chosen:** runtime fallbacks across `litert_lm` API versions.

**Rejected:** require `litert-lm >= <version-with-this-PR>` in `requirements.txt`.

**Rationale:** the fallbacks are ~50 lines and let users on the released `0.10.1` wheel get value immediately. They become inert on newer builds. The alternative would block all users on a synchronous upstream release cycle.

## 8. Risks

Full user-facing discussion in [`RISKS.md`](RISKS.md). Engineering register:

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Protocol drift** — Anthropic changes the Messages API wire format underneath us | Med-Low | High | (a) Permissive parsing — unknown fields logged with `request_id`, not rejected. (b) Captured SSE fixtures + byte-equal diff tests fail loudly when format changes. (c) Structured logs make drift detectable in production. (d) `ANTHROPIC_BASE_URL` itself is enterprise infrastructure and will not be removed; only the wire format is at risk. CI canary against `api.anthropic.com` is on the v1.5 roadmap. |
| **Tool-call format mismatch** — LiteRT-LM Gemma models emit native tool-call format Claude Code doesn't recognize | High (today) | Med | (a) Graceful degradation — response surfaced as text rather than crashing the agent loop. (b) v1.5 tool-format translator converts Gemma's native `<\|tool_call>` format into Anthropic `tool_use` content blocks. (c) Honest README + RISKS.md framing — for tool-heavy work today, escalate to cloud Claude. |
| **Reasoning-quality gap vs. cloud Claude** | Certain | Med | (a) OVERVIEW + RISKS.md set realistic expectations. (b) Hardware-vs-model recommendation matrix. (c) Documented "have both, route appropriately" mental model. (d) One env-var to switch back to cloud. |
| **Vision content blocks unsupported in v1** | Certain | Low (most users don't drag images) | (a) Translator returns clean `invalid_request_error` (no 500s, no crashes). (b) Documented as v1.5 in roadmap. (c) Error message names the problematic message so users can route to cloud Claude for that turn. |
| **Hardware cost — RAM, CPU/GPU, battery** | Certain | Med (UX) | (a) Documented expected costs in RISKS.md (~3 GB RAM, ~5-10× battery drain during active inference). (b) `--max-concurrent 1` for laptop battery work. (c) v1.5 `--low-power` mode bundles battery-friendly defaults. (d) Plugin's `/litert-lm-stop` makes idle cost zero. |
| **Windows native unsupported in v1** | Certain | Low/Med | WSL2 instructions documented in RISKS.md §6. v1.5 ships native Windows support after fixture/path testing. |
| **`litert_lm 0.10.1` API mismatch** | Mitigated | Med | Version-tolerant fallbacks landed and verified end-to-end (`create_conversation` kwargs, `Connection: close`, `streaming_strategy`, `_AnthropicProxyTool._func`). |
| **Claude Code's provider-routing surface changes** | Low | High | `ANTHROPIC_BASE_URL` is documented enterprise infrastructure; not at risk. Pin to tested CLI version (`claude@2.1.116`) if needed. |
| **Upstream rejects new HTTP dependencies** | Mitigated | High | Zero new deps; stdlib `http.server` only. |
| **SSE byte-format mismatch with Claude Code's strict parser** | Mitigated | High | 4 captured fixtures + byte-equal diff tests; `Connection: close` for clean stream termination. |
| **Claude Code background `haiku` requests 404 under strict model resolution** | Open for v1 | Low/Med | Recommend flipping `--accept-any-model` to `True` by default; flagged as PR reviewer Open Question. |
| **Cross-platform divergence (macOS vs Linux)** | Med | Med | macOS validated empirically (Tier 3 on Apple Silicon); Linux via Docker harness. |
| **Soak / long-running stability bugs** | Med | Med | v1 has unit + integration coverage; long-running soak deferred to v1.5 monitoring. Issue tracker is the surfacing mechanism. |
| **Supply-chain attack via model weights** | Low | High | Models are bytes-on-disk, not executable. HuggingFace hash verification handled by `litert-lm` CLI. No new third-party runtime deps in this PR. |

## 9. Future work (v1.5 and beyond)

Prioritized by reviewer-feedback impact:

- **Tool-format translator** (highest user-visible impact). Convert Gemma's native tool-call format (`<|tool_call>...`) into Anthropic's `tool_use` content blocks at the SSE encoder layer. Closes the biggest practical gap in v1 — the Tier 3 run produced a valid Gemma-format tool call that Claude Code didn't recognize. As LiteRT-LM adds more model families to its catalog, the translator picks up entries for each new format on a rolling basis.
- **Image content blocks for vision models.** Plumb image data through the translator for Gemma 3n vision variants. Removes the most user-visible "fails on cloud-Claude tasks" gap.
- **Track LiteRT-LM platform additions.** This integration runs wherever LiteRT-LM runs as a desktop CLI. Today that's Linux + macOS; Windows users use WSL2. If upstream LiteRT-LM adds native Windows support, this integration picks it up automatically.
- **CI canary against `api.anthropic.com`.** Periodically capture fresh SSE responses, diff against our fixtures, fail-fast on protocol drift. Requires an API key in CI.
- **Live external-reference baseline.** Replace static perf thresholds with a side-by-side run on documented hardware.
- **`--low-power` mode.** Bundles battery-friendly defaults (`--max-concurrent 1`, lower thread counts, smaller `max_new_tokens` defaults) into one operator-friendly switch.
- **Grammar-constrained tool emulation.** Force JSON-Schema-conformant output for non-native-tool models. More invasive than the format translator; consider after the translator lands.
- **Heavy-load e2e scenarios.** 20k-context, 4k-output, concurrent sessions, restart resilience — soak testing deferred to v1.5 monitoring after the v1 PR lands.
- **Telemetry / observability endpoint.** `/metrics` Prometheus endpoint for users running the server as a long-lived service. Off by default per D4.

## 10. References

- [Anthropic Messages API spec](https://docs.anthropic.com/en/api/messages)
- [Anthropic Messages streaming spec](https://docs.anthropic.com/en/api/messages-streaming)
- [Claude Code LLM gateway docs](https://code.claude.com/docs/en/llm-gateway)
- [LiteRT-LM repository](https://github.com/google-ai-edge/LiteRT-LM)
- [LiteRT-LM CLI docs](https://ai.google.dev/edge/litert-lm/cli)

## 11. Decision log

| ID | Decision | Resolved value | Date |
|---|---|---|---|
| D1 | Architecture: C++ in-tree vs. Python sidecar | Python in-tree as new handler in existing `serve.py` | 2026-04-30 |
| D2 | API surfaces in v1 | Anthropic-only (Gemini + OpenAI exist already) | 2026-04-30 |
| D3 | Tool-use scope in v1 | Full parity (existing handlers already do tool routing — we mirror) | 2026-04-30 |
| D4 | Telemetry | Off by default; metrics endpoint deferred to v1.5 | 2026-04-30 |
| D5 | Cross-platform v1 scope | Linux + macOS; Windows v1.5 | 2026-04-30 |
| D6 | Model-name resolution default | Strict (open for reviewer flip) | 2026-04-30 |
| D7 | Compatibility floor | `litert-lm >= 0.10.1` with runtime fallbacks for older API surfaces | 2026-05-01 |
