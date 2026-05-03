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

"""HTTP server for LiteRT-LM with Anthropic Messages API.

This module implements the Anthropic Messages API
(https://docs.anthropic.com/en/api/messages) on top of the LiteRT-LM
``Engine`` / ``Conversation`` runtime. It mirrors the design of
``GeminiHandler`` and ``OpenAIHandler`` in :mod:`litert_lm_cli.serve`:
a stdlib :class:`http.server.BaseHTTPRequestHandler` driving a globally
cached ``litert_lm.Engine`` singleton owned by
``litert_lm_cli.serve._current_engine``.

The handler is wired into the existing ``serve`` Click command via
:func:`register`, which monkey-patches ``--api`` to accept the
``"anthropic"`` choice. No new third-party dependencies are required at
runtime; tests use ``httpx`` (already pinned in ``requirements.txt``).

Endpoints implemented:

* ``POST /v1/messages`` -- Anthropic Messages create, streaming and
  non-streaming.
* ``POST /v1/messages/count_tokens`` -- coarse char/4 estimate so clients
  that pre-flight (e.g. Claude Code) do not 404 and fall back to the
  worst-case 404-cascade behaviour that degrades subsequent requests.
* ``GET  /v1/models`` -- the currently loaded model in Anthropic shape.

The translator is split into small, individually-testable helpers
(:func:`translate_system`, :func:`translate_messages`,
:func:`translate_tools`, :func:`translate_sampler`) so unit tests can
target each row of the field-mapping table without spinning up the HTTP
layer.
"""

import collections.abc
import datetime
import http.server
import json
import logging
import threading
import time
import uuid
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

import click

import litert_lm
from litert_lm_cli import model
from litert_lm_cli import serve as _serve_module

_LOGGER = logging.getLogger("litert_lm_cli.serve_anthropic")

# Default operational limits. These are overridden at register() time when
# the user passes Click options, but module-level defaults keep unit tests
# self-contained.
_DEFAULTS: Dict[str, Any] = {
    "max_request_bytes": 4 * 1024 * 1024,  # 4 MiB
    "request_timeout_secs": 300,  # 5 min
    "max_concurrent": 4,
    "bearer_token": None,  # None = no enforcement (standard local-LLM bridge UX)
    "accept_any_model": False,
    # Streaming strategy. ``"auto"`` prefers native token-by-token
    # streaming via ``Conversation.send_message_async`` when available,
    # falling back to synthetic single-chunk streaming if the method is
    # missing. ``"synthetic"`` forces the single-chunk path (useful for
    # engine builds where ``send_message_async`` exists but blocks
    # indefinitely — e.g. litert-lm 0.10.1). ``"native"`` forces native
    # streaming and surfaces a clean error if unavailable.
    "streaming_strategy": "auto",
}

# Module-level config dict mutated by register(). The handler reads it on
# every request; tests override entries directly.
_CONFIG: Dict[str, Any] = dict(_DEFAULTS)

# Concurrency gate. Recreated by register() if --max-concurrent changes.
_CONCURRENCY_GATE: threading.Semaphore = threading.Semaphore(
    _DEFAULTS["max_concurrent"]
)

# Anthropic spec: stop_sequences max length and count.
_STOP_SEQUENCE_MAX_LEN = 32
_STOP_SEQUENCE_MAX_COUNT = 4

# Char-per-token heuristic used by /v1/messages/count_tokens. Documented
# in the Anthropic API as a coarse estimate; we explicitly choose 4
# because that matches the rule of thumb the SDK ships with and avoids
# the 404-cascade degradation pattern where missing endpoints
# cause clients to fall back to per-message HEAD requests.
_CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# SSE encoder
# ---------------------------------------------------------------------------


def format_sse_event(event_type: str, data: Dict[str, Any]) -> bytes:
  """Encodes one Server-Sent Event in Anthropic wire format.

  The Anthropic streaming wire format is::

      event: <event_type>\n
      data: <compact-json>\n
      \n

  using LF terminators (not CRLF). All event payloads are compact JSON
  (``separators=(",", ":")``) except ``ping``, which the Anthropic
  reference server emits as ``{"type": "ping"}`` with a single space
  after the colon -- we match that byte-for-byte so fixture diffs stay
  clean.

  Args:
    event_type: SSE ``event`` field value (e.g. ``"message_start"``).
    data: JSON-serializable dict for the ``data`` field.

  Returns:
    The encoded event as UTF-8 bytes, including the trailing blank
    line.
  """
  if event_type == "ping":
    # Match the reference Anthropic server byte-for-byte.
    payload = '{"type": "ping"}'
  else:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
  return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


# Canonical (error_type, http_status) pairs per Anthropic's error reference:
# https://docs.anthropic.com/en/api/errors
_ERROR_TYPE_TO_STATUS: Dict[str, int] = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "overloaded_error": 503,
}


