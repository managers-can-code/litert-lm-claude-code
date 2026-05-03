#!/usr/bin/env python3
# Copyright 2026 ram <tenheadedram@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""MCP server exposing litert-lm lifecycle operations as tools.

Tools:
    litert_lm_status() -> dict
    litert_lm_list_models() -> list[dict]
    litert_lm_start(model_path: str, port: int = 9379, host: str = "127.0.0.1") -> dict
    litert_lm_stop() -> dict
    litert_lm_switch_model(model_path: str, port: int = 9379, host: str = "127.0.0.1") -> dict

Tries the official MCP Python SDK first (``pip install mcp``). If that's
unavailable, falls back to a small stdio JSON-RPC handler that implements
the subset of MCP that Claude Code uses for tool discovery and invocation
(``initialize``, ``tools/list``, ``tools/call``).

Designed to run as ``python3 mcp/litert_lm_mcp.py`` over stdio. Every tool
delegates to ``scripts/litert_lm_control.py`` for the heavy lifting so behaviour
stays consistent with the slash commands.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"


# ---------------------------------------------------------------------------
# Bridge to scripts/litert_lm_control.py
# ---------------------------------------------------------------------------


def _load_control_module():
    """Import scripts/litert_lm_control.py without polluting sys.path globally."""
    spec = importlib.util.spec_from_file_location(
        "litert_lm_control",
        SCRIPTS_DIR / "litert_lm_control.py",
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"could not import litert_lm_control from {SCRIPTS_DIR}"
        )
    module = importlib.util.module_from_spec(spec)
    # The module must be registered in sys.modules before exec_module so
    # that decorators that introspect the defining module (such as
    # dataclasses) can find it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTROL = _load_control_module()


def _run_control(argv: list[str]) -> dict[str, Any]:
    """Run a litert_lm_control subcommand and capture stdout/stderr/exit-code."""
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    exit_code = 0
    with redirect_stdout(out_buf), redirect_stderr(err_buf):
        try:
            exit_code = CONTROL.main(argv)
        except SystemExit as exc:
            exit_code = int(exc.code or 0)
        except Exception as exc:  # noqa: BLE001 - surface to MCP caller
            err_buf.write(f"unhandled exception: {exc}\n")
            exit_code = 1
    return {
        "exit_code": exit_code,
        "stdout": out_buf.getvalue(),
        "stderr": err_buf.getvalue(),
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def tool_litert_lm_status() -> dict[str, Any]:
    """Return server status as a structured dict."""
    cfg = CONTROL.load_config()
    host = cfg.host if cfg else CONTROL.DEFAULT_HOST
    port = cfg.port if cfg else CONTROL.DEFAULT_PORT
    pid = CONTROL.read_pid()
    pid_alive = pid is not None and CONTROL.process_is_alive(pid)
    if pid is not None and not pid_alive:
        CONTROL.clear_pid_file()
        pid = None
    model_id = CONTROL.resolve_model_id(host, port)
    return {
        "pid": pid if pid_alive else None,
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}",
        "reachable": model_id is not None,
        "model_id": model_id,
        "pid_file": str(CONTROL.PID_FILE),
        "log_file": str(CONTROL.LOG_FILE),
    }


def tool_litert_lm_list_models() -> dict[str, Any]:
    """Return /v1/models verbatim from the running server."""
    cfg = CONTROL.load_config()
    host = cfg.host if cfg else CONTROL.DEFAULT_HOST
    port = cfg.port if cfg else CONTROL.DEFAULT_PORT
    url = f"http://{host}:{port}/v1/models"
    try:
        data = CONTROL.http_get_json(url)
    except Exception as exc:  # noqa: BLE001 - reporting only
        return {"ok": False, "url": url, "error": str(exc)}
    return {"ok": True, "url": url, "data": data}


