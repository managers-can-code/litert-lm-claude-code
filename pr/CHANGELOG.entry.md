<!-- Proposed entry for the LiteRT-LM CHANGELOG. Goes under ## Unreleased. -->

### Added
- `serve --api anthropic` — Anthropic Messages API surface for the existing alpha `serve` subcommand. Lets [Claude Code](https://docs.claude.com/en/docs/claude-code) and other Anthropic SDK clients talk to a local LiteRT-LM model. Provides a standard local-LLM bridge UX (server on localhost, `ANTHROPIC_BASE_URL` override, optional bearer token). Implements `POST /v1/messages` (streaming + non-streaming), `POST /v1/messages/count_tokens` (heuristic estimate), `GET /v1/models`, and Anthropic `tool_use` / `tool_result` round-trip for tool-capable models. Linux + macOS supported in this release; Windows planned for the next.
