#!/usr/bin/env bash
# diagnose-status-bug.command
#
# The subagent calls the litert_lm_status MCP tool, which returns
# reachable:false even though the launcher is listening on :9379.
# This script isolates which layer is broken:
#
#   A. Server itself (launcher) responds wrong on /v1/models
#   B. resolve_model_id() in litert_lm_control.py can't parse what the
#      server returns
#   C. tool_litert_lm_status() in litert_lm_mcp.py wraps it wrong
#   D. Network: curl works but Python urllib doesn't reach 127.0.0.1
#
# Brings up the launcher, runs each check, prints a diagnosis.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LAUNCHER="$OUTPUTS_DIR/tier3-runner/launcher.py"
SERVER_RUNNER="$SCRIPT_DIR/run-anthropic-server.command"
PLUGIN_SCRIPTS="$OUTPUTS_DIR/plugin/litert-lm/scripts"
PLUGIN_MCP="$OUTPUTS_DIR/plugin/litert-lm/mcp"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

LOG="$SCRIPT_DIR/diagnose-status-bug.log"
exec > >(tee "$LOG") 2>&1

echo "================================================================"
echo "diagnose-status-bug — $(date -u +%Y-%m-%dT%H:%MZ)"
echo "================================================================"

# ---------------------------------------------------------------------------
# Bring up server (skip if already up)
# ---------------------------------------------------------------------------
SERVER_STARTED_BY_US=0
if curl -fsS --max-time 2 http://127.0.0.1:9379/v1/models >/dev/null 2>&1; then
  echo "Server already up on :9379 — using it."
  SERVER_PID=""
else
  echo "Starting launcher in background..."
  nohup "$SERVER_RUNNER" > "$SCRIPT_DIR/diagnose-server.log" 2>&1 &
  SERVER_PID=$!
  SERVER_STARTED_BY_US=1
  echo "  pid=$SERVER_PID"
  for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 http://127.0.0.1:9379/v1/models >/dev/null 2>&1; then
      echo "  server ready after warmup"
      break
    fi
    sleep 1
  done
  if ! curl -fsS --max-time 2 http://127.0.0.1:9379/v1/models >/dev/null 2>&1; then
    echo "FATAL: launcher never came up. See $SCRIPT_DIR/diagnose-server.log"
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
    exit 1
  fi
fi

cleanup() {
  if [ "$SERVER_STARTED_BY_US" = "1" ] && [ -n "${SERVER_PID:-}" ]; then
    echo ""
    echo "Stopping server (pid=$SERVER_PID)..."
    kill "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo ""
echo "--- A. Direct curl /v1/models ---"
RESP="$(curl -fsS --max-time 5 http://127.0.0.1:9379/v1/models 2>&1)"
RC=$?
if [ "$RC" -eq 0 ]; then
  echo "OK ($((${#RESP})) bytes):"
  echo "$RESP" | python3 -m json.tool 2>/dev/null || echo "$RESP" | head -10
else
  echo "FAIL (rc=$RC): $RESP"
fi

echo ""
echo "--- B. Python urllib from system python3 ---"
python3 - <<'PY'
import json, sys, urllib.request, urllib.error
try:
    with urllib.request.urlopen("http://127.0.0.1:9379/v1/models", timeout=5) as r:
        body = r.read().decode("utf-8")
    data = json.loads(body)
    print("OK: parsed JSON, keys =", list(data.keys()))
    if isinstance(data.get("data"), list) and data["data"]:
        print("  data[0]:", data["data"][0])
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")
PY

echo ""
echo "--- C. resolve_model_id() from plugin's litert_lm_control.py ---"
python3 - <<PY
import sys
sys.path.insert(0, "$PLUGIN_SCRIPTS")
import litert_lm_control as c
result = c.resolve_model_id("127.0.0.1", 9379)
print(f"resolve_model_id('127.0.0.1', 9379) = {result!r}")
print(f"DEFAULT_HOST={c.DEFAULT_HOST!r}, DEFAULT_PORT={c.DEFAULT_PORT}")
PY

echo ""
echo "--- D. tool_litert_lm_status() from plugin's litert_lm_mcp.py ---"
python3 - <<PY
import sys, json
sys.path.insert(0, "$PLUGIN_SCRIPTS")
sys.path.insert(0, "$PLUGIN_MCP")
import litert_lm_mcp
status = litert_lm_mcp.tool_litert_lm_status()
print(json.dumps(status, indent=2, default=str))
PY

echo ""
echo "--- E. Reproduce as the MCP subprocess sees it ---"
echo "Spawning litert_lm_mcp.py via JSON-RPC over stdio…"
python3 - <<PY
import json, subprocess, time

# Spawn the MCP server like Claude Code does (stdio transport).
proc = subprocess.Popen(
    ["python3", "$PLUGIN_MCP/litert_lm_mcp.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1,
)

def send(obj):
    line = json.dumps(obj) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()

def recv(timeout=10):
    """Read one JSON-RPC line from stdout (with timeout)."""
    import select
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.5)
        if ready:
            line = proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                # Notification or something else — keep going
                continue
    return None

try:
    # 1. Initialize
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "diag", "version": "0"},
    }})
    init_resp = recv(timeout=10)
    print("init response:")
    print(json.dumps(init_resp, indent=2)[:600])

    # 2. Send 'initialized' notification (required by spec)
    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    # 3. List tools
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    list_resp = recv(timeout=10)
    if list_resp:
        tools = (list_resp.get("result") or {}).get("tools", [])
        print(f"\ntools/list returned {len(tools)} tools:")
        for t in tools:
            print(f"  - {t.get('name')}")

    # 4. Call litert_lm_status
    send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "litert_lm_status", "arguments": {},
    }})
    call_resp = recv(timeout=10)
    print("\ntools/call litert_lm_status response:")
    print(json.dumps(call_resp, indent=2)[:1500])

finally:
    try:
        proc.stdin.close()
    except Exception:
        pass
    proc.terminate()
    try:
        _, err = proc.communicate(timeout=3)
        if err:
            print("\nstderr from MCP subprocess:")
            print(err[:1500])
    except Exception:
        proc.kill()
PY

echo ""
echo "================================================================"
echo "Diagnosis complete. Review the four sections above."
echo "  A → server's /v1/models response shape"
echo "  B → can system python reach the server"
echo "  C → does resolve_model_id parse the response"
echo "  D → what tool_litert_lm_status returns when called in-process"
echo "  E → what the spawned MCP subprocess returns"
echo ""
echo "If A=OK, B=OK, C=FAIL → parser bug in litert_lm_control"
echo "If A=OK, B=OK, C=OK, D=reachable:false → bug in tool wrapper"
echo "If A=OK, B=FAIL → urllib blocked (firewall/IPv6)"
echo "If C=OK, D=OK, E=reachable:false → MCP subprocess env issue"
echo "================================================================"