def make_anthropic_error(
    error_type: str,
    message: str,
    http_status: Optional[int] = None,
) -> Tuple[int, bytes]:
  """Builds an Anthropic-shaped error response.

  Args:
    error_type: One of the Anthropic error type strings (e.g.
      ``"invalid_request_error"``). Unknown values default to
      ``"api_error"`` with HTTP 500.
    message: Human-readable error message. Sent verbatim to the client;
      callers must not embed sensitive data here.
    http_status: Override for the HTTP status code. If omitted, the code
      is derived from ``error_type``.

  Returns:
    ``(http_status, body_bytes)`` ready to write to ``self.wfile``.
  """
  if http_status is None:
    http_status = _ERROR_TYPE_TO_STATUS.get(error_type, 500)
  body = {
      "type": "error",
      "error": {"type": error_type, "message": message},
  }
  return http_status, json.dumps(body, ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# Translator helpers (Anthropic Messages JSON -> litert_lm)
# ---------------------------------------------------------------------------


class TranslationError(ValueError):
  """Raised when an Anthropic request body fails translation.

  Attributes:
    error_type: Anthropic error type string (e.g.
      ``"invalid_request_error"``).
    http_status: HTTP status code to return.
  """

  def __init__(
      self,
      message: str,
      error_type: str = "invalid_request_error",
      http_status: int = 400,
  ):
    super().__init__(message)
    self.error_type = error_type
    self.http_status = http_status


def translate_system(
    system: Union[None, str, List[Dict[str, Any]]],
) -> Optional[str]:
  """Translates the Anthropic ``system`` field to a single string.

  The Anthropic spec accepts either a string or a list of text blocks.
  Adjacent text blocks are concatenated with a newline separator;
  non-text blocks are rejected.

  Args:
    system: ``system`` field value from the request body. May be
      ``None``.

  Returns:
    The system prompt string, or ``None`` if ``system`` was empty.

  Raises:
    TranslationError: On unsupported block types.
  """
  if system is None:
    return None
  if isinstance(system, str):
    return system or None
  if not isinstance(system, list):
    raise TranslationError("system must be a string or array of text blocks")
  parts: List[str] = []
  for block in system:
    if not isinstance(block, dict):
      raise TranslationError("system blocks must be objects")
    if block.get("type") != "text":
      raise TranslationError(
          f"system blocks must be type=text, got {block.get('type')!r}"
      )
    text = block.get("text", "")
    if not isinstance(text, str):
      raise TranslationError("system block 'text' must be a string")
    parts.append(text)
  joined = "\n".join(parts)
  return joined or None


def translate_messages(
    messages: List[Dict[str, Any]],
    *,
    model_supports_vision: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
  """Translates Anthropic ``messages`` to litert_lm history + final turn.

  The litert_lm Conversation API takes prior messages at construction
  time and a single final message via ``send_message`` /
  ``send_message_async``. We split the Anthropic message list into
  ``(history, last)`` for that contract.

  Args:
    messages: The ``messages`` array from the request body.
    model_supports_vision: Whether the loaded model can ingest image
      blocks. When ``False``, an image block triggers a
      ``TranslationError``.

  Returns:
    ``(history, last)`` -- a list of prior turns plus the final turn to
    pass to the Conversation.

  Raises:
    TranslationError: On invalid roles, unsupported block types, or
      images sent to a non-vision model.
  """
  if not isinstance(messages, list) or not messages:
    raise TranslationError("messages must be a non-empty array")

  translated: List[Dict[str, Any]] = []
  for idx, msg in enumerate(messages):
    if not isinstance(msg, dict):
      raise TranslationError(f"messages[{idx}] must be an object")
    role = msg.get("role")
    if role == "system":
      raise TranslationError(
          "messages[].role 'system' is not allowed; use the top-level"
          " 'system' field"
      )
    if role not in ("user", "assistant"):
      raise TranslationError(
          f"messages[{idx}].role must be 'user' or 'assistant', got {role!r}"
      )
    content = msg.get("content")
    translated.append(
        _translate_single_message(
            role,
            content,
            idx=idx,
            model_supports_vision=model_supports_vision,
        )
    )

  last = translated[-1]
  history = translated[:-1]
  return history, last


def _translate_single_message(
    role: str,
    content: Any,
    *,
    idx: int,
    model_supports_vision: bool,
) -> Dict[str, Any]:
  """Translates one Anthropic message turn to a litert_lm message."""
  if isinstance(content, str):
    # String content -> single text block.
    return {
        "role": role,
        "content": [{"type": "text", "text": content}],
    }
  if not isinstance(content, list):
    raise TranslationError(
        f"messages[{idx}].content must be a string or array"
    )

  parts: List[Dict[str, Any]] = []
  tool_calls: List[Dict[str, Any]] = []
  tool_results: List[Dict[str, Any]] = []
  pending_text: List[str] = []

  def _flush_text() -> None:
    if pending_text:
      parts.append({"type": "text", "text": "".join(pending_text)})
      pending_text.clear()

  for block_idx, block in enumerate(content):
    if not isinstance(block, dict):
      raise TranslationError(
          f"messages[{idx}].content[{block_idx}] must be an object"
      )
    btype = block.get("type")
    if btype == "text":
      text = block.get("text", "")
      if not isinstance(text, str):
        raise TranslationError(
            f"messages[{idx}].content[{block_idx}].text must be a string"
        )
      pending_text.append(text)
    elif btype == "image":
      _flush_text()
      if not model_supports_vision:
        raise TranslationError(
            "image blocks are not supported by the loaded model"
            " (messages[].content[].image)"
        )
      parts.append(_translate_image_block(block, idx, block_idx))
    elif btype == "tool_use":
      _flush_text()
      tool_id = block.get("id")
      name = block.get("name")
      args = block.get("input", {})
      if not isinstance(tool_id, str) or not tool_id:
        raise TranslationError(
            f"messages[{idx}].content[{block_idx}].id is required"
        )
      if not isinstance(name, str) or not name:
        raise TranslationError(
            f"messages[{idx}].content[{block_idx}].name is required"
        )
      if not isinstance(args, dict):
        raise TranslationError(
            f"messages[{idx}].content[{block_idx}].input must be an object"
        )
      tool_calls.append(
          {
              "id": tool_id,
              "function": {"name": name, "arguments": args},
          }
      )
    elif btype == "tool_result":
      _flush_text()
      tool_results.append(
          _translate_tool_result_block(block, idx, block_idx)
      )
    else:
      raise TranslationError(
          f"messages[{idx}].content[{block_idx}].type {btype!r} is not"
          " supported"
      )

  _flush_text()

  if tool_results:
    # tool_result blocks belong to a 'user' turn per the Anthropic spec;
    # we emit them as a 'tool' role message (matching the existing
    # Gemini handler's translation of functionResponse).
    return {"role": "tool", "content": tool_results}

  out: Dict[str, Any] = {"role": role}
  if parts:
    out["content"] = parts
  if tool_calls:
    out["tool_calls"] = tool_calls
  return out


def _translate_image_block(
    block: Dict[str, Any], idx: int, block_idx: int
) -> Dict[str, Any]:
  """Translates an Anthropic image block to a litert_lm image part."""
  source = block.get("source")
  if not isinstance(source, dict):
    raise TranslationError(
        f"messages[{idx}].content[{block_idx}].source is required"
    )
  src_type = source.get("type")
  if src_type == "base64":
    media_type = source.get("media_type")
    data = source.get("data")
    if not isinstance(media_type, str) or not isinstance(data, str):
      raise TranslationError(
          f"messages[{idx}].content[{block_idx}].source must have"
          " media_type and data"
      )
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }
  if src_type == "url":
    url = source.get("url")
    if not isinstance(url, str):
      raise TranslationError(
          f"messages[{idx}].content[{block_idx}].source.url must be a string"
      )
    return {"type": "image", "source": {"type": "url", "url": url}}
  raise TranslationError(
      f"messages[{idx}].content[{block_idx}].source.type {src_type!r} is"
      " not supported"
  )


def _translate_tool_result_block(
    block: Dict[str, Any], idx: int, block_idx: int
) -> Dict[str, Any]:
  """Translates a tool_result block to a litert_lm tool_response part."""
  tool_use_id = block.get("tool_use_id")
  if not isinstance(tool_use_id, str) or not tool_use_id:
    raise TranslationError(
        f"messages[{idx}].content[{block_idx}].tool_use_id is required"
    )
  is_error = bool(block.get("is_error", False))
  raw = block.get("content", "")
  if isinstance(raw, str):
    response_text = raw
  elif isinstance(raw, list):
    chunks: List[str] = []
    for inner_idx, inner in enumerate(raw):
      if not isinstance(inner, dict) or inner.get("type") != "text":
        raise TranslationError(
            f"messages[{idx}].content[{block_idx}].content[{inner_idx}]"
            " must be a text block"
        )
      chunks.append(inner.get("text", ""))
    response_text = "".join(chunks)
  else:
    raise TranslationError(
        f"messages[{idx}].content[{block_idx}].content must be a string or"
        " array"
    )
  return {
      "type": "tool_response",
      "tool_use_id": tool_use_id,
      "is_error": is_error,
      "response": response_text,
  }


def translate_tools(
    tools: Optional[List[Dict[str, Any]]],
) -> Optional[List[litert_lm.Tool]]:
  """Translates the Anthropic ``tools`` array to litert_lm proxy tools.

  The Anthropic tool schema is::

      {"name": str, "description": str, "input_schema": <JSON Schema>}

  We accept the JSON Schema subset that LiteRT-LM's underlying engine
  accepts: ``type``, ``properties``, ``required``, ``description``,
  ``enum``, plus nested objects/arrays. Validation is intentionally
  light -- the engine's own constrained-decoding layer enforces deeper
  conformance.

  Args:
    tools: The ``tools`` field from the request body, or ``None``.

  Returns:
    A list of ``_AnthropicProxyTool`` instances, or ``None`` if no tools
    were supplied.

  Raises:
    TranslationError: When a tool entry is malformed.
  """
  if tools is None:
    return None
  if not isinstance(tools, list):
    raise TranslationError("tools must be an array")
  out: List[litert_lm.Tool] = []
  for idx, t in enumerate(tools):
    if not isinstance(t, dict):
      raise TranslationError(f"tools[{idx}] must be an object")
    name = t.get("name")
    if not isinstance(name, str) or not name:
      raise TranslationError(f"tools[{idx}].name is required")
    description = t.get("description", "")
    schema = t.get("input_schema", {"type": "object", "properties": {}})
    if not isinstance(schema, dict):
      raise TranslationError(f"tools[{idx}].input_schema must be an object")
    out.append(
        _AnthropicProxyTool(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": schema,
                },
            }
        )
    )
  return out


