#!/usr/bin/env bash
# verify-mode2-live.command
#
# Live end-to-end test of Mode 2 (subagent delegation). Sends a prompt to
# cloud Claude that explicitly requests delegation to the litert-lm-local
# subagent, captures the full transcript, and detects evidence that the
# subagent + the local model actually ran.
#
# Prereqs (see TESTING.md):
# - litert-lm fork installed (uv tool install)
# - Claude Code installed and authenticated (`claude` on PATH)
# - The litert-lm plugin installed via local marketplace (verify-modes M2.0 PASS)
# - A cached .litertlm model file
#
# This script will:
#   1. Make sure ANTHROPIC_BASE_URL is NOT set (Mode 2 needs cloud connection).
#   2. Start the litert-lm server in the background if not already running.
#   3. Run `claude -p` with a delegation prompt + JSON output capture.
#   4. Search the captured output for evidence of subagent / MCP-tool usage.
#   5. Write a report with PASS/FAIL.
#
# Designed to be double-clickable from Finder.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec > >(tee -a "$SCRIPT_DIR/verify-mode2-live.log") 2>&1

OUTPUTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Make sure tools installed via Homebrew + uv + npm-global are visible.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:$HOME/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
export PATH="$HOME/Library/Application Support/uv/tools/litert-lm/bin:$PATH"

REPORT="$SCRIPT_DIR/verify-mode2-live-report.md"
TRANSCRIPT="$SCRIPT_DIR/verify-mode2-live-transcript.txt"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
results=()
record() {
  local name="$1" status="$2" detail="$3"
  results+=("| $name | $status | $detail |")
  printf '%-50s  %-4s  %s\n' "$name" "$status" "$detail"
}

banner() {
  echo "================================================================"
  echo "verify-mode2-live  —  $(date -u +%Y-%m-%dT%H:%MZ)"
  echo "================================================================"
}
banner

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
if ! command -v claude >/dev/null 2>&1; then
  record "preflight: claude on PATH" "FAIL" "claude CLI not found — install via npm install -g @anthropic-ai/claude-code"
  exit_code=1
else
  CLAUDE_VERSION="$(claude --version 2>/dev/null | head -1)"
  record "preflight: claude on PATH" "PASS" "$CLAUDE_VERSION"
fi

if ! command -v litert-lm >/dev/null 2>&1; then
  record "preflight: litert-lm on PATH" "FAIL" "litert-lm CLI not found"
  exit_code=1
else
  record "preflight: litert-lm on PATH" "PASS" "$(which litert-lm)"
fi

# The standalone runner is required because upstream litert-lm doesn't yet
# ship a `serve` subcommand. Our PR adds it; until it lands, we run the
# patched serve.py directly via the uv tool's python.
SERVER_RUNNER="$SCRIPT_DIR/run-anthropic-server.command"
if [ ! -x "$SERVER_RUNNER" ]; then
  record "preflight: server runner present" "FAIL" "missing $SERVER_RUNNER"
  exit 1
else
  record "preflight: server runner present" "PASS" "$SERVER_RUNNER"
fi

# Mode 2 requires the cloud connection; bail if Mode 1 env vars are set.
if [ "${ANTHROPIC_BASE_URL:-}" != "" ]; then
  record "preflight: ANTHROPIC_BASE_URL unset" "FAIL" "currently set to '$ANTHROPIC_BASE_URL' — Mode 2 needs cloud. Run: unset ANTHROPIC_BASE_URL ANTHROPIC_AUTH_TOKEN"
  echo ""
  echo "Refusing to run with ANTHROPIC_BASE_URL set — would route everything"
  echo "to your local server and bypass the very subagent we're testing."
  exit 1
else
  record "preflight: ANTHROPIC_BASE_URL unset" "PASS" "ok — cloud Claude reachable"
fi

# ---------------------------------------------------------------------------
# Server bring-up (if not already up)
# ---------------------------------------------------------------------------
SERVER_STARTED_BY_US=0
if curl -fsS --max-time 2 http://localhost:9379/v1/models >/dev/null 2>&1; then
  record "server: already up" "PASS" "responding on :9379"
