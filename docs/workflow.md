# LiteRT-LM × Claude Code Integration — Proposed Workflow (v0.2)

**Goal:** Land a production-ready PR in `google-ai-edge/LiteRT-LM` that lets users point Claude Code at a local LiteRT-LM server, providing a standard local-LLM bridge UX (server on localhost, `ANTHROPIC_BASE_URL` override, optional bearer token).

**Owner:** ram (tenheadedram@gmail.com)
**Drafted:** 2026-04-30 (v0.1)
**Revised:** 2026-04-30 (v0.2 — workflow-review pass applied)
**Status:** DRAFT v0.2 — review-agent edits applied; awaiting owner sign-off on Phase −1 intake

---

## Changelog from v0.1

- **Added Phase −1 (Intake & Pre-flight).** v0.1's "Open questions blocking workflow lock-in" are blockers, not postscripts; promoted into a sign-off gate that runs before any verification work.
- **Phase 0 expanded.** Added: CLA / `CONTRIBUTING.md` adherence check, external-reference baseline capture on target hardware, definitive verification of Claude Code's automation surface (with a fallback HTTP-driver design if no headless mode), golden SSE fixture capture from the real Anthropic API, and a working tool-use spike.
- **Phase 1 sharpened.** Translator field mapping is now an explicit table (the test oracle for Agent A), not prose. Added request-size limits, timeout policy, observability minimum, image-content-block behavior, and a strict model-alias resolution rule that resolves the v0.1 contradiction.
- **Phase 2 hardened.** Added a rollback-verification step (build with the flag off, diff against `main`, assert zero delta). Reordered commits so observability primitives land in commit 1.
- **Phase 3 Agent A expanded.** Added mutation testing on the translator, request-size / timeout negative tests, and a focused security review (auth, header injection, body limits, secret-handling).
- **Phase 3 Agent B sharpened.** Flake budget tiered (50 / 20 / 5 across smoke / mid / heavy scenarios). Added scenario 13 (cold-start TTFT), scenario 14 (image content block), scenario 15 (alias-passthrough flag). Cross-platform target made explicit. Fixed the v0.1 contradiction between alias default and scenario 12.
- **Phase 4 clarified.** "Squash to 4 commits" reworded to remove ambiguity; rollback gate added to release checklist.
- **Risk register expanded.** Added headless-mode unavailability, CLA non-compliance, image-handling regressions.

---

## Guiding principles

1. **Verify before designing.** A wrong assumption about Claude Code's wire protocol or LiteRT-LM's existing serve story sinks the whole plan. Phase 0 exists to kill those.
2. **In-tree, opt-in.** The integration must build behind a flag so upstream maintainers can merge it without inheriting a hard dependency.
3. **Mirror, don't fork.** Match LiteRT-LM's existing CLI ergonomics, code style, and test framework. The PR should look like it was always meant to be there.
4. **Three independent gates.** Unit tests, integration/e2e, and docs each have their own agent with a sign-off artifact. No agent's pass is conditional on another's.
5. **Reasonable user-perceived latency.** Steady-state token throughput should be in line with similar local-LLM bridge implementations. Otherwise it's not "production-ready."
6. **Evidence over assumption.** Every "production-ready" claim has a measurable check tied to a captured artifact (baseline file, golden fixture, signed report).
7. **Block early.** Decisions and external dependencies that can derail the plan are surfaced as gates *before* code is written, not as post-hoc questions.

---

## Phase −1 — Intake & Pre-flight (NEW — blocks Phase 0)

These are facts and decisions the owner must capture in writing before Phase 0 starts. Several of them change the entire structure of Phase 2; resolving them after the fact causes rework.