def tool_litert_lm_start(
    model_path: str,
    port: int = CONTROL.DEFAULT_PORT,
    host: str = CONTROL.DEFAULT_HOST,
) -> dict[str, Any]:
    """Start the server. Idempotent: returns current state if already up."""
    argv = [
        "start",
        "--model",
        str(model_path),
        "--port",
        str(port),
        "--host",
        host,
    ]
    result = _run_control(argv)
    result["status"] = tool_litert_lm_status()
    return result


def tool_litert_lm_stop() -> dict[str, Any]:
    result = _run_control(["stop"])
    result["status"] = tool_litert_lm_status()
    return result


def tool_litert_lm_switch_model(
    model_path: str,
    port: int = CONTROL.DEFAULT_PORT,
    host: str = CONTROL.DEFAULT_HOST,
) -> dict[str, Any]:
    argv = [
        "switch",
        "--model",
        str(model_path),
        "--port",
        str(port),
        "--host",
        host,
    ]
    result = _run_control(argv)
    result["status"] = tool_litert_lm_status()
    return result


def tool_litert_lm_generate(
    prompt: str,
    model: Optional[str] = None,
    max_tokens: int = 1024,
    temperature: Optional[float] = None,
    system: Optional[str] = None,
) -> dict[str, Any]:
  """Run a one-shot inference against the local litert-lm server.

  Used by the ``litert-lm-local`` subagent for delegated inference. The
  subagent receives a task from the main Claude conversation, calls this
  tool with a prompt, and returns the result. The bulk of the inference
  work happens locally on the user's machine; the orchestrating Claude
  uses minimal cloud tokens to formulate the call and present the result.

  Args:
    prompt: The text prompt to send to the local model.
    model: Model id to target. Defaults to whichever model is currently
      loaded by the server.
    max_tokens: Generation cap. Defaults to 1024.
    temperature: Sampling temperature (0.0 - 1.0). When None, uses the
      server's default.
    system: Optional system prompt.

  Returns:
    {
      "text": <generated text>,
      "stop_reason": <"end_turn" | "max_tokens" | "stop_sequence" | "tool_use">,
      "usage": {"input_tokens": ..., "output_tokens": ...},
      "model": <model id reported by server>,
    }
    On error: {"error": "<message>"}.
  """
  status = tool_litert_lm_status()
  if not status.get("reachable"):
    return {
        "error": (
            "litert-lm server is not running. Start it first via the "
            "/litert-lm-start slash command, or call the litert_lm_start "
            "tool with a model_path argument."
        ),
        "hint": status,
    }

  url = status.get("url") or "http://127.0.0.1:9379"
  body: dict[str, Any] = {
      "model": model or status.get("model_id") or "local-model",
      "max_tokens": int(max_tokens),
      "messages": [{"role": "user", "content": prompt}],
  }
  if temperature is not None:
    body["temperature"] = float(temperature)
  if system:
    body["system"] = system

  try:
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"{url}/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer ignored",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
      raw = resp.read().decode("utf-8")
  except urllib.error.HTTPError as exc:
    return {"error": f"HTTP {exc.code}: {exc.reason}", "body": exc.read().decode("utf-8", errors="replace")}
  except Exception as exc:  # pylint: disable=broad-exception-caught
    return {"error": f"request failed: {exc}"}

  try:
    parsed = json.loads(raw)
  except json.JSONDecodeError as exc:
    return {"error": f"invalid JSON from server: {exc}", "raw": raw[:500]}

  # Extract text from content blocks.
  text_blocks = []
  for block in parsed.get("content", []) or []:
    if block.get("type") == "text":
      text_blocks.append(block.get("text", ""))
  return {
      "text": "".join(text_blocks),
      "stop_reason": parsed.get("stop_reason"),
      "usage": parsed.get("usage", {}),
      "model": parsed.get("model"),
  }


# ---------------------------------------------------------------------------
# Tool registry shared by both transports
# ---------------------------------------------------------------------------


