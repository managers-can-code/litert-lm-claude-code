# Agent B — End-to-End Integration Runner

**Role:** prove a real Claude Code CLI talking to a real `litert-lm serve --api anthropic` works smoothly end-to-end.

**Inputs:**
- A built `litert-lm` package with `serve_anthropic.py` merged
- A small real model (recommended: a Gemma 3n variant compatible with LiteRT-LM, ≤4GB)
- Claude Code CLI ≥ v2.1.123
- `outputs/agents/e2e-harness/Dockerfile` and `outputs/agents/e2e-harness/run_scenarios.py`
- `outputs/design.md` § 9 (static perf threshold table)

**Output:** `reports/agent-B-e2e-report.md` containing per-scenario pass/fail, latency tables vs. the static thresholds, regressions called out, and an explicit `STATUS: APPROVED` / `STATUS: REJECTED` line.

---

## Scenario list (v1)

| # | Scenario | Tier | Runs (zero-flake target) |
|---|---|---|---|
| 1 | Single-turn chat ("what is 2+2?") | smoke | 20 |
| 2 | Multi-turn with 3 follow-ups | smoke | 20 |
| 3 | Streaming cancellation mid-response | smoke | 20 |
| 4 | Tool use: `Read` a file | mid | 10 |
| 5 | Tool use: `Bash` command | mid | 10 |
| 6 | Tool use: `Edit` a file (round-trip) | mid | 10 |
| 11 | Bad request (missing max_tokens) → 400 | smoke | 20 |
| 12 | Unknown model (strict mode) → 404 | smoke | 20 |
| 13 | Cold-start TTFT (first request after `serve` start) | mid | 10 |
| 14 | Image content block to non-vision model → 400 | mid | 10 |
| 15 | --accept-any-model passthrough → 200 | smoke | 20 |

Total runs: 5 × 20 + 6 × 10 = 160. Zero flakes allowed at any tier.

Heavy scenarios (long-context, long-output, concurrency, restart resilience) are deferred to v1.5 per acceleration trade T4.

---

## Performance gates (vs. static thresholds in design.md § 9)

| Scenario | Threshold |
|---|---|
| Scenario 1 first-token p50 | ≤ 1500 ms |
| Scenario 1 tokens/sec p50 | ≥ 15 tok/s |
| Scenario 1 total wall-time p50 | ≤ 3 s |
| Scenario 13 cold-start TTFT p50 | ≤ 6 s |

If any threshold is missed, FAIL with the actual measured value and the delta. Owner can override with `--allow-perf-regression` if the regression is understood and documented in the PR.

---

## Invocation prompt (paste into Claude Code or comparable runtime)

```
You are Agent B — the end-to-end integration runner for the LiteRT-LM × Claude Code integration PR.

Your job:
1. Build the test container from outputs/agents/e2e-harness/Dockerfile.
2. Inside the container, run `litert-lm serve --api anthropic --model <model> --port 9379 &` and wait for /v1/models to respond.
3. Set ANTHROPIC_BASE_URL=http://localhost:9379 and ANTHROPIC_AUTH_TOKEN=any-string in the container.
4. Run outputs/agents/e2e-harness/run_scenarios.py which executes each scenario via `claude -p ... --bare --output-format json --allowedTools "Read,Bash,Edit"` for the appropriate number of repetitions.
5. Collect timings from --output-format=json's stdout (Claude Code reports timing in the JSON envelope).
6. Compare measured p50s against the static thresholds in design.md § 9.
7. Write reports/agent-B-e2e-report.md per the format below.

Do NOT modify the server code or the harness. If a scenario fails, capture the request, response, and any server-side log, and include in the report. If the same scenario flakes (passes some runs, fails others), that is a FAIL — even one flake means REJECTED.

REPORT FORMAT:

# Agent B Report — <ISO8601 datestamp>
## Environment
- Model: <model name + path>
- Hardware: <CPU/RAM>
- LiteRT-LM version: <git SHA>
- Claude Code version: <claude --version output>
- Total scenarios: 11
- Total runs: 160
## Per-scenario results
| # | Scenario | Tier | Runs | Pass | Fail | Status |
| 1 | Single-turn chat | smoke | 20 | 20 | 0 | PASS |
| ...
## Performance vs. thresholds
| Scenario | Metric | Measured p50 | Threshold | Status |
| 1 | first-token | <X> ms | ≤ 1500 ms | PASS/FAIL |
| ...
## Failures
<list every failure with full request, response, server log excerpt, root-cause hypothesis. EMPTY if APPROVED.>

STATUS: APPROVED  (or REJECTED with reason)
```

---

## Hardware run

The harness is designed to run on the owner's hardware (per the I3 / earlier intake choice). Owner runs:

```bash
cd outputs/agents/e2e-harness
docker build -t litert-claude-e2e .
docker run --rm -v $(pwd)/reports:/reports litert-claude-e2e
```

Report drops to `reports/agent-B-e2e-report.md`. Owner reviews + commits to the PR.

## Stop conditions

REJECTED if: any scenario flakes, any perf threshold missed without owner override, any scenario fails outright.