| # | Item | Why it gates everything |
|---|---|---|
| I1 | **Timeline target.** Full ~7-day plan vs. 2-day text-only v1 with tool use as a follow-up PR? | Determines whether D3 (tool-use scope) is decided up-front or punted. |
| I2 | **D1 preference.** C++ in-tree vs. Python sidecar — strong owner preference, or trust Phase 0 evidence? | Determines whether the C++ Phase 0 deep-dive is exhaustive (decision-driving) or confirmatory. |
| I3 | **Maintainer signal + CLA status.** Has the owner contacted LiteRT-LM maintainers? Is the Google CLA already signed? Are there preferred commit/PR conventions? | A cold PR vs. a pre-discussed PR changes how defensively we structure the build flag and dependency story. CLA non-compliance blocks PR merge regardless of code quality. |
| I4 | **Benchmark hardware.** Exact CPU/RAM/GPU spec where Agent B's external-reference baseline is captured. | Without a fixed hardware target, the perf gates are meaningless and the PR's benchmark table is non-reproducible. |
| I5 | **PR scope guard for missing HTTP primitive.** If Phase 0 reveals LiteRT-LM has no HTTP server primitive at all, is the owner OK with this PR including the whole HTTP layer, or should that be a preceding PR? | A 2× scope swing depending on the answer. |

**Deliverable:** `intake.md` — answers signed off by the owner. No discovery work begins until this lands.

---

## Phase 0 — Discovery (≈1 day, blocks Phase 1)

Phase 0 has been expanded from v0.1's three questions to seven verification tasks. Several of them produce artifacts that downstream phases depend on — they cannot be done lazily.

### 0.1 — Claude Code wire protocol

Verify the current behavior of `ANTHROPIC_BASE_URL` (and any sibling vars: `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_DEFAULT_*`). Read Claude Code's public docs, settings reference, and any provider-routing notes. Confirm whether Claude Code talks Anthropic Messages API directly to the base URL or expects a different shape. **Output:** section in `discovery.md` with exact env-var matrix, request shape, and version of the CLI tested.

### 0.2 — Claude Code automation surface (CRITICAL)

Definitively answer: does Claude Code support a non-interactive / headless / scripted mode suitable for Agent B's automation? Check for `--print`, `-p`, JSON-RPC mode, batch mode, etc.

If headless **does not exist** or is too brittle, design and document a **fallback HTTP driver**: a test harness that emits the same Anthropic Messages requests Claude Code would emit, in the same sequence, against `litert_lm serve`. The fallback is a worse-than-real-CLI test but is better than a non-deterministic `expect` script. **Output:** decision recorded in `discovery.md` with the chosen driver approach.

### 0.3 — LiteRT-LM existing surface

Inspect the C++ CLI source. Document: existing subcommands, presence/absence of an HTTP wrapper, runtime token-streaming callback API, build system (Bazel/CMake), test framework (GoogleTest/Catch2/etc.), code style (clang-format config?), and dependency policy (header-only HTTP libs only? gRPC? vendored deps?). **Output:** section in `discovery.md`.

### 0.4 — Local-LLM bridge integration contract

Identify the standard local-LLM bridge contract: which endpoint to expose, model-name remapping behavior, how to handle tool use, how to stream, what authentication (if any) is required, and what UX flows are supported (`pull` / `run` / `serve`). **We adopt that contract — we do not invent a new one.** **Output:** section in `discovery.md` with an "adopted / skipped" table.

### 0.5 — External-reference performance baseline (CRITICAL)

On the I4 benchmark hardware, set up an external-reference local-LLM bridge + Claude Code with the test model (see Phase −1 / 0.7) and record:

- p50 first-token latency (steady-state)
- p50 tokens/sec (steady-state)
- Total wall-time on scenario 1 ("what's 2+2")
- Cold-start time-to-first-token (immediately after the reference server starts)
- Long-output (4k tokens) total time
- Long-context (20k tokens in) total time

Persist as `baselines/external-reference.json` with a hardware fingerprint and CLI versions. **This is the file Agent B reads.** Without it, every perf gate in Phase 3 is unrooted.

If the reference server cannot be brought up on the test hardware, document the failure and substitute a *static* threshold table (slower, but at least deterministic). Note this in the PR.

### 0.6 — Golden Anthropic SSE fixtures (CRITICAL)

