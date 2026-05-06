# Verification Report — 2026-05-01T00:41Z

## Summary

| Tier | Description | Status | Notes |
|------|-------------|--------|-------|
| 1.1 | Python `py_compile` (6 files) | PASS | 6/6 compile, 0 errors |
| 1.2 | `plugin.json` validity | PASS | Required keys present (name, version, description, author) |
| 1.3 | YAML frontmatter (8 files) | PARTIAL | 7/8 OK; `commands/litert-lm-start.md` has unquoted flow-sequence in `argument-hint` |
| 1.4 | Shell `bash -n` | PASS | 2/2 scripts parse, both have `#!/usr/bin/env bash` shebang |
| 1.5 | Standalone-litert regression grep | PASS | Source clean; one stale `.pyc` in `__pycache__` (not source) |
| 2a | Unit tests | 57/61 passing | 4 errors are test-harness gaps in `_FakeWfile` (no `requestline`); 0 production-code regressions |
| 2b | SSE byte-equality | PASS | All 4 fixtures (simple, multi-turn, cancel, tool-use) byte-equal; previously skipped, now exercised |
| 2c | MCP JSON-RPC | PASS | `initialize` + `tools/list` (5 tools) + `tools/call litert_lm_status` all returned valid JSON-RPC |
| 2d | Helper script | PASS | `--help` works on all 5 declared subcommands; `status` returns "not running" cleanly (exit 1, no traceback) |

Coverage on `serve_anthropic.py`: **50% line coverage** (604 stmts, 303 missed). Untested paths are dominated by streaming/non-streaming generation flows that require a live engine; the translator/auth/error/SSE-formatting layers are well-covered.

## Tier 1 details

### 1.1 Python compile (`python3 -m py_compile`)
```
=== pr/python/litert_lm_cli/serve_anthropic_integration_test.py === OK
=== pr/python/litert_lm_cli/serve_anthropic.py === OK
=== pr/python/litert_lm_cli/serve_anthropic_test.py === OK
=== plugin/litert-lm/scripts/litert_lm_control.py === OK
=== plugin/litert-lm/mcp/litert_lm_mcp.py === OK
=== agents/e2e-harness/run_scenarios.py === OK
```
**6 files, 0 compile errors.**

### 1.2 `plugin.json`
Valid JSON. Keys: `$schema, name, version, description, author, homepage, repository, license, keywords, mcpServers`.
- `name = "litert-lm"`
- `version = "0.1.0"`
- `description` present
- `author = {"name": "ram", "email": "tenheadedram@gmail.com"}`

### 1.3 YAML frontmatter (8 markdown files)
7 OK. **1 defect:**
```
YAML-ERROR: commands/litert-lm-start.md:
  while parsing a block mapping
  expected <block end>, but found '['
  in line 2, column 29:
    argument-hint: [model-path] [--port N] [--host H]
                                ^
```
Cause: unquoted `[...]` is parsed as a YAML flow sequence; the second `[...]` violates flow-sequence syntax. Fix: quote the value (`argument-hint: "[model-path] [--port N] [--host H]"`). This will likely cause Claude Code to silently ignore the frontmatter for this command.

### 1.4 Shell scripts
```
=== entrypoint.sh ===          shebang: #!/usr/bin/env bash    syntax OK
=== install_litert_lm.sh ===   shebang: #!/usr/bin/env bash    syntax OK
```

### 1.5 Standalone-litert regression
Source-level grep returns nothing. The single match is `plugin/litert-lm/scripts/__pycache__/litert_control.cpython-310.pyc` — a stale bytecode cache from a pre-rename build. Recommendation: `find outputs -name __pycache__ -exec rm -rf {} +` before publishing.

## Tier 2a details

Test workdir built per spec at `/tmp/verif/workdir/`:
```
workdir/
  litert_lm_cli/   __init__.py, serve.py (stub), serve_anthropic.py, serve_anthropic_test.py
  litert_lm/       __init__.py (stub: Engine, Conversation, Session, Backend, SamplerConfig,
                                 Tool, ToolEventHandler, Responses, tool_from_function,
                                 set_min_log_severity, LogSeverity)
```
Stubs derived by reading every `litert_lm.<attr>` and `_serve_module.<attr>` reference in `serve_anthropic.py` (Conversation, Engine, LogSeverity, SamplerConfig, Tool, set_min_log_severity; `_current_model_id`, `get_engine`, `run_server`).

Fixtures copied to `/tmp/verif/fixtures/` so `_FIXTURE_DIR = __file__.parent.parent.parent / "fixtures"` resolves.

Final result:
```
Ran 61 tests in 0.003s
FAILED (errors=4)

passed:  53 (then 57 after fixtures placed)
errors:  4
skipped: 0 (was 4 before fixtures placed)
```

The 4 errors all share the same root cause:
```
AttributeError: 'AnthropicHandler' object has no attribute 'requestline'
  File ".../http/server.py", line 551, in log_request
    self.requestline, str(code), str(size))
  File ".../http/server.py", line 498, in send_response
    self.log_request(code)
  File "serve_anthropic.py", line 890, in _write_json
    self.send_response(status)
```
Affected tests: `BodyLimitTest.test_oversize_returns_413`, `CountTokensTest.test_estimate_simple`, `ListModelsTest.test_no_model_loaded`, `ListModelsTest.test_with_loaded_model`. All four go through `_write_json` → `send_response` → `log_request`, which reads `self.requestline`. The test scaffolding (`_build_handler`/`_FakeWfile`) does not initialise `requestline`, so the stdlib base class crashes during logging. This is a **test-harness defect**, not a production regression: real HTTP traffic always populates `requestline` via `parse_request()`. A two-line fix in the test (`handler.requestline = "POST /v1/foo HTTP/1.1"; handler.command = "POST"`) would unblock all four. Did not patch since spec says "structural test gap → document and stop"; this is borderline but I left it alone to surface the gap.

