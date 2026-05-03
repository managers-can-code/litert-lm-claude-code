# LiteRT-LM × Claude Code Integration — Accelerated Execution Plan (v1.0)

**Goal:** Ship a credible v1 PR in ~3 calendar days by cutting non-essential v1 scope into a v1.5 follow-up PR. Do not compromise the gates that survive the cut.
**Source workflow:** `litert-lm-claude-code-integration-workflow.md` (v0.2)
**Drafted:** 2026-04-30
**Owner:** ram

---

## Acceleration thesis

v0.2's rigor is correct. The way to compress wall-time without lying about "production-ready" is to **shrink v1 scope and defer the rest to v1.5** — not to dilute the gates on what ships.

Five trade-offs make the math work. Each is explicit, named, and reversible in v1.5.

| # | Trade | What it costs in v1 | What it saves |
|---|---|---|---|
| **T1** | Text-only v1; tool use → v1.5 PR | No `tool_use` / `tool_result` round-trip; no grammar-constrained decoder | Phase 2 commit 4 (~1 day); Agent A's grammar-decoder tests; Agent B scenarios 4, 5, 6, 14 (~30% of e2e runs); Phase 0.7 spike collapses to a 30-min HTTP smoke. |
| **T2** | Linux-only v1; macOS → v1.5 | No macOS host runs; no platform-divergence detection | Cross-platform setup time (~3 hrs). |
| **T3** | Static perf thresholds in v1; live external-reference baseline → v1.5 | Perf gate is "reasonable" not pegged to a specific external bridge's numbers | Phase 0.5 ~ 4 hrs of reference-server setup + recording. |
| **T4** | Smoke + mid scenarios only in v1; heavy → v1.5 monitoring | No 20k-context, 4k-output, 3-way concurrent, restart-resilience scenarios | ~30 runs × heavy-scenario time, plus harness work for those flows. |
| **T5** | Flake budget tightened to 20 / 10 (vs. 50 / 20 / 5) | Slightly weaker statistical confidence on flake-freeness | ~60% reduction in total e2e runs (390 → ~150). |

**With all five trades, v1 scope is:**
- 3 commits (scaffolding+obs, non-streaming Messages API, SSE streaming)
- 7 e2e scenarios (1, 2, 3, 11, 12, 13, 15)
- Linux only
- Text only (no tool use)
- Static perf thresholds in the PR description, with a footnote committing to a live baseline in v1.5
- Translator field-mapping table coverage on text-only rows (skip `tool_use`, `tool_result`, image rows)

**v1.5 follow-up PR (separately scoped, not blocking v1):**
- Tool use (commit 4 from the original Phase 2)
- Image content blocks
- macOS support
- Live external-reference baseline + heavy scenarios
- Mutation testing extension to tool-use decoder

This split is also a stronger upstream story — small, focused first PR is much easier to land than a 7-day monolith.

---

## Phase −1 — Intake (compressed)

Five items from v0.2's Phase −1, with default answers proposed. Owner confirms in chat in one message and we move:

