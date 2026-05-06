#!/usr/bin/env bash
# diagnose-mcp-spawn.command
#
# Watches what actually happens when claude -p spawns the litert-lm MCP.
# Specifically: what file path is the spawned python3 running?
# Without this answer we can't know whether our updated marketplace install
# is the one Claude Code is actually invoking.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_RUNNER="$SCRIPT_DIR/run-anthropic-server.command"
PLUGIN_DIR="$HOME/.claude/plugins/marketplace/litert-lm"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

echo "================================================================"
echo "diagnose-mcp-spawn  -  $(date -u +%Y-%m-%dT%H:%MZ)"
echo "================================================================"

echo ""
echo "--- Claude config files ---"
for f in "$HOME/.claude.json" "$HOME/.claude/settings.json"; do
  if [ -f "$f" ]; then
    echo "[$f] exists"
    grep -i "litert\|mcp" "$f" 2>/dev/null | head -20
    echo "---"
  else
    echo "[$f] missing"
  fi
done

echo ""
echo "--- All copies of litert_lm_mcp.py on disk ---"
find "$HOME/.claude" -name "litert_lm_mcp.py" 2>/dev/null

echo ""
echo "--- Marketplace plugin's litert_lm_mcp.py first 5 lines + has-debug-string ---"
head -5 "$PLUGIN_DIR/mcp/litert_lm_mcp.py"
echo "(grep mcp-generate-debug.log -> count):"
grep -c "mcp-generate-debug.log" "$PLUGIN_DIR/mcp/litert_lm_mcp.py"

echo ""
echo "--- Reset state ---"
rm -f "$HOME/.litert-lm/mcp-generate-debug.log"
pkill -f run-anthropic-server 2>/dev/null
pkill -f tier3-runner 2>/dev/null
lsof -ti :9379 | xargs -r kill 2>/dev/null

echo "Starting launcher in background..."
nohup "$SERVER_RUNNER" > /tmp/diag-server.log 2>&1 &
LAUNCHER_PID=$!
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:9379/v1/models >/dev/null 2>&1; then
    echo "  launcher up"
    break
  fi
  sleep 1
done

echo ""
echo "--- Running claude -p with a delegation prompt ---"
claude -p "Use the litert-lm-local subagent to say hi in one word" \
  --plugin-dir "$PLUGIN_DIR" \
  --output-format json \
  > /tmp/claude-p-out.json 2>&1 &
CLAUDE_PID=$!

# Take process snapshots while claude -p is alive
sleep 3
echo ""
echo "--- ps snapshot t+3s ---"
ps -ef | grep -iE "litert|mcp|claude" | grep -v grep | head -30

sleep 3
echo ""
echo "--- ps snapshot t+6s ---"
ps -ef | grep -iE "litert|mcp|claude" | grep -v grep | head -30

# Find python processes whose command line includes litert_lm_mcp.py
echo ""
echo "--- ANY python process running litert_lm_mcp.py ---"
ps -eo pid,ppid,comm,args | grep "litert_lm_mcp" | grep -v grep

wait $CLAUDE_PID
echo ""
echo "--- claude -p result ---"
python3 -c "import json; d=json.load(open('/tmp/claude-p-out.json')); print('result:', d.get('result',''))" 2>/dev/null \
  || cat /tmp/claude-p-out.json | head -50

echo ""
echo "--- debug log after claude -p ---"
if [ -f "$HOME/.litert-lm/mcp-generate-debug.log" ]; then
  cat "$HOME/.litert-lm/mcp-generate-debug.log"
else
  echo "(no debug log written - tool_litert_lm_generate from our marketplace install was NOT executed)"
fi

echo ""
echo "--- launcher stderr/stdout tail ---"
tail -30 /tmp/diag-server.log 2>/dev/null

kill "$LAUNCHER_PID" 2>/dev/null

echo ""
echo "================================================================"
echo "Diagnosis: look for the line under 'ANY python process running"
echo "litert_lm_mcp.py' - the path it shows is the file Claude Code"
echo "actually executes for the MCP."
echo "================================================================"
