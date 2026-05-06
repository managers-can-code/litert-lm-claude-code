#!/usr/bin/env bash
# Tier 3 real-hardware verification runner.
#
# Boots our AnthropicHandler against the user's installed litert-lm and the
# real .litertlm model passed as $1, drives real Claude Code at it, writes
# a report.
#
# Usage:
#   bash run-tier3.sh <path-to-model.litertlm>
#
# Example:
#   bash run-tier3.sh ~/Downloads/gemma-3n-E2B-it.litertlm
#
# Prereqs (the script verifies them up front):
#   - python3 (with the user's installed litert_lm package)
#   - claude (Claude Code CLI 2.x)
#   - curl
#   - jq (optional, for prettier JSON output; falls back to python -m json.tool)

set -uo pipefail

# --- args ---------------------------------------------------------------------

MODEL_PATH="${1:-}"
if [ -z "$MODEL_PATH" ]; then
  echo "usage: bash run-tier3.sh <path-to-model.litertlm>" >&2
  echo "  e.g.  bash run-tier3.sh ~/Downloads/gemma-3n-E2B-it.litertlm" >&2
  exit 2
fi
MODEL_PATH="${MODEL_PATH/#\~/$HOME}"

PORT="${PORT:-9379}"

# --- locations ---------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHER="$SCRIPT_DIR/launcher.py"
REPORT="$OUTPUTS_DIR/tier3-real-report.md"
SERVER_LOG="$(mktemp -t tier3-server.XXXXXX.log)"

# --- preflight ---------------------------------------------------------------

fail() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$LAUNCHER" ] || fail "launcher.py not found at $LAUNCHER"
[ -f "$MODEL_PATH" ] || fail "model not found at $MODEL_PATH"

command -v python3 >/dev/null || fail "python3 not in PATH"
command -v claude >/dev/null || fail "claude (Claude Code CLI) not in PATH. Install: npm install -g @anthropic-ai/claude-code"
command -v curl >/dev/null || fail "curl not in PATH"

# Find a Python interpreter that can `import litert_lm`. The CLI is typically
# installed via `uv tool install litert-lm`, which puts it in an isolated
# venv — so the system python3 cannot import litert_lm even though the
# `litert-lm` shim is on PATH.
LITERT_PYTHON=""
_PYTHON_CANDIDATES=(
  "$HOME/Library/Application Support/uv/tools/litert-lm/bin/python3"
  "$HOME/Library/Application Support/uv/tools/litert-lm/bin/python"
  "$HOME/.local/share/uv/tools/litert-lm/bin/python3"
  "$HOME/.local/share/uv/tools/litert-lm/bin/python"
)
# Plus whatever's in PATH
if which python3 >/dev/null 2>&1; then _PYTHON_CANDIDATES+=("$(which python3)"); fi
if which python  >/dev/null 2>&1; then _PYTHON_CANDIDATES+=("$(which python)"); fi
# Plus a fallback discovered from `litert-lm` shim's shebang (uv shims point at the venv)
LITERT_LM_BIN="$(command -v litert-lm 2>/dev/null || true)"
if [ -n "$LITERT_LM_BIN" ] && [ -L "$LITERT_LM_BIN" ]; then
  _LITERT_LM_RESOLVED="$(readlink -f "$LITERT_LM_BIN" 2>/dev/null || readlink "$LITERT_LM_BIN" 2>/dev/null || echo "")"
  if [ -n "$_LITERT_LM_RESOLVED" ]; then
    _PYTHON_CANDIDATES+=("$(dirname "$_LITERT_LM_RESOLVED")/python3" "$(dirname "$_LITERT_LM_RESOLVED")/python")
  fi
fi

for _candidate in "${_PYTHON_CANDIDATES[@]}"; do
  if [ -x "$_candidate" ] && "$_candidate" -c "import litert_lm" 2>/dev/null; then
    LITERT_PYTHON="$_candidate"
    break
  fi
done

if [ -z "$LITERT_PYTHON" ]; then
  echo "[tier3] WARNING: no Python found that can 'import litert_lm'." >&2
  echo "[tier3] Tried:" >&2
  for c in "${_PYTHON_CANDIDATES[@]}"; do echo "  - $c" >&2; done
  fail "cannot import litert_lm. Install: uv tool install litert-lm OR pip install --break-system-packages litert-lm"
fi

echo "[tier3] Using Python: $LITERT_PYTHON"

PRETTY="cat"
if command -v jq >/dev/null; then
  PRETTY="jq ."
else
  # Use the same Python that has litert_lm — system python3 may be locked down.
  PRETTY="\"$LITERT_PYTHON\" -m json.tool"
fi

CLAUDE_VERSION="$(claude --version 2>/dev/null || echo unknown)"
echo "[tier3] Claude Code: $CLAUDE_VERSION"
echo "[tier3] Model:      $MODEL_PATH"
echo "[tier3] Port:       $PORT"
echo "[tier3] Report:     $REPORT"
echo "[tier3] Server log: $SERVER_LOG"
echo ""

# --- start launcher in background --------------------------------------------

