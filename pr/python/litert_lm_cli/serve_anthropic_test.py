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

"""Unit tests for the LiteRT-LM Anthropic Messages API handler.

These tests exercise the translator, error mapper, SSE encoder, auth
middleware, body-size limit and concurrency gate without spinning up a
real model. The C extension that backs ``litert_lm.Engine`` is replaced
by a ``MagicMock`` before the module under test is imported, mirroring
the stubbing approach used by ``serve_test.py``. The mocking happens at
import time at the top of this file because the ``litert_lm`` module
calls into the FFI as soon as it is imported.
"""

import io
import json
import pathlib
import sys
import threading
import time
from unittest import mock

from absl.testing import absltest
from absl.testing import parameterized

# 1. Mock the C++ extension specifically to prevent loading it.
# This MUST happen before importing anything from litert_lm.
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

# Ensure SamplerConfig and Tool are real lightweight stand-ins so the
# translator can construct them without touching the FFI.
mock_litert_lm.SamplerConfig = type("SamplerConfig", (), {
    "__init__": lambda self, **kwargs: setattr(self, "_kw", kwargs) or None,
})


class _StubTool:
  """Minimal stand-in for litert_lm.Tool used by the translator."""

  def get_tool_description(self):
    return {}

  def execute(self, param):
    raise NotImplementedError


mock_litert_lm.Tool = _StubTool
mock_litert_lm.Engine = mock_engine.Engine
mock_litert_lm.set_min_log_severity = mock_ffi.set_min_log_severity

# Mock model module too -- it imports litert_lm internally.
mock_model_mod = mock.Mock(spec_set=["Model"])
mock_model_mod.Model = mock.Mock(spec_set=["from_model_id"])
mock_model_mod.Model.from_model_id = mock.Mock()
sys.modules["litert_lm_cli.model"] = mock_model_mod

from litert_lm_cli import serve  # pylint: disable=g-import-not-at-top
from litert_lm_cli import serve_anthropic  # pylint: disable=g-import-not-at-top


_FIXTURE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / (
    "fixtures"
)


def _parse_sse_fixture(text: str):
  """Parses an SSE fixture into ``[(event_type, data_dict_or_str), ...]``.

  Records that fail to parse as JSON (e.g. truncated final chunk in the
  cancellation fixture) are returned with the raw string payload so
  callers can still assert on byte prefixes.
  """
  records = []
  for chunk in text.split("\n\n"):
    if not chunk.strip():
      continue
    lines = chunk.split("\n")
    event_type = None
    data_line = None
    for line in lines:
      if line.startswith("event: "):
        event_type = line[len("event: "):]
      elif line.startswith("data: "):
        data_line = line[len("data: "):]
    if event_type is None or data_line is None:
      continue
    try:
      records.append((event_type, json.loads(data_line)))
    except json.JSONDecodeError:
      records.append((event_type, data_line))
  return records


class _RecordingHandler:
  """Test double for AnthropicHandler used by _StreamState tests."""

  def __init__(self):
    self.wfile = io.BytesIO()

  def _send_sse(self, event_type, data):
    self.wfile.write(serve_anthropic.format_sse_event(event_type, data))


