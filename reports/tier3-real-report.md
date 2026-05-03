# Tier 3 Real-Hardware Verification Report

- Date: 2026-05-01T18:56:14Z
- Model: `/Users/ramiyengar/.cache/huggingface/hub/models--litert-community--gemma-4-E2B-it-litert-lm/snapshots/84b6978eff6e4eea02825bc2ee4ea48579f13109/gemma-4-E2B-it.litertlm`
- Port: 9379
- Claude Code: 2.1.116 (Claude Code)
- LiteRT-LM Python package: 

## Step 1 — /v1/models

```json
{
  "data": [
    {
      "id": "local-model",
      "display_name": "local-model",
      "type": "model",
      "created_at": "2026-05-01T18:56:14Z"
    }
  ],
  "first_id": null,
  "last_id": null
}
```

## Step 2 — Non-streaming /v1/messages

```json
{
  "id": "msg_25ca7a826c964c4091aa1bf6",
  "type": "message",
  "role": "assistant",
  "model": "local-model",
  "content": [
    {
      "type": "text",
      "text": "2 + 2 = **4**"
    }
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 0,
    "output_tokens": 4
  }
}
```

## Step 3 — Streaming /v1/messages

```
event: message_start
data: {"type":"message_start","message":{"id":"msg_e1699a747eb5474bbcd66e15","type":"message","role":"assistant","model":"local-model","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":0,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: ping
data: {"type": "ping"}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"2 + 2 = **4**"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":4}}

event: message_stop
data: {"type":"message_stop"}

```

## Step 4 — Real Claude Code → our server

Duration: 4658 ms

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "api_error_status": null,
  "duration_ms": 4104,
  "duration_api_ms": 6024,
  "num_turns": 1,
  "result": "2+2 is 4.",
  "stop_reason": "end_turn",
  "session_id": "b7053333-45c4-4c84-8cda-29c2d20f304d",
  "total_cost_usd": 0.000115,
  "usage": {
    "input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 3,
    "server_tool_use": {
      "web_search_requests": 0,
      "web_fetch_requests": 0
    },
    "service_tier": "standard",
    "cache_creation": {
      "ephemeral_1h_input_tokens": 0,
      "ephemeral_5m_input_tokens": 0
    },
    "inference_geo": "",
    "iterations": [],
    "speed": "standard"
  },
  "modelUsage": {
    "claude-haiku-4-5-20251001": {
      "inputTokens": 0,
      "outputTokens": 14,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 0,
      "webSearchRequests": 0,
      "costUSD": 0.00007,
      "contextWindow": 200000,
      "maxOutputTokens": 32000
    },
    "local-model": {
      "inputTokens": 0,
      "outputTokens": 3,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 0,
      "webSearchRequests": 0,
      "costUSD": 0.000045,
      "contextWindow": 200000,
      "maxOutputTokens": 32000
    }
  },
  "permission_denials": [],
  "terminal_reason": "completed",
  "fast_mode_state": "off",
  "uuid": "437af809-d10c-4a53-87fd-a89c3542d329"
}
```

## Step 5 — Tool use (Read /etc/hostname)

Duration: 4729 ms

```json
{
  "type": "result",
  "subtype": "success",
  "is_error": false,
  "api_error_status": null,
  "duration_ms": 4227,
  "duration_api_ms": 6196,
  "num_turns": 1,
  "result": "<|tool_call>call:read\ninput: /etc/hostname",
  "stop_reason": "end_turn",
  "session_id": "44987152-9b88-4964-9c5b-02b77e68dfaa",
  "total_cost_usd": 0.00023,
  "usage": {
    "input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": 11,
    "server_tool_use": {
      "web_search_requests": 0,
      "web_fetch_requests": 0
    },
    "service_tier": "standard",
    "cache_creation": {
      "ephemeral_1h_input_tokens": 0,
      "ephemeral_5m_input_tokens": 0
    },
    "inference_geo": "",
    "iterations": [],
    "speed": "standard"
  },
  "modelUsage": {
    "claude-haiku-4-5-20251001": {
      "inputTokens": 0,
      "outputTokens": 13,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 0,
      "webSearchRequests": 0,
      "costUSD": 0.000065,
      "contextWindow": 200000,
      "maxOutputTokens": 32000
    },
    "local-model": {
      "inputTokens": 0,
      "outputTokens": 11,
      "cacheReadInputTokens": 0,
      "cacheCreationInputTokens": 0,
      "webSearchRequests": 0,
      "costUSD": 0.000165,
      "contextWindow": 200000,
      "maxOutputTokens": 32000
    }
  },
  "permission_denials": [],
  "terminal_reason": "completed",
  "fast_mode_state": "off",
  "uuid": "da57b883-f46a-403a-aa53-062042c2b32f"
}
```

## Server log (last 100 lines)

```
Bad (wrong case): {"title": "Fix Login Button On Mobile"}<turn|>
<|turn>user
what is 2+2?<turn|>
<|turn>model

