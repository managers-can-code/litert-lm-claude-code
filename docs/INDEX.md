# LiteRT-LM × Claude Code Integration — Deliverables Index

**Owner:** ram
**Date:** 2026-04-30
**Status:** Implementation drafted; awaiting owner review + Agent A/B/C validation runs.

This is the manifest of every file produced for the LiteRT-LM × Claude Code integration PR. Files are grouped by purpose. Every path is absolute on this machine.

---

## 1. Workflow & planning

| File | Purpose |
|---|---|
| `outputs/litert-lm-claude-code-integration-workflow.md` | The reviewed workflow (v0.2). Reference doc. |
| `outputs/litert-lm-claude-code-execution-plan.md` | The accelerated execution plan (v1.0). Reference doc. |
| `outputs/discovery.md` | Phase 0 consolidated findings from all four discovery agents. |
| `outputs/design.md` | Final design (v0.3). The architectural source of truth. |

## 2. Golden SSE fixtures (Phase 0 deliverables)

These are the byte-equal oracles for the SSE encoder. Captured / reconstructed from Anthropic's documented spec + a live capture by Simon Willison.

| File |
|---|
| `outputs/fixtures/anthropic-sse-stream-1-simple.txt` |
| `outputs/fixtures/anthropic-sse-stream-2-multi-turn.txt` |
| `outputs/fixtures/anthropic-sse-stream-3-cancel.txt` |
| `outputs/fixtures/anthropic-sse-stream-4-tool-use.txt` |

## 3. PR-ready code (drop into `python/litert_lm_cli/` of the LiteRT-LM repo)

| File | Lines | Purpose |
|---|---|---|
| `outputs/pr/python/litert_lm_cli/serve_anthropic.py` | ~1480 | The handler, translator, SSE encoder, error mapper, auth, limits, tool routing |
| `outputs/pr/python/litert_lm_cli/serve_anthropic_test.py` | ~770 | Unit tests (absltest + parameterized) |
| `outputs/pr/python/litert_lm_cli/serve_anthropic_integration_test.py` | ~560 | Integration tests (in-process server + httpx) |
| `outputs/pr/serve_py_patch.diff` | small | One-line edit to existing `serve.py` to extend `--api` choice |
| `outputs/pr/BUILD.additions` | ~30 | Bazel `py_library` + `py_test` rules |
| `outputs/pr/README.section.md` | ~50 | New "Use with Claude Code" section for top-level README |
| `outputs/pr/CHANGELOG.entry.md` | ~5 | Unreleased entry |

## 4. Three validation agents (per the Phase 3 spec)

Each agent has its own spec file. Run via Claude Code (or comparable) per the invocation prompt embedded in each spec.

| File | Purpose |
|---|---|
| `outputs/agents/agent-A-unit-test-validator.md` | Unit test gates: coverage ≥90%, mutation ≥80% on translator, security checks, mypy --strict |
| `outputs/agents/agent-B-e2e-integration-runner.md` | E2E gates: 11 scenarios × tiered runs (160 total), perf vs. static thresholds, zero flakes |
| `outputs/agents/agent-C-docs-reviewer.md` | Docs gates: clean-machine smoke, snippet execution, flag-prose ↔ --help parity, link integrity |

## 5. E2E test harness (Agent B's container)

| File | Purpose |
|---|---|
| `outputs/agents/e2e-harness/Dockerfile` | Ubuntu container with Claude Code CLI + litert-lm install + harness |
| `outputs/agents/e2e-harness/install_litert_lm.sh` | Mounts /src, installs litert-lm via uv |
| `outputs/agents/e2e-harness/entrypoint.sh` | Starts server, waits for /v1/models, runs scenarios, persists report |
| `outputs/agents/e2e-harness/run_scenarios.py` | The scenario driver. Drives `claude -p` against the server, aggregates p50, writes `agent-B-e2e-report.md` |

## 6. PR submission artifacts

| File | Purpose |
|---|---|
| `outputs/pr-submission/github-issue.md` | Text for the GitHub Issue to open first (per "Issue first, then PR" intake choice) |
| `outputs/pr-submission/pr-description.md` | The draft PR description, with placeholders for measured perf numbers and agent-report links |

---

## What you do next

The fastest path from here to a real PR:

### Step 1 — open the GitHub Issue (~10 min)

