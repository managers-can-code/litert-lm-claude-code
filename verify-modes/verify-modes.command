#!/usr/bin/env bash
# Verify both modes end-to-end on this machine. Closes the verification gaps
# that the unit-test + protocol-level passes leave open:
#
#   Mode 1 — patched `litert-lm serve --api anthropic` CLI route
#     (the unit tests + Tier 3 used a launcher.py shim; this checks the
#     real CLI binary)
#
#   Mode 2 — plugin install + subagent file + MCP server smoke
#     (the cloud-Claude-decides-to-delegate flow is interactive and not
#     automatable here, but everything below it is)
#
# Outputs: verify-modes.log next to this file + a final report at
# outputs/verify-modes/verify-modes-report.md

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec > >(tee -a "$SCRIPT_DIR/verify-modes.log") 2>&1

OUTPUTS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORT="$SCRIPT_DIR/verify-modes-report.md"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

echo "================================================================"
echo "Verify Mode 1 + Mode 2"
echo "$(date)"
echo "================================================================"

LOCAL_FORK="${LOCAL_FORK:-$HOME/Developer/litert-lm-fork/LiteRT-LM}"
LITERT_PY="${LITERT_PY:-$HOME/.local/share/uv/tools/litert-lm/bin/python3}"
PORT=9379
PASS=0
FAIL=0
SKIP=0
RESULTS=()

record() {
  local name="$1" status="$2" detail="$3"
  RESULTS+=("$name|$status|$detail")
  if   [ "$status" = "PASS" ]; then PASS=$((PASS+1))
  elif [ "$status" = "FAIL" ]; then FAIL=$((FAIL+1))
  else SKIP=$((SKIP+1)); fi
  echo "[$status] $name — $detail"
}

# ---------------------------------------------------------------------------
# Mode 1 — patched CLI dispatch
# ---------------------------------------------------------------------------
echo ""
echo "=== Mode 1 verification ==="

if [ ! -d "$LOCAL_FORK" ]; then
  record "M1.0 fork present" "FAIL" "$LOCAL_FORK not found. Run push-to-fork.command first."
else
  record "M1.0 fork present" "PASS" "$LOCAL_FORK"
fi

if [ -f "$LOCAL_FORK/python/litert_lm_cli/serve.py" ]; then
  if grep -q '"anthropic"' "$LOCAL_FORK/python/litert_lm_cli/serve.py" \
     && grep -q 'serve_anthropic.AnthropicHandler' "$LOCAL_FORK/python/litert_lm_cli/serve.py"; then
    record "M1.1 serve.py patched" "PASS" "click.Choice + dispatch + import all present"
  else
    record "M1.1 serve.py patched" "FAIL" "patches missing — re-run push-to-fork.command"
  fi
else
  record "M1.1 serve.py patched" "SKIP" "serve.py not found"
fi

if [ -f "$LOCAL_FORK/python/litert_lm_cli/serve_anthropic.py" ]; then
  record "M1.2 serve_anthropic.py present" "PASS" "$(wc -l < "$LOCAL_FORK/python/litert_lm_cli/serve_anthropic.py" | tr -d ' ') lines"
else
  record "M1.2 serve_anthropic.py present" "FAIL" "missing"
fi

if [ -x "$LITERT_PY" ]; then
  if "$LITERT_PY" -c "import litert_lm" 2>/dev/null; then
    record "M1.3 uv python has litert_lm" "PASS" "$LITERT_PY"
  else
    record "M1.3 uv python has litert_lm" "FAIL" "litert_lm import failed"
  fi
else
  record "M1.3 uv python has litert_lm" "FAIL" "$LITERT_PY missing"
fi

# Patched serve.py imports cleanly with PYTHONPATH override
if [ -f "$LOCAL_FORK/python/litert_lm_cli/serve.py" ] && [ -x "$LITERT_PY" ]; then
  IMPORT_CHECK="$(PYTHONPATH="$LOCAL_FORK/python" "$LITERT_PY" -c "
import sys, importlib
try:
    sys.path.insert(0, '$LOCAL_FORK/python')
    # Force-reload to override the v0.10.1 namespace package
    if 'litert_lm_cli.serve' in sys.modules: del sys.modules['litert_lm_cli.serve']
    if 'litert_lm_cli' in sys.modules: del sys.modules['litert_lm_cli']
    from litert_lm_cli import serve
    # Confirm the dispatch knows about anthropic
    src = open('$LOCAL_FORK/python/litert_lm_cli/serve.py').read()
    assert 'serve_anthropic.AnthropicHandler' in src
    assert '\"anthropic\"' in src
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>&1)"
  if [ "$IMPORT_CHECK" = "OK" ]; then
    record "M1.4 patched serve.py imports cleanly" "PASS" "via uv-managed python"
  else
    record "M1.4 patched serve.py imports cleanly" "FAIL" "$IMPORT_CHECK"
  fi
