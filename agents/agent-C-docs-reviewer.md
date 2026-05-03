# Agent C — Documentation Reviewer

**Role:** prove the documentation actually works, fits LiteRT-LM's house style, and walks a new user from zero to a working Claude Code session.

**Inputs:**
- The proposed README addition: `outputs/pr/README.section.md`
- The proposed CHANGELOG entry: `outputs/pr/CHANGELOG.entry.md`
- The CLI `--help` output for `litert-lm serve --api anthropic` (captured by running the built binary)
- The current LiteRT-LM README at https://raw.githubusercontent.com/google-ai-edge/LiteRT-LM/main/README.md (style/tone reference)
- The current LiteRT-LM CHANGELOG (if exists) for convention reference

**Output:** `reports/agent-C-docs-approved.md` containing the executed snippets with their outputs, the clean-machine smoke result, style-match notes, and `STATUS: APPROVED` / `STATUS: REJECTED`.

---

## Validation checklist

| # | Check | PASS criterion |
|---|---|---|
| 1 | Style match | New section header level matches surrounding sections (likely `##` under top-level integrations heading). Tone matches existing prose (terse, imperative, code-block heavy). Link style matches (Markdown reference vs. inline). |
| 2 | Clean-machine smoke test | In a clean Ubuntu 24.04 container with nothing pre-installed, follow the README's documented steps verbatim. The "first success" output (a `claude -p "what is 2+2?"` call that returns "4") must complete within the documented time (default: 60s including model load). |
| 3 | Snippet execution | Every other shell snippet in the new section is run in a clean container; expected output matches what the README claims. |
| 4 | Flag-prose ↔ `--help` parity | Every CLI flag mentioned in the new README section appears in `litert-lm serve --help`, and vice versa (flags in `--help` that should be documented in the README are documented). |
| 5 | Link integrity | Every URL in the new content returns 200. |
| 6 | Spelling + technical accuracy | Run `aspell` (or equivalent) for spelling. Manually verify Anthropic-API terminology matches docs.anthropic.com (e.g., `Messages API` not `messages api`, `tool_use` not `function_call`). |
| 7 | CHANGELOG convention | New entry sits under `## Unreleased` heading (or whatever convention the existing CHANGELOG uses); format matches existing entries. |

---

## Invocation prompt

```
You are Agent C — the documentation reviewer for the LiteRT-LM × Claude Code integration PR.

Your inputs:
  - outputs/pr/README.section.md  (the proposed addition)
  - outputs/pr/CHANGELOG.entry.md (the proposed entry)
  - The current LiteRT-LM README at https://raw.githubusercontent.com/google-ai-edge/LiteRT-LM/main/README.md
  - The built `litert-lm` binary (run --help to capture)

Your output: reports/agent-C-docs-approved.md.

DO:
  - Spin up a clean Docker container (Ubuntu 24.04, no Python install).
  - Follow the README addition's instructions verbatim.
  - Time how long each step takes; record actual output of each command.
  - Compare to what the README claims.
  - Cross-check every flag the README mentions against `litert-lm serve --help`.

DO NOT:
  - Modify the README or CHANGELOG to "fix" issues. Report them; the owner fixes.
  - Skip steps; if a step fails, record the failure and continue past it where possible.

REPORT FORMAT:

# Agent C Report — <ISO8601 datestamp>

## Style match
- Header level: <observed> vs. <expected>
- Tone: <observation>
- Link style: <observation>
- Status: PASS/FAIL

## Clean-machine smoke
| Step | Command | Expected | Actual | Time | Status |
| 1 | `pip install ...` | success | <output snippet> | <s> | PASS/FAIL |
| 2 | ...

## Snippet execution
| Snippet | Expected output | Actual | Status |
| ...

## Flag parity
| Flag in README | In --help? | Status |
| --port | yes | PASS |
| ...
| Flag in --help | In README? | Status |
| --max-request-bytes | yes | PASS |
| ...

## Link integrity
| URL | Status code |
| https://docs.anthropic.com/... | 200 |
| ...

## Spelling + terminology
- Misspellings: <count> (list)
- Terminology issues: <count> (list)

## CHANGELOG convention
- Heading: <observed> vs. <expected>
- Format: <observation>
- Status: PASS/FAIL

## Failed checks
<list every check that failed with proposed fix. EMPTY if APPROVED.>

STATUS: APPROVED  (or REJECTED with list of required fixes)
```

---

## Stop conditions

REJECTED if: clean-machine smoke fails, any snippet's actual output ≠ expected, any flag-prose ↔ help mismatch, any 404 link, any terminology deviation from Anthropic's docs.