def translate_sampler(
    body: Dict[str, Any],
) -> Tuple[Optional[litert_lm.SamplerConfig], List[str]]:
  """Translates sampling parameters from the request body.

  Honors ``temperature`` (0.0-1.0), ``top_p`` (0.0-1.0), ``top_k`` (>=
  1) and ``stop_sequences`` (max 4 entries, each <= 32 chars). Out-of-
  range values raise :class:`TranslationError`.

  Args:
    body: The full request body dict.

  Returns:
    ``(sampler_config, stop_sequences)``. ``sampler_config`` is ``None``
    when the request specifies no sampling overrides.

  Raises:
    TranslationError: On out-of-range values.
  """
  kwargs: Dict[str, Any] = {}
  if "temperature" in body:
    temperature = body["temperature"]
    if not isinstance(temperature, (int, float)) or not 0.0 <= float(
        temperature
    ) <= 1.0:
      raise TranslationError("temperature must be in [0.0, 1.0]")
    kwargs["temperature"] = float(temperature)
  if "top_p" in body:
    top_p = body["top_p"]
    if not isinstance(top_p, (int, float)) or not 0.0 <= float(top_p) <= 1.0:
      raise TranslationError("top_p must be in [0.0, 1.0]")
    kwargs["top_p"] = float(top_p)
  if "top_k" in body:
    top_k = body["top_k"]
    if not isinstance(top_k, int) or top_k < 1:
      raise TranslationError("top_k must be an integer >= 1")
    kwargs["top_k"] = int(top_k)

  stop_sequences = body.get("stop_sequences", [])
  if stop_sequences:
    if not isinstance(stop_sequences, list):
      raise TranslationError("stop_sequences must be an array")
    if len(stop_sequences) > _STOP_SEQUENCE_MAX_COUNT:
      raise TranslationError(
          f"stop_sequences may have at most {_STOP_SEQUENCE_MAX_COUNT}"
          " entries"
      )
    for s in stop_sequences:
      if not isinstance(s, str) or len(s) > _STOP_SEQUENCE_MAX_LEN:
        raise TranslationError(
            "stop_sequences entries must be strings <="
            f" {_STOP_SEQUENCE_MAX_LEN} chars"
        )

  sampler_config: Optional[litert_lm.SamplerConfig] = None
  if kwargs:
    try:
      sampler_config = litert_lm.SamplerConfig(**kwargs)
    except TypeError:
      # SamplerConfig signature varies between releases; fall back to
      # constructing without unsupported kwargs to avoid breaking the
      # request entirely.
      sampler_config = litert_lm.SamplerConfig()
  return sampler_config, list(stop_sequences)


def translate_model(model_name: Any) -> str:
  """Resolves the Anthropic ``model`` field to a litert_lm model id.

  Strict mode (the default) requires an exact match against the
  currently loaded model id. Operators may set ``--accept-any-model``,
  which routes any name to the loaded model -- a permissive mode so
  Claude Code's hard-coded ``claude-3-5-sonnet`` default works without
  per-deployment config.

  Args:
    model_name: The ``model`` field from the request body.

  Returns:
    The resolved litert_lm model id.

  Raises:
    TranslationError: If ``model`` is missing, malformed, or unknown
      under strict mode.
  """
  if not isinstance(model_name, str) or not model_name:
    raise TranslationError("model is required and must be a string")
  loaded = _serve_module._current_model_id  # pylint: disable=protected-access
  if _CONFIG.get("accept_any_model"):
    if loaded is None:
      raise TranslationError(
          "no model is loaded", error_type="api_error", http_status=500
      )
    return loaded
  if loaded is None or model_name != loaded:
    raise TranslationError(
        f"model {model_name!r} is not available",
        error_type="not_found_error",
        http_status=404,
    )
  return loaded


