# Copyright 2026 The ODML Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Integration tests for the LiteRT-LM Anthropic Messages API handler.

These tests stand up a real :class:`http.server.HTTPServer` on an
ephemeral port, point an :mod:`httpx` client at it, and exercise the
HTTP layer + translator + SSE encoder end-to-end. They do **not** load
a real model -- the underlying ``litert_lm.Engine`` and its
``Conversation`` are replaced by lightweight in-process stubs so the
tests run without the C++ FFI. This mirrors the approach used by the
existing :mod:`litert_lm_cli.serve_test` unit tests.

The point of these tests is to catch regressions in the HTTP wire
format, header handling, error mapping, and stream framing -- the bits
that an actual LiteRT-LM model would not exercise.
"""

import http.server
import json
import socket
import sys
import threading
import time
from unittest import mock

from absl.testing import absltest
import httpx

# 1. Mock the C++ extension before importing anything from litert_lm.
mock_ffi = mock.MagicMock()
mock_ffi.LogSeverity = type("LogSeverity", (), {})
mock_ffi.set_min_log_severity = mock.Mock()

mock_benchmark = mock.MagicMock()
mock_benchmark.Benchmark = type("Benchmark", (), {})

mock_conversation = mock.MagicMock()
mock_conversation.Conversation = type("Conversation", (), {})

mock_engine = mock.MagicMock()
mock_engine.Engine = mock.Mock()

mock_session = mock.MagicMock()
mock_session.Session = type("Session", (), {})

sys.modules["litert_lm._ffi"] = mock_ffi
sys.modules["litert_lm.benchmark"] = mock_benchmark
sys.modules["litert_lm.conversation"] = mock_conversation
sys.modules["litert_lm.engine"] = mock_engine
sys.modules["litert_lm.session"] = mock_session

import litert_lm as mock_litert_lm  # pylint: disable=g-import-not-at-top

mock_litert_lm.SamplerConfig = type("SamplerConfig", (), {
    "__init__": lambda self, **kwargs: setattr(self, "_kw", kwargs) or None,
})


class _StubTool:
  """Stand-in for litert_lm.Tool used by the translator."""

  def get_tool_description(self):
    return {}

  def execute(self, param):
    raise NotImplementedError


mock_litert_lm.Tool = _StubTool

# Mock model module so we never touch the filesystem looking for weights.
mock_model_mod = mock.Mock(spec_set=["Model"])
mock_model_mod.Model = mock.Mock(spec_set=["from_model_id"])
mock_model_mod.Model.from_model_id = mock.Mock()
sys.modules["litert_lm_cli.model"] = mock_model_mod

from litert_lm_cli import serve  # pylint: disable=g-import-not-at-top
from litert_lm_cli import serve_anthropic  # pylint: disable=g-import-not-at-top


# ---------------------------------------------------------------------------
# Stub Conversation / Engine
# ---------------------------------------------------------------------------


class _StubConversation:
  """In-process fake of :class:`litert_lm.Conversation` for integration tests.

  Configurable to yield a sequence of pre-baked chunks for streaming, or
  return a pre-baked response for non-streaming. Cancellation is
  signalled via a threading.Event so tests can assert clean teardown.
  """

  def __init__(
      self,
      *,
      response=None,
      stream_chunks=None,
      delay_secs: float = 0.0,
  ):
    self._response = response
    self._chunks = list(stream_chunks or [])
    self._delay = delay_secs
    self.cancelled = threading.Event()
    self.last_message = None

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    return False

  def send_message(self, message):
    self.last_message = message
    return self._response or {"content": [{"type": "text", "text": ""}]}

  def send_message_async(self, message):
    self.last_message = message
    for chunk in self._chunks:
      if self.cancelled.is_set():
        break
      if self._delay:
        time.sleep(self._delay)
      yield chunk

  def cancel_process(self):
    self.cancelled.set()


class _StubEngine:
  """In-process fake of :class:`litert_lm.Engine`.

  ``next_conversation`` controls what ``create_conversation`` returns
  for the next call; tests set it before issuing each request.
  """

  def __init__(self):
    self.vision_backend = None
    self.supports_tools = True
    self.next_conversation = None
    self.created = []

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    return False

  def create_conversation(self, **kwargs):
    self.created.append(kwargs)
    if self.next_conversation is None:
      raise AssertionError("test forgot to set next_conversation")
    return self.next_conversation


def _free_port() -> int:
  """Returns an available TCP port on localhost."""
  with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.bind(("localhost", 0))
    return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Test base class
# ---------------------------------------------------------------------------


class _ServerTestCase(absltest.TestCase):
  """Spins up the AnthropicHandler on an ephemeral port for each test."""

  MODEL_ID = "gemma3"

  def setUp(self):
    super().setUp()

    # Reset module state so tests are isolated.
    serve._current_engine = None
    serve._current_model_id = None
    serve_anthropic._CONFIG.update(serve_anthropic._DEFAULTS)
    serve_anthropic._CONCURRENCY_GATE = threading.Semaphore(
        serve_anthropic._DEFAULTS["max_concurrent"]
    )

    # Inject the stub engine into the global slot read by translate_model
    # and serve.get_engine.
    self.engine = _StubEngine()
    serve._current_engine = self.engine
    serve._current_model_id = self.MODEL_ID

    # Patch serve.get_engine to return our stub regardless of model id.
    self._get_engine_patch = mock.patch.object(
        serve, "get_engine", return_value=self.engine
    )
    self._get_engine_patch.start()

    self.port = _free_port()
    self.server = http.server.HTTPServer(
        ("localhost", self.port), serve_anthropic.AnthropicHandler
    )
    self.server_thread = threading.Thread(
        target=self.server.serve_forever, daemon=True
    )
    self.server_thread.start()
    self.base_url = f"http://localhost:{self.port}"
    self.client = httpx.Client(base_url=self.base_url, timeout=10.0)

  def tearDown(self):
    self.client.close()
    self.server.shutdown()
    self.server.server_close()
    self.server_thread.join(timeout=5.0)
    self._get_engine_patch.stop()
    super().tearDown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class NonStreamingMessagesTest(_ServerTestCase):

  def test_single_turn_returns_anthropic_shape(self):
    self.engine.next_conversation = _StubConversation(
        response={"content": [{"type": "text", "text": "hi there"}]}
    )

    resp = self.client.post(
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    self.assertEqual(resp.status_code, 200)
    body = resp.json()
    self.assertEqual(body["type"], "message")
    self.assertEqual(body["role"], "assistant")
    self.assertEqual(body["model"], self.MODEL_ID)
    self.assertEqual(body["stop_reason"], "end_turn")
    [block] = body["content"]
    self.assertEqual(block, {"type": "text", "text": "hi there"})
    self.assertIn("usage", body)


class StreamingMessagesTest(_ServerTestCase):

  def test_streaming_event_sequence(self):
    self.engine.next_conversation = _StubConversation(
        stream_chunks=[
            {"content": [{"type": "text", "text": "Hello "}]},
            {"content": [{"type": "text", "text": "world"}]},
        ]
    )

    with self.client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 64,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as resp:
      self.assertEqual(resp.status_code, 200)
      raw = resp.read().decode("utf-8")

    events = [
        line[len("event: "):]
        for line in raw.split("\n")
        if line.startswith("event: ")
    ]
    self.assertEqual(events[0], "message_start")
    self.assertIn("content_block_start", events)
    self.assertIn("content_block_delta", events)
    self.assertIn("content_block_stop", events)
    self.assertEqual(events[-2], "message_delta")
    self.assertEqual(events[-1], "message_stop")
    self.assertIn('"text":"Hello "', raw)
    self.assertIn('"text":"world"', raw)

  def test_multi_turn_history_threaded(self):
    self.engine.next_conversation = _StubConversation(
        stream_chunks=[
            {"content": [{"type": "text", "text": "ok"}]},
        ]
    )
    resp = self.client.post(
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 64,
            "stream": True,
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "still there?"},
            ],
        },
    )
    self.assertEqual(resp.status_code, 200)
    [created_kwargs] = self.engine.created
    history = created_kwargs["messages"]
    # Two prior turns get threaded as history; the final turn is sent
    # via send_message_async.
    self.assertLen(history, 2)
    self.assertEqual(history[0]["role"], "user")
    self.assertEqual(history[1]["role"], "assistant")


class ToolUseRoundTripTest(_ServerTestCase):

  def test_tool_use_then_tool_result(self):
    # First request: model wants to call a tool.
    self.engine.next_conversation = _StubConversation(
        stream_chunks=[
            {"content": [{"type": "text", "text": "Let me check."}]},
            {
                "tool_calls": [
                    {
                        "id": "toolu_abc",
                        "function": {
                            "name": "get_weather",
                            "arguments": {"city": "SF"},
                        },
                    }
                ]
            },
        ]
    )

    resp = self.client.post(
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 64,
            "stream": True,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "look up the weather",
                    "input_schema": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                }
            ],
            "messages": [{"role": "user", "content": "weather in SF?"}],
        },
    )
    self.assertEqual(resp.status_code, 200)
    raw = resp.text
    self.assertIn('"tool_use"', raw)
    self.assertIn('"input_json_delta"', raw)
    self.assertIn('"toolu_abc"', raw)

    # Second request: client returns the tool_result.
    self.engine.next_conversation = _StubConversation(
        stream_chunks=[
            {"content": [{"type": "text", "text": "Sunny, 68F."}]},
        ]
    )
    resp2 = self.client.post(
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 64,
            "stream": True,
            "messages": [
                {"role": "user", "content": "weather in SF?"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_abc",
                            "name": "get_weather",
                            "input": {"city": "SF"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_abc",
                            "content": "sunny, 68F",
                        }
                    ],
                },
            ],
        },
    )
    self.assertEqual(resp2.status_code, 200)
    self.assertIn('Sunny, 68F.', resp2.text)


class BadRequestTest(_ServerTestCase):

  def test_missing_max_tokens_returns_400(self):
    resp = self.client.post(
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    self.assertEqual(resp.status_code, 400)
    body = resp.json()
    self.assertEqual(body["type"], "error")
    self.assertEqual(body["error"]["type"], "invalid_request_error")
    self.assertIn("max_tokens", body["error"]["message"])

  def test_unknown_model_returns_404_in_strict_mode(self):
    resp = self.client.post(
        "/v1/messages",
        json={
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    self.assertEqual(resp.status_code, 404)
    body = resp.json()
    self.assertEqual(body["error"]["type"], "not_found_error")

  def test_invalid_json_returns_400(self):
    resp = self.client.post(
        "/v1/messages",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    self.assertEqual(resp.status_code, 400)


class CountTokensEndpointTest(_ServerTestCase):

  def test_returns_input_tokens(self):
    resp = self.client.post(
        "/v1/messages/count_tokens",
        json={
            "model": self.MODEL_ID,
            "messages": [{"role": "user", "content": "abcdefgh"}],
        },
    )
    self.assertEqual(resp.status_code, 200)
    body = resp.json()
    # 8 chars / 4 chars-per-token = 2 tokens.
    self.assertEqual(body["input_tokens"], 2)


class ListModelsEndpointTest(_ServerTestCase):

  def test_returns_loaded_model(self):
    resp = self.client.get("/v1/models")
    self.assertEqual(resp.status_code, 200)
    body = resp.json()
    self.assertLen(body["data"], 1)
    entry = body["data"][0]
    self.assertEqual(entry["id"], self.MODEL_ID)
    self.assertEqual(entry["display_name"], self.MODEL_ID)
    self.assertEqual(entry["type"], "model")
    self.assertIn("created_at", entry)


class StreamingCancellationTest(_ServerTestCase):

  def test_client_disconnect_cancels_conversation(self):
    # A slow stream so the client can disconnect mid-flight.
    conv = _StubConversation(
        stream_chunks=[
            {"content": [{"type": "text", "text": "chunk1"}]},
            {"content": [{"type": "text", "text": "chunk2"}]},
            {"content": [{"type": "text", "text": "chunk3"}]},
            {"content": [{"type": "text", "text": "chunk4"}]},
        ],
        delay_secs=0.2,
    )
    self.engine.next_conversation = conv

    with self.client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 64,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as resp:
      self.assertEqual(resp.status_code, 200)
      # Read just enough to start the stream, then bail out -- httpx
      # closes the connection on context exit.
      iterator = resp.iter_bytes()
      next(iterator, None)

    # Give the server a moment to notice the broken pipe and cancel.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not conv.cancelled.is_set():
      time.sleep(0.05)
    self.assertTrue(
        conv.cancelled.is_set(),
        "server did not cancel the conversation after client disconnect",
    )


class AuthEnforcementTest(_ServerTestCase):

  def setUp(self):
    super().setUp()
    serve_anthropic._CONFIG["bearer_token"] = "topsecret"

  def test_missing_bearer_returns_401(self):
    self.engine.next_conversation = _StubConversation(
        response={"content": [{"type": "text", "text": ""}]}
    )
    resp = self.client.post(
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    self.assertEqual(resp.status_code, 401)
    self.assertEqual(resp.json()["error"]["type"], "authentication_error")

  def test_correct_bearer_passes_through(self):
    self.engine.next_conversation = _StubConversation(
        response={"content": [{"type": "text", "text": "ok"}]}
    )
    resp = self.client.post(
        "/v1/messages",
        json={
            "model": self.MODEL_ID,
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer topsecret"},
    )
    self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
  absltest.main()