Capture real Anthropic API SSE streams against `api.anthropic.com` for: simple chat, multi-turn chat, tool-use round-trip (tool_use block out, tool_result block in), streaming-cancel mid-stream, long-output. Persist byte-exact under `fixtures/anthropic-sse/`. These are the oracle for Agent A's encoder tests *and* Agent B's diff-tests against our SSE output.

Without these, "match Anthropic's spec byte-for-byte" cannot be proved.

### 0.7 — Tool-use spike

A working spike — not desk research. Pick a small local model (Gemma 3n E2B unless I3/I4 dictate otherwise), wire a *throwaway* HTTP server that emits Anthropic-style `tool_use` content blocks and consumes `tool_result` blocks, and make Claude Code complete one tool-use turn end-to-end. Goal: prove the protocol path works *at all* before committing 3 days to Phase 2.

If the spike reveals features the local model cannot support (e.g., parallel tool calls, structured `input_schema` validation), the evidence drives D3 in Phase 1 instead of guesswork.

### 0.8 — CLA, contribution flow, code style

Read `CONTRIBUTING.md` and any DCO/CLA pages on the LiteRT-LM repo. Confirm the owner has signed Google's CLA. Identify required commit-message format, copyright headers, clang-format config, lint rules, sign-off requirements. **Output:** section in `discovery.md`. One missed convention costs a day of post-review rework.

**Phase 0 deliverables:**

- `intake.md` (already signed in Phase −1).
- `discovery.md` — answers to 0.1–0.8 + a 3-bullet architecture recommendation.
- `baselines/external-reference.json` — recorded.
- `fixtures/anthropic-sse/*.bin` — captured.
- Tool-use spike binary + a `spike-notes.md` summarising findings.
- Owner sign-off before Phase 1.

---

## Phase 1 — Architecture & Decision Doc (≈4–6 hrs)

Produce `design.md` covering everything in v0.1 *plus* the new requirements below. Drafting can begin while Phase 0 is in flight; sign-off blocks on Phase 0 evidence.

- **Wire protocol:** Anthropic Messages API surface — `/v1/messages` with the standard request/response/SSE event spec.
- **Component boundary (DECISION D1):** in-tree C++ subcommand `litert_lm serve` **vs.** Python sidecar that wraps existing bindings. Default: C++ in-tree; reverse if Phase 0 reveals no convenient HTTP story in C++.
- **Translation layer contract — explicit field-mapping table.** No prose hand-waves. The table below is the oracle for Agent A's translator tests; every row gets at least one happy-path test and one edge case.

| Anthropic field | LiteRT-LM target | Notes / edge cases |
|---|---|---|
| `model` | model-alias lookup → loaded model id | Strict by default — see below |
| `system` | prepended system prompt | Empty string allowed; multi-block system content concatenated |
| `messages[*].role` | turn role | `user` / `assistant` only; `system`-as-message rejected with 400 |
| `messages[*].content` (string) | text turn | Pass through |
| `messages[*].content[*]` type=`text` | text turn | Concatenate adjacent text blocks |
| `messages[*].content[*]` type=`image` | image input | If model can't process: 400 `invalid_request_error` with documented field |
| `messages[*].content[*]` type=`tool_use` | tool-call assistant turn | Re-emitted in conversation history |
| `messages[*].content[*]` type=`tool_result` | tool-result user turn | `is_error` honored |
| `tools[*]` | tool catalog for grammar-constrained decode | `input_schema` JSON-Schema subset documented |
| `stop_sequences` | sampling stop strings | Max length / count limits documented |
| `temperature` / `top_p` / `top_k` | sampling params | Out-of-range → 400 |
| `max_tokens` | generation cap | Required field; absence → 400 |
| `metadata.user_id` | passthrough log field | Not used for inference |
| `stream` | response mode | `true` → SSE; `false` → single JSON |