I0000 00:00:1777661776.882086 2547899 session_basic.cc:485] RunDecodeSync
unhandled exception in do_POST
Traceback (most recent call last):
  File "/Users/ramiyengar/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/python/litert_lm_cli/serve_anthropic.py", line 964, in do_POST
    status = self._handle_messages(request_id)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/ramiyengar/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/python/litert_lm_cli/serve_anthropic.py", line 1247, in _handle_messages
    ctx = _create_conversation_with_fallbacks(
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/ramiyengar/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/python/litert_lm_cli/serve_anthropic.py", line 910, in _create_conversation_with_fallbacks
    return engine.create_conversation(**kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
RuntimeError: python/litert_lm/litert_lm.cc:627: operator(): FAILED_PRECONDITION: A session already exists. Only one session is supported at a time. Please delete the existing session before creating a new one.
I0000 00:00:1777661777.916739 2547966 model_data_processor_factory.cc:361] Creating Gemma4DataProcessor
W0000 00:00:1777661777.916794 2547966 mel_filterbank.cc:137] Missing 10 bands  starting at 0 in mel-frequency design. Perhaps too many channels or not enough frequency resolution in spectrum. (fft_length: 257 sample_rate: 16000 mel_channel_count: 128 lower_frequency_limit: 0 upper_frequency_limit: 8000
I0000 00:00:1777661777.917520 2547966 session_basic.cc:299] RunPrefill: 
I0000 00:00:1777661777.917535 2547966 session_basic.cc:301] <|turn>system
x-anthropic-billing-header: cc_version=2.1.116.de8; cc_entrypoint=sdk-cli; cch=aed43;
You are a Claude agent, built on Anthropic's Claude Agent SDK.
You are Claude Code, Anthropic's official CLI for Claude.

CWD: /Users/ramiyengar
Date: 2026-05-01<turn|>
<|turn>user
<system-reminder>
As you answer the user's questions, you can use the following context:
# currentDate
Today's date is 2026-05-01.

      IMPORTANT: this context may or may not be relevant to your tasks. You should not respond to this context unless it is highly relevant to your task.
</system-reminder>

what is 2+2?<turn|>
<|turn>model

I0000 00:00:1777661779.330015 2547966 session_basic.cc:485] RunDecodeSync
I0000 00:00:1777661780.131097 2548077 model_data_processor_factory.cc:361] Creating Gemma4DataProcessor
W0000 00:00:1777661780.131143 2548077 mel_filterbank.cc:137] Missing 10 bands  starting at 0 in mel-frequency design. Perhaps too many channels or not enough frequency resolution in spectrum. (fft_length: 257 sample_rate: 16000 mel_channel_count: 128 lower_frequency_limit: 0 upper_frequency_limit: 8000
I0000 00:00:1777661780.131481 2548077 session_basic.cc:299] RunPrefill: 
I0000 00:00:1777661780.131489 2548077 session_basic.cc:301] <|turn>system
x-anthropic-billing-header: cc_version=2.1.116.e40; cc_entrypoint=sdk-cli; cch=948b0;
You are a Claude agent, built on Anthropic's Claude Agent SDK.
Generate a concise, sentence-case title (3-7 words) that captures the main topic or goal of this coding session. The title should be clear enough that the user recognizes the session in a list. Use sentence case: capitalize only the first word and proper nouns.

Return JSON with a single "title" field.

Good examples:
{"title": "Fix login button on mobile"}
{"title": "Add OAuth authentication"}
{"title": "Debug failing CI tests"}
{"title": "Refactor API client error handling"}

Bad (too vague): {"title": "Code changes"}
Bad (too long): {"title": "Investigate and fix the issue where the login button does not respond on mobile devices"}
Bad (wrong case): {"title": "Fix Login Button On Mobile"}<turn|>
<|turn>user
use your Read tool to read /etc/hostname and tell me what it says<turn|>
<|turn>model

I0000 00:00:1777661781.505796 2548077 session_basic.cc:485] RunDecodeSync
unhandled exception in do_POST
Traceback (most recent call last):
  File "/Users/ramiyengar/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/python/litert_lm_cli/serve_anthropic.py", line 964, in do_POST
    status = self._handle_messages(request_id)
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/ramiyengar/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/python/litert_lm_cli/serve_anthropic.py", line 1247, in _handle_messages
    ctx = _create_conversation_with_fallbacks(
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/Users/ramiyengar/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/pr/python/litert_lm_cli/serve_anthropic.py", line 910, in _create_conversation_with_fallbacks
    return engine.create_conversation(**kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
RuntimeError: python/litert_lm/litert_lm.cc:627: operator(): FAILED_PRECONDITION: A session already exists. Only one session is supported at a time. Please delete the existing session before creating a new one.
I0000 00:00:1777661782.645687 2548130 model_data_processor_factory.cc:361] Creating Gemma4DataProcessor
W0000 00:00:1777661782.645736 2548130 mel_filterbank.cc:137] Missing 10 bands  starting at 0 in mel-frequency design. Perhaps too many channels or not enough frequency resolution in spectrum. (fft_length: 257 sample_rate: 16000 mel_channel_count: 128 lower_frequency_limit: 0 upper_frequency_limit: 8000
I0000 00:00:1777661782.646550 2548130 session_basic.cc:299] RunPrefill: 
I0000 00:00:1777661782.646569 2548130 session_basic.cc:301] <|turn>system
x-anthropic-billing-header: cc_version=2.1.116.e40; cc_entrypoint=sdk-cli; cch=199a2;
You are a Claude agent, built on Anthropic's Claude Agent SDK.
You are Claude Code, Anthropic's official CLI for Claude.

CWD: /Users/ramiyengar
Date: 2026-05-01<turn|>
<|turn>user
<system-reminder>
As you answer the user's questions, you can use the following context:
# currentDate
Today's date is 2026-05-01.

      IMPORTANT: this context may or may not be relevant to your tasks. You should not respond to this context unless it is highly relevant to your task.
</system-reminder>

use your Read tool to read /etc/hostname and tell me what it says<turn|>
<|turn>model

I0000 00:00:1777661784.037541 2548130 session_basic.cc:485] RunDecodeSync
```

## Verdict

Inspect Step 4 — if Claude Code returned a JSON envelope with a non-empty
assistant message, the protocol path against the real model is working.
If it returned an error, see the server log above for the failure mode.
