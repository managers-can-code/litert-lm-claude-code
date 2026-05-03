---
description: Probe the local LiteRT-LM server's /v1/models endpoint and print health info.
allowed-tools: Bash(python3 *)
---

Show the current state of the local LiteRT-LM server by running:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/litert_lm_control.py" status
```

The script:

1. Reads the PID file at `~/.litert-lm/server.pid` (if any) and confirms the
   process is alive.
2. Pings `http://<host>:<port>/v1/models` and prints the returned model id.
3. Reports server uptime (seconds since the process started, where
   measurable).
4. Reports an approximate recent-request count by tailing
   `~/.litert-lm/server.log`.

Report the output verbatim. If the script reports "not running" or
"connection refused", suggest the user run `/litert-lm-start <model-path>`.