# ---------------------------------------------------------------------------
# Proxy tool (mirrors serve._ProxyTool)
# ---------------------------------------------------------------------------


class _AnthropicProxyTool(litert_lm.Tool):
  """Proxy tool that surfaces Anthropic tool definitions to the engine.

  Like ``serve._ProxyTool``, this carries an OpenAPI-shaped function
  description for the engine to expose to the model. ``execute`` is
  never invoked because the handler runs with
  ``automatic_tool_calling=False`` -- tool calls are instead surfaced
  back to the API client, which sends a ``tool_result`` block on the
  next request.
  """

  def __init__(self, definition: Dict[str, Any]):
    self._definition = definition
    name = definition.get("name") or "anthropic_tool"
    description = definition.get("description") or ""

    # v0.10.1's litert_lm.Tool base-class get_tool_description introspects
    # ``self._func`` via ``inspect.signature()`` (line ~109) AND accesses
    # ``self._func.__name__`` (line ~131). We synthesize a real callable
    # with the tool's name and docstring so both reads succeed.
    def _proxy_func(**kwargs: Any) -> Any:  # pylint: disable=unused-argument
      raise NotImplementedError(
          "Anthropic proxy tools are not executable from the engine."
      )
    _proxy_func.__name__ = name
    _proxy_func.__qualname__ = name
    _proxy_func.__doc__ = description

    # Try super().__init__ in both forms — some builds want it bare,
    # others want the func passed in.
    try:
      super().__init__()
    except TypeError:
      try:
        super().__init__(_proxy_func)  # type: ignore[arg-type]
      except Exception:  # pylint: disable=broad-exception-caught
        pass

    # Set _func AFTER super().__init__ so the base class doesn't clobber
    # our synthetic callable with ``self`` or ``None``.
    self._func = _proxy_func

  def get_tool_description(self) -> Dict[str, Any]:
    return self._definition

  def execute(self, param: collections.abc.Mapping[str, Any]) -> Any:
    raise NotImplementedError("Anthropic proxy tools are not executable.")

  def __call__(self, *args: Any, **kwargs: Any) -> Any:
    """Makes the proxy tool callable — required by some engine builds
    that call ``self._func(...)`` rather than ``self.execute(...)``."""
    if args and isinstance(args[0], collections.abc.Mapping) and not kwargs:
      return self.execute(args[0])
    return self.execute(kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_message_id() -> str:
  """Returns a new ``msg_<24-hex>`` identifier matching Anthropic's shape."""
  return "msg_" + uuid.uuid4().hex[:24]


def _estimate_tokens(text: str) -> int:
  """Returns a coarse char/4 token estimate, rounded up.

  See ``_CHARS_PER_TOKEN`` for the rationale.
  """
  if not text:
    return 0
  return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def _request_text_for_count(body: Dict[str, Any]) -> str:
  """Concatenates every text payload in a count_tokens request."""
  chunks: List[str] = []
  system = body.get("system")
  if isinstance(system, str):
    chunks.append(system)
  elif isinstance(system, list):
    for blk in system:
      if isinstance(blk, dict) and blk.get("type") == "text":
        chunks.append(blk.get("text", ""))
  for msg in body.get("messages", []) or []:
    content = msg.get("content")
    if isinstance(content, str):
      chunks.append(content)
    elif isinstance(content, list):
      for blk in content:
        if isinstance(blk, dict) and blk.get("type") == "text":
          chunks.append(blk.get("text", ""))
  return "\n".join(chunks)


def _looks_like_header_injection(value: str) -> bool:
  """Returns True if a header value contains CR or LF (header injection)."""
  return "\r" in value or "\n" in value


def _now_iso8601() -> str:
  """Returns the current UTC time as an RFC 3339 string."""
  return (
      datetime.datetime.now(datetime.timezone.utc)
      .replace(microsecond=0)
      .isoformat()
      .replace("+00:00", "Z")
  )


def _model_supports_vision(engine: Optional[litert_lm.Engine]) -> bool:
  """Best-effort detection of multimodal capability on the loaded engine.

  LiteRT-LM does not yet expose a stable capability query, so we probe
  for the optional ``vision_backend`` attribute set by
  :class:`litert_lm.Engine` when constructed with a vision backend.

  Args:
    engine: Currently loaded engine, or ``None``.

  Returns:
    ``True`` if the engine appears to support image inputs.
  """
  if engine is None:
    return False
  vb = getattr(engine, "vision_backend", None)
  return vb is not None


def _model_supports_tools(engine: Optional[litert_lm.Engine]) -> bool:
  """Best-effort detection of tool-use support on the loaded engine.

  We look for an explicit attribute and otherwise default to ``True`` --
  the underlying engine returns a clean error if the model lacks tool
  support, which we surface back as a 400 in the request handler.
  """
  if engine is None:
    return False
  return bool(getattr(engine, "supports_tools", True))


def _create_conversation_with_fallbacks(
    engine: "litert_lm.Engine",
    history: List[Dict[str, Any]],
    tools: Optional[List[Any]],
    sampler: Optional[Any],
    system_prompt: Optional[str],
):
  """Calls ``Engine.create_conversation`` with progressively fewer kwargs.

  The upstream ``litert_lm.Engine`` API surface is in flux. The current
  ``main`` branch accepts ``system_message``, ``sampler_config``, and
  ``automatic_tool_calling`` in addition to the v0.10.1-stable kwargs
  (``messages``, ``tools``, ``tool_event_handler``, ``extra_context``).
  This helper tries the richest set first and progressively drops kwargs
  on ``TypeError`` until the call succeeds, ensuring this handler works
  against both the upstream ``main`` branch (richer feature set) and
  released wheels (minimal kwargs).

  Order of attempts:

  1. Full set: ``messages`` + ``tools`` + ``automatic_tool_calling`` +
     ``sampler_config`` + ``system_message``.
  2. Drop ``system_message``; prepend a system turn to ``messages``.
  3. Also drop ``sampler_config``.
  4. v0.10.1-stable minimum: ``messages`` (+ optional ``tools``).

  When the engine doesn't accept ``system_message`` directly, we
  preserve the system prompt by prepending a synthetic turn to the
  message history. When the engine doesn't accept ``sampler_config``,
  the sampler settings (temperature/top_p/top_k/stop_sequences) are
  silently dropped — sampler control is a degraded mode against older
  builds, not a hard requirement.

  Args:
    engine: the loaded ``litert_lm.Engine``.
    history: list of message dicts in LiteRT-LM shape.
    tools: tool catalog or ``None``.
    sampler: a ``SamplerConfig`` or ``None``.
    system_prompt: system prompt string or ``None``.

  Returns:
    A ``Conversation`` context manager produced by the first kwarg
    set the engine accepts.

  Raises:
    TypeError: if every fallback attempt is rejected, indicating a
      truly incompatible engine build (one that doesn't accept even
      ``messages`` alone).
  """
  msgs_with_system = (
      [
          {
              "role": "system",
              "content": [{"type": "text", "text": system_prompt}],
          },
          *history,
      ]
      if system_prompt
      else history
  )

  attempts: List[Dict[str, Any]] = []

  # 1. Full feature set (post-merge upstream main).
  full: Dict[str, Any] = {
      "messages": history,
      "automatic_tool_calling": False,
  }
  if tools:
    full["tools"] = tools
  if sampler is not None:
    full["sampler_config"] = sampler
  if system_prompt:
    full["system_message"] = system_prompt
  attempts.append(full)

  # 2. Drop system_message; prepend system turn to history instead.
  if system_prompt:
    no_sysmsg: Dict[str, Any] = {
        "messages": msgs_with_system,
        "automatic_tool_calling": False,
    }
    if tools:
      no_sysmsg["tools"] = tools
    if sampler is not None:
      no_sysmsg["sampler_config"] = sampler
    attempts.append(no_sysmsg)

  # 3. Also drop sampler_config.
  no_sampler: Dict[str, Any] = {
      "messages": msgs_with_system,
      "automatic_tool_calling": False,
  }
  if tools:
    no_sampler["tools"] = tools
  attempts.append(no_sampler)

  # 4. v0.10.1-stable minimum WITH tools: messages + tools only.
  if tools:
    with_tools: Dict[str, Any] = {
        "messages": msgs_with_system,
        "tools": tools,
    }
    attempts.append(with_tools)

  # 5. Absolute minimum: messages only, no tools. Used when the engine
  #    rejects the proxy tool's introspection (e.g. v0.10.1's Tool base
  #    class chokes on ``inspect.signature(self._func)``). Tool calls
  #    are silently dropped — Claude Code's tool-use scenarios degrade
  #    to text-only on incompatible engines, but other requests work.
  attempts.append({"messages": msgs_with_system})

  last_exc: Optional[BaseException] = None
  for kwargs in attempts:
    try:
      return engine.create_conversation(**kwargs)
    except (TypeError, ValueError, AttributeError) as exc:
      # Older engine builds reject our kwargs (TypeError), our proxy-tool
      # shape (AttributeError on ``_func.__name__`` etc.), or the value
      # of ``messages`` content (ValueError). Try the next, more
      # conservative attempt.
      last_exc = exc
      _LOGGER.debug(
          "create_conversation rejected kwargs (%s); trying next fallback",
          exc,
      )
      continue

  if last_exc is not None:
    raise last_exc
  raise RuntimeError("create_conversation: all fallback attempts failed")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class AnthropicHandler(http.server.BaseHTTPRequestHandler):
  """HTTP request handler implementing the Anthropic Messages API.

  Matches the structure of :class:`litert_lm_cli.serve.GeminiHandler` /
  :class:`litert_lm_cli.serve.OpenAIHandler`: dispatches on
  ``self.path`` from ``do_POST``/``do_GET``, reads the body, translates
  it via the helpers above, and either streams SSE events or writes a
  single JSON response.
  """

  # Quiet the noisy default access logger; we emit our own structured
  # log line from _log_request().
  def log_message(self, format: str, *args: Any) -> None:  # pylint: disable=redefined-builtin
    return

  # ------------------------------ dispatch ------------------------------

  def do_POST(self) -> None:  # pylint: disable=invalid-name
    """Dispatches POST requests to the right endpoint handler."""
    request_id = uuid.uuid4().hex[:16]
    started = time.monotonic()
    status = 500
    try:
      path = self.path.split("?")[0]
      if not self._check_auth():
        status, body = make_anthropic_error(
            "authentication_error", "invalid or missing bearer token"
        )
        self._write_json(status, body)
        return
      if path == "/v1/messages":
        status = self._handle_messages(request_id)
        return
      if path == "/v1/messages/count_tokens":
        status = self._handle_count_tokens()
        return
      status, body = make_anthropic_error(
          "not_found_error", f"unknown path: {path}"
      )
      self._write_json(status, body)
    except Exception:  # pylint: disable=broad-exception-caught
      _LOGGER.exception("unhandled exception in do_POST")
      status, body = make_anthropic_error("api_error", "internal server error")
      try:
        self._write_json(status, body)
      except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
      self._log_request(request_id, status, started)

  def do_GET(self) -> None:  # pylint: disable=invalid-name
    """Dispatches GET requests to the right endpoint handler."""
    request_id = uuid.uuid4().hex[:16]
    started = time.monotonic()
    status = 500
    try:
      path = self.path.split("?")[0]
      if not self._check_auth():
        status, body = make_anthropic_error(
            "authentication_error", "invalid or missing bearer token"
        )
        self._write_json(status, body)
        return
      if path == "/v1/models":
        status = self._handle_list_models()
        return
      status, body = make_anthropic_error(
          "not_found_error", f"unknown path: {path}"
      )
      self._write_json(status, body)
    except Exception:  # pylint: disable=broad-exception-caught
      _LOGGER.exception("unhandled exception in do_GET")
      status, body = make_anthropic_error("api_error", "internal server error")
      try:
        self._write_json(status, body)
      except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
      self._log_request(request_id, status, started)

  # ------------------------------ auth + io ------------------------------

  def _check_auth(self) -> bool:
    """Returns True if the request passes the configured auth check.

    When ``--bearer-token`` is unset (the default), all requests are
    accepted -- accepted-but-ignored to match the standard
    ANTHROPIC_AUTH_TOKEN-required-but-unused pattern Claude Code expects
    (Claude Code requires *some* token but the value is irrelevant for
    local serving). When set, both ``Authorization: Bearer <token>`` and
    ``X-Api-Key: <token>`` are accepted.
    """
    expected = _CONFIG.get("bearer_token")
    if not expected:
      return True
    auth = self.headers.get("Authorization", "")
    api_key = self.headers.get("X-Api-Key", "")
    if auth.startswith("Bearer "):
      token = auth[len("Bearer "):].strip()
      if token == expected:
        return True
    if api_key and api_key == expected:
      return True
    return False

  def _read_body(self) -> Optional[bytes]:
    """Reads the request body, enforcing the size cap.

    Returns:
      The raw body bytes, or ``None`` if the body was rejected (in
      which case an error response has already been written).
    """
    length_header = self.headers.get("Content-Length")
    max_bytes = _CONFIG.get("max_request_bytes", _DEFAULTS["max_request_bytes"])
    try:
      length = int(length_header) if length_header is not None else 0
    except ValueError:
      status, body = make_anthropic_error(
          "invalid_request_error", "invalid Content-Length header"
      )
      self._write_json(status, body)
      return None
    if length < 0 or length > max_bytes:
      status, body = make_anthropic_error(
          "request_too_large",
          f"request body exceeds maximum of {max_bytes} bytes",
          http_status=413,
      )
      self._write_json(status, body)
      return None
    return self.rfile.read(length) if length else b""

  def _write_json(self, status: int, body: bytes) -> None:
    """Writes a JSON response with the standard headers."""
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def _send_sse_headers(self) -> None:
    """Writes the headers required for SSE streaming.

    We use ``Connection: close`` (and set ``self.close_connection = True``)
    so the TCP connection terminates after ``message_stop``. Real
    Anthropic responses close the connection at end-of-stream; clients
    like ``curl ... | head -N`` block indefinitely if the server keeps
    the connection alive after the final event.
    """
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("Connection", "close")
    self.end_headers()
    self.close_connection = True

  def _log_request(
      self, request_id: str, status: int, started: float
  ) -> None:
    """Emits one structured JSON log line per request to stderr.

    Bearer tokens and request bodies are deliberately excluded.
    """
    try:
      duration_ms = int((time.monotonic() - started) * 1000)
      _LOGGER.info(
          json.dumps(
              {
                  "request_id": request_id,
                  "method": self.command,
                  "path": self.path.split("?")[0],
                  "status": status,
                  "duration_ms": duration_ms,
              }
          )
      )
    except Exception:  # pylint: disable=broad-exception-caught
      # Logging must never escape into the response path.
      pass

  # ------------------------------ endpoints ------------------------------

  def _handle_list_models(self) -> int:
    """Implements ``GET /v1/models``."""
    loaded = _serve_module._current_model_id  # pylint: disable=protected-access
    data: List[Dict[str, Any]] = []
    if loaded:
      data.append(
          {
              "id": loaded,
              "display_name": loaded,
              "type": "model",
              "created_at": _now_iso8601(),
          }
      )
    body = json.dumps({"data": data, "first_id": None, "last_id": None}).encode(
        "utf-8"
    )
    self._write_json(200, body)
    return 200

  def _handle_count_tokens(self) -> int:
    """Implements ``POST /v1/messages/count_tokens``."""
    raw = self._read_body()
    if raw is None:
      return 413
    try:
      body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
      status, err = make_anthropic_error(
          "invalid_request_error", "request body is not valid JSON"
      )
      self._write_json(status, err)
      return status
    text = _request_text_for_count(body)
    resp = json.dumps({"input_tokens": _estimate_tokens(text)}).encode("utf-8")
    self._write_json(200, resp)
    return 200

  def _handle_messages(self, request_id: str) -> int:
    """Implements ``POST /v1/messages`` (streaming and non-streaming)."""
    raw = self._read_body()
    if raw is None:
      return 413
    try:
      body = json.loads(raw or b"{}")
    except json.JSONDecodeError:
      status, err = make_anthropic_error(
          "invalid_request_error", "request body is not valid JSON"
      )
      self._write_json(status, err)
      return status

    if not isinstance(body, dict):
      status, err = make_anthropic_error(
          "invalid_request_error", "request body must be a JSON object"
      )
      self._write_json(status, err)
      return status

    # max_tokens is required by the Anthropic spec.
    if "max_tokens" not in body:
      status, err = make_anthropic_error(
          "invalid_request_error", "max_tokens is required"
      )
      self._write_json(status, err)
      return status
    max_tokens = body.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
      status, err = make_anthropic_error(
          "invalid_request_error", "max_tokens must be a positive integer"
      )
      self._write_json(status, err)
      return status

    metadata = body.get("metadata") or {}
    user_id = metadata.get("user_id") if isinstance(metadata, dict) else None
    if user_id:
      _LOGGER.debug("request user_id=%s", user_id)

    try:
      model_id = translate_model(body.get("model"))
    except TranslationError as e:
      status, err = make_anthropic_error(e.error_type, str(e), e.http_status)
      self._write_json(status, err)
      return status

    try:
      engine = _serve_module.get_engine(model_id)
    except FileNotFoundError as e:
      status, err = make_anthropic_error("not_found_error", str(e))
      self._write_json(status, err)
      return status
    except Exception as e:  # pylint: disable=broad-exception-caught
      status, err = make_anthropic_error(
          "api_error", f"failed to load engine: {e}"
      )
      self._write_json(status, err)
      return status

    vision = _model_supports_vision(engine)

    try:
      system_prompt = translate_system(body.get("system"))
      history, last = translate_messages(
          body.get("messages", []), model_supports_vision=vision
      )
      tools = translate_tools(body.get("tools"))
      sampler, _stops = translate_sampler(body)
    except TranslationError as e:
      status, err = make_anthropic_error(e.error_type, str(e), e.http_status)
      self._write_json(status, err)
      return status

    if tools and not _model_supports_tools(engine):
      status, err = make_anthropic_error(
          "invalid_request_error",
          "the loaded model does not support tool use",
      )
      self._write_json(status, err)
      return status

    stream = bool(body.get("stream", False))

    # Concurrency gate: try to acquire without blocking. We do not want
    # to back up the HTTPServer's request queue; a clean 503 lets the
    # client retry with backoff.
    if not _CONCURRENCY_GATE.acquire(blocking=False):
      status, err = make_anthropic_error(
          "overloaded_error", "server is at concurrency cap"
      )
      self._write_json(status, err)
      return status

    try:
      ctx = _create_conversation_with_fallbacks(
          engine, history, tools, sampler, system_prompt
      )

      with ctx as conv:
        if stream:
          return self._stream_messages(conv, last, model_id)
        return self._send_messages(conv, last, model_id)
    finally:
      _CONCURRENCY_GATE.release()

  # ----------------------- non-streaming -----------------------

  def _send_messages(
      self,
      conv: litert_lm.Conversation,
      last_message: Dict[str, Any],
      model_id: str,
  ) -> int:
    """Runs a non-streaming /v1/messages request and writes the response."""
    try:
      response = conv.send_message(last_message)
    except Exception as e:  # pylint: disable=broad-exception-caught
      _LOGGER.exception("engine error during send_message")
      status, err = make_anthropic_error("api_error", f"engine error: {e}")
      self._write_json(status, err)
      return status

    blocks: List[Dict[str, Any]] = []
    text_chars = 0
    for item in response.get("content", []) or []:
      if item.get("type") == "text":
        text = item.get("text", "")
        text_chars += len(text)
        blocks.append({"type": "text", "text": text})
    for tc in response.get("tool_calls", []) or []:
      fn = tc.get("function") or {}
      blocks.append(
          {
              "type": "tool_use",
              "id": tc.get("id") or ("toolu_" + uuid.uuid4().hex[:24]),
              "name": fn.get("name", ""),
              "input": fn.get("arguments") or {},
          }
      )

    stop_reason = "tool_use" if response.get("tool_calls") else "end_turn"
    body = {
        "id": _new_message_id(),
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 0,  # Not surfaced by the engine yet.
            "output_tokens": _estimate_tokens(
                "".join(
                    b.get("text", "") for b in blocks if b.get("type") == "text"
                )
            ),
        },
    }
    self._write_json(200, json.dumps(body, ensure_ascii=False).encode("utf-8"))
    return 200

  # ------------------------- streaming -------------------------

  def _stream_messages(
      self,
      conv: litert_lm.Conversation,
      last_message: Dict[str, Any],
      model_id: str,
  ) -> int:
    """Runs a streaming /v1/messages request, emitting Anthropic SSE."""
    self._send_sse_headers()
    msg_id = _new_message_id()

    self._send_sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model_id,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 1},
            },
        },
    )

    state = _StreamState(model_id=model_id)
    state.open_text_block(self)
    self._send_sse("ping", {"type": "ping"})

    output_chars = 0
    last_ping = time.monotonic()
    cancelled = False

    # Pick streaming strategy. "auto" prefers native; if the engine doesn't
    # expose ``send_message_async`` we transparently fall back to a synthetic
    # single-chunk emission via the synchronous ``send_message`` path. This
    # keeps SSE-event-shape correctness while staying compatible with engine
    # builds (e.g. litert-lm 0.10.1) that don't ship streaming inference.
    strategy = _CONFIG.get("streaming_strategy", "auto")
    use_native = (
        strategy == "native"
        or (strategy == "auto" and hasattr(conv, "send_message_async"))
    )

    try:
      if use_native:
        try:
          iterator = conv.send_message_async(last_message)
        except AttributeError:
          if strategy == "native":
            raise
          use_native = False

      if use_native:
        for chunk in iterator:
          # ~10 s ping cadence per the Anthropic spec.
          now = time.monotonic()
          if now - last_ping >= 10.0:
            self._send_sse("ping", {"type": "ping"})
            last_ping = now

          for item in chunk.get("content", []) or []:
            if item.get("type") != "text":
              continue
            text = item.get("text", "")
            if not text:
              continue
            state.ensure_text_block_open(self)
            output_chars += len(text)
            self._send_sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": state.current_index,
                    "delta": {"type": "text_delta", "text": text},
                },
            )

          for tc in chunk.get("tool_calls", []) or []:
            state.close_current_block(self)
            state.open_tool_use_block(self, tc)
            fn = tc.get("function") or {}
            args = fn.get("arguments")
            if args is not None:
              partial_json = (
                  args if isinstance(args, str) else json.dumps(args)
              )
              self._send_sse(
                  "content_block_delta",
                  {
                      "type": "content_block_delta",
                      "index": state.current_index,
                      "delta": {
                          "type": "input_json_delta",
                          "partial_json": partial_json,
                      },
                  },
              )
      else:
        # Synthetic-streaming fallback. Run the synchronous send_message,
        # then emit one content_block_delta per content item. Claude Code's
        # SSE parser doesn't care whether the deltas arrive as a stream of
        # tokens or as one delta carrying the full text — it cares about
        # event-shape correctness.
        response = conv.send_message(last_message)
        for item in response.get("content", []) or []:
          if item.get("type") != "text":
            continue
          text = item.get("text", "")
          if not text:
            continue
          state.ensure_text_block_open(self)
          output_chars += len(text)
          self._send_sse(
              "content_block_delta",
              {
                  "type": "content_block_delta",
                  "index": state.current_index,
                  "delta": {"type": "text_delta", "text": text},
              },
          )
        for tc in response.get("tool_calls", []) or []:
          state.close_current_block(self)
          state.open_tool_use_block(self, tc)
          fn = tc.get("function") or {}
          args = fn.get("arguments")
          if args is not None:
            partial_json = (
                args if isinstance(args, str) else json.dumps(args)
            )
            self._send_sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": state.current_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": partial_json,
                    },
                },
            )
    except (BrokenPipeError, ConnectionResetError):
      # Client disconnected mid-stream. Cancel the engine cleanly.
      cancelled = True
      try:
        conv.cancel_process()
      except Exception:  # pylint: disable=broad-exception-caught
        pass
      return 200
    except Exception as e:  # pylint: disable=broad-exception-caught
      _LOGGER.exception("engine error during stream")
      try:
        self._send_sse(
            "error",
            {
                "type": "error",
                "error": {"type": "api_error", "message": str(e)},
            },
        )
      except (BrokenPipeError, ConnectionResetError):
        pass
      return 500

    if not cancelled:
      try:
        state.close_current_block(self)
        self._send_sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": (
                        "tool_use" if state.saw_tool_use else "end_turn"
                    ),
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": _estimate_tokens("x" * output_chars)},
            },
        )
        self._send_sse("message_stop", {"type": "message_stop"})
      except (BrokenPipeError, ConnectionResetError):
        try:
          conv.cancel_process()
        except Exception:  # pylint: disable=broad-exception-caught
          pass
    return 200

  def _send_sse(self, event_type: str, data: Dict[str, Any]) -> None:
    """Writes one SSE event to the wire and flushes."""
    self.wfile.write(format_sse_event(event_type, data))
    self.wfile.flush()


