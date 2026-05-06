# Risks, limitations, and what to expect

A direct answer to the most common question reviewers ask: "what could go wrong with this, realistically?" Each concern below is named, calibrated, and has a concrete mitigation strategy.

This document supplements [README.md](README.md), [OVERVIEW.md](OVERVIEW.md), and [PRD.md](PRD.md). Read those for what the integration *is*; read this for what it *won't be*.

---

## 1. Protocol drift (the foundational risk)

**The concern.** This integration intercepts Claude Code's API traffic via the `ANTHROPIC_BASE_URL` environment variable and serves a server that imitates `api.anthropic.com`. Claude Code is closed-source. If Anthropic changes the wire format — a new event type, a new mandatory header, a new content-block shape — our handler breaks until someone patches it.

**Why `ANTHROPIC_BASE_URL` itself is safe.** This isn't a workaround we discovered; it's a documented enterprise feature used by every major API SDK. Customers route traffic through corporate proxies, internal load balancers, fine-tuned-model services, and on-prem gateways. Removing the override would break millions of dollars of enterprise integrations. It's not going anywhere.

**What is at risk.** The wire format underneath. Past Anthropic API changes have included: adding `pause_turn` and `refusal` stop reasons (handler tolerates unknown values via fallthrough); introducing the `anthropic-beta` header for opt-in features (we ignore unknown betas); adjusting the `usage` field (we round-trip it as opaque). Future changes could be more invasive.

**Mitigations shipped in this PR.**

- **Permissive parsing.** Unknown fields are logged with the request ID rather than rejected. Unknown stop reasons fall through to a documented default. Unknown content-block types return a clean `invalid_request_error` rather than a 500.
- **Captured SSE fixtures.** [`fixtures/anthropic-sse-stream-{1,2,3,4}-*.txt`](../fixtures/) are byte-equal oracles for our encoder. If a client (or Anthropic itself) changes the format, the diff-test fails loudly rather than silently.
- **Structured logging with `request_id`.** Every request emits a JSON log line. Drift looks like "field X showed up in 3% of recent requests but isn't in our translator" — easy to detect from logs.

**Mitigations on the roadmap.**

- **CI canary.** Periodically capture fresh SSE responses from `api.anthropic.com` and diff against our fixtures. If they drift, fail the build with a pointer to which event changed. (Requires an API key in CI; deferred.)
- **Active maintenance.** This is a community project; if Anthropic changes the wire format, the fix is a small patch. Set issue notifications on the repo.