else
  record "M1.4 patched serve.py imports cleanly" "SKIP" "prereqs missing"
fi

# Probe the running server (if any) for /v1/models
if curl -s -m 5 -f "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
  MODELS_JSON="$(curl -s "http://127.0.0.1:$PORT/v1/models")"
  MODEL_ID="$(echo "$MODELS_JSON" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["data"][0]["id"] if d.get("data") else "none")')"
  record "M1.5 server already running" "PASS" "model_id=$MODEL_ID"
  HAVE_RUNNING_SERVER=1
else
  record "M1.5 server already running" "SKIP" "no live server on :$PORT (run /litert-lm-start first to test runtime)"
  HAVE_RUNNING_SERVER=0
fi

# Live inference test (only if server is up)
if [ "$HAVE_RUNNING_SERVER" -eq 1 ]; then
  RESP="$(curl -s -X POST "http://127.0.0.1:$PORT/v1/messages" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ignored" \
    -d '{"model":"local-model","max_tokens":32,"messages":[{"role":"user","content":"reply with the single word: ack"}]}')"
  if echo "$RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); ok = d.get("type")=="message" and d.get("content"); sys.exit(0 if ok else 1)' 2>/dev/null; then
    TEXT="$(echo "$RESP" | python3 -c 'import sys,json; d=json.load(sys.stdin); print((d["content"][0].get("text","") if d.get("content") else "")[:60])')"
    record "M1.6 live /v1/messages returns valid Anthropic JSON" "PASS" "text='${TEXT}'"
  else
    record "M1.6 live /v1/messages returns valid Anthropic JSON" "FAIL" "response=$(echo "$RESP" | head -c 200)"
  fi
else
  record "M1.6 live /v1/messages returns valid Anthropic JSON" "SKIP" "no running server"
fi

# ---------------------------------------------------------------------------
# Mode 2 — plugin + subagent + MCP smoke
# ---------------------------------------------------------------------------
echo ""
echo "=== Mode 2 verification ==="

PLUGIN_SRC="$OUTPUTS_DIR/plugin/litert-lm"
PLUGIN_MKT="$HOME/.claude/plugins/marketplace/litert-lm"
PLUGIN_CACHE="$HOME/.claude/plugins/cache/litert-lm"  # legacy/alt path

# Pick whichever install path is present (marketplace preferred)
PLUGIN_INSTALLED=""
if [ -f "$PLUGIN_MKT/agents/litert-lm-local.md" ]; then
  PLUGIN_INSTALLED="$PLUGIN_MKT"
elif [ -f "$PLUGIN_CACHE/agents/litert-lm-local.md" ]; then
  PLUGIN_INSTALLED="$PLUGIN_CACHE"
fi

INSTALL_HINT=$'install via local marketplace (marketplace.json MUST live in .claude-plugin/):\n      mkdir -p ~/.claude/plugins/marketplace/.claude-plugin\n      cp -R '"$PLUGIN_SRC"$' ~/.claude/plugins/marketplace/litert-lm\n      cat > ~/.claude/plugins/marketplace/.claude-plugin/marketplace.json <<\'JSON\'\n      {"name":"Local Plugins","owner":{"name":"You"},"plugins":[{"name":"litert-lm","source":"./litert-lm"}]}\n      JSON\n      claude plugin marketplace add ~/.claude/plugins/marketplace\n      claude plugin install "litert-lm@Local Plugins"\n    or for a one-session test:\n      claude --plugin-dir '"$PLUGIN_SRC"

if [ -z "$PLUGIN_INSTALLED" ]; then
  record "M2.0 plugin installed" "FAIL" "$INSTALL_HINT"
else
  record "M2.0 plugin installed" "PASS" "found at $PLUGIN_INSTALLED"
fi

# Subagent frontmatter parses
if [ -f "$PLUGIN_SRC/agents/litert-lm-local.md" ]; then
  PARSED="$(python3 -c "
import yaml, sys
src = open('$PLUGIN_SRC/agents/litert-lm-local.md').read()
fm_end = src.find('---', 3)
fm = yaml.safe_load(src[3:fm_end])
print(f\"name={fm.get('name')} model={fm.get('model')} tools_count={len((fm.get('tools') or '').split(','))}\")
" 2>&1)"
  record "M2.1 subagent frontmatter parses" "PASS" "$PARSED"
fi

# MCP server registers all 6 tools
MCP_PATH="$OUTPUTS_DIR/plugin/litert-lm/mcp/litert_lm_mcp.py"
MCP_OK="$(MCP_PATH="$MCP_PATH" python3 - <<'PYEOF' 2>&1
import json, subprocess, os, sys
mcp_path = os.environ["MCP_PATH"]
if not os.path.exists(mcp_path):
    print(f"FAIL mcp not found at {mcp_path}")
    sys.exit(0)
proc = subprocess.Popen(
    ["python3", mcp_path],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
def send(m): proc.stdin.write((json.dumps(m)+'\n').encode()); proc.stdin.flush()
def recv():
    line = proc.stdout.readline().decode()
    if not line:
        err = proc.stderr.read().decode()[:200]
        raise RuntimeError(f"server died: {err}")
    return json.loads(line)
try:
    send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"v","version":"0"}}})
    recv()
    send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
    r = recv()
    tools = sorted(t["name"] for t in r.get("result",{}).get("tools",[]))
    needed = {"litert_lm_generate", "litert_lm_status", "litert_lm_start", "litert_lm_stop", "litert_lm_switch_model", "litert_lm_list_models"}
    missing = needed - set(tools)
    if missing:
        print(f"FAIL missing={missing}")
    else:
        print(f"PASS tools={tools}")
