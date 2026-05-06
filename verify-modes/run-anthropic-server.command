#!/usr/bin/env bash
# run-anthropic-server.command
#
# Wrapper that delegates to outputs/tier3-runner/launcher.py — the
# battle-tested launcher that already handles the v0.10.1 install
# topology (litert_lm imported from the uv venv with FFI bindings;
# serve_anthropic.py loaded via importlib from outputs/pr/...).
#
# Why a wrapper at all: verify-mode2-live.command needs a single
# entry point that knows how to find the model and the right Python.
# The tier3 launcher doesn't ship a Finder-friendly wrapper, so this
# script provides one and reuses the launcher for the heavy lifting.
#
# Usage:
#   ./run-anthropic-server.command [<model.litertlm>]
#
# Env overrides:
#   PORT=9379
#   LITERT_PYTHON=/path/to/python3   (auto-detected if unset)

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHER="$OUTPUTS_DIR/tier3-runner/launcher.py"
PORT="${PORT:-9379}"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

if [ ! -f "$LAUNCHER" ]; then
  echo "FATAL: launcher.py not found at $LAUNCHER" >&2
  exit 1
fi

# Auto-detect the right Python — same probe order as run-tier3.sh.
LITERT_PYTHON="${LITERT_PYTHON:-}"
if [ -z "$LITERT_PYTHON" ]; then
  for cand in \
    "$HOME/Library/Application Support/uv/tools/litert-lm/bin/python3" \
    "$HOME/Library/Application Support/uv/tools/litert-lm/bin/python" \
    "$HOME/.local/share/uv/tools/litert-lm/bin/python3" \
    "$HOME/.local/share/uv/tools/litert-lm/bin/python"
  do
    if [ -x "$cand" ]; then
      if "$cand" -c "import litert_lm" 2>/dev/null; then
        LITERT_PYTHON="$cand"
        break
      fi
    fi
  done
fi

if [ -z "$LITERT_PYTHON" ] || [ ! -x "$LITERT_PYTHON" ]; then
  echo "FATAL: no Python found that can 'import litert_lm'." >&2
  echo "       Install: uv tool install litert-lm" >&2
  exit 1
fi

MODEL="${1:-}"
if [ -z "$MODEL" ]; then
  MODEL="$(find "$HOME/.cache/huggingface" -name "*.litertlm" 2>/dev/null | head -1)"
fi
if [ -z "$MODEL" ] || [ ! -f "$MODEL" ]; then
  echo "FATAL: no model file given and none found under ~/.cache/huggingface" >&2
  echo "       Pre-cache one with:" >&2
  echo "         litert-lm run --from-huggingface-repo=litert-community/Gemma-4-E2B-it gemma-4-E2B-it.litertlm --prompt hi" >&2
  exit 1
fi

echo "launcher: $LAUNCHER"
echo "python:   $LITERT_PYTHON"
echo "model:    $MODEL"
echo "port:     $PORT"
echo ""

exec "$LITERT_PYTHON" "$LAUNCHER" "$MODEL" "$PORT"