else
  MODEL_PATH="$(find "$HOME/.cache/huggingface" -name "*.litertlm" 2>/dev/null | head -1)"
  if [ -z "$MODEL_PATH" ]; then
    record "server: cached model" "FAIL" "no .litertlm under ~/.cache/huggingface — run: litert-lm run --from-huggingface-repo=litert-community/Gemma-4-E2B-it gemma-4-E2B-it.litertlm --prompt hi"
    exit 1
  fi
  echo ""
  echo "Starting Anthropic API server via standalone runner…"
  echo "  model: $MODEL_PATH"
  nohup "$SERVER_RUNNER" "$MODEL_PATH" \
    > "$SCRIPT_DIR/litert-lm-server.log" 2>&1 &
  SERVER_PID=$!
  SERVER_STARTED_BY_US=1
  echo "  pid: $SERVER_PID"

  # Wait up to 60s for the server to come up.
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 http://localhost:9379/v1/models >/dev/null 2>&1; then
      record "server: started by runner" "PASS" "pid=$SERVER_PID up after warmup"
      break
    fi
    sleep 1
  done

  if ! curl -fsS --max-time 2 http://localhost:9379/v1/models >/dev/null 2>&1; then
    record "server: started by runner" "FAIL" "did not respond on :9379 after 60s — see $SCRIPT_DIR/litert-lm-server.log"
    [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Compose the delegation prompt
# ---------------------------------------------------------------------------
PROMPT='Use the local model (the litert-lm-local subagent) to generate exactly one short Python docstring for this function. Delegate via Task(subagent_type="litert-lm-local", ...). Return only the docstring on a single line, no preamble.

def merge_sort(arr):
    if len(arr) <= 1:
        return arr
    mid = len(arr) // 2
    return merge(merge_sort(arr[:mid]), merge_sort(arr[mid:]))'

echo ""
echo "--- delegation prompt ---"
echo "$PROMPT"
echo "--- end prompt ---"
echo ""

# ---------------------------------------------------------------------------
# Run claude -p — try stream-json first (richer), fall back to json
# ---------------------------------------------------------------------------
> "$TRANSCRIPT"

# Locate the plugin so we can force-load it. `claude -p` does NOT pick up
# marketplace-installed plugins automatically — we need --plugin-dir.
PLUGIN_PATH=""
for cand in \
  "$HOME/.claude/plugins/marketplace/litert-lm" \
  "$HOME/.claude/plugins/cache/litert-lm" \
  "$OUTPUTS_DIR/plugin/litert-lm"
do
  if [ -f "$cand/agents/litert-lm-local.md" ]; then
    PLUGIN_PATH="$cand"
    break
  fi
done

if [ -z "$PLUGIN_PATH" ]; then
  record "preflight: plugin path for --plugin-dir" "FAIL" "no install location found with agents/litert-lm-local.md"
  exit 1
else
  record "preflight: plugin path for --plugin-dir" "PASS" "$PLUGIN_PATH"
fi

run_claude() {
  local fmt="$1"
  echo "" >> "$TRANSCRIPT"
  echo "=== claude -p with --output-format $fmt --plugin-dir <plugin> ===" >> "$TRANSCRIPT"
  # `--verbose` surfaces tool-use events on stream-json.
  local extra=""
  [ "$fmt" = "stream-json" ] && extra="--verbose"
  set +e
  claude -p "$PROMPT" --plugin-dir "$PLUGIN_PATH" --output-format "$fmt" $extra \
    >> "$TRANSCRIPT" 2>&1
  local rc=$?
  set -e
  echo "=== exit $rc ===" >> "$TRANSCRIPT"
  return $rc
}

CLAUDE_RC=0
if claude -p "ok" --plugin-dir "$PLUGIN_PATH" --output-format stream-json --verbose >/dev/null 2>&1; then
  echo "Using --output-format stream-json --verbose --plugin-dir $PLUGIN_PATH"
  run_claude stream-json
  CLAUDE_RC=$?
else
  echo "stream-json not accepted, falling back to --output-format json"
  run_claude json
  CLAUDE_RC=$?
fi

if [ "$CLAUDE_RC" -ne 0 ]; then
  record "claude -p exit code" "FAIL" "rc=$CLAUDE_RC — see $TRANSCRIPT"
else
  record "claude -p exit code" "PASS" "rc=0"
fi

# ---------------------------------------------------------------------------
# Detection — search the transcript for evidence of delegation
# ---------------------------------------------------------------------------
detect() {
  local label="$1" pattern="$2"
  if grep -E -q "$pattern" "$TRANSCRIPT"; then
    record "$label" "PASS" "found in transcript"
  else
    record "$label" "FAIL" "NOT found in transcript"
  fi
}

detect "evidence: subagent name 'litert-lm-local'" 'litert-lm-local'
detect "evidence: MCP tool 'litert_lm_generate'"   'litert_lm_generate'
detect "evidence: Task tool / subagent_type"       'subagent_type|"name":"Task"|tool_use.*Task'

# Bonus check — was the subagent actually advertised in the session init?
# Allow plugin-namespace prefix (e.g. "litert-lm:litert-lm-local").
if grep -E -q '"agents":\[[^]]*litert-lm-local' "$TRANSCRIPT"; then
  record "evidence: litert-lm-local in agents list" "PASS" "subagent registered in session"
else
  record "evidence: litert-lm-local in agents list" "FAIL" "subagent missing from session init — plugin probably not loaded"
fi

# Bonus check — did a real tool_use actually fire (vs. hallucinated XML)?
if grep -E -q '"tool_uses":[1-9]|"name":"litert_lm_status"|"name":"litert_lm_generate"' "$TRANSCRIPT"; then
  record "evidence: real MCP tool execution (not hallucinated)" "PASS" "tool_uses>0 or named MCP tool found"
else
  record "evidence: real MCP tool execution (not hallucinated)" "FAIL" "no real tool_use fired — subagent may have generated XML markup as prose"
fi

# Quick sanity: did the response include something that looks like a docstring?
if grep -E -q '"""|sorts.*list|Sort.*using|merge[- _]sort' "$TRANSCRIPT"; then
  record "evidence: response contains docstring-like text" "PASS" "found docstring-shaped text"
else
  record "evidence: response contains docstring-like text" "INFO" "no docstring marker found — model may have responded differently"
fi

# Did the local server log a hit during this window? (best-effort; needs server log)
if [ -f "$SCRIPT_DIR/litert-lm-server.log" ]; then
  if grep -E -q "POST /v1/messages|inference|generate" "$SCRIPT_DIR/litert-lm-server.log"; then
    record "evidence: local server saw a request" "PASS" "POST /v1/messages or inference in litert-lm-server.log"
  else
    record "evidence: local server saw a request" "INFO" "no inference entry in server log (server may have been up before this run; check timestamps in $SCRIPT_DIR/litert-lm-server.log)"
  fi
fi

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
if [ "$SERVER_STARTED_BY_US" -eq 1 ] && [ -n "${SERVER_PID:-}" ]; then
  echo ""
  echo "Stopping server we started (pid=$SERVER_PID)"
  kill "$SERVER_PID" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
PASS_COUNT=$(printf '%s\n' "${results[@]}" | grep -c '| PASS |' || true)
FAIL_COUNT=$(printf '%s\n' "${results[@]}" | grep -c '| FAIL |' || true)
INFO_COUNT=$(printf '%s\n' "${results[@]}" | grep -c '| INFO |' || true)

{
  echo "# verify-mode2-live report — $(date -u +%Y-%m-%dT%H:%MZ)"
  echo ""
  echo "**Pass: $PASS_COUNT · Fail: $FAIL_COUNT · Info: $INFO_COUNT**"
  echo ""
  echo "| Check | Status | Detail |"
  echo "|---|---|---|"
  printf '%s\n' "${results[@]}"
  echo ""
  echo "## Interpretation"
  echo ""
  echo "- **All PASS on subagent + MCP tool evidence** = cloud Claude actually delegated to the litert-lm-local subagent, which in turn called the litert_lm_generate MCP tool against your local server. M2.4 verified."
  echo "- **FAIL on 'subagent name'** = cloud Claude answered without using the subagent. Try a more explicit prompt, or check that the plugin is loaded (\`/plugin\` inside Claude Code)."
  echo "- **FAIL on 'MCP tool'** = subagent ran but didn't reach the MCP tool. Check server log + plugin.json mcpServers entry."
  echo "- The full transcript with raw JSON is in \`verify-mode2-live-transcript.txt\` next to this report."
} > "$REPORT"

echo ""
echo "================================================================"
cat "$REPORT"
echo "================================================================"
echo ""
echo "Full transcript: $TRANSCRIPT"
echo "Report:          $REPORT"

if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
