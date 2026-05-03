#!/usr/bin/env bash
# Auto-discover the cached gemma .litertlm and run Tier 3 verification.
# Designed to be double-clicked from Finder. No arguments needed.

set -u
exec > >(tee -a "$HOME/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs/tier3-runner/run-tier3-auto.log") 2>&1

OUTPUTS_DIR="/Users/ramiyengar/Library/Application Support/Claude/local-agent-mode-sessions/b4ec9bfb-e89e-423a-bef4-0394e5e8b846/719d98c6-e57d-47c7-80dd-63a3beec0515/local_9aa572bc-5d60-4b0d-b654-50f10af02e48/outputs"
RUNNER_DIR="$OUTPUTS_DIR/tier3-runner"
RUN_SH="$RUNNER_DIR/run-tier3.sh"

# Make sure things are on PATH (Terminal launched from Finder may have a minimal PATH).
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.cargo/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
# Pick up uv tool installs (litert-lm typically lives here)
export PATH="$HOME/Library/Application Support/uv/tools/litert-lm/bin:$PATH"
# Pick up node global installs (claude lives here)
export PATH="$HOME/.npm-global/bin:$PATH"

echo "================================================================"
echo "Tier 3 Auto-Runner"
echo "$(date)"
echo "================================================================"
echo ""
echo "PATH=$PATH"
echo ""

# Diagnostics block — always runs so we can see state even if we abort.
{
  echo "--- which python3 ---"; which python3 || echo "MISSING"
  echo "--- which litert-lm ---"; which litert-lm || echo "MISSING"
  echo "--- which claude ---"; which claude || echo "MISSING"
  echo "--- python3 -c 'import litert_lm' ---"
  python3 -c "import litert_lm; print('litert_lm OK, version:', getattr(litert_lm, '__version__', 'unknown'))" 2>&1 || echo "litert_lm import FAILED"
  echo "--- litert-lm list (top 30 lines) ---"
  litert-lm list 2>&1 | head -30 || echo "litert-lm list FAILED"
  echo "--- find ~/.cache for .litertlm ---"
  find ~/.cache -name "*.litertlm" 2>/dev/null | head -10 || true
  echo "--- find ~/Library/Caches for .litertlm ---"
  find ~/Library/Caches -name "*.litertlm" 2>/dev/null | head -10 || true
  echo "--- find ~/Library/Application\ Support for .litertlm ---"
  find "$HOME/Library/Application Support" -name "*.litertlm" 2>/dev/null | head -10 || true
  echo "--- find ~/Downloads for .litertlm ---"
  find ~/Downloads -name "*.litertlm" 2>/dev/null | head -10 || true
  echo "--- find ~ for litertlm (deep, capped) ---"
  find ~ -name "*.litertlm" 2>/dev/null | head -20 || true
}

echo ""
echo "Picking model file..."
MODEL_PATH=""
# Strategy: prefer the exact gemma-4-E2B-it filename, then any gemma E2B, then any .litertlm.
for cand in \
  $(find ~ -iname "gemma-4-E2B-it.litertlm" 2>/dev/null) \
  $(find ~ -iname "gemma*E2B*.litertlm" 2>/dev/null) \
  $(find ~ -iname "*.litertlm" 2>/dev/null); do
  if [ -f "$cand" ]; then
    MODEL_PATH="$cand"
    break
  fi
done

if [ -z "$MODEL_PATH" ]; then
  echo ""
  echo "ERROR: could not auto-locate any .litertlm file."
  echo "Pre-cache it with: litert-lm run --from-huggingface-repo=litert-community/Gemma-4-E2B-it gemma-4-E2B-it.litertlm --prompt='hi'"
  echo ""
  echo "Discovery log saved at: $RUNNER_DIR/run-tier3-auto.log"
  echo "Closing in 30 seconds..."
  sleep 30
  exit 1
fi

echo "Selected model: $MODEL_PATH"
echo ""

if [ ! -x "$RUN_SH" ]; then
  echo "ERROR: $RUN_SH is not executable. Fixing..."
  chmod +x "$RUN_SH"
fi

echo "Invoking run-tier3.sh..."
echo "================================================================"
"$RUN_SH" "$MODEL_PATH"
RC=$?
echo "================================================================"
echo "run-tier3.sh exited with code $RC"
echo ""
echo "Report (if produced): $OUTPUTS_DIR/tier3-real-report.md"
echo ""
echo "This window will close in 30 seconds. Check the report file."
sleep 30
exit $RC
