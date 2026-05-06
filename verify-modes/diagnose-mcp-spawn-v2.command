#!/usr/bin/env bash
# Round 2 of MCP spawn diagnosis: write breadcrumbs to /tmp (always writable)
# so we can prove whether tool_litert_lm_generate is being entered at all.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_RUNNER="$SCRIPT_DIR/run-anthropic-server.command"
PLUGIN_DIR="$HOME/.claude/plugins/marketplace/litert-lm"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

echo "================================================================"
echo "diagnose-mcp-spawn-v2  -  $(date -u +%Y-%m-%dT%H:%MZ)"
echo "================================================================"

# Re-copy the marketplace install with our newly-instrumented file
echo ""
echo "--- refreshing marketplace install ---"
rm -rf "$PLUGIN_DIR"
cp -R "$SCRIPT_DIR/../plugin/litert-lm" "$PLUGIN_DIR"
echo "marketplace updated"

# Wipe both possible debug log locations
rm -f /tmp/litert-lm-mcp-debug.log
rm -f "$HOME/.litert-lm/mcp-generate-debug.log"

# Make sure the launcher is up
pkill -f run-anthropic-server 2>/dev/null
pkill -f tier3-runner 2>/dev/null
lsof -ti :9379 | xargs -r kill 2>/dev/null
echo ""
echo "--- starting launcher ---"
nohup "$SERVER_RUNNER" > /tmp/diag-server.log 2>&1 &
LAUNCHER_PID=$!
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:9379/v1/models >/dev/null 2>&1; then
    echo "  launcher up"
    break
  fi
  sleep 1
done

# Run claude -p with a delegation prompt
echo ""
echo "--- claude -p (delegation prompt) ---"
claude -p "Use the litert-lm-local subagent to say hi in one word" \
  --plugin-dir "$PLUGIN_DIR" \
  --output-format json \
  > /tmp/claude-p-out.json 2>&1

echo "claude -p exited"

# Read result
echo ""
echo "--- claude -p result ---"
python3 -c "import json; d=json.load(open('/tmp/claude-p-out.json')); print(d.get('result',''))" 2>/dev/null \
  || head -10 /tmp/claude-p-out.json

# Show breadcrumbs
echo ""
echo "--- /tmp/litert-lm-mcp-debug.log ---"
if [ -f /tmp/litert-lm-mcp-debug.log ]; then
  cat /tmp/litert-lm-mcp-debug.log
else
  echo "(no /tmp log written)"
fi

echo ""
echo "--- ~/.litert-lm/mcp-generate-debug.log ---"
if [ -f "$HOME/.litert-lm/mcp-generate-debug.log" ]; then
  cat "$HOME/.litert-lm/mcp-generate-debug.log"
else
  echo "(no home log written)"
fi

echo ""
echo "--- launcher: any incoming POST? ---"
grep -E "POST|/v1/messages|inference" /tmp/diag-server.log 2>/dev/null | head -10 \
  || echo "(no POST in launcher log)"

kill "$LAUNCHER_PID" 2>/dev/null

echo ""
echo "================================================================"
echo "If /tmp log shows ENTRY but no BEGIN: function entered, but cfg/url"
echo "  setup raised — the urllib request is never made"
echo "If /tmp log shows ENTRY + BEGIN + TCP connect: full flow works,"
echo "  and the launcher should show a POST"
echo "If /tmp log is empty: tool_litert_lm_generate was NEVER entered"
echo "  — Claude Code is calling something else with the same name"
echo "================================================================"
