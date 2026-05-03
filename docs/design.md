# LiteRT-LM Anthropic Server — Design (v0.3)

**Status:** DRAFT — awaiting owner sign-off before Phase 2 implementation.
**Supersedes:** the C++/cpp-httplib + FastAPI/sse-starlette design implied by earlier plan revisions. Discovery-B revealed that LiteRT-LM already ships an alpha `serve` subcommand on stdlib `http.server`; the integration mirrors the existing pattern.
**Owner:** ram

---

## 1. Architecture (revised based on Phase 0)

The integration is a new `AnthropicHandler` class added to `python/litert_lm_cli/serve.py`'s existing handler set, plus its registration as a third `--api` choice.

### File-level changes

| Action | Path | Size estimate |
|---|---|---|
| **New file** | `python/litert_lm_cli/serve_anthropic.py` | ~600 lines |
| **New file** | `python/litert_lm_cli/serve_anthropic_test.py` | ~500 lines (unit tests on translator + SSE encoder + error mapper) |
| **New file** | `python/litert_lm_cli/serve_anthropic_integration_test.py` | ~400 lines (e2e against an in-process server + a real or stub `Engine`) |
| **Edit** | `python/litert_lm_cli/serve.py` | ~5 lines (extend `--api` Click choice; register `AnthropicHandler`) |
| **Edit** | `python/litert_lm_cli/BUILD` | ~30 lines (Bazel `py_library` + `py_test` rules) |
| **Edit** | `README.md` | ~50 lines (new "Use with Claude Code" section) |
| **Edit** | `CHANGELOG.md` | ~5 lines (Unreleased entry) |
| **New (optional)** | `python/litert_lm_cli/fixtures/anthropic-sse/*.txt` | 4 files, ~25 lines each |

**Total:** ~3 new files, ~3 file edits, ~7 if you count tests + fixtures + docs. PR is small and reviewable.

### Runtime architecture

```
                                      +------------------------------+
  Claude Code CLI  --> HTTP --> POST /v1/messages -->  AnthropicHandler
  (ANTHROPIC_BASE_URL)                                  (in serve_anthropic.py)
                                                              |
                                                              v
                                              [translator: Anthropic JSON -> LiteRT-LM call]
                                                              |
                                                              v
                                              litert_lm.Engine (singleton _current_engine)
                                                .create_conversation(...)
                                                .send_message_async(...)  <-- streaming
                                                              |
                                                              v
                                              [SSE encoder: chunks -> Anthropic SSE events]
                                                              |
                                                              v
                                                          HTTP response
```

No new dependencies. Reuses:
- `http.server.HTTPServer` (stdlib) — already used by existing handlers.
- `litert_lm.Engine`, `Conversation`, `Session`, `Tool` — already public, already exercised by `GeminiHandler` and `OpenAIHandler`.
- `_current_engine` singleton — pattern established in `serve.py`.

---