Coverage:
```
Name                                   Stmts   Miss  Cover
litert_lm_cli/serve_anthropic.py         604    303    50%
litert_lm_cli/serve_anthropic_test.py    360     21    94%
TOTAL                                    999    331    67%
```

## Tier 2b details

The 4 SSE byte-equality tests are part of `FormatSseEventTest` (subclass of `parameterized.TestCase`). Once fixtures were placed at `/tmp/verif/fixtures/`, all 4 pass:
```
test_replays_each_fixture_event_byte_equal_simple('anthropic-sse-stream-1-simple.txt') ... ok
test_replays_each_fixture_event_byte_equal_multi_turn('anthropic-sse-stream-2-multi-turn.txt') ... ok
test_replays_each_fixture_event_byte_equal_cancel('anthropic-sse-stream-3-cancel.txt') ... ok
test_replays_each_fixture_event_byte_equal_tool_use('anthropic-sse-stream-4-tool-use.txt') ... ok
```
All 4 fixtures at `outputs/fixtures/anthropic-sse-stream-{1,2,3,4}-*.txt` were exercised. Other SSE-format tests (`test_simple_text_delta`, `test_message_stop`, `test_ping_matches_anthropic_byte_format`, `test_uses_lf_not_crlf`) also passed.

## Tier 2c details

Spawned `python3 plugin/litert-lm/mcp/litert_lm_mcp.py` over stdio.

**Request 1 — initialize:**
```
>>> {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"verifier","version":"0.0"}}}
<<< {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"litert-lm","version":"0.1.0"}}}
```

**Request 2 — tools/list:** returned all 5 expected tools with full input schemas:
- `litert_lm_status` (no args)
- `litert_lm_list_models` (no args)
- `litert_lm_start` (`model_path` required; `port`, `host` optional)
- `litert_lm_stop` (no args)
- `litert_lm_switch_model` (`model_path` required; `port`, `host` optional)

**Request 3 — tools/call `litert_lm_status`:**
```
<<< {"jsonrpc":"2.0","id":3,"result":{"isError":false,"content":[{"type":"text","text":
    "{\"pid\":null,\"host\":\"127.0.0.1\",\"port\":9379,
      \"url\":\"http://127.0.0.1:9379\",\"reachable\":false,
      \"model_id\":null,
      \"pid_file\":\"...litert-lm/server.pid\",
      \"log_file\":\"...litert-lm/server.log\"}"}]}}
```
`isError:false`, structured payload reports server-not-running cleanly. The MCP fallback path (no SDK installed) is what's running here, which is the worst-case for support coverage and it works.

## Tier 2d details

`litert_lm_control.py --help` lists 5 subcommands: `start, stop, status, switch, list-models`. Each `<sub> --help` returns valid argparse output:
- `start [-h] [--model M] [--port P] [--host H] [model_positional]`
- `stop [-h]`
- `status [-h] [--print-env]`
- `switch [-h] [--model M] [--port P] [--host H] [model_positional]`
- `list-models [-h]`

`status` against not-running server:
```
litert-lm status
  pid              : not running
  url              : http://127.0.0.1:9379
  reachable        : no
  model_id         : unknown
  recent_requests  : 0
  log              : .../litert-lm/server.log
exit=1
```
Clean output, no traceback. Exit 1 is semantic ("not running"), not a crash.

**Minor inconsistency:** there is a `commands/litert-lm-config.md` slash command but no `config` subcommand in `litert_lm_control.py`. Either the slash command does its work without invoking the helper, or it's a spec/impl drift. Not a blocker, but worth a glance.

## Confidence verdict

**v1 confidence: MEDIUM-HIGH.**

What's solid: the translator, error mapper, SSE encoder (including byte-equal replay against all 4 captured Anthropic stream fixtures), auth middleware, header-injection guard, body-size limit, concurrency gate, and structured logging are all unit-tested and pass. The MCP server speaks JSON-RPC correctly without the official SDK, enumerates the right 5 tools with full schemas, and the helper script's argparse surface and idle-state status path both work. Static checks are clean except for one quotable YAML defect in `litert-lm-start.md` and a stale `.pyc` to delete.

What I can't claim from this pass: the four `_write_json`-touching tests (BodyLimit, CountTokens, ListModels) didn't actually exercise that code path because of the test scaffolding gap, so the body-size 413 path and the 200-OK paths for `count_tokens` and `list_models` are unverified at the HTTP layer (though their helpers are covered). And nothing here proves end-to-end behaviour against a real `litert-lm` runtime — that's Tier 3.

## What still requires owner action (Tier 3)

- **Fix `commands/litert-lm-start.md`**: quote the `argument-hint` value to make the YAML parse.
- **Delete stale `__pycache__`** under `plugin/litert-lm/scripts/` (contains pre-rename `litert_control.cpython-310.pyc`).
- **Two-line fix to test scaffolding** to set `requestline` and `command` on `_build_handler`'s returned handler — unblocks the 4 errored tests and lifts coverage measurably.
- **Reconcile `litert-lm-config` slash command** with the missing `config` subcommand in `litert_lm_control.py` (or remove the slash command).
- **Run Agent A** on a clone of the LiteRT-LM repo to confirm `register()` monkey-patch wires into the existing Click `--api` choice surface.
- **Run Agent B's Docker harness** (`agents/e2e-harness/`) against a real `.litertlm` model — exercises the streaming generation path that 50% line coverage doesn't reach.
- **Run Agent C** against the built docs to verify external-link integrity.
- **Tier 4: submit to maintainers.**