echo "[tier3] Starting launcher (loading model, this can take 30-60s)..."
"$LITERT_PYTHON" "$LAUNCHER" "$MODEL_PATH" "$PORT" > "$SERVER_LOG" 2>&1 &
SERVER_PID=$!

cleanup() {
  echo ""
  echo "[tier3] Stopping launcher (PID $SERVER_PID)..."
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait up to 90s for /v1/models to respond.
READY=0
for i in $(seq 1 90); do
  if curl -s -f -o /dev/null "http://127.0.0.1:${PORT}/v1/models" 2>/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[tier3] Launcher exited before becoming ready. Log:" >&2
    cat "$SERVER_LOG" >&2
    exit 4
  fi
  sleep 1
done
[ "$READY" -eq 1 ] || { echo "[tier3] Server not ready after 90s. Log:" >&2; cat "$SERVER_LOG" >&2; exit 5; }
echo "[tier3] Server ready."

# --- run scenarios -----------------------------------------------------------

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
ms_now() { python3 -c 'import time; print(int(time.time()*1000))'; }

run_curl() {
  local label="$1"; shift
  echo "### $label" >> "$REPORT"
  echo '' >> "$REPORT"
  echo '```' >> "$REPORT"
  ( "$@" ) 2>&1 | tee -a "$REPORT"
  echo '```' >> "$REPORT"
  echo '' >> "$REPORT"
}

# Initialize report
{
  echo "# Tier 3 Real-Hardware Verification Report"
  echo ""
  echo "- Date: $(ts)"
  echo "- Model: \`$MODEL_PATH\`"
  echo "- Port: $PORT"
  echo "- Claude Code: $CLAUDE_VERSION"
  echo "- LiteRT-LM Python package: $(python3 -c 'import litert_lm; print(getattr(litert_lm, "__version__", "unknown"))')"
  echo ""
  echo "## Step 1 — /v1/models"
  echo ""
  echo '```json'
  curl -s "http://127.0.0.1:${PORT}/v1/models" | $PRETTY
  echo '```'
  echo ""
} > "$REPORT"

echo "[tier3] Step 2 — non-streaming Anthropic Messages..."
{
  echo "## Step 2 — Non-streaming /v1/messages"
  echo ""
  echo '```json'
  curl -s -X POST "http://127.0.0.1:${PORT}/v1/messages" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ignored" \
    -d '{"model":"local-model","max_tokens":50,"messages":[{"role":"user","content":"what is 2+2?"}]}' \
    | $PRETTY
  echo '```'
  echo ""
} >> "$REPORT"

echo "[tier3] Step 3 — streaming Anthropic Messages..."
{
  echo "## Step 3 — Streaming /v1/messages"
  echo ""
  echo '```'
  curl -s -N -X POST "http://127.0.0.1:${PORT}/v1/messages" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ignored" \
    -d '{"model":"local-model","max_tokens":50,"stream":true,"messages":[{"role":"user","content":"what is 2+2?"}]}' \
    | head -200
  echo '```'
  echo ""
} >> "$REPORT"

echo "[tier3] Step 4 — Real Claude Code talking to our server..."
{
  echo "## Step 4 — Real Claude Code → our server"
  echo ""
} >> "$REPORT"

export ANTHROPIC_BASE_URL="http://127.0.0.1:${PORT}"
export ANTHROPIC_AUTH_TOKEN="any-value-server-ignores-it"

T_START=$(ms_now)
CLAUDE_OUT=$(claude -p "what is 2+2?" --bare --output-format json --model local-model 2>&1) || true
T_END=$(ms_now)
T_DUR=$((T_END - T_START))

{
  echo "Duration: ${T_DUR} ms"
  echo ""
  echo '```json'
  echo "$CLAUDE_OUT" | $PRETTY 2>/dev/null || echo "$CLAUDE_OUT"
  echo '```'
  echo ""
} >> "$REPORT"

echo "[tier3] Step 5 — Tool use (Read)..."
{
  echo "## Step 5 — Tool use (Read /etc/hostname)"
  echo ""
} >> "$REPORT"

T_START=$(ms_now)
CLAUDE_OUT=$(claude -p "use your Read tool to read /etc/hostname and tell me what it says" \
  --bare --output-format json --model local-model --allowedTools "Read" 2>&1) || true
T_END=$(ms_now)
T_DUR=$((T_END - T_START))

{
  echo "Duration: ${T_DUR} ms"
  echo ""
  echo '```json'
  echo "$CLAUDE_OUT" | $PRETTY 2>/dev/null || echo "$CLAUDE_OUT"
  echo '```'
  echo ""

  echo "## Server log (last 100 lines)"
  echo ""
  echo '```'
  tail -100 "$SERVER_LOG"
  echo '```'
  echo ""

  echo "## Verdict"
  echo ""
  echo "Inspect Step 4 — if Claude Code returned a JSON envelope with a non-empty"
  echo "assistant message, the protocol path against the real model is working."
  echo "If it returned an error, see the server log above for the failure mode."
} >> "$REPORT"

echo ""
echo "[tier3] Done. Report at: $REPORT"
echo "[tier3] Server log preserved at: $SERVER_LOG"
echo ""
echo "View the report:"
echo "  open '$REPORT'   # macOS"
echo "  cat  '$REPORT'   # any shell"