- **Streaming:** SSE event sequence — `message_start` → `content_block_start` → `content_block_delta` (text or `input_json_delta` for tool_use) → `content_block_stop` → `message_delta` → `message_stop`. Must match Anthropic's spec byte-for-byte against the Phase 0.6 golden fixtures.
- **Tool use (DECISION D3):** Anthropic `tool_use` / `tool_result` content blocks. If the local model has no native function-calling, implement a grammar-constrained JSON decode shim. Scope decided by the Phase 0.7 spike.
- **Model-name mapping (RESOLVED):** **strict by default** — unknown model name returns `not_found_error` (404). Operator opt-in `--accept-any-model` flag routes any incoming model name to the loaded model. `--model-alias claude-sonnet-4=gemma-3n-e4b` adds explicit aliases. (Resolves the v0.1 contradiction between line 46 and scenario 12.)
- **Auth:** localhost-only by default (binds `127.0.0.1`), optional `--bearer-token` flag for LAN use, no TLS in v1 (deferred — recommend a reverse proxy in docs).
- **Error mapping:** map LiteRT-LM errors → Anthropic error JSON shapes (`overloaded_error`, `invalid_request_error`, `not_found_error`, `rate_limit_error`, `api_error`). Documented table in `design.md`.
- **Request limits & timeouts (NEW):**
  - Request body max: 4 MB (configurable via `--max-request-bytes`).
  - Per-request timeout: 5 min total wall-time (configurable via `--request-timeout-secs`).
  - Concurrency cap: 4 in-flight inferences (configurable via `--max-concurrent`).
  - Oversize / timeout / over-cap → clean Anthropic error JSON.
- **Observability (NEW):**
  - Structured JSON logs to stderr (one line per event), with a per-request `request_id`.
  - Latency histogram + counter on `GET /metrics` (Prometheus text format), behind the same build flag.
  - Telemetry off by default (D4); `--enable-metrics` opt-in flag.
- **Rollback / kill switch:** building with `LITERTLM_ENABLE_CLAUDE_SERVE=OFF` produces an artifact byte-equal to `main` HEAD's build (or, where deterministic builds aren't on, semantically identical — verified by Agent A's rollback gate).
- **Cross-platform target:** Linux + macOS in v1; Windows tracked as a follow-up. README states this explicitly.

**Decisions requiring owner sign-off before Phase 2:**

| # | Decision | Default recommendation |
|---|---|---|
| D1 | C++ in-tree vs. Python sidecar | C++ in-tree (confirmed by Phase 0.3) |
| D2 | API surfaces: Anthropic-only vs. Anthropic + OpenAI-compatible | Anthropic-only v1 |
| D3 | Tool-use scope | Full parity if Phase 0.7 spike succeeded; otherwise text-only v1 with documented follow-up |
| D4 | Telemetry / metrics endpoint | Off by default, opt-in `--enable-metrics` |
| D5 | Cross-platform v1 scope | Linux + macOS; Windows follow-up |

---

## Phase 2 — Implementation (≈3 days)

Branch: `feat/claude-code-integration`. Build behind `LITERTLM_ENABLE_CLAUDE_SERVE` flag. **Four logical commits, preserved in the final PR** (squash only in-progress fixups within each, not across):

1. **`serve` scaffolding + observability primitives** — subcommand, flag parsing, health endpoint (`GET /health`), `GET /metrics` (gated by `--enable-metrics`), structured JSON logger with `request_id`, config plumbing, request-size + timeout middleware.
2. **Messages API, non-streaming** — full request/response translation per the Phase 1 field-mapping table, single-shot completion path. Translator is a separately-testable unit.
3. **Streaming (SSE)** — event encoder, backpressure handling, client-disconnect cancellation propagating to the inference loop. Diff-tested against `fixtures/anthropic-sse/*.bin`.
4. **Tool use + image handling** — `tool_use` blocks out, `tool_result` blocks in, grammar-constrained JSON generation for models without native FC, image-block handling per D3 / image-mapping rule.

Each commit ships with the unit tests for that module so CI is always green per-commit (helps the upstream review).

