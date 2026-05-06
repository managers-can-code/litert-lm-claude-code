---
description: Print the env vars and claude command needed to point Claude Code at the local LiteRT-LM server.
allowed-tools: Bash(python3 *)
---

Print the env vars and example invocation the user needs in order to point
Claude Code at the running local server:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/litert_lm_control.py" status --print-env
```

If the server is running, that prints something like:

```bash
export ANTHROPIC_BASE_URL=http://localhost:9379
export ANTHROPIC_AUTH_TOKEN=any-value
# example:
claude -p "what is 2+2?" --model gemma-3n-e2b
```

The model id substituted in the example is whatever `/v1/models` reports.
Suggest the user paste the two `export` lines into their shell profile so
they don't have to repeat this every session.

If the server is not running, do not invent a model id. Tell the user to run
`/litert-lm-start <model-path>` first.
