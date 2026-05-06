# Copyright 2026 The ODML Authors. All Rights Reserved.
# Licensed under the Apache License, Version 2.0.
"""Tier 3 launcher — boot AnthropicHandler against a real litert_lm.Engine.

Invoked by run-tier3.sh. Handles two install topologies:

1. Upstream-merged: a future LiteRT-LM release has serve_anthropic.py merged
   into ``litert_lm_cli/``. Then ``from litert_lm_cli import serve_anthropic``
   just works.
2. Pre-merge (today): ``litert_lm_cli`` is installed via uv but lacks our
   serve_anthropic.py. We load ``outputs/pr/python/litert_lm_cli/serve_anthropic.py``
   via importlib and register it as ``litert_lm_cli.serve_anthropic`` in
   ``sys.modules``. The real ``litert_lm_cli.serve`` and ``litert_lm_cli.model``
   are still found through the regular package import machinery.

Crucially we do NOT inject our path with ``sys.path.insert(0, ...)`` — that
shadows the real ``litert_lm_cli`` package as a namespace package and breaks
``from litert_lm_cli import serve``.

Usage (invoked by run-tier3.sh):
  python3 launcher.py <model-path> [<port>]
"""

from __future__ import annotations

import http.server
import importlib
import importlib.util
import os
import sys
import time
import traceback
from pathlib import Path


