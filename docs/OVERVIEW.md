# What this is, and how to use it

A short, plain-English explainer you can hand to anyone — teammate, manager, friend curious about local AI. No prior context required.

## What is it?

It's a small piece of software that lets [Claude Code](https://docs.claude.com/en/docs/claude-code) — Anthropic's coding assistant — talk to an AI model running on your own laptop instead of the cloud.

Today, when you use Claude Code, every message goes to Anthropic's servers. Their model thinks, sends back an answer, and you pay for the tokens. That's great when you want the smartest possible model, but it costs money, requires internet, and means your code is leaving your machine.

This integration adds a different option: point Claude Code at a model running on your laptop instead. The Claude Code interface, tool use, file editing, planning — all of it works exactly the same. The difference is just where the model lives.

It's built directly into Google's [LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM) — Google's runtime for running language models efficiently on devices.

## Why would anyone want this?

Four reasons:

**Privacy.** If you're working on sensitive code (say, internal tools, security work, code under embargo), local models keep everything on your machine. Nothing leaves your laptop.

**Offline.** Plane, train, coffee shop with broken Wi-Fi, conference Wi-Fi behind a corporate proxy. Your AI assistant keeps working.

**Cost.** Claude Code's bill scales with usage. A heavy day can rack up real money. For routine refactoring, lint-fixing, or doc-string generation, a small local model is good enough — and free per-token.

**Speed for small tasks.** Round-tripping to the cloud takes ~1 second of network. For small completions, a local model running on your GPU can respond instantly.

**Real-world honest take:** the smartest cloud models will outperform a small local model on hard reasoning. The point of this isn't to replace cloud Claude — it's to give you the *option* of local for the right tasks.

## What does it do, exactly?

There are **two ways** to use it. Pick whichever fits your situation, or use both.

### Mode 1 — Replace cloud Claude with the local model entirely (server-proxy)

You start a server on your laptop, set one environment variable, and Claude Code talks to your laptop instead of `api.anthropic.com`. Every keystroke, every response, every tool call — local. Cloud Claude is bypassed.

- Best for: working offline, sensitive code that can't leave the machine, max cost savings.
- Trade-off: a small local model can't match cloud Claude on hard reasoning. Quality on routine tasks is fine; on hard tasks, you'll feel the gap.

### Mode 2 — Keep cloud Claude in charge, delegate routine work to local (subagent delegation)

Cloud Claude stays your main agent, with full quality on hard problems. When the conversation needs something routine — doc strings, simple refactors, code summaries — Claude delegates that specific task to a local subagent that runs your local model.

- Best for: most day-to-day usage. Cloud Claude handles the hard parts; the local model handles the boring parts to save tokens or keep certain inputs private.
- Trade-off: you're still using cloud Claude (just less). Doesn't work fully offline.

Both modes share the same plugin install. You can flip between them with one environment variable.

In either mode: Claude Code doesn't know or care whether the model is "really" Anthropic's. The wire format is identical. Streaming, tool use, multi-turn conversations — everything works the same way.

## Capabilities — the short list

What works today:

- **Single-turn chat.** Ask a question, get an answer.
- **Multi-turn conversations.** Claude Code maintains context; the local model sees the full history.
- **Streaming responses.** Tokens appear as they're generated, just like cloud Claude.
- **Tool use, at the protocol level.** Claude Code can use its built-in tools (Read, Edit, Bash, etc.) and the local model receives tool definitions correctly. Whether the local model is good at using them depends on the model.
- **Multiple model swap.** Switch between locally-cached models without restarting your session.
- **Privacy-by-default.** The server only listens on `localhost` unless you tell it otherwise.

What doesn't work yet (planned for v1.5):

- Vision models (image content blocks).
- Tool calls from models that use a non-Anthropic format (some models output tool calls in their own native syntax — we don't yet translate those into Anthropic's format for Claude Code to recognize).
- Native Windows — runs on Windows through WSL2 since LiteRT-LM supports Linux. Native Windows tracks upstream LiteRT-LM's platform support.