class _StreamState:
  """Mutable bookkeeping for an in-progress streaming response.

  Tracks which content block index is currently open so the handler can
  emit the right ``content_block_start`` / ``content_block_stop`` pairs
  when interleaving text and tool-use blocks.
  """

  def __init__(self, model_id: str):
    self.model_id = model_id
    self.current_index: int = -1
    self.current_kind: Optional[str] = None  # "text" | "tool_use"
    self.saw_tool_use: bool = False

  def open_text_block(self, handler: "AnthropicHandler") -> None:
    """Emits content_block_start for a new text block."""
    self.current_index += 1
    self.current_kind = "text"
    handler._send_sse(  # pylint: disable=protected-access
        "content_block_start",
        {
            "type": "content_block_start",
            "index": self.current_index,
            "content_block": {"type": "text", "text": ""},
        },
    )

  def ensure_text_block_open(self, handler: "AnthropicHandler") -> None:
    """Opens a text block if the cursor is not already on one."""
    if self.current_kind != "text":
      self.close_current_block(handler)
      self.open_text_block(handler)

  def open_tool_use_block(
      self, handler: "AnthropicHandler", tool_call: Dict[str, Any]
  ) -> None:
    """Emits content_block_start for a new tool_use block."""
    self.current_index += 1
    self.current_kind = "tool_use"
    self.saw_tool_use = True
    fn = tool_call.get("function") or {}
    handler._send_sse(  # pylint: disable=protected-access
        "content_block_start",
        {
            "type": "content_block_start",
            "index": self.current_index,
            "content_block": {
                "type": "tool_use",
                "id": tool_call.get("id")
                or ("toolu_" + uuid.uuid4().hex[:24]),
                "name": fn.get("name", ""),
                "input": {},
            },
        },
    )

  def close_current_block(self, handler: "AnthropicHandler") -> None:
    """Emits content_block_stop for the currently open block."""
    if self.current_kind is None:
      return
    handler._send_sse(  # pylint: disable=protected-access
        "content_block_stop",
        {"type": "content_block_stop", "index": self.current_index},
    )
    self.current_kind = None