## 2. Endpoints

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/v1/messages` | Primary endpoint. Accepts Anthropic Messages request, returns single JSON if `stream:false`, SSE if `stream:true`. |
| `POST` | `/v1/messages/count_tokens` | Returns a coarse estimate (`{"input_tokens": <heuristic>}`). Ensures clients that pre-flight `count_tokens` don't see a 404 cascade that degrades subsequent requests. |
| `GET` | `/v1/models` | Returns the list of currently loaded model IDs in Anthropic-shaped JSON. |

CLI invocation: `litert-lm serve --api anthropic --model <path-or-id> [--port 9379] [--host localhost]`.

---

## 3. Translator field-mapping table (test oracle)

This table is the test oracle for `serve_anthropic_test.py`. Every row gets at least one happy-path test and one edge case.

| Anthropic field | LiteRT-LM target | Notes / edge cases |
|---|---|---|
| `model` | model-alias lookup → `_current_engine[model_id]` | Strict by default — unknown name → `not_found_error` (404). Operator opt-in flag `--accept-any-model` routes any name to the loaded model. |
| `system` (string) | prepended system prompt | Empty string allowed. |
| `system` (array of text blocks) | concatenated, prepended | Adjacent blocks joined by `\n`. |
| `messages[*].role` | turn role | `user` / `assistant` only; `system` as message → 400. |
| `messages[*].content` (string) | text turn | Pass through. |
| `messages[*].content[*]` `type=text` | text turn | Concatenate adjacent text blocks within a turn. |
| `messages[*].content[*]` `type=image` | image input if model supports | If model can't process: 400 `invalid_request_error` with field name. |
| `messages[*].content[*]` `type=tool_use` | assistant tool-call turn | Re-emitted in conversation history; carries `id`, `name`, `input`. |
| `messages[*].content[*]` `type=tool_result` | user tool-result turn | `is_error` honored; content can be string or block array. |
| `tools[*]` | `litert_lm.Tool` catalog | Mapped via `Tool` constructor; `input_schema` JSON-Schema subset documented. |
| `stop_sequences` | sampler stop strings | Max length (32) and count (4) limits. |
| `temperature` / `top_p` / `top_k` | `SamplerConfig` fields | Out-of-range → 400. |
| `max_tokens` | generation cap | Required field — absence → 400. |
| `metadata.user_id` | passthrough log field | Not used for inference. |
| `stream` | response mode | `true` → SSE; `false` → single JSON. |

---

## 4. SSE encoder

Diff-tested byte-for-byte against `outputs/fixtures/anthropic-sse-stream-{1,2,3,4}-*.txt`.

**Event sequence:** `message_start` → (per block: `content_block_start` → `content_block_delta+` → `content_block_stop`) → `message_delta` → `message_stop`. Optional `ping` events.

**Wire format:**
```
event: <name>\ndata: <json>\n\n
```
LF, not CRLF. UTF-8.

**Engineering improvements (per Discovery-C findings):**
- **Stream tool-call arguments incrementally** as `input_json_delta` chunks. Avoids the 255-second Claude Code timeout that buffered tool-call arguments would otherwise trigger.
- **Emit `ping` events** every ~10 s during long content_block_deltas. Mitigates client-side proxy/connection timeouts.
- **`count_tokens` returns a rough estimate** (1 token ≈ 4 chars heuristic) rather than 404'ing.

---

## 5. Tool use (in v1 — was deferred, now in scope)

The existing `GeminiHandler` and `OpenAIHandler` in `serve.py` already implement tool routing via `_ProxyTool` (returns tool calls to the client when `automatic_tool_calling=False`) and the `litert_lm.Tool` API. The `AnthropicHandler` mirrors this pattern.

**Anthropic shape:** `tool_use` content block out → `tool_result` content block in next user message. We translate the `Conversation`'s tool-call dict into `tool_use` blocks, and incoming `tool_result` blocks into LiteRT-LM's tool-response shape.

**Caveat:** native tool support depends on the underlying model. For models without it, return a clean error rather than try a grammar-constrained shim — that's a v1.5 enhancement.

---

## 6. Limits, timeouts, observability

- Request body max: 4 MB (configurable `--max-request-bytes`).
- Per-request timeout: 5 min total wall-time (configurable `--request-timeout-secs`).
- Concurrency cap: 4 in-flight inferences (configurable `--max-concurrent`). Over-cap → `overloaded_error`.
- Logs: structured JSON to stderr, one line per event, with `request_id`. Reuses any logging utility in `litert_lm_cli` (TBD in implementation, fallback: stdlib `logging` with JSON formatter).
- Metrics endpoint: deferred to v1.5 (Discovery-B did not surface a metrics convention in the existing `serve.py`; adding one would be an out-of-scope opinion).

---

## 7. Auth

- Default: bind `127.0.0.1`. No auth required, but `Authorization: Bearer <anything>` and `X-Api-Key: <anything>` accepted-but-ignored (matches the standard local-LLM bridge UX).
- Optional: `--bearer-token <token>` flag enforces strict bearer match. Missing/wrong → 401 `authentication_error`.
- No TLS in v1 — recommend reverse proxy in README.

---

## 8. Error mapping

| Condition | HTTP status | Anthropic error type |
|---|---|---|
| Body parse fail / missing required field | 400 | `invalid_request_error` |
| Unknown model (strict mode) | 404 | `not_found_error` |
| Bearer token missing/wrong (when enforced) | 401 | `authentication_error` |
| Body > `--max-request-bytes` | 413 | `invalid_request_error` |
| Concurrency cap hit | 503 | `overloaded_error` |
| Request timeout | 504 | `api_error` |
| Image content block to non-vision model | 400 | `invalid_request_error` |
| Internal LiteRT-LM failure | 500 | `api_error` |

---

## 9. Static performance threshold table (v1 substitute for live external-reference baseline)

Owner runs `baselines/external-reference.json` capture as a v1.5 follow-up. For v1, Agent B asserts these static thresholds (derived from publicly reported small-model performance on consumer hardware):

| Scenario | v1 threshold |
|---|---|
| First-token latency, scenario 1 (single-turn) | p50 ≤ 1500 ms |
| Tokens/sec, scenario 1 | p50 ≥ 15 tok/s |
| Total wall-time, scenario 1 ("4.") | p50 ≤ 3 s |
| Cold-start TTFT, scenario 13 | p50 ≤ 6 s |
| Long-output total time, scenario 8 (4k tokens out) | ≤ 90 s |

These are conservative — actual numbers on owner's hardware will be better. PR description includes a footnote committing to a live external-reference baseline in v1.5.

---

## 10. Cross-platform target

- **v1:** Linux (Docker on owner's hardware) + macOS (host run for at least scenarios 1, 3, 13).
- **v1.5:** Windows.

---

## 11. Decision log (D1–D5 resolved)

| # | Decision | Resolved value | Source |
|---|---|---|---|
| D1 | Architecture | **Python sidecar in-tree as new handler in existing `serve.py`** | Owner choice (I2) + Discovery-B finding |
| D2 | API surfaces | Anthropic-only v1 | Owner default |
| D3 | Tool-use scope | **Full parity in v1** (revised from defer-to-v1.5) | Discovery-B revealed existing tool-routing pattern |
| D4 | Telemetry | Off in v1; metrics endpoint deferred to v1.5 | Acceleration trade |
| D5 | Cross-platform | Linux + macOS in v1; Windows v1.5 | Acceleration trade |

---

## 12. PR posture

Per Discovery-B, `CONTRIBUTING.md` says contributions aren't open. Owner reported maintainers contacted + CLA signed. PR posture:

1. Open a GitHub Issue first declaring intent and linking the prior conversation.
2. Branch: `feat/serve-anthropic-api`.
3. Three commits (preserved, not squashed across):
   - **C1:** scaffolding — `AnthropicHandler` class with `/v1/messages` (non-streaming), error mapping, request limits/timeouts, integration into `serve.py` `--api` choice. Includes file headers, BUILD entries, basic unit tests.
   - **C2:** SSE streaming — event encoder, ping emitter, tool-use streaming via `input_json_delta`. Adds SSE byte-equal tests against Phase 0 fixtures.
   - **C3:** tool use — Anthropic `tool_use` / `tool_result` round-trip, `--accept-any-model` flag, `count_tokens` stub, `GET /v1/models`. Adds integration tests + README + CHANGELOG.
4. Draft PR with: motivation paragraph, "what's in this PR" list, perf-threshold footnote, three signed agent reports, prior-conversation reference. Self-review pass. Flip to ready.

---

## 13. Open issues for owner sign-off

1. **C3 commit ordering** — OK to bundle docs + count_tokens + /v1/models + tool-use into a single commit, or prefer separating tool-use into its own C3 and docs+endpoints into C4? Affects PR commit count (3 vs 4).
2. **`--accept-any-model` default** — strict (default off, unknown model 404) vs permissive (default on, route any name to loaded model). Recommend **strict** for predictability (de-facto strict in similar local-LLM bridges). Sign off or override.
3. **CHANGELOG section** — add new "Unreleased" section if not present, or append to existing. Need owner to confirm convention preferences from the maintainer conversation.
4. **GitHub Issue first** — owner OK with this protocol, or skip straight to PR?

Once these four are resolved, Phase 2 implementation begins.

*End of design.md v0.3.*
