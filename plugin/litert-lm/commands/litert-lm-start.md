---
description: Start a local LiteRT-LM Anthropic-compatible server in the background.
argument-hint: "[model-path] [--port N] [--host H]"
allowed-tools: Bash(python3 *)
---

Start a local `litert-lm serve --api anthropic` server in the background by
running the helper script:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/litert_lm_control.py" start $ARGUMENTS
```

If `$ARGUMENTS` is empty, the script falls back to the last-used model
recorded in `~/.litert-lm/config.json`. If there is no such record, ask the
user for an absolute path to a `.litertlm` file (recommend Gemma 3n E2B from
<https://ai.google.dev/edge/litert-lm/models> if they don't have one yet).

After the script returns, report:

- the PID it printed,
- the URL the server is listening on,
- a one-line "next" suggestion: run `/litert-lm-config` to print the env vars
  Claude Code needs.

If the script reports the server is already running, do not retry; just
report the existing PID and URL.

If the script reports the model file is missing, surface that error directly
to the user and stop. Do not try to download anything.