class FormatSseEventTest(parameterized.TestCase):
  """Byte-equality tests for :func:`format_sse_event`."""

  def test_simple_text_delta(self):
    out = serve_anthropic.format_sse_event(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "4"},
        },
    )
    self.assertEqual(
        out,
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","index":0,"delta":'
        b'{"type":"text_delta","text":"4"}}\n\n',
    )

  def test_ping_matches_anthropic_byte_format(self):
    # Anthropic's reference server emits ping with a space after the
    # colon. We match this byte-for-byte to keep fixture diffs clean.
    self.assertEqual(
        serve_anthropic.format_sse_event("ping", {"type": "ping"}),
        b'event: ping\ndata: {"type": "ping"}\n\n',
    )

  def test_message_stop(self):
    self.assertEqual(
        serve_anthropic.format_sse_event(
            "message_stop", {"type": "message_stop"}
        ),
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    )

  def test_uses_lf_not_crlf(self):
    out = serve_anthropic.format_sse_event(
        "message_stop", {"type": "message_stop"}
    )
    self.assertNotIn(b"\r", out)

  @parameterized.named_parameters(
      ("simple", "anthropic-sse-stream-1-simple.txt"),
      ("multi_turn", "anthropic-sse-stream-2-multi-turn.txt"),
      ("cancel", "anthropic-sse-stream-3-cancel.txt"),
      ("tool_use", "anthropic-sse-stream-4-tool-use.txt"),
  )
  def test_replays_each_fixture_event_byte_equal(self, fixture_name: str):
    """Each parseable SSE record in every fixture round-trips byte-equal."""
    path = _FIXTURE_DIR / fixture_name
    if not path.exists():
      self.skipTest(f"fixture {fixture_name} not present in this environment")
    text = path.read_text(encoding="utf-8")

    records = _parse_sse_fixture(text)
    self.assertNotEmpty(records)

    rebuilt = b""
    for event_type, payload in records:
      if isinstance(payload, str):
        # Truncated record (cancellation fixture). Reconstruct verbatim.
        rebuilt += (
            f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")
        )
      else:
        rebuilt += serve_anthropic.format_sse_event(event_type, payload)

    # The rebuilt stream must contain every parseable record from the
    # fixture in order, with Anthropic's exact compact-JSON encoding.
    for event_type, payload in records:
      if isinstance(payload, str):
        continue
      encoded = serve_anthropic.format_sse_event(event_type, payload)
      self.assertIn(
          encoded,
          rebuilt,
          msg=f"event {event_type} did not round-trip in {fixture_name}",
      )


class TranslateSystemTest(parameterized.TestCase):

  def test_none(self):
    self.assertIsNone(serve_anthropic.translate_system(None))

  def test_empty_string_normalized_to_none(self):
    self.assertIsNone(serve_anthropic.translate_system(""))

  def test_string_passthrough(self):
    self.assertEqual(
        serve_anthropic.translate_system("be helpful"), "be helpful"
    )

  def test_text_blocks_concatenated_with_newline(self):
    self.assertEqual(
        serve_anthropic.translate_system(
            [
                {"type": "text", "text": "be"},
                {"type": "text", "text": "helpful"},
            ]
        ),
        "be\nhelpful",
    )

  def test_non_text_block_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_system(
          [{"type": "image", "source": {"type": "url", "url": "x"}}]
      )

  def test_garbage_type_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_system(123)


class TranslateMessagesTest(parameterized.TestCase):

  def test_simple_user_string(self):
    history, last = serve_anthropic.translate_messages(
        [{"role": "user", "content": "hello"}]
    )
    self.assertEqual(history, [])
    self.assertEqual(
        last, {"role": "user", "content": [{"type": "text", "text": "hello"}]}
    )

  def test_multi_turn_history(self):
    history, last = serve_anthropic.translate_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "again"},
        ]
    )
    self.assertLen(history, 2)
    self.assertEqual(history[0]["role"], "user")
    self.assertEqual(history[1]["role"], "assistant")
    self.assertEqual(last["role"], "user")

  def test_adjacent_text_blocks_concatenated(self):
    _history, last = serve_anthropic.translate_messages(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "foo"},
                    {"type": "text", "text": "bar"},
                ],
            }
        ]
    )
    self.assertEqual(last["content"], [{"type": "text", "text": "foobar"}])

  def test_system_role_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_messages(
          [{"role": "system", "content": "hi"}]
      )

  def test_image_to_non_vision_model_rejected(self):
    with self.assertRaisesRegex(
        serve_anthropic.TranslationError, "image blocks"
    ):
      serve_anthropic.translate_messages(
          [
              {
                  "role": "user",
                  "content": [
                      {
                          "type": "image",
                          "source": {
                              "type": "base64",
                              "media_type": "image/png",
                              "data": "AA==",
                          },
                      }
                  ],
              }
          ],
          model_supports_vision=False,
      )

  def test_image_to_vision_model_passthrough(self):
    _history, last = serve_anthropic.translate_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "AA==",
                        },
                    }
                ],
            }
        ],
        model_supports_vision=True,
    )
    self.assertEqual(last["content"][0]["type"], "image")

  def test_tool_use_block_translated(self):
    _history, last = serve_anthropic.translate_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "get_weather",
                        "input": {"city": "SF"},
                    }
                ],
            }
        ]
    )
    self.assertEqual(last["role"], "assistant")
    self.assertEqual(
        last["tool_calls"],
        [
            {
                "id": "toolu_1",
                "function": {
                    "name": "get_weather",
                    "arguments": {"city": "SF"},
                },
            }
        ],
    )

  def test_tool_result_block_string_content(self):
    _history, last = serve_anthropic.translate_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "sunny, 68F",
                    }
                ],
            }
        ]
    )
    self.assertEqual(last["role"], "tool")
    self.assertEqual(last["content"][0]["response"], "sunny, 68F")
    self.assertEqual(last["content"][0]["tool_use_id"], "toolu_1")
    self.assertFalse(last["content"][0]["is_error"])

  def test_tool_result_block_array_content_and_is_error(self):
    _history, last = serve_anthropic.translate_messages(
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "is_error": True,
                        "content": [
                            {"type": "text", "text": "boom"},
                            {"type": "text", "text": " happened"},
                        ],
                    }
                ],
            }
        ]
    )
    self.assertTrue(last["content"][0]["is_error"])
    self.assertEqual(last["content"][0]["response"], "boom happened")

  def test_empty_messages_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_messages([])

  def test_unknown_block_type_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_messages(
          [{"role": "user", "content": [{"type": "audio"}]}]
      )


