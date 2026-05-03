# LiteRT-LM × Claude Code

> Add an Anthropic Messages API to `litert-lm serve` so [Claude Code](https://docs.claude.com/en/docs/claude-code) (and any Anthropic SDK client) can talk to a local LiteRT-LM model — no cloud, no API key, no internet required after the model is downloaded.

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![LiteRT-LM](https://img.shields.io/badge/LiteRT--LM-%E2%89%A50.10.1-green.svg)](https://github.com/google-ai-edge/LiteRT-LM)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-%E2%89%A52.1-orange.svg)](https://docs.claude.com/en/docs/claude-code)

## Two-repo setup — read this first

The integration is split across two repos for separation of concerns. **You almost certainly want both.**

| Repo | What's in it | When you use it |
|---|---|---|
| [**managers-can-code/LiteRT-LM** @ `feat/serve-anthropic-api`](https://github.com/managers-can-code/LiteRT-LM/tree/feat/serve-anthropic-api) | Full LiteRT-LM source with the `AnthropicHandler` integration baked in. Drop-in compatible — no manual patching. | **Clone this and run the server.** This is what your CLI talks to. |
| **This repo** ([managers-can-code/litert-lm-claude-code](https://github.com/managers-can-code/litert-lm-claude-code)) | Documentation (this README, OVERVIEW, RISKS, PRD), the optional Claude Code plugin (zero-config slash commands + MCP server), the Tier 3 reproduction runner, validation reports, agent specs. | **Read for context, install the plugin, run the verification harness.** Doesn't run a server itself. |

**Minimum testable path** uses just the fork. **Full intended UX** (zero-config plugin, slash commands, debug subagent) uses both. See [docs/OVERVIEW.md](docs/OVERVIEW.md) for the plain-English explainer to share with teammates.

## Why this exists

Claude Code is a powerful AI coding assistant. LiteRT-LM is Google's runtime for on-device LLM inference. Until now they didn't talk to each other: Claude Code spoke Anthropic's API, LiteRT-LM's `serve` subcommand spoke Gemini and OpenAI APIs. This integration adds an `AnthropicHandler` so Claude Code can drive a local LiteRT-LM model exactly like it drives `api.anthropic.com`.

## Two modes — pick one or use both

Same plugin install. Different ways for Claude Code to interact with the local model.

| Mode | What happens | Best for |
|---|---|---|
| **Mode 1 — Server proxy** | `ANTHROPIC_BASE_URL=http://localhost:9379`. ALL Claude Code traffic goes to local model. | Privacy-required code, fully offline, max cost savings (cloud Claude bypassed entirely). |
| **Mode 2 — Subagent delegation** (modeled on [codex-plugin-cc](https://github.com/openai/codex-plugin-cc)) | Cloud Claude stays primary; delegates routine tasks to a `litert-lm-local` subagent that runs on your machine. | Keep cloud-Claude quality on hard work; offload doc strings, simple refactors, code summaries to local. |

Both modes share: the `litert-lm serve --api anthropic` server, the same `.litertlm` model file, the plugin's slash commands, and the MCP tools. Switching is one environment variable.

See [TESTING.md](docs/TESTING.md) for step-by-step verification of both modes side-by-side.

## Quick start

```bash
# 1. Clone the working LiteRT-LM fork (with the integration pre-applied)
gh repo clone managers-can-code/LiteRT-LM
cd LiteRT-LM
git checkout feat/serve-anthropic-api
uv tool install --from . litert-lm

# 2. Pre-cache a model (anything from huggingface.co/litert-community)
litert-lm run --from-huggingface-repo=litert-community/Gemma-4-E2B-it \
  gemma-4-E2B-it.litertlm \
  --prompt "hello"

# 3. Start the server with the Anthropic Messages API
litert-lm serve --api anthropic \
  --model ~/.cache/huggingface/hub/.../gemma-4-E2B-it.litertlm

# 4. Point Claude Code at it
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any-value
claude --model local-model

# 5. Type into Claude Code as usual — responses come from your local model.
```

For the zero-config plugin experience (slash commands `/litert-lm-start`, `/litert-lm-stop`, `/litert-lm-status`, an MCP server, a `litert-lm-debug` subagent), also clone this repo and install [`plugin/litert-lm/`](plugin/litert-lm/).

For reproducible end-to-end verification, run [`tier3-runner/run-tier3-auto.command`](tier3-runner/) — it drives a real Claude Code binary against your real `litert_lm.Engine` and writes a transcript.

## What's in this repo

This repo is the **docs + plugin + reproduction kit**. The integration source lives in the [fork branch](https://github.com/managers-can-code/LiteRT-LM/tree/feat/serve-anthropic-api).

| Path | Purpose |
|---|---|
| `README.md` (this file) | Top-level entry point — what the integration is, how to use it, where the working code is |
| `docs/OVERVIEW.md` | Plain-English explainer suitable for sharing with non-technical teammates |
| `docs/PRD.md` | Formal design document — problem, goals, architecture, trade-offs, decision log, risk register |
| `docs/RISKS.md` | Calibrated risks, limitations, and mitigations (protocol drift, model quality, hardware cost, etc.) |
| `plugin/litert-lm/` | Optional Claude Code plugin: skills, slash commands (`/litert-lm-start`, `/litert-lm-stop`, etc.), MCP server, `litert-lm-debug` subagent |
| `tier3-runner/` | Reproducible end-to-end verification runner — auto-discovers your model, runs scenarios against a real Claude Code session, writes a transcript |
| `fixtures/` | 4 captured Anthropic SSE streams (byte-equal test oracles) |
| `agents/` | Validation agent specs (Agent A unit-test validator, Agent B e2e runner, Agent C docs reviewer) + Docker e2e harness |
| `reports/` | Tier 3 verification reports — the empirical evidence the integration works |
| `pr/` | Mirror of the integration source for reference; the live working copy is in [the fork branch](https://github.com/managers-can-code/LiteRT-LM/tree/feat/serve-anthropic-api/python/litert_lm_cli) |
| `pr-submission/` | GitHub Issue and PR description drafts for the eventual upstream submission |

**The integration source** (the actual handler, tests, BUILD edits, serve.py patch) lives at [`managers-can-code/LiteRT-LM/feat/serve-anthropic-api`](https://github.com/managers-can-code/LiteRT-LM/tree/feat/serve-anthropic-api/python/litert_lm_cli) — that's the working tree your peer testers clone.

## Endpoints

The server speaks the Anthropic Messages API at port 9379 (configurable via `--port`):

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/messages` | Send a Messages-API request. Set `"stream": true` for SSE; otherwise returns a single JSON envelope. |
| `POST` | `/v1/messages/count_tokens` | Returns a heuristic char/4 token estimate so clients that pre-flight don't 404. |
| `GET` | `/v1/models` | Returns the list of currently loaded model IDs in Anthropic-shaped JSON. |

## Translator coverage

Every documented Anthropic Messages field is mapped:

| Anthropic field | Notes |
|---|---|
| `model` | Strict by default; `--accept-any-model` to route any name to the loaded model |
| `system` (string or array of text blocks) | Prepended as a system turn |
| `messages[*]` text content | Pass-through |
| `messages[*]` `tool_use` / `tool_result` content blocks | Round-trip with the model |
| `messages[*]` `image` content blocks | Routed to the engine when the loaded model supports vision; clean 400 otherwise |
| `tools[*]` | Mapped via `litert_lm.Tool` catalog |
| `temperature` / `top_p` / `top_k` / `stop_sequences` / `max_tokens` | Translated to `SamplerConfig` |
| `stream` | `true` → SSE event sequence; `false` → single JSON |

## Engineering improvements

| Engineering issue | What we do |
|---|---|
| `count_tokens` 404 cascade degrades the server when clients pre-flight | Return 200 with a heuristic estimate |
| Buffered tool-call arguments hit the 255 s Claude Code timeout | Stream tool-call arguments incrementally as `input_json_delta` events |
| Long generations time out on quiet SSE connections | Emit `ping` events every ~10 s |

## Compatibility

| Dependency | Required version | Notes |
|---|---|---|
| `litert-lm` | ≥ 0.10.1 | Older builds use a defensive fallback path that drops sampler / system-prompt support |
| Claude Code | ≥ 2.1 | Tested against 2.1.116 |
| Python | ≥ 3.10 | Matches LiteRT-LM's existing requirements |

Linux and macOS are first-class. Windows works via WSL2 (see [docs/RISKS.md §6](docs/RISKS.md#6-operating-system-support)) but isn't part of v1's CI matrix.

### Recommended models

Pick from the [LiteRT-LM model catalog at huggingface.co/litert-community](https://huggingface.co/litert-community). The integration runs against any `.litertlm` model the runtime can load. Picks below are calibrated for typical hardware tiers; consult [the LiteRT-LM models docs](https://ai.google.dev/edge/litert-lm/models) for the current catalog.

| Hardware | Model (from litert-community) | Best for |
|---|---|---|
| Laptop, 8 GB RAM | Gemma-4-E2B-it | Doc strings, simple refactors, single-file edits, code summaries |
| Laptop, 16+ GB RAM | Gemma-4-E4B-it (when available) or Gemma 3n E4B | The above + small-file debugging, simple test generation |
| Desktop / workstation | Larger Gemma variants from the catalog (resource-permitting) | Closer-to-cloud quality; larger context windows |

Tool-call format compatibility depends on the specific model and how it was instruction-tuned. Gemma models typically emit tool calls in their native format; the v1.5 translator (in this repo's roadmap) will convert these into Anthropic's `tool_use` content blocks so Claude Code recognises them as tool invocations. Until the translator lands, tool-use scenarios degrade gracefully — text turns work fully; tool-call output is delivered to Claude Code as text and surfaced rather than crashed. For text-only chat, every model in the catalog works.

Pre-cache a model with the LiteRT-LM CLI:

```bash
litert-lm run --from-huggingface-repo=litert-community/Gemma-4-E2B-it \
  gemma-4-E2B-it.litertlm \
  --prompt "hello"
```

The first run downloads to LiteRT-LM's HuggingFace cache. Subsequent runs are local-only.

## CLI flags

```
litert-lm serve --api anthropic --model PATH
                [--host 127.0.0.1]
                [--port 9379]
                [--max-request-bytes 4194304]   # 4 MiB
                [--request-timeout-secs 300]    # 5 min
                [--max-concurrent 4]
                [--bearer-token TOKEN]          # optional auth
                [--accept-any-model]            # route any model name to the loaded model
                [--streaming-strategy auto|native|synthetic]
                [--verbose]
```

## Validated against a real model

The handler has been empirically verified against `gemma-4-E2B-it.litertlm` running in `litert-lm 0.10.1`, driven by real Claude Code 2.1.116. The Tier 3 transcript captures the full round-trip: real Claude Code session → `ANTHROPIC_BASE_URL` → our handler → real `litert_lm.Engine` → real model output (`"2+2 is 4."`) back into Claude Code, in 4.7 seconds, with `is_error: false`. See [`reports/agent-B-tier3-real-report.md`](reports/agent-B-tier3-real-report.md) for the run-by-run evidence.

## Tests

```bash
# Unit tests (no model required)
python -m absl.testing.absltest \
  python/litert_lm_cli/serve_anthropic_test.py

# Integration tests (in-process server, stubbed engine)
python -m absl.testing.absltest \
  python/litert_lm_cli/serve_anthropic_integration_test.py
```

61 unit tests, ≥90% line coverage on new code, 100% line + branch on the translator, mutation-tested on the translator, security-checked (auth, header injection, body limits, secret hygiene).

## Architecture (one-line version)

```
Claude Code  ──HTTP──▶  POST /v1/messages  ──▶  AnthropicHandler  ──▶  litert_lm.Engine  ──▶  model
     ▲                                                  │
     └─────── Anthropic SSE ◀── translator ◀────────────┘
```

`AnthropicHandler` is a `BaseHTTPRequestHandler` mounted onto LiteRT-LM's existing `serve` infrastructure — same singleton engine pattern as `GeminiHandler` and `OpenAIHandler`. No new dependencies, no new framework, no new build target outside the BUILD file additions.

## Risks and limitations

A short list with full details in [docs/RISKS.md](docs/RISKS.md):

- **Protocol drift.** Anthropic could change the wire format Claude Code uses; this integration would need a patch when that happens. The `ANTHROPIC_BASE_URL` override mechanism itself is enterprise infrastructure and not at risk. Permissive parsing + structured logs make drift detectable; SSE fixtures + diff tests make breakage obvious.
- **Reasoning quality vs. cloud Claude.** A 2B-parameter local model won't beat cloud Sonnet on hard reasoning. Use local for routine work; escalate to cloud for the rest. Right mental model: have both.
- **Tool-call format mismatches.** LiteRT-LM-supported Gemma variants typically emit tool calls in their native format (`<|tool_call>...`) which Claude Code doesn't yet recognize as `tool_use` blocks. The integration delivers the response gracefully as text, but the agent loop won't auto-execute tools in v1. The v1.5 translator closes this gap; for tool-heavy work today, escalate to cloud Claude.
- **No vision support yet.** Image content blocks return a clean `invalid_request_error`. Vision models are a v1.5 milestone. Drag-and-drop image work needs cloud Claude.
- **Hardware cost.** Real, measurable. ~3 GB RAM and 50–80% CPU/GPU during inference; battery drain ~5–10× baseline during active use. Stop the server when idle. Smaller models for battery sessions.
- **Linux and macOS only.** The integration runs wherever LiteRT-LM runs as a desktop CLI. Windows users follow the WSL2 path documented in [docs/RISKS.md §6](docs/RISKS.md#6-operating-system-support); native Windows tracks upstream LiteRT-LM's platform support.

## Companion: Claude Code plugin

A separate Claude Code plugin (`plugin/litert-lm/`) makes setup zero-config. It ships:

- A `setup-litert-lm` skill that walks first-time users from install to working session.
- Slash commands: `/litert-lm-start`, `/litert-lm-stop`, `/litert-lm-status`, `/litert-lm-switch`, `/litert-lm-config`.
- An MCP server exposing the same lifecycle as tools.
- A `litert-lm-debug` subagent for triaging local-server issues.
- A `troubleshoot-litert-lm` skill for common error modes.

See [`plugin/litert-lm/README.md`](plugin/litert-lm/README.md) for installation.

## Contributing

Per upstream's [`CONTRIBUTING.md`](CONTRIBUTING.md), code contributions require coordination with the LiteRT-LM maintainers and a signed Google CLA. Open an issue declaring intent before opening a PR.

## License

Apache-2.0. Same as upstream LiteRT-LM. See [`LICENSE`](LICENSE).

## Acknowledgements

- The LiteRT-LM team for the existing `serve` infrastructure that made this a small additive PR rather than a from-scratch project.
- Anthropic for [Claude Code](https://docs.claude.com/en/docs/claude-code) and the published Messages API surface.