TOOL_DEFINITIONS = [
    {
        "name": "litert_lm_status",
        "description": (
            "Return the current state of the local litert-lm server: "
            "PID, URL, reachability, and the loaded model id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "fn": lambda _args: tool_litert_lm_status(),
    },
    {
        "name": "litert_lm_list_models",
        "description": (
            "Fetch /v1/models from the running server and return the raw "
            "response. Use this to get the exact model id Claude Code "
            "should pass with --model."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "fn": lambda _args: tool_litert_lm_list_models(),
    },
    {
        "name": "litert_lm_start",
        "description": (
            "Start the local litert-lm Anthropic-compatible server in the "
            "background. Idempotent: if the server is already running, "
            "returns the current state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_path": {
                    "type": "string",
                    "description": "Absolute path to a .litertlm file.",
                },
                "port": {
                    "type": "integer",
                    "default": CONTROL.DEFAULT_PORT,
                    "description": "TCP port to listen on.",
                },
                "host": {
                    "type": "string",
                    "default": CONTROL.DEFAULT_HOST,
                    "description": "Bind host.",
                },
            },
            "required": ["model_path"],
            "additionalProperties": False,
        },
        "fn": lambda args: tool_litert_lm_start(
            args["model_path"],
            int(args.get("port", CONTROL.DEFAULT_PORT)),
            str(args.get("host", CONTROL.DEFAULT_HOST)),
        ),
    },
    {
        "name": "litert_lm_stop",
        "description": "Stop the running litert-lm server, if any.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "fn": lambda _args: tool_litert_lm_stop(),
    },
    {
        "name": "litert_lm_switch_model",
        "description": (
            "Stop the running server and start a new one with the supplied "
            "model. Useful when comparing models across a single session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_path": {
                    "type": "string",
                    "description": "Absolute path to a .litertlm file.",
                },
                "port": {
                    "type": "integer",
                    "default": CONTROL.DEFAULT_PORT,
                    "description": "TCP port to listen on.",
                },
                "host": {
                    "type": "string",
                    "default": CONTROL.DEFAULT_HOST,
                    "description": "Bind host.",
                },
            },
            "required": ["model_path"],
            "additionalProperties": False,
        },
        "fn": lambda args: tool_litert_lm_switch_model(
            args["model_path"],
            int(args.get("port", CONTROL.DEFAULT_PORT)),
            str(args.get("host", CONTROL.DEFAULT_HOST)),
        ),
    },
    {
        "name": "litert_lm_generate",
        "description": (
            "Run a one-shot inference against the local litert-lm server. "
            "Used by the litert-lm-local subagent for delegated work — "
            "main Claude formulates the prompt, this tool runs the bulk "
            "inference locally, and returns the generated text. Server "
            "must be running (call litert_lm_status / litert_lm_start "
            "first if needed)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The text prompt to send to the local model.",
                },
                "model": {
                    "type": "string",
                    "description": (
                        "Optional model id. Defaults to whichever model is "
                        "currently loaded by the server."
                    ),
                },
                "max_tokens": {
                    "type": "integer",
                    "default": 1024,
                    "description": "Generation cap.",
                },
                "temperature": {
                    "type": "number",
                    "description": (
                        "Sampling temperature (0.0 - 1.0). Omit for the "
                        "server default."
                    ),
                },
                "system": {
                    "type": "string",
                    "description": "Optional system prompt.",
                },
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
        "fn": lambda args: tool_litert_lm_generate(
            prompt=str(args["prompt"]),
            model=args.get("model"),
            max_tokens=int(args.get("max_tokens", 1024)),
            temperature=args.get("temperature"),
            system=args.get("system"),
        ),
    },
]


def _tool_by_name(name: str):
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return tool
    return None


# ---------------------------------------------------------------------------
# Transport: prefer the official SDK, fall back to a tiny JSON-RPC server
# ---------------------------------------------------------------------------


def _have_official_sdk() -> bool:
    return importlib.util.find_spec("mcp") is not None


