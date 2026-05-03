---
description: Stop the running LiteRT-LM server.
allowed-tools: Bash(python3 *)
---

Stop the LiteRT-LM server by running:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/litert_lm_control.py" stop
```

Report the script's output verbatim. If no server is running, the script
exits cleanly and says so; do not treat that as an error. After a successful
stop, the PID file at `~/.litert-lm/server.pid` is removed.