Copy the contents of `outputs/pr-submission/github-issue.md` into a new issue at `https://github.com/google-ai-edge/LiteRT-LM/issues/new`. Edit the maintainer handle reference to match your prior conversation. Wait for ack.

### Step 2 — clone the repo and drop the files in (~15 min)

```bash
git clone https://github.com/google-ai-edge/LiteRT-LM.git
cd LiteRT-LM
git checkout -b feat/serve-anthropic-api

# Drop the implementation in
cp /Users/ramiyengar/Library/Application\ Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/python/litert_lm_cli/*.py \
   python/litert_lm_cli/

# Apply the serve.py patch (re-anchor line numbers against current main first)
# See outputs/pr/serve_py_patch.diff for the intent

# Append BUILD additions
cat /Users/ramiyengar/Library/Application\ Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/BUILD.additions \
   >> python/litert_lm_cli/BUILD

# README + CHANGELOG additions: paste manually into the right places
```

### Step 3 — run Agent A locally (~10 min)

Validates unit tests, coverage, mutation, security checks. Run from the LiteRT-LM repo root:

```bash
claude -p "$(cat /Users/ramiyengar/Library/Application\ Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/agents/agent-A-unit-test-validator.md)" \
       --bare \
       --output-format json \
       --allowedTools "Read,Bash" \
       > reports/agent-A-unit-tests-approved.md
```

### Step 4 — run Agent B (the e2e harness) on real hardware (~1 hr)

Requires a `.litertlm` model file. Adjust the Dockerfile MODEL_FILENAME or mount your model at `/models`.

```bash
cd /Users/ramiyengar/Library/Application\ Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/agents/e2e-harness
docker build -t litert-claude-e2e --build-arg MODEL_FILENAME=your-model.litertlm .
docker run --rm \
  -v /path/to/cloned/LiteRT-LM:/src \
  -v /path/to/your/models:/models \
  -v $(pwd)/reports:/reports \
  litert-claude-e2e
```

Report drops to `reports/agent-B-e2e-report.md`. STATUS line at the bottom must say APPROVED.

### Step 5 — run Agent C (~15 min)

Validates docs against the actual built binary:

```bash
claude -p "$(cat /Users/ramiyengar/Library/Application\ Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/agents/agent-C-docs-reviewer.md)" \
       --bare \
       --output-format json \
       --allowedTools "Read,Bash" \
       > reports/agent-C-docs-approved.md
```

### Step 6 — open the PR (~30 min)

Once all three reports say APPROVED:
1. Commit your three logical commits per the design.md commit-structure plan.
2. Push to your fork.
3. Open a draft PR using `outputs/pr-submission/pr-description.md` as the description (replace the perf-table placeholders with measured numbers from Agent B's report).
4. Self-review pass.
5. Flip draft → ready.

---

## Important notes & known deviations

The implementation subagent flagged four items the owner should be aware of:

1. **Click registration approach** — The subagent created a standalone `litert-lm serve-anthropic` command via `register(cli)` rather than runtime-mutating the existing `--api` Click choice (Click choices are decorator-fixed). The subagent's docstring also recommends a one-line edit to `serve.py`'s Click decorator to add `"anthropic"` to the existing `--api` choice list — this is what `outputs/pr/serve_py_patch.diff` covers. With the patch applied, **both** `litert-lm serve-anthropic` and `litert-lm serve --api anthropic` work; without it, only the former. The PR should ship with the patch so the documented UX in the README matches.

2. **Engine API assumptions wrapped in fallbacks** — `Engine.create_conversation(system_message=...)`, `SamplerConfig` constructor, and `engine.vision_backend`/`engine.supports_tools` capability flags are all probed with TypeError-safe fallbacks since the subagent couldn't fully verify against the live `interfaces.py`. Agent A's run will reveal if any of these need the fallback to actually fire on the user's installed version.

3. **Fixture byte-equality test approach** — Rather than asserting whole-file byte-equality (which would fail due to dynamic message IDs and the intentional truncation in fixture 3), the unit tests parse fixtures into events and assert each event re-encodes byte-equal via `format_sse_event`. This is the meaningful invariant.

4. **Heavy e2e scenarios deferred** — Agent B's harness covers 11 scenarios (smoke + mid). Long-context, 4k-output, concurrency, restart-resilience scenarios are deferred to v1.5 per the T4 acceleration trade.

If any of these surface as a problem during your local Agent A/B/C runs, ping me and I'll iterate.
