---
description: Stop the running LiteRT-LM server and start it again with a different model.
argument-hint: "<model-path> [--port N]"
allowed-tools: Bash(python3 *)
---

Hot-swap the local LiteRT-LM server to a different model by running:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/litert_lm_control.py" switch $ARGUMENTS
```

The script stops any running server, then starts a new one with the supplied
model path. After it returns, summarise the new state for the user (PID,
model id, URL) and remind them that the model id passed to
`claude --model` may have changed - they can confirm with `/litert-lm-status`.

If `$ARGUMENTS` is empty, ask the user which model to switch to. Do not pick
one for them.