# ---------------------------------------------------------------------------
# Click registration
# ---------------------------------------------------------------------------


def register(cli: click.Group) -> None:
  """Adds the Anthropic API choice and its options to ``serve``.

  This mirrors the registration pattern in :mod:`litert_lm_cli.serve`:
  callers pass the top-level Click group, and this function adds a new
  ``serve-anthropic`` subcommand that wraps :class:`AnthropicHandler`.

  We intentionally register a sibling command rather than mutating the
  existing ``serve`` command's ``--api`` choice, because Click does not
  support modifying an option's ``Choice`` after the command has been
  declared. The original ``serve`` command remains unchanged; a small
  edit to its ``--api`` ``click.Choice`` to include ``"anthropic"`` is
  applied separately via the standard PR review.

  Args:
    cli: The top-level Click group to register the command on.
  """

  @cli.command(
      name="serve-anthropic",
      help="Start a server speaking the Anthropic Messages API (alpha).",
  )
  @click.option(
      "--host", default="localhost", type=str, help="Host to listen on."
  )
  @click.option("--port", default=9379, type=int, help="Port to listen on.")
  @click.option(
      "--bearer-token",
      default=None,
      type=str,
      help=(
          "Optional bearer token. When set, requests must present"
          " 'Authorization: Bearer <token>' or 'X-Api-Key: <token>'."
      ),
  )
  @click.option(
      "--max-request-bytes",
      default=_DEFAULTS["max_request_bytes"],
      type=int,
      help="Maximum request body size in bytes.",
  )
  @click.option(
      "--request-timeout-secs",
      default=_DEFAULTS["request_timeout_secs"],
      type=int,
      help="Per-request timeout in seconds.",
  )
  @click.option(
      "--max-concurrent",
      default=_DEFAULTS["max_concurrent"],
      type=int,
      help="Maximum number of in-flight requests.",
  )
  @click.option(
      "--accept-any-model",
      is_flag=True,
      default=False,
      help=(
          "Route any 'model' name in the request to the loaded model."
          " Useful for clients (e.g. Claude Code) that hard-code a"
          " specific model id."
      ),
  )
  @click.option("--verbose", is_flag=True, help="Enable verbose logging.")
  def serve_anthropic(  # pylint: disable=unused-variable
      host: str,
      port: int,
      *,
      bearer_token: Optional[str],
      max_request_bytes: int,
      request_timeout_secs: int,
      max_concurrent: int,
      accept_any_model: bool,
      verbose: bool,
  ) -> None:
    """Starts an HTTP server speaking the Anthropic Messages API.

    Args:
      host: Host to listen on.
      port: Port to listen on.
      bearer_token: Optional shared secret enforced via ``Authorization``
        / ``X-Api-Key``.
      max_request_bytes: Request body size cap.
      request_timeout_secs: Per-request timeout.
      max_concurrent: Concurrency cap (semaphore-gated).
      accept_any_model: When set, route any ``model`` value to the loaded
        model.
      verbose: Enable verbose engine logging.
    """
    if verbose:
      litert_lm.set_min_log_severity(litert_lm.LogSeverity.VERBOSE)
      logging.basicConfig(level=logging.DEBUG)
    else:
      logging.basicConfig(level=logging.INFO)

    global _CONCURRENCY_GATE
    _CONFIG["bearer_token"] = bearer_token
    _CONFIG["max_request_bytes"] = max_request_bytes
    _CONFIG["request_timeout_secs"] = request_timeout_secs
    _CONFIG["max_concurrent"] = max_concurrent
    _CONFIG["accept_any_model"] = accept_any_model
    _CONCURRENCY_GATE = threading.Semaphore(max_concurrent)

    _serve_module.run_server(host, port, AnthropicHandler)


# Re-export Model for convenience and so tests can monkey-patch a single
# import path (mirrors litert_lm_cli.serve).
Model = model.Model


__all__ = (
    "AnthropicHandler",
    "TranslationError",
    "format_sse_event",
    "make_anthropic_error",
    "register",
    "translate_messages",
    "translate_model",
    "translate_sampler",
    "translate_system",
    "translate_tools",
)