## How to use it — five steps

Assumes you have a Mac or a Linux box. The whole thing takes about 10 minutes the first time, including downloading a model.

### Step 1 — Install LiteRT-LM

```bash
uv tool install litert-lm
```

If you don't have `uv`, install it first: `curl -LsSf https://astral.sh/uv/install.sh | sh`. (`uv` is a fast Python package manager.)

### Step 2 — Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

If you don't have `npm`, install Node.js first via your package manager (Homebrew, apt, etc.).

### Step 3 — Download a model

The smallest one that works well is Gemma-4-E2B-it (E2B = "Effective 2B parameters" — runs comfortably on a laptop):

```bash
litert-lm run --from-huggingface-repo=litert-community/Gemma-4-E2B-it \
  gemma-4-E2B-it.litertlm \
  --prompt "hello"
```

The first run downloads the model (~1-2 GB) and caches it locally. You'll get a one-line response from the model — that confirms the model loaded.

### Step 4 — Start the server

```bash
litert-lm serve --api anthropic \
  --model ~/.cache/huggingface/hub/models--litert-community--gemma-4-E2B-it-litert-lm/snapshots/*/gemma-4-E2B-it.litertlm
```

The server prints `Listening on http://127.0.0.1:9379`. Leave it running in this terminal.

### Step 5 — Point Claude Code at it

In a new terminal:

```bash
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any-value-the-server-ignores-it
claude --model local-model
```

Type a question. The response comes from your local model.

That's the whole flow. To go back to cloud Claude, `unset ANTHROPIC_BASE_URL` and run `claude` normally.

## Easier still: the Claude Code plugin

If five steps feel like too many, there's a companion Claude Code plugin that wraps all of it. After installing the plugin, you type **"set up litert-lm"** inside Claude Code and a guided skill walks you through the whole process — picks the model, starts the server, configures the env vars, runs a smoke test.

Plus four slash commands for ongoing use:

- `/litert-lm-start` — start the server with your last-used model
- `/litert-lm-stop` — stop the server
- `/litert-lm-status` — check if it's running and which model is loaded
- `/litert-lm-switch <model>` — swap to a different model
- `/litert-lm-config` — show the env vars Claude Code needs

See the plugin's README for installation.

## How fast is it really?

On an Apple M-series MacBook Pro with the Gemma-4-E2B-it model:

- **Simple prompt** ("what is 2+2?"): ~5 seconds end-to-end including Claude Code's overhead, tool-use planning, and the model's reasoning.
- **Steady-state output**: ~15-30 tokens/second once generation kicks in.
- **First-token latency**: under 1 second on warm runs, ~5 seconds on cold start (loading the model into memory).

These numbers vary by hardware and model size. A bigger model on the same laptop will be slower; a smaller model will be faster but less accurate. Gemma-4-E2B-it is a reasonable starting point for laptops.

## How does this compare to alternatives?

| | Cloud Claude | This integration |
|---|---|---|
| Setup | Already works | Easy (with the plugin) or 5 commands |
| Privacy | Nothing local | Local |
| Offline | No | Yes |
| Cost per use | Per-token | Free |
| Model quality | Best | Whatever you pick (LiteRT-LM catalog) |
| Tool use | Native | Native |
| Bundles into LiteRT-LM | N/A | Yes |

This integration addresses several engineering issues common in local-LLM bridges: a missing `count_tokens` endpoint that can degrade the server through 404 cascades, a 255-second client timeout caused by buffered tool arguments, and a lack of keepalive pings during long generations. We return a heuristic for the missing endpoint instead of 404'ing, stream tool args incrementally, and emit pings every ~10 seconds.

## When should I use cloud Claude vs local?

Use **cloud Claude** when you want the smartest answer: complex reasoning, novel architectures, debugging tricky bugs, anything where small differences in model quality matter.

Use **this local integration** when you care more about privacy, cost, latency, or offline access than peak quality: routine refactoring, doc-string generation, lint cleanups, code summaries, sample data generation, or just learning + experimenting.