class TranslateToolsTest(parameterized.TestCase):

  def test_none(self):
    self.assertIsNone(serve_anthropic.translate_tools(None))

  def test_minimal_tool(self):
    tools = serve_anthropic.translate_tools(
        [{"name": "get_weather", "input_schema": {"type": "object"}}]
    )
    self.assertLen(tools, 1)
    desc = tools[0].get_tool_description()
    self.assertEqual(desc["function"]["name"], "get_weather")

  def test_missing_name_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_tools([{"input_schema": {}}])

  def test_non_array_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_tools({"name": "x"})


class TranslateSamplerTest(parameterized.TestCase):

  def test_no_overrides(self):
    cfg, stops = serve_anthropic.translate_sampler({})
    self.assertIsNone(cfg)
    self.assertEqual(stops, [])

  def test_temperature_out_of_range(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_sampler({"temperature": 5.0})

  def test_top_k_must_be_positive(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_sampler({"top_k": 0})

  def test_stop_sequences_too_long(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_sampler(
          {"stop_sequences": ["x" * 33]}
      )

  def test_stop_sequences_too_many(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_sampler(
          {"stop_sequences": ["a", "b", "c", "d", "e"]}
      )

  def test_valid_sampler(self):
    cfg, stops = serve_anthropic.translate_sampler(
        {"temperature": 0.5, "top_p": 0.9, "top_k": 40,
         "stop_sequences": ["END"]}
    )
    self.assertIsNotNone(cfg)
    self.assertEqual(stops, ["END"])


class TranslateModelTest(parameterized.TestCase):

  def setUp(self):
    super().setUp()
    serve._current_engine = None
    serve._current_model_id = None
    serve_anthropic._CONFIG.update(serve_anthropic._DEFAULTS)

  def test_strict_unknown_model_404(self):
    serve._current_model_id = "gemma3"
    with self.assertRaises(serve_anthropic.TranslationError) as cm:
      serve_anthropic.translate_model("claude-3-5-sonnet")
    self.assertEqual(cm.exception.http_status, 404)
    self.assertEqual(cm.exception.error_type, "not_found_error")

  def test_strict_match_returns_loaded(self):
    serve._current_model_id = "gemma3"
    self.assertEqual(serve_anthropic.translate_model("gemma3"), "gemma3")

  def test_accept_any_model_routes_through(self):
    serve._current_model_id = "gemma3"
    serve_anthropic._CONFIG["accept_any_model"] = True
    self.assertEqual(
        serve_anthropic.translate_model("claude-3-5-sonnet"), "gemma3"
    )

  def test_missing_model_rejected(self):
    with self.assertRaises(serve_anthropic.TranslationError):
      serve_anthropic.translate_model(None)


class MakeAnthropicErrorTest(parameterized.TestCase):

  @parameterized.named_parameters(
      ("invalid", "invalid_request_error", 400),
      ("auth", "authentication_error", 401),
      ("not_found", "not_found_error", 404),
      ("too_large", "request_too_large", 413),
      ("api", "api_error", 500),
      ("overloaded", "overloaded_error", 503),
  )
  def test_default_status_for_each_error_type(
      self, error_type: str, expected_status: int
  ):
    status, body = serve_anthropic.make_anthropic_error(error_type, "msg")
    self.assertEqual(status, expected_status)
    parsed = json.loads(body)
    self.assertEqual(parsed["type"], "error")
    self.assertEqual(parsed["error"]["type"], error_type)
    self.assertEqual(parsed["error"]["message"], "msg")

  def test_explicit_http_status_override(self):
    status, _body = serve_anthropic.make_anthropic_error(
        "api_error", "x", http_status=504
    )
    self.assertEqual(status, 504)

  def test_unknown_error_type_defaults_to_500(self):
    status, _body = serve_anthropic.make_anthropic_error(
        "no_such_thing", "x"
    )
    self.assertEqual(status, 500)


class _FakeWfile(io.BytesIO):
  closed_attr = False

  @property
  def closed(self):
    return self.closed_attr

  def flush(self):
    pass


def _build_handler(
    method: str,
    path: str,
    body: bytes = b"",
    headers=None,
):
  """Constructs an AnthropicHandler bypassing socket setup, for unit tests."""
  handler = serve_anthropic.AnthropicHandler.__new__(
      serve_anthropic.AnthropicHandler
  )
  handler.command = method
  handler.path = path
  # ``requestline`` and ``request_version`` are populated by
  # ``BaseHTTPRequestHandler.parse_request()`` in real traffic; the unit
  # tests bypass that path, so set them explicitly so ``log_request`` (which
  # ``send_response`` calls) does not crash with AttributeError.
  handler.requestline = f"{method} {path} HTTP/1.1"
  handler.request_version = "HTTP/1.1"
  handler.rfile = io.BytesIO(body)
  handler.wfile = _FakeWfile()
  handler.headers = _Headers(headers or {})
  handler.client_address = ("127.0.0.1", 0)
  handler.server = mock.Mock()
  handler._headers_buffer = []
  return handler


class _Headers:
  """Minimal http.client.HTTPMessage-like dict."""

  def __init__(self, mapping):
    self._mapping = {k.lower(): v for k, v in mapping.items()}

  def get(self, key, default=None):
    return self._mapping.get(key.lower(), default)


class HandlerAuthTest(parameterized.TestCase):

  def setUp(self):
    super().setUp()
    serve_anthropic._CONFIG.update(serve_anthropic._DEFAULTS)

  def test_no_token_configured_accepts_anything(self):
    handler = _build_handler("POST", "/v1/messages")
    self.assertTrue(handler._check_auth())

  def test_token_configured_rejects_missing(self):
    serve_anthropic._CONFIG["bearer_token"] = "secret"
    handler = _build_handler("POST", "/v1/messages")
    self.assertFalse(handler._check_auth())

  def test_token_configured_accepts_bearer(self):
    serve_anthropic._CONFIG["bearer_token"] = "secret"
    handler = _build_handler(
        "POST", "/v1/messages", headers={"Authorization": "Bearer secret"}
    )
    self.assertTrue(handler._check_auth())

  def test_token_configured_accepts_x_api_key(self):
    serve_anthropic._CONFIG["bearer_token"] = "secret"
    handler = _build_handler(
        "POST", "/v1/messages", headers={"X-Api-Key": "secret"}
    )
    self.assertTrue(handler._check_auth())

  def test_token_configured_rejects_wrong_bearer(self):
    serve_anthropic._CONFIG["bearer_token"] = "secret"
    handler = _build_handler(
        "POST", "/v1/messages", headers={"Authorization": "Bearer nope"}
    )
    self.assertFalse(handler._check_auth())


class BodyLimitTest(parameterized.TestCase):

  def setUp(self):
    super().setUp()
    serve_anthropic._CONFIG.update(serve_anthropic._DEFAULTS)

  def test_oversize_returns_413(self):
    serve_anthropic._CONFIG["max_request_bytes"] = 16
    big = b"x" * 1000
    handler = _build_handler(
        "POST",
        "/v1/messages",
        body=big,
        headers={"Content-Length": str(len(big))},
    )
    raw = handler._read_body()
    self.assertIsNone(raw)
    self.assertIn(b'"request_too_large"', handler.wfile.getvalue())


class CountTokensTest(parameterized.TestCase):

  def test_estimate_simple(self):
    handler = _build_handler(
        "POST",
        "/v1/messages/count_tokens",
        body=b'{"messages":[{"role":"user","content":"abcd"}]}',
        headers={"Content-Length": "47"},
    )
    handler._handle_count_tokens()
    out = handler.wfile.getvalue()
    self.assertIn(b'"input_tokens"', out)
    body_json = json.loads(out.split(b"\r\n\r\n")[-1])
    # 4 chars / 4 chars-per-token = 1 token.
    self.assertEqual(body_json["input_tokens"], 1)


class ListModelsTest(parameterized.TestCase):

  def setUp(self):
    super().setUp()
    serve._current_engine = None
    serve._current_model_id = None

  def test_no_model_loaded(self):
    handler = _build_handler("GET", "/v1/models")
    handler._handle_list_models()
    body = handler.wfile.getvalue().split(b"\r\n\r\n")[-1]
    parsed = json.loads(body)
    self.assertEqual(parsed["data"], [])

  def test_with_loaded_model(self):
    serve._current_model_id = "gemma3"
    handler = _build_handler("GET", "/v1/models")
    handler._handle_list_models()
    body = handler.wfile.getvalue().split(b"\r\n\r\n")[-1]
    parsed = json.loads(body)
    self.assertLen(parsed["data"], 1)
    self.assertEqual(parsed["data"][0]["id"], "gemma3")
    self.assertEqual(parsed["data"][0]["display_name"], "gemma3")
    self.assertEqual(parsed["data"][0]["type"], "model")


class HeaderInjectionTest(parameterized.TestCase):

  def test_crlf_in_value_detected(self):
    self.assertTrue(
        serve_anthropic._looks_like_header_injection("foo\r\nX-Bad: 1")
    )

  def test_clean_value_passes(self):
    self.assertFalse(serve_anthropic._looks_like_header_injection("foo"))


class ConcurrencyGateTest(parameterized.TestCase):

  def setUp(self):
    super().setUp()
    serve_anthropic._CONFIG.update(serve_anthropic._DEFAULTS)
    serve_anthropic._CONCURRENCY_GATE = threading.Semaphore(1)

  def test_overloaded_when_gate_full(self):
    # Exhaust the semaphore from another thread.
    self.assertTrue(serve_anthropic._CONCURRENCY_GATE.acquire(blocking=False))
    try:
      acquired = serve_anthropic._CONCURRENCY_GATE.acquire(blocking=False)
      self.assertFalse(acquired)
      status, body = serve_anthropic.make_anthropic_error(
          "overloaded_error", "server is at concurrency cap"
      )
      self.assertEqual(status, 503)
      self.assertIn(b'"overloaded_error"', body)
    finally:
      serve_anthropic._CONCURRENCY_GATE.release()


class StructuredLoggingTest(parameterized.TestCase):

  def setUp(self):
    super().setUp()
    serve_anthropic._CONFIG.update(serve_anthropic._DEFAULTS)
    serve_anthropic._CONFIG["bearer_token"] = "supersecret"

  def test_bearer_token_never_appears_in_logs(self):
    handler = _build_handler(
        "POST",
        "/v1/messages",
        headers={"Authorization": "Bearer supersecret"},
    )
    with self.assertLogs(
        "litert_lm_cli.serve_anthropic", level="INFO"
    ) as cm:
      handler._log_request("rid_test", 200, time.monotonic() - 0.001)
    joined = "\n".join(cm.output)
    self.assertIn("rid_test", joined)
    self.assertNotIn("supersecret", joined)


class StreamStateTest(parameterized.TestCase):
  """Confirms _StreamState emits the right block_start/stop pairs."""

  def test_text_then_tool_use_sequence(self):
    h = _RecordingHandler()
    state = serve_anthropic._StreamState(model_id="gemma3")
    state.open_text_block(h)
    h._send_sse(
        "content_block_delta",
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Let me check."},
        },
    )
    state.close_current_block(h)
    state.open_tool_use_block(
        h,
        {"id": "toolu_x", "function": {"name": "f", "arguments": {}}},
    )
    state.close_current_block(h)

    out = h.wfile.getvalue()
    self.assertIn(b'"index":0', out)
    self.assertIn(b'"index":1', out)
    self.assertIn(b'content_block_start', out)
    self.assertIn(b'content_block_stop', out)
    self.assertIn(b'tool_use', out)


if __name__ == "__main__":
  absltest.main()