def _run_official_sdk() -> int:
    from mcp.server.fastmcp import FastMCP  # type: ignore

    server = FastMCP("litert-lm")

    @server.tool()
    def litert_lm_status() -> dict[str, Any]:
        """Return server status."""
        return tool_litert_lm_status()

    @server.tool()
    def litert_lm_list_models() -> dict[str, Any]:
        """Return /v1/models verbatim."""
        return tool_litert_lm_list_models()

    @server.tool()
    def litert_lm_start(
        model_path: str,
        port: int = CONTROL.DEFAULT_PORT,
        host: str = CONTROL.DEFAULT_HOST,
    ) -> dict[str, Any]:
        """Start the server in background. Idempotent."""
        return tool_litert_lm_start(model_path, port, host)

    @server.tool()
    def litert_lm_stop() -> dict[str, Any]:
        """Stop the running server, if any."""
        return tool_litert_lm_stop()

    @server.tool()
    def litert_lm_switch_model(
        model_path: str,
        port: int = CONTROL.DEFAULT_PORT,
        host: str = CONTROL.DEFAULT_HOST,
    ) -> dict[str, Any]:
        """Stop the running server and start with a new model."""
        return tool_litert_lm_switch_model(model_path, port, host)

    server.run()
    return 0


# Minimal stdio JSON-RPC fallback implementing just enough of MCP for tools.
# Spec: https://spec.modelcontextprotocol.io/ . Claude Code's MCP client
# negotiates with `initialize`, then calls `tools/list` and `tools/call`.

PROTOCOL_VERSION = "2024-11-05"


def _read_message() -> dict[str, Any] | None:
    line = sys.stdin.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return _read_message()
    return json.loads(line)


def _write_message(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def _handle_initialize(req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    return _result(
        req_id,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "litert-lm", "version": "0.1.0"},
        },
    )


def _handle_tools_list(req_id: Any, _params: dict[str, Any]) -> dict[str, Any]:
    tools = [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["inputSchema"],
        }
        for t in TOOL_DEFINITIONS
    ]
    return _result(req_id, {"tools": tools})


def _handle_tools_call(req_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    tool = _tool_by_name(str(name))
    if tool is None:
        return _error(req_id, -32601, f"unknown tool: {name!r}")
    try:
        payload = tool["fn"](arguments)
    except Exception as exc:  # noqa: BLE001 - surface to MCP caller
        return _result(
            req_id,
            {
                "isError": True,
                "content": [
                    {
                        "type": "text",
                        "text": f"tool {name!r} raised: {exc}",
                    }
                ],
            },
        )
    return _result(
        req_id,
        {
            "isError": False,
            "content": [
                {"type": "text", "text": json.dumps(payload, indent=2)}
            ],
        },
    )


def _run_fallback_stdio() -> int:
    while True:
        try:
            msg = _read_message()
        except json.JSONDecodeError as exc:
            _write_message(_error(None, -32700, f"parse error: {exc}"))
            continue
        if msg is None:
            return 0
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            _write_message(_handle_initialize(req_id, params))
        elif method == "initialized":
            # notification, no response
            continue
        elif method == "tools/list":
            _write_message(_handle_tools_list(req_id, params))
        elif method == "tools/call":
            _write_message(_handle_tools_call(req_id, params))
        elif method == "ping":
            _write_message(_result(req_id, {}))
        elif method == "shutdown":
            _write_message(_result(req_id, {}))
            return 0
        else:
            if req_id is not None:
                _write_message(_error(req_id, -32601, f"unsupported method: {method}"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="litert_lm_mcp")
    parser.add_argument(
        "--no-sdk",
        action="store_true",
        help="force the stdio JSON-RPC fallback even if the mcp SDK is "
        "installed (useful for debugging the fallback)",
    )
    args = parser.parse_args(argv)
    if not args.no_sdk and _have_official_sdk():
        return _run_official_sdk()
    return _run_fallback_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