A common pattern is to use both — keep cloud Claude for the hard tasks, route the rest to local.

## When NOT to use this

Be honest with yourself about the trade-off. **Use cloud Claude (not this) when:**

- You're debugging a hard, novel issue where every percentage point of model quality matters.
- You're doing multi-file refactoring across a sizeable codebase. Local models fall apart on long-context, cross-file reasoning faster than cloud models.
- You drag images into Claude Code for frontend work, screenshots, or UI debugging. Vision support is a v1.5 milestone.
- You need a tool-using agent loop (Read/Edit/Bash). LiteRT-LM-supported Gemma variants typically emit tool calls in their native format, which Claude Code doesn't yet recognise as `tool_use` blocks. The v1.5 translator closes this gap; until then, use cloud Claude for tool-heavy agentic work.
- You're on battery and want the laptop fans quiet — local inference will spin them up.
- You need a production-grade, soak-tested service. v1 is daily-driver-grade, not yet enterprise-grade.

## What about Anthropic blocking this?

Reasonable question. Short answer: very unlikely they'd intentionally block it.

The integration works by overriding the `ANTHROPIC_BASE_URL` environment variable. That's a standard feature in essentially every API SDK — it's how enterprise customers route traffic through corporate proxies, internal load balancers, on-prem gateways, and security inspection layers. Removing it would break large enterprise deployments that pay Anthropic real money. It's not going anywhere.

The real risk is *protocol drift* — Anthropic changing the wire format underneath in a way that requires a patch. Past changes have been incremental (new event types, new optional headers) and our handler tolerates unknown fields gracefully. If a future change is more invasive, the fix is a small open-source patch and a re-release. The repo's [issue tracker](https://github.com/managers-can-code/litert-lm-claude-code/issues) is the right place to surface symptoms.

Full discussion in [RISKS.md](RISKS.md#1-protocol-drift-the-foundational-risk).

## What about safety / jailbreaks / prompt injection?

The local model gets the same tool definitions and system prompt that Claude Code would normally send to Anthropic. Tool execution still happens client-side in Claude Code's sandbox with its permission model. The server doesn't add any tool-execution surface of its own. So the safety story is essentially "whatever Claude Code's safety story is" plus "the local model is whatever you downloaded."

The server only binds to `localhost` by default. If you want to expose it to your local network (for sharing with a teammate, say), use the `--host 0.0.0.0` flag and the `--bearer-token` flag for basic auth.

## How can I tell if it's working?

Three quick checks:

1. **Is the server up?** `curl http://localhost:9379/v1/models` should return a JSON with the loaded model.
2. **Does inference work?** `curl -X POST http://localhost:9379/v1/messages -H "Content-Type: application/json" -d '{"model":"local-model","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'` should return a model response.
3. **Does Claude Code see it?** With `ANTHROPIC_BASE_URL` set, `claude -p "hi" --model local-model` should print a model response.

If any of those fail, the [troubleshooting section in the README](README.md#troubleshooting) — and the companion plugin's troubleshoot skill — covers the common failure modes (port conflicts, model file not found, missing dependencies, etc.).

## Where does this go from here?

This is v1. Things planned for v1.5 (separate follow-up PR):

- Vision support (image content blocks for vision-capable models).
- Tool-format translation (so models that emit native tool-call syntax also work seamlessly with Claude Code).
- Live external-reference performance baseline on your hardware.
- Windows native support.

Once v1 lands upstream, you'll be able to install it from a regular `litert-lm` release rather than building from source. Until then, see the README for instructions on dropping the handler into a clone of the repo.

## TL;DR

It's a small server that lets Claude Code use a model running on your laptop instead of the cloud. Three commands to install, two more to start it. Privacy, offline, free per-token. Ship it via [google-ai-edge/LiteRT-LM](https://github.com/google-ai-edge/LiteRT-LM) when v1 merges; until then, it works against the released `litert-lm 0.10.1` wheel via the companion plugin or by dropping `serve_anthropic.py` into a local checkout.