**Rollback verification (added to commit 1's CI):** every CI run also builds with `LITERTLM_ENABLE_CLAUDE_SERVE=OFF` and asserts a clean diff against `main` HEAD's flag-off build. If anything in the new code path leaks into the off-build, CI fails.

---

## Phase 3 — Three validation agents (run in parallel, gated on Phase 2)

This is the spine of "production-ready." Each agent has a tight contract and produces a signed report artifact. **No PR opens until all three pass.**

Agents A and C may begin partial work as soon as their inputs land in Phase 2 (Agent A as soon as commit 1 lands; Agent C as soon as the README draft lands). Agent B cannot start until commit 4 lands but its harness/Docker setup may be built earlier.

### Agent A — `unit-test-validator`

**Mission:** prove the new code is correct in isolation, including security-sensitive paths.

- **Scope:** translator (Anthropic↔LiteRT-LM, every row of the Phase 1 field-mapping table), SSE event encoder/decoder, model-name mapper (strict + alias + passthrough), grammar-constrained tool-call decoder, error mapper, request-size + timeout + concurrency middleware, auth (`--bearer-token`), structured logger (no secret leakage).
- **Framework:** GoogleTest (confirmed in Phase 0.3).
- **Coverage gates:** ≥90% line coverage on new code; **100% line + branch** on the translator (pure logic, no excuse).
- **Mutation testing (NEW):** translator module run under `mull` or equivalent; ≥80% mutation score required. Catches logic gaps that line coverage misses.
- **Test design rules:** every public function gets happy-path + 2 edge cases (empty input, malformed/oversized input). Property tests on the translator using fuzz-generated valid Anthropic requests. Negative-path tests for every Anthropic error shape.
- **Security pass (NEW, folded into Agent A scope):**
  - Auth: bearer-token rejection on missing/wrong token.
  - Header injection: CRLF in headers rejected.
  - Body limits: oversize body returns 413, no OOM.
  - Secret hygiene: confirm `Authorization` header value never appears in logs or error responses.
  - Prompt-injection surface noted but not a unit-test concern (model-side).
- **Hardening:** runs under ASan + UBSan + TSan in CI matrix.
- **Rollback gate:** Agent A signs off only if the flag-off build is clean per Phase 2's verification.
- **Output artifact:** `reports/unit-tests-approved.md` — coverage table, mutation table, security-check table, failed-cases section (must be empty), and an explicit "APPROVED" or "REJECTED" line.
- **Stop conditions:** any failure, any coverage gate miss, any sanitizer hit, any mutation-score miss, any security check failure, any rollback delta → REJECTED.

### Agent B — `e2e-integration-runner`

**Mission:** prove a real Claude Code CLI talking to a real `litert_lm serve` works smoothly end-to-end, and is fast enough.

- **Setup:** ephemeral Docker container with (a) a built `litert_lm serve` binary, (b) a small real model (selected in Phase −1 / Phase 0), (c) a real Claude Code CLI. Same container also runs on macOS host for cross-platform sample.
- **Driver:** scripted Claude Code sessions via the headless mode chosen in Phase 0.2 (or the documented fallback HTTP driver if no headless mode exists).
- **Scenarios (15 total — v0.1 had 12):**
  1. Single-turn chat ("what's 2+2") — *smoke*
  2. Multi-turn with context (3 follow-ups) — *smoke*
  3. Streaming cancellation mid-response — *smoke*
  4. Tool use: `Read` a file — *mid*
  5. Tool use: `Bash` command — *mid*
  6. Tool use: `Edit` a file (round-trip tool_result) — *mid*
  7. Long context (20k tokens in) — *heavy*
  8. Long output (4k tokens out) — *heavy*
  9. Concurrent: 3 sessions simultaneously — *heavy*
  10. Restart resilience: kill server mid-stream, client gets clean error — *heavy*
  11. Bad request: malformed Messages payload returns proper Anthropic error JSON — *smoke*
  12. Strict alias miss: unknown model name returns `not_found_error` (404) — *mid*
  13. **(NEW)** Cold-start TTFT: first request after `serve` startup — *mid*
  14. **(NEW)** Image content block to a model that can't process: clean `invalid_request_error` — *mid*
  15. **(NEW)** Alias passthrough: `--accept-any-model` set, unknown model resolves to loaded model — *smoke*
- **Performance gates (vs. `baselines/external-reference.json` from Phase 0.5):**
  - Steady-state p50 first-token latency (scenario 1): ≤ baseline × 1.25
  - Steady-state p50 tokens/sec (scenario 1): ≥ baseline × 0.80
  - Total session wall-time on scenario 1: ≤ baseline × 1.25
  - **(NEW)** Cold-start TTFT (scenario 13): ≤ baseline × 1.25
  - Long-output total time (scenario 8): ≤ baseline × 1.25
  - Long-context total time (scenario 7): ≤ baseline × 1.25
- **Reliability gate — tiered (REVISED):**
  - Smoke scenarios (1, 2, 3, 11, 15): **50** consecutive runs, zero flakes.
  - Mid scenarios (4, 5, 6, 12, 13, 14): **20** consecutive runs, zero flakes.
  - Heavy scenarios (7, 8, 9, 10): **5** consecutive runs, zero flakes.
  - Total runs: 250 + 120 + 20 = 390 (down from v0.1's 600), preserving rigor on the high-frequency paths.
- **Cross-platform sample:** at least scenarios 1, 3, 4, 13 run on macOS host as well as Linux Docker.
- **Output artifact:** `reports/e2e-report.md` — per-scenario pass/fail, latency tables (vs. baseline), regressions, cross-platform notes, and APPROVED/REJECTED.

### Agent C — `docs-reviewer`

**Mission:** prove the documentation actually works and fits LiteRT-LM's house style.

- **Scope:** the new README section, any new `docs/` pages, CLI `--help` output, the CHANGELOG entry.
- **Validation steps:**
  1. Read the existing README in full; check the new section's tone, ordering, header level, and link style match.
  2. **Clean-machine smoke test (NEW):** spin up a container with nothing installed, follow only the README's documented steps, verify the documented "first-success" output appears within the documented time. This is what "production-ready local-LLM bridge" actually means in practice.
  3. Execute every other shell snippet in the new docs in a clean container and verify expected output appears.
  4. Confirm every CLI flag mentioned in prose appears in `--help` and vice versa.
  5. Lint links (no 404s).
  6. Spell-check + technical-accuracy pass (terminology consistent with Anthropic's docs and LiteRT-LM's docs).
  7. Confirm CHANGELOG entry follows project conventions (per Phase 0.8).
- **Output artifact:** `reports/docs-approved.md` — list of executed snippets with their outputs, clean-machine smoke result, style-match notes, and APPROVED/REJECTED.

**Parallelism:** A and C start during Phase 2; B starts after Phase 2 completes. Total wall-time ≈ duration of B (the slowest).

---

## Phase 4 — PR packaging (≈4 hrs)

- **Commit structure:** preserve the 4 logical commits from Phase 2 in the final PR. Squash any in-progress fixups *within* each commit but never *across* them. Confirm the structure matches the conventions captured in Phase 0.8.
- **README addition:** a self-contained "Use with Claude Code" section, ~40 lines, structured as Install → Configure → Run → Troubleshoot. Drafted to drop in cleanly without disturbing surrounding sections (verified by Agent C). Includes the v1 cross-platform statement (Linux + macOS supported; Windows follow-up).
- **`CHANGELOG.md` entry under "Unreleased."**
- **PR description template:**
  - One-paragraph motivation describing the standard local-LLM bridge UX this implementation provides.
  - "What's in this PR" bullet list.
  - Benchmark table from Agent B (with hardware fingerprint from I4).
  - Links to the three signed agent reports.
  - "Build flag default" note (off by default, opt-in) + rollback-verification note.
  - Cross-platform support statement.
  - CLA confirmation.
  - Reviewer checklist.
- **Release-readiness checklist (gate before flipping draft → ready):**
  - All three agent reports APPROVED.
  - Rollback build clean.
  - CLA signed (per I3).
  - CHANGELOG entry present.
  - All Phase 0 fixtures + `baselines/external-reference.json` checked into the test directory.
- Open as **draft** PR first; self-review pass; flip to ready.

---

## Phase 5 — Workflow review loop (this document)

This file (`workflow.md`) is the artifact. The owner can:

1. Edit it directly and tell the executing agent what changed.
2. Hand it to a workflow-review subagent. Suggested reviewer prompt:

   > You are reviewing a software-delivery workflow for landing a Claude Code integration in LiteRT-LM. Challenge every assumption. Identify: (a) phases that are wrongly ordered or could parallelize, (b) gates that are too lax or too strict, (c) missing risks (security, licensing, upstream-maintainer politics, model-availability, cross-platform), (d) any place "production-ready" is asserted without a measurable check, (e) any decision/dependency that would cause rework if surfaced late. Output a numbered list of concrete edit suggestions, ranked by impact.

Workflow-review pass v0.2 has been incorporated. Further review passes welcome.

---

## Risk register (expanded)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Claude Code's provider-routing surface changes during dev | Low | High | Pin to current public docs; integration test agent re-runs against latest CLI weekly |
| Local model has no native tool calling | High | Med | Grammar-constrained JSON shim; documented as best-effort; spike validates in Phase 0.7 |
| Upstream maintainers reject the new HTTP dep | Med | High | Build flag off-by-default; choose header-only HTTP lib (e.g., cpp-httplib, MIT); rollback-verified |
| SSE byte-format mismatch with Claude Code's strict parser | Med | High | Phase 0.6 captures golden fixtures; Agent A diff-tests SSE bytes against them |
| Performance regression vs. external-reference baseline | Med | Med | Phase 0.5 captures baseline; Agent B has explicit perf gates; profiling surfaced in PR if missed |
| Licensing — adding a new HTTP/JSON lib | Low | Med | Verify Apache-2.0 / MIT compatible (cpp-httplib MIT, nlohmann/json MIT); document in PR |
| **(NEW)** Headless Claude Code automation surface unavailable | Med | High | Phase 0.2 verifies; fallback HTTP driver designed; if neither is viable, escalate to owner before Phase 2 |
| **(NEW)** CLA non-compliance / commit-format mismatch | Med | High | Phase 0.8 confirms upfront; PR template includes CLA sign-off |
| **(NEW)** Image content blocks crash on non-vision model | Med | Med | Translator returns clean `invalid_request_error`; Agent B scenario 14 verifies |
| **(NEW)** Cold-start TTFT regression from model load | Med | Med | Scenario 13 explicitly gates; document `serve` warm-up recommendation in README |
| **(NEW)** Concurrency-cap exceeded under load | Low | Med | `--max-concurrent` defaults to 4; over-cap returns `overloaded_error`; tested in scenario 9 |
| **(NEW)** Cross-platform divergence (macOS vs Linux) | Med | Med | Agent B runs subset on macOS; v1 scope is Linux + macOS only |

---

## Summary of structural changes from v0.1 → v0.2

- v0.1 had 6 phases (0–5) and 6 "open questions" treated as postscripts. v0.2 has 7 phases (−1 through 5); the open questions are now Phase −1 sign-off items.
- Phase 0 grew from 3 verification questions to 8 verification tasks, three of which produce concrete artifacts (`baselines/external-reference.json`, `fixtures/anthropic-sse/*`, the tool-use spike).
- Phase 1 added a translator field-mapping table that *is* the test oracle, plus explicit limits/timeouts/observability/rollback specs.
- Phase 2 added a rollback-verification CI step.
- Phase 3 Agent A added mutation testing + a security pass. Agent B grew from 12 to 15 scenarios (cold-start, image, alias-passthrough), tiered the flake budget, and added cross-platform. Agent C added a clean-machine smoke test.
- Phase 4 clarified commit preservation and added a release-readiness checklist.
- Risk register grew from 6 entries to 12.

*End of workflow draft v0.2. Ready for owner sign-off on Phase −1 intake or another review-agent pass.*