def main() -> int:
  if len(sys.argv) < 2:
    print("usage: launcher.py <model-path> [<port>]", file=sys.stderr)
    return 2

  # NOTE: do NOT use .resolve() — HuggingFace cache stores .litertlm files as
  # symlinks pointing into a content-addressable blobs/ directory where the
  # target has no extension. Engine() inspects the path's extension to detect
  # the format, so we must keep the symlink path with its .litertlm suffix.
  model_path = Path(sys.argv[1]).expanduser().absolute()
  port = int(sys.argv[2]) if len(sys.argv) > 2 else 9379

  if not model_path.exists():
    print(f"[launcher] ERROR: model not found at {model_path}", file=sys.stderr)
    return 3
  print(f"[launcher] Model path (unresolved): {model_path}", flush=True)

  # ---------------------------------------------------------------------------
  # 1. Import real litert_lm and litert_lm_cli (NO sys.path injection)
  # ---------------------------------------------------------------------------
  try:
    import litert_lm  # noqa: F401  used later via getattr
  except ImportError as exc:
    print(f"[launcher] ERROR: cannot import litert_lm: {exc}", file=sys.stderr)
    print(
      "[launcher] Hint: this script must be invoked with a Python that has "
      "litert_lm installed (e.g. the uv-managed Python at "
      "~/.local/share/uv/tools/litert-lm/bin/python3 or "
      "~/Library/Application\\ Support/uv/tools/litert-lm/bin/python3).",
      file=sys.stderr,
    )
    return 4

  import types
  try:
    import litert_lm_cli  # noqa: F401  needed so the package is initialized
  except ImportError as exc:
    print(
      f"[launcher] ERROR: cannot even import litert_lm_cli: {exc}",
      file=sys.stderr,
    )
    return 5

  print(
    f"[launcher] litert_lm_cli loaded from "
    f"{getattr(litert_lm_cli, '__file__', None) or list(getattr(litert_lm_cli, '__path__', ['<unknown>']))}",
    flush=True,
  )

  # The uv-released litert-lm wheel ships litert_lm_cli as a namespace
  # package but does NOT include serve.py or model.py (those live in source
  # but aren't packaged). Try the real import; on failure, synthesize a stub.
  try:
    from litert_lm_cli import serve as _serve_module
    print(f"[launcher] Real litert_lm_cli.serve found at {_serve_module.__file__}",
          flush=True)
  except ImportError:
    print(
      "[launcher] litert_lm_cli.serve not present in installed package — "
      "synthesizing stub module (this is the pre-merge dev path; after the PR "
      "lands, the real serve.py will be used).",
      flush=True,
    )
    _serve_module = types.ModuleType("litert_lm_cli.serve")
    _serve_module._current_engine = {}
    _serve_module._current_model_id = None
    _serve_module.get_engine = lambda mid=None: _serve_module._current_engine.get(
        mid or _serve_module._current_model_id
    )
    sys.modules["litert_lm_cli.serve"] = _serve_module
    setattr(litert_lm_cli, "serve", _serve_module)

  # Same for litert_lm_cli.model — our handler imports it for image MIME
  # validation. The stub provides the attributes the handler probes.
  try:
    from litert_lm_cli import model as _model_module  # noqa: F401
    print(f"[launcher] Real litert_lm_cli.model found at {_model_module.__file__}",
          flush=True)
  except ImportError:
    print(
      "[launcher] litert_lm_cli.model not present — synthesizing stub.",
      flush=True,
    )
    _model_module = types.ModuleType("litert_lm_cli.model")
    sys.modules["litert_lm_cli.model"] = _model_module
    setattr(litert_lm_cli, "model", _model_module)

  # ---------------------------------------------------------------------------
  # 1b. Patch litert_lm top-level with attrs that may live in submodules.
  # In v0.10.1, the released wheel's top-level __init__.py doesn't re-export
  # everything our handler uses (SamplerConfig, LogSeverity, etc. live in
  # litert_lm.interfaces). Walk known submodules and forward-inject.
  # ---------------------------------------------------------------------------
  needed_top_level = [
      "Engine", "Conversation", "Session",
      "Tool", "ToolEventHandler", "tool_from_function",
      "SamplerConfig", "Backend", "LogSeverity", "Responses",
      "set_min_log_severity",
  ]
  for submod_name in ("interfaces", "tools"):
    try:
      submod = importlib.import_module(f"litert_lm.{submod_name}")
      for attr in needed_top_level:
        if hasattr(submod, attr) and not hasattr(litert_lm, attr):
          setattr(litert_lm, attr, getattr(submod, attr))
          print(
            f"[launcher] Patched litert_lm.{attr} from "
            f"litert_lm.{submod_name}",
            flush=True,
          )
    except ImportError:
      pass

  still_missing = [a for a in needed_top_level if not hasattr(litert_lm, a)]
  if still_missing:
    print(
      f"[launcher] WARNING: still missing from litert_lm top-level: "
      f"{still_missing}. Adding no-op stubs.",
      flush=True,
    )
    for attr in still_missing:
      # Heuristic: classes get a placeholder type, functions get a no-op.
      setattr(litert_lm, attr, type(attr, (object,), {}))

  # ---------------------------------------------------------------------------
  # 2. Load our serve_anthropic.py as litert_lm_cli.serve_anthropic via
  #    importlib — bypassing sys.path tricks that would create a namespace
  #    package and shadow the real litert_lm_cli.
  # ---------------------------------------------------------------------------
  this_dir = Path(__file__).resolve().parent
  serve_anthropic_path = (
      this_dir.parent / "pr" / "python" / "litert_lm_cli" / "serve_anthropic.py"
  )
  if not serve_anthropic_path.exists():
    print(
      f"[launcher] ERROR: cannot find serve_anthropic.py at "
      f"{serve_anthropic_path}",
      file=sys.stderr,
    )
    return 6

  print(
    f"[launcher] Loading serve_anthropic.py from {serve_anthropic_path}",
    flush=True,
  )
  spec = importlib.util.spec_from_file_location(
      "litert_lm_cli.serve_anthropic", str(serve_anthropic_path)
  )
  serve_anthropic = importlib.util.module_from_spec(spec)
  sys.modules["litert_lm_cli.serve_anthropic"] = serve_anthropic
  try:
    spec.loader.exec_module(serve_anthropic)
  except Exception:
    print("[launcher] ERROR: failed to load serve_anthropic.py:", file=sys.stderr)
    traceback.print_exc()
    return 7

  # ---------------------------------------------------------------------------
  # 3. Construct a real Engine and wire it into the global state on the
  #    REAL litert_lm_cli.serve module (which our handler reads via
  #    `from litert_lm_cli import serve as _serve_module`).
  # ---------------------------------------------------------------------------
  print(
    f"[launcher] Loading model from {model_path} (this can take 30-60s)...",
    flush=True,
  )
  t0 = time.monotonic()
  try:
    engine = litert_lm.Engine(str(model_path))
  except Exception:
    print("[launcher] ERROR: Engine() failed:", file=sys.stderr)
    traceback.print_exc()
    return 8
  load_secs = time.monotonic() - t0
  print(f"[launcher] Engine loaded in {load_secs:.1f}s.", flush=True)

  model_id = "local-model"

  # Set globals on the REAL serve module. The handler reads
  # _serve_module._current_engine and _serve_module.get_engine.
  _serve_module._current_engine = {model_id: engine}
  _serve_module._current_model_id = model_id

  def get_engine(mid: str | None = None):
    return _serve_module._current_engine.get(mid or model_id)

  _serve_module.get_engine = get_engine

  # ---------------------------------------------------------------------------
  # 4. Configure our handler — accept any model name (Claude Code sends
  #    background haiku requests we don't want to 404).
  # ---------------------------------------------------------------------------
  serve_anthropic._CONFIG.update(serve_anthropic._DEFAULTS)
  serve_anthropic._CONFIG["accept_any_model"] = True
  # v0.10.1's Conversation.send_message_async exists but blocks indefinitely.
  # Force the synthetic-streaming fallback so SSE clients still get a valid
  # event sequence (one content_block_delta carrying the full assistant text).
  serve_anthropic._CONFIG["streaming_strategy"] = "synthetic"

  # ---------------------------------------------------------------------------
  # 5. Boot
  # ---------------------------------------------------------------------------
  server = http.server.ThreadingHTTPServer(
      ("127.0.0.1", port),
      serve_anthropic.AnthropicHandler,
  )
  print(
    f"[launcher] Listening on http://127.0.0.1:{port} "
    f"with model_id={model_id!r}",
    flush=True,
  )
  print(f"[launcher] PID: {os.getpid()}", flush=True)
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("[launcher] Interrupted, shutting down.", flush=True)
    return 0
  finally:
    server.server_close()
    try:
      engine.close()
    except Exception:
      pass

  return 0


if __name__ == "__main__":
  sys.exit(main())
