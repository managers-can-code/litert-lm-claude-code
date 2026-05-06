# Changelog

## v0.1.0 — 2026-05-01

Initial release: LiteRT-LM × Claude Code integration.

### Added
- `pr/python/litert_lm_cli/serve_anthropic.py` — AnthropicHandler implementing
  `POST /v1/messages` (streaming + non-streaming), `POST /v1/messages/count_tokens`,
  `GET /v1/models`. Drops into upstream `python/litert_lm_cli/` as a sibling of
  `serve.py`, registered as `--api anthropic`.
- 61 unit tests (`serve_anthropic_test.py`), ≥90% line coverage, 100% line + branch
  on the translator, mutation tested via mutmut, security-checked.
- 4 captured Anthropic SSE fixtures + byte-equal diff tests.
- Companion Claude Code plugin (`plugin/litert-lm/`) with skills, slash commands,
  MCP server, and a `litert-lm-debug` subagent.
- Tier 3 reproduction kit (`tier3-runner/`) for verifying against a real
  `.litertlm` model + real Claude Code on Linux/macOS.
- Three v0.10.1 compatibility fallbacks: `create_conversation` kwarg progressive
  fallback, SSE `Connection: close`, `streaming_strategy` config knob,
  `_AnthropicProxyTool._func` synthesis for `litert_lm.tools` introspection.

### Verified
- Real Claude Code 2.1.116 + Gemma-4-E2B-it on macOS Apple Silicon: `is_error: false`,
  `result: "2+2 is 4."`, `terminal_reason: "completed"`, 4.7s end-to-end.

### Engineering improvements
- `count_tokens` 200 with heuristic estimate (avoids 404-cascade degradation when clients pre-flight).
- Tool args streamed incrementally as `input_json_delta` (avoids the 255s Claude Code timeout that buffered tool args would otherwise trigger).
- `ping` events every ~10s during long generations.