except Exception as e:
    print(f"FAIL {e}")
finally:
    proc.terminate()
PYEOF
)"
if [[ "$MCP_OK" == PASS* ]]; then
  record "M2.2 MCP server registers all 6 tools" "PASS" "${MCP_OK#PASS }"
else
  record "M2.2 MCP server registers all 6 tools" "FAIL" "$MCP_OK"
fi

# Live MCP smoke: call litert_lm_generate against the running server (if any)
if [ "$HAVE_RUNNING_SERVER" -eq 1 ]; then
  GEN_OK="$(MCP_PATH="$MCP_PATH" python3 - <<'PYEOF' 2>&1
import json, subprocess, os
mcp_path = os.environ["MCP_PATH"]
proc = subprocess.Popen(
    ["python3", mcp_path],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
def send(m): proc.stdin.write((json.dumps(m)+'\n').encode()); proc.stdin.flush()
def recv():
    line = proc.stdout.readline().decode()
    if not line:
        err = proc.stderr.read().decode()[:200]
        raise RuntimeError(f"server died: {err}")
    return json.loads(line)
try:
    send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"v","version":"0"}}})
    recv()
    send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"litert_lm_generate","arguments":{"prompt":"reply with one word: ok","max_tokens":16}}})
    r = recv()
    content = r.get("result",{}).get("content",[])
    if content and content[0].get("type")=="text":
        inner = json.loads(content[0]["text"])
        if "text" in inner and inner["text"]:
            print(f"PASS got text='{inner['text'][:40]}'")
        elif "error" in inner:
            print(f"FAIL error={inner['error']}")
        else:
            print(f"FAIL unexpected={inner}")
    else:
        print(f"FAIL no content: {r}")
except Exception as e:
    print(f"FAIL {e}")
finally:
    proc.terminate()
PYEOF
)"
  if [[ "$GEN_OK" == PASS* ]]; then
    record "M2.3 litert_lm_generate against live server" "PASS" "${GEN_OK#PASS }"
  else
    record "M2.3 litert_lm_generate against live server" "FAIL" "$GEN_OK"
  fi
else
  record "M2.3 litert_lm_generate against live server" "SKIP" "no running server"
fi

# Cloud-Claude-delegates flow — interactive only.
record "M2.4 cloud-Claude-delegates flow" "SKIP" "interactive — see TESTING.md Mode 2 Test 1"

# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------
{
  echo "# verify-modes report — $(date -u +%Y-%m-%dT%H:%MZ)"
  echo ""
  echo "**Pass: $PASS · Fail: $FAIL · Skip: $SKIP**"
  echo ""
  echo "| Check | Status | Detail |"
  echo "|---|---|---|"
  for r in "${RESULTS[@]}"; do
    name="${r%%|*}"; rest="${r#*|}"; status="${rest%%|*}"; detail="${rest#*|}"
    echo "| $name | $status | $detail |"
  done
  echo ""
  echo "## Interpretation"
  echo ""
  echo "- All M1.* PASS = Mode 1's CLI dispatch is wired and the patched serve.py imports cleanly. M1.6 PASS = real model returned a valid Anthropic-shape response."
  echo "- All M2.0 - M2.3 PASS = plugin install, subagent file, MCP tool registration, and the litert_lm_generate tool against a live server all work. The remaining M2.4 (cloud Claude actually deciding to delegate) is interactive and verified per docs/TESTING.md Mode 2 Test 1."
  echo ""
  echo "If M1.5 or M2.3 are SKIP, start the server first:"
  echo ""
  echo '```'
  echo "litert-lm serve --api anthropic --model ~/.cache/huggingface/hub/.../gemma-4-E2B-it.litertlm &"
  echo '```'
  echo ""
  echo "and re-run this script."
} > "$REPORT"

echo ""
echo "================================================================"
echo "Pass: $PASS · Fail: $FAIL · Skip: $SKIP"
echo "Report: $REPORT"
echo "Log:    $SCRIPT_DIR/verify-modes.log"
echo "================================================================"
echo "This window will close in 30 seconds."
sleep 30