**What you should do as a user.** If something starts behaving oddly after a Claude Code update, check the [issues page](https://github.com/managers-can-code/litert-lm-claude-code/issues) — protocol drift will be triaged there first. Pinning to a known-good Claude Code version (`npm install -g @anthropic-ai/claude-code@2.1.116`) is a temporary workaround.

---

## 2. Reasoning quality vs. cloud Claude

**The concern.** A 2B-parameter model on your laptop can't outthink Claude Sonnet 4.6. The OVERVIEW says this; the reviewer rightly underlines it.

**Honest take.** For routine tasks — formatting, doc-string generation, predictable refactors, lint cleanups, code summaries, sample-data generation, learning experiments — a small local model is fine. Sometimes great. For complex debugging, novel architecture decisions, hard reasoning, agentic multi-step planning across a large codebase — cloud Claude wins, often dramatically.

**The right mental model.** Treat the local model as your "L1 support" tier and cloud Claude as your specialist. Route the boring stuff to local; escalate to cloud when you hit something hard. The setup cost to switch is one environment variable.

**Concrete model-size recommendations.**

All recommendations are pulled from [the LiteRT-LM model catalog at huggingface.co/litert-community](https://huggingface.co/litert-community). LiteRT-LM only loads `.litertlm`-format models from this catalog; third-party models (Llama, Qwen, Mistral, etc.) are not in scope unless and until they're available in `.litertlm` form.

| Hardware | Recommended model | What it's good for | What it'll struggle with |
|---|---|---|---|
| Laptop, 8 GB RAM | Gemma-4-E2B-it (~2 GB on disk) | Doc strings, simple refactors, single-file edits, code summaries | Multi-file reasoning, novel architectures, subtle bugs |
| Laptop, 16+ GB RAM | Gemma 3n E4B / Gemma-4-E4B-it (when available) | The above + small-file debugging, simple test generation | Cross-file refactoring, performance tuning |
| Desktop / workstation | Larger Gemma variants from the catalog (resource-permitting) | Closer-to-cloud quality; larger context windows | Truly hard reasoning still escalates to cloud |

These are starting points. Consult the [LiteRT-LM models docs](https://ai.google.dev/edge/litert-lm/models) for the current catalog — what's available evolves with each LiteRT-LM release. Sizes and quantization affect both speed and quality.

---

## 3. Tool-calling format mismatches

**The concern.** Claude Code relies heavily on tool use — Read, Edit, Bash, Grep, Glob — to do real work. LiteRT-LM-supported Gemma variants typically emit tool calls in their native format (e.g. `<|tool_call>call:read\ninput: /etc/hostname` from Gemma-4-E2B-it during our Tier 3 run) instead of Anthropic's `tool_use` content block, so Claude Code doesn't recognize them as tool invocations and the agent loop stalls on tool turns.

**This is the biggest practical limitation today.** Our Tier 3 verification confirmed it: Gemma-4-E2B-it produced valid tool-call intent (`<|tool_call>call:read\ninput: /etc/hostname`) but in its native format, which Claude Code parsed as plain text rather than a tool invocation.

**Mitigations shipped in this PR.**

- **Tool definitions are forwarded faithfully.** The translator passes Claude Code's `tools[*]` array to the engine via `litert_lm.Tool`, so the model knows what's available.
- **Graceful degradation.** When the model emits a tool call in its native format that Claude Code doesn't recognise, the response is surfaced as text rather than crashing the agent loop. The user sees what the model intended; nothing 500s.
- **Honest documentation.** The README and OVERVIEW set expectations that v1's tool-use scenarios depend on the specific Gemma variant's instruction-tuning.

**Mitigations on the v1.5 roadmap.**

- **Tool-format translator module.** A small parser that detects Gemma's native tool-call format (e.g. `<|tool_call>call:read\ninput: /etc/hostname` from Gemma-4-E2B-it during our Tier 3 run) and translates it into Anthropic `tool_use` content blocks at the SSE encoder layer. Once landed, Gemma models become transparently tool-callable from Claude Code. Future LiteRT-LM model additions get a translator entry as needed.

**What you should do as a user (today).** For tool-heavy agentic work, escalate to cloud Claude until v1.5's translator lands. For text-only chat — questions, summaries, doc generation, refactor suggestions — any LiteRT-LM-supported model in the catalog works.

---

## 4. Vision support

**The concern.** If you drag an image into Claude Code (common for frontend work, design review, debugging visual UIs), the local model fails on the image.

**What happens today.** The handler returns a clean `invalid_request_error` with a message naming the image content block. Claude Code surfaces this back to the user. No 500, no crash, no half-rendered response.

**Mitigation on the v1.5 roadmap.** Image content-block routing for vision-capable LiteRT-LM models (Gemma 3n vision variants when available). The translator already has the field-mapping table row; the runtime path is what's deferred.

**What you should do as a user.** For vision-required work, escalate to cloud Claude. The error message tells you which message had the unsupported content.

---

## 5. Hardware cost — RAM, CPU/GPU, battery

**The concern.** Running an LLM locally is not free. Your laptop fans spin up, RAM fills, battery drains.

**What to actually expect** (Apple M-series MacBook Pro, Gemma-4-E2B-it):

| Metric | Idle (server up, no traffic) | Active inference |
|---|---|---|
| RAM | ~2 GB resident | ~3 GB resident |
| CPU/GPU | <5% | 50–80% on the model thread |
| Battery drain | ~2x baseline | ~5–10x baseline during active generation |
| Fan | Quiet | Audible during longer responses |

Bigger models cost more. A 7B model roughly doubles RAM; a 32B model doubles again.

**Mitigations.**

- **Stop the server when not in use.** `/litert-lm-stop` (via the plugin) or `pkill -f "litert-lm serve"`. Idle cost drops to zero.
- **`--max-concurrent 1` for laptops.** Default is 4 in-flight inferences; for battery work, drop it to 1 so background haiku requests don't pile up.
- **Smaller models for short-battery sessions.** Gemma-4-E2B-it draws less power than larger Gemma variants in the catalog at the cost of some reasoning quality. Pick the smallest model that meets your task needs.

**Mitigations on the roadmap.**

- **`--low-power` mode flag.** Bundles smaller `max_concurrent`, lower `max_new_tokens`, and a thread cap into one operator-friendly switch. Tracked as a v1.5 enhancement.

---

## 6. Operating system support

**The concern.** Which platforms is the integration usable on?

**Reality.** This integration runs wherever LiteRT-LM itself runs as a desktop CLI — currently **Linux** and **macOS**. Tier 3 was verified empirically on macOS Apple Silicon, and the Docker e2e harness runs on Linux. Other platforms (Android, embedded) are within LiteRT-LM's scope but aren't relevant to this integration since Claude Code is a desktop tool.

**On Windows.** LiteRT-LM CLI installation on native Windows is not officially supported by upstream LiteRT-LM. If you're on Windows and have a Linux environment available via WSL2, the Linux instructions in the README work inside WSL2 because it's a Linux runtime. The `localhost:9379` WSL2 ↔ Windows networking bridge handles the routing automatically:

```powershell
# 1. Enable WSL2 + install Ubuntu (one-time, on Windows)
wsl --install -d Ubuntu

# 2. Inside the Ubuntu shell — install litert-lm exactly as on Linux
uv tool install litert-lm

# 3. Start the server inside WSL, bound to 0.0.0.0 so Windows can reach it
litert-lm serve --api anthropic --host 0.0.0.0 --model <path>

# 4. From Windows PowerShell — point Claude Code at the WSL port
$env:ANTHROPIC_BASE_URL = "http://localhost:9379"
$env:ANTHROPIC_AUTH_TOKEN = "any-value"
claude --model local-model
```

This is "use LiteRT-LM's Linux support inside a Linux environment that happens to be WSL2." Native Windows depends on whether upstream LiteRT-LM adds Windows to its supported platforms.

---

## 7. Stability and bugs

**The concern.** This is fresh code. Bugs are likely.

**What we've done.** 61 unit tests, ≥90% line coverage, mutation testing on the translator, byte-equal SSE fixtures, four iterations of real-hardware Tier 3 verification against a real model + real Claude Code (each one surfaced and fixed a v0.10.1 compatibility issue). Three security checks landed (auth, header injection, body limits, secret hygiene).

**What we haven't done.** Long-running soak tests (>1 hour continuous use). Heavy concurrent load. 20k-token context windows. 4k-token output generation under timing pressure. These are tracked for v1.5 monitoring once v1 lands upstream.

**What you should do as a user.** Treat v1 as a daily-driver-with-occasional-quirks tool, not yet a production-grade service. File issues. The v1.5 milestone bakes in soak testing and stability hardening.

---

## 8. Trust and supply chain

**The concern.** You're running model weights from HuggingFace and code from a third-party PR. Both are supply-chain attack surfaces.

**Mitigations.**

- **All code is Apache-2.0 licensed and reviewable.** `serve_anthropic.py` is ~1500 lines; you can read it.
- **No new third-party runtime dependencies.** stdlib `http.server` only. The integration adds no new attack surface beyond what `litert_lm` already imports.
- **Model files are bytes-on-disk.** `.litertlm` files don't execute code; they're loaded by the LiteRT-LM runtime. Standard HuggingFace cache rules apply.
- **Localhost-only by default.** The server binds `127.0.0.1` unless you pass `--host 0.0.0.0`. Network exposure is opt-in.
- **No telemetry.** The server logs to stderr only. Nothing leaves the box.

---

## When NOT to use this

A short, opinionated list:

- **Hard architectural decisions.** Use cloud Claude.
- **Multi-file refactoring across a large codebase.** Use cloud Claude.
- **Frontend / design / image-heavy work.** Use cloud Claude.
- **Production CI loops where reliability matters more than cost.** Use cloud Claude.
- **Pair-programming with a model good enough to catch your subtle mistakes.** Use cloud Claude.

A short list of when this *is* the right choice:

- **Working offline.**
- **Working with code under embargo or NDA where nothing should leave your machine.**
- **Routine code-cleanup work where speed and cost matter more than peak quality.**
- **Learning, experimentation, prototyping.**
- **Burning through Claude Code's quota during demos.**

The right mental model is "have both, route appropriately." Local for the routine; cloud for the hard.

---

## Reporting issues

The repo is at [github.com/managers-can-code/litert-lm-claude-code](https://github.com/managers-can-code/litert-lm-claude-code). Open an issue for:

- Behavior that differs from cloud Claude in ways the docs don't predict.
- Protocol-drift symptoms (errors that mention unknown event types or fields).
- Models you tried whose tool-format compatibility surprised you (good or bad).
- Hardware costs that materially differ from the table above.

Each issue helps tune the v1.5 roadmap.