| # | Item | Proposed default for accelerated track |
|---|---|---|
| I1 | Timeline | 3-day v1; v1.5 follow-up captures deferred work |
| I2 | D1 (C++ in-tree vs. Python sidecar) | C++ in-tree, cpp-httplib (header-only, MIT) + nlohmann/json (MIT). Maximises chance of upstream acceptance. |
| I3 | Maintainer signal + CLA | Owner confirms CLA status. If unsigned, sign before Phase 2 lands (it's required to merge). Cold PR assumed unless owner reports otherwise. |
| I4 | Benchmark hardware | T3 defers live baseline; in-session, I'll generate a synthetic threshold table. Owner records real numbers later. |
| I5 | Missing HTTP primitive scope | Owner OK with this PR including the HTTP layer (necessary in any case — there's no path to integration without it). |

**Single owner action to unblock execution:** confirm I1–I5 (or override) in a chat reply. No separate `intake.md` needed for the accelerated track; defaults are recorded in this file.

---

## Day-by-day execution

Wall-time is calendar time, not work-hours. Parallelism is real — multiple subagents run concurrently.

### Day 0 — Discovery + skeleton (today, ~3 hrs after intake confirmed)

**Run in parallel as separate subagents:**

- **Discovery-A:** Claude Code wire protocol — read public docs, identify exact env-var matrix (`ANTHROPIC_BASE_URL`, etc.), confirm the HTTP shape Claude Code emits. Output: section of `discovery.md`.
- **Discovery-B:** LiteRT-LM source inspection — clone the repo, document existing CLI subcommands, build system (Bazel? CMake?), test framework, code style config, dependency policy, presence/absence of HTTP wrapper. Output: section of `discovery.md`.
- **Discovery-C:** Local-LLM bridge integration patterns — research the standard contract (endpoint, auth, streaming, model naming) used by similar bridges, produce an "adopted / skipped" table. Output: section of `discovery.md`.
- **Discovery-D:** CLA + contribution conventions — read `CONTRIBUTING.md`, identify required commit format, copyright headers, lint config. Output: section of `discovery.md`.
- **Fixtures-Capture:** capture real Anthropic API SSE responses for: simple chat, multi-turn, streaming-cancel. Persist as `fixtures/anthropic-sse/*.bin`. (Skip tool-use fixture per T1.) Output: 3 binary files + manifest.

**Main thread (me) does in parallel:**
- Draft `design.md` skeleton with the v0.2 field-mapping table trimmed to text-only rows.
- Decide D1 (C++ + cpp-httplib) and D2 (Anthropic-only) given the defaults.
- Draft commit-1 skeleton structure (subcommand + flag parsing + cpp-httplib integration).

**End-of-day-0 deliverables:**
- `discovery.md` consolidated.
- `design.md` v1 (text-only scope baked in).
- `fixtures/anthropic-sse/{simple,multi-turn,cancel}.bin`.
- Commit-1 scaffolding code drafted (not yet pushed).
- Static perf threshold table drafted (T3 substitute for `baselines/external-reference.json`).

**Owner sign-off gate:** review `design.md`. Reject or approve before Day 1.

### Day 1 — Phase 2 commits 1 + 2

**Morning (4 hrs):**
- **Implementation-1:** finalize commit 1 — `serve` subcommand, `GET /health`, `GET /metrics` (gated), structured JSON logger with `request_id`, request-size + timeout middleware, cpp-httplib wired in. Push to `feat/claude-code-integration`.
- **Agent-A-Pass-1** dispatched the moment commit 1 lands: unit-tests on the middleware + logger + health/metrics endpoints. Output: partial `reports/unit-tests-approved.md`.

**Afternoon (4 hrs):**
- **Implementation-2:** commit 2 — non-streaming Messages API. Translator implementing the trimmed field-mapping table (text-only rows). Strict model-alias resolution rule. Anthropic error-shape mapping. Push.
- **Agent-A-Pass-2** dispatched the moment commit 2 lands: translator tests (every text-only row of the field-mapping table → happy + 2 edge cases), property tests via fuzzed valid Anthropic requests, mutation testing on the translator (≥80% mutation score gate), security pass (auth, header injection, body limits, secret hygiene).

**End-of-day-1 deliverables:**
- Commits 1 + 2 on branch.
- Agent A partial sign-off on commits 1 + 2.
- README "Use with Claude Code" section drafted (Install → Configure → Run, no Troubleshoot yet).

### Day 2 — Phase 2 commit 3 + parallel agents

**Morning (4 hrs):**
- **Implementation-3:** commit 3 — SSE streaming. Event encoder, backpressure, client-disconnect cancellation. Diff-tested against `fixtures/anthropic-sse/*.bin`. Push.
- **Agent-A-Pass-3** dispatched: SSE encoder unit tests (byte-equal diff against fixtures), cancellation tests, backpressure tests. Final Agent A sign-off lands here.

**Afternoon (4 hrs):**
- **Agent-B-Harness:** Dockerfile + harness scripts that drive Claude Code (or fallback HTTP driver per Phase 0.2 outcome) through scenarios 1, 2, 3, 11, 12, 13, 15. Owner runs this on their hardware.
- **Agent-C:** dispatched. Reads README diff, runs every shell snippet in a clean container, validates flag-prose ↔ `--help` parity, checks tone match, lints links. Output: `reports/docs-approved.md`.
- README Troubleshoot section + CHANGELOG entry drafted.
- **Rollback-verification CI step** added: build with flag off, diff against `main` HEAD's flag-off build, fail on delta.

**End-of-day-2 deliverables:**
- Commits 1 + 2 + 3 on branch.
- Agent A APPROVED.
- Agent C APPROVED (or fix list returned).
- Agent B harness ready for owner to run.

### Day 3 — Validation + PR

**Morning (4 hrs):**
- **Owner runs Agent B's harness** on target hardware. ~150 runs total (smoke 5 × 20 + mid 3 × 10 = 130 + cold-start 20 = 150). Owner uploads `reports/e2e-report.md`.
- **Or:** if owner cedes hardware control, Agent B runs in-session against a smaller model that fits in the sandbox (degraded perf data, but functional pass/fail signal). Document this as "in-session pass; production hardware run as PR follow-up."

**Afternoon (3 hrs):**
- Fix any Agent B failures.
- Squash in-progress fixups within commits (preserve the 3 logical commits).
- Self-review pass.
- Open draft PR with: motivation paragraph, "what's in this PR" bullets, perf table (static thresholds + footnote), all three signed agent reports linked, build-flag default note, rollback-verification note, CLA confirmation, v1.5 roadmap pointing to deferred items.
- Flip draft → ready.

**End-of-day-3:** PR open, awaiting upstream review.

---

## What I do vs. what you do

**I do (in this Cowork session):**
- All discovery / Phase 0 work via parallel subagents.
- All design / `design.md` authoring.
- All Phase 2 implementation code (drafted; whether it builds in this sandbox depends on whether LiteRT-LM's Bazel can run here — TBD in Discovery-B).
- All Agent A work (unit tests + mutation + security review) — runs in this sandbox.
- All Agent C work (docs review + clean-container snippet execution) — runs in this sandbox.
- Agent B *harness construction* (Dockerfile + driver scripts).
- README, CHANGELOG, PR description draft.

**You do:**
- Confirm I1–I5 in chat (1 reply, ~30 seconds of decisions).
- Sign off on `design.md` end of Day 0 (~10 min).
- Run Agent B harness on real hardware Day 3 (~1 hr supervised). Or accept the in-session degraded variant.
- Sign Google CLA if not yet signed (gates merge regardless).
- Open / submit the PR from your GitHub identity (I shouldn't push under your account).

---

## What can go wrong (compressed risk register)

| Risk | Most-likely week-1 impact | Acceleration-specific mitigation |
|---|---|---|
| LiteRT-LM doesn't build in the Cowork sandbox (Bazel + heavy deps) | Implementation lands as un-CI'd code on Day 2 | Owner runs CI on their hardware Day 2 evening; fixes fold into Day 3 morning. |
| Claude Code has no headless mode | Agent B falls back to HTTP driver, slightly weaker fidelity | Phase 0.2 documents fallback driver design upfront; harness covers both shapes. |
| SSE byte format diverges from real Anthropic | Caught Day 2 by Agent A's diff against fixtures | Phase 0.6 fixtures captured Day 0 so the gate is real. |
| Owner's CLA not signed | PR cannot merge regardless of code quality | Phase −1 / I3 surfaces this Day 0; owner signs in parallel with discovery. |
| Static perf thresholds too lax (PR reviewer pushes back) | Reviewer asks for live baseline | v1.5 roadmap explicitly commits to live baseline; reviewer can co-sign the deferral or block on it. |

---

## What you confirm to start now

Reply with any of:

- **"Go with the defaults"** — I confirm I1–I5 as proposed in the table above and dispatch the parallel discovery agents immediately.
- **Specific overrides** — e.g., "I1 yes, I2 prefer Python sidecar, I3 CLA already signed, I4 use a Mac M3 24GB, I5 yes."
- **Rejections of any trade-off T1–T5** — e.g., "T1 is unacceptable, tool use must ship in v1." Each rejection adds ~1 calendar day, and I'll quote the exact cost.

Once intake is confirmed, Day 0 starts and you don't need to interact again until end-of-Day-0 sign-off on `design.md`.

*End of execution plan v1.0.*
