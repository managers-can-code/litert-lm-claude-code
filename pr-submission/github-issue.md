# [Proposal] Add Anthropic Messages API to `serve` subcommand for Claude Code integration

## Context

The existing alpha `serve` subcommand at `python/litert_lm_cli/serve.py` already exposes Gemini and OpenAI-compatible APIs via `GeminiHandler` and `OpenAIHandler`. This issue proposes adding a third handler — `AnthropicHandler` — that speaks the Anthropic Messages API, enabling [Claude Code](https://docs.claude.com/en/docs/claude-code) to talk to LiteRT-LM models locally.

This provides a standard local-LLM bridge UX (`ANTHROPIC_BASE_URL=http://localhost:...`, `ANTHROPIC_AUTH_TOKEN=any`).

I have a draft implementation ready. Per our prior conversation, I'm opening this issue first to declare intent before submitting the PR. CLA is signed.

## Scope (v1)

- `POST /v1/messages` — streaming (SSE) and non-streaming
- `POST /v1/messages/count_tokens` — heuristic estimate (avoids the 404-cascade degradation pattern when clients pre-flight `count_tokens`)
- `GET /v1/models` — currently loaded model list
- Anthropic `tool_use` / `tool_result` round-trip for tool-capable models, mirroring the existing `_ProxyTool` pattern
- Strict model-alias resolution by default (unknown name → 404 `not_found_error`); `--accept-any-model` opt-in for permissive routing
- `--bearer-token` opt-in for shared-secret auth on LAN deployments
- Stdlib `http.server` only — no new third-party deps; matches the existing serve.py architectural choice

## Engineering improvements

- Tool-call arguments streamed incrementally as `input_json_delta` (avoids the 255-second Claude Code timeout that buffered tool-call arguments would otherwise trigger)
- `ping` events emitted every ~10 s during long generations
- `count_tokens` returns a 200 with a heuristic estimate rather than 404'ing

## Out of scope for v1 (planned for v1.5)

- Windows support (Linux + macOS in v1)
- Image content block routing for vision models (currently returns `invalid_request_error` for non-vision)
- Grammar-constrained tool-call shim for non-native-tool models
- Live `baselines/external-reference.json` benchmark capture (using static thresholds in v1)
- Heavy-load scenarios in the e2e harness (long-context, 4k output, concurrency, restart resilience)

## File-level changes

- **New:** `python/litert_lm_cli/serve_anthropic.py`
- **New:** `python/litert_lm_cli/serve_anthropic_test.py` (absltest unit tests, ~90% coverage; ~100% on translator)
- **New:** `python/litert_lm_cli/serve_anthropic_integration_test.py` (absltest in-process integration tests)
- **Edit:** `python/litert_lm_cli/serve.py` (~5 lines: extend `--api` choice + dispatch)
- **Edit:** `python/litert_lm_cli/BUILD` (~30 lines: new `py_library` + `py_test` rules)
- **Edit:** `README.md` (~50 lines: new "Use with Claude Code" section)
- **Edit:** `CHANGELOG.md` (~5 lines: Unreleased entry)

## Validation

The PR ships with three sign-off artifacts:

- **Agent A (unit tests)** — coverage gates, mutation testing on the translator, security pass (auth, header injection, body limits, secret hygiene). Report: `reports/agent-A-unit-tests-approved.md`.
- **Agent B (e2e)** — real Claude Code CLI driven against `litert-lm serve --api anthropic` in Docker, 11 scenarios, 160 zero-flake runs, perf gates against static thresholds. Report: `reports/agent-B-e2e-report.md`.
- **Agent C (docs)** — clean-machine smoke test of the README addition, snippet execution, flag-prose ↔ `--help` parity, link integrity. Report: `reports/agent-C-docs-approved.md`.

All three reports will be linked from the PR description.

## Asks before I open the PR

1. Confirm placement: in-tree under `python/litert_lm_cli/` matching the existing serve_* pattern? Or do you prefer a different layout (e.g., separate `serve_anthropic/` subdirectory, or a companion repo)?
2. Confirm CHANGELOG convention: I'm proposing entry under `## Unreleased` — does that match your release process?
3. Any preferred PR commit structure beyond "small, logically separated, tests in the same commit as the code"?
4. Anything I should know about the `serve` subcommand's roadmap that would affect the design?

Happy to iterate on any of these before opening the PR. Implementation is ready and the validation harness has been run; turnaround on edits should be fast.
