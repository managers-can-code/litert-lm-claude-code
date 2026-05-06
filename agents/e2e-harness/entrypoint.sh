#!/usr/bin/env bash
# Agent B entrypoint:
#   1. Install litert-lm from /src
#   2. Start the server
#   3. Wait for readiness
#   4. Run the scenario harness
#   5. Persist the report to /reports

set -euo pipefail

REPORT_DIR="${REPORT_DIR:-/reports}"
mkdir -p "$REPORT_DIR"

echo "[entrypoint] Installing litert-lm from /src..."
/work/install_litert_lm.sh

if [ ! -f "$MODEL_PATH" ]; then
  echo "ERROR: model not found at $MODEL_PATH. Mount your model dir at /models." >&2
  exit 2
fi

echo "[entrypoint] Starting litert-lm serve --api anthropic..."
litert-lm serve --api anthropic --model "$MODEL_PATH" --host 0.0.0.0 --port 9379 \
  --max-concurrent 4 --max-request-bytes 4194304 --request-timeout-secs 300 \
  > /tmp/server.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

echo "[entrypoint] Waiting for server readiness on /v1/models..."
for i in $(seq 1 60); do
  if curl -s -f -o /dev/null "http://localhost:9379/v1/models"; then
    echo "[entrypoint] Server ready after ${i}s."
    break
  fi
  sleep 1
done

if ! curl -s -f -o /dev/null "http://localhost:9379/v1/models"; then
  echo "ERROR: server did not become ready in 60s. Server log:" >&2
  cat /tmp/server.log >&2
  exit 3
fi

echo "[entrypoint] Running scenarios..."
python3 /work/run_scenarios.py \
  --report "$REPORT_DIR/agent-B-e2e-report.md" \
  --server-log /tmp/server.log

EXIT_CODE=$?
echo "[entrypoint] Scenarios exited with $EXIT_CODE. Report at $REPORT_DIR/agent-B-e2e-report.md"
exit $EXIT_CODE
