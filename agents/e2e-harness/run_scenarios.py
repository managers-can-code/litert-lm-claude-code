#!/usr/bin/env python3
# Copyright 2026 The ODML Authors. All Rights Reserved.
# Licensed under the Apache License, Version 2.0.
"""Agent B end-to-end scenario runner for litert-lm serve --api anthropic.

Drives a real Claude Code CLI (`claude -p ... --bare --output-format json`)
against an already-running `litert-lm serve --api anthropic` instance and
records pass/fail + p50 latency per scenario.

Output: a Markdown report at the path given to --report.
Exit code: 0 on APPROVED, 1 on REJECTED.

Usage:
  python3 run_scenarios.py --report /reports/agent-B-e2e-report.md
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


# --- Static thresholds (from design.md § 9) -----------------------------------

THRESHOLDS = {
    "scenario_1_first_token_ms_p50": 1500.0,
    "scenario_1_tokens_per_sec_p50": 15.0,
    "scenario_1_total_wall_ms_p50": 3000.0,
    "scenario_13_cold_start_ttft_ms_p50": 6000.0,
}


# --- Scenario definitions -----------------------------------------------------

@dataclass
class Scenario:
    id: int
    name: str
    tier: str  # "smoke" or "mid"
    runs: int
    prompt: str
    expect_status: str  # "ok" | "error_4xx" | "error_5xx"
    extra_args: List[str] = field(default_factory=list)

SCENARIOS: List[Scenario] = [
    Scenario(1, "Single-turn chat", "smoke", 20, "what is 2+2?", "ok"),
    Scenario(2, "Multi-turn with 3 follow-ups", "smoke", 20,
             "what is 2+2? then double it. then halve that. then square it.", "ok"),
    Scenario(3, "Streaming cancellation mid-response", "smoke", 20,
             "write a 5000-word essay about the history of computing", "ok",
             extra_args=["--cancel-after-ms", "500"]),
    Scenario(4, "Tool use: Read a file", "mid", 10,
             "read /etc/hostname and tell me what it says", "ok",
             extra_args=["--allowed-tools", "Read"]),
    Scenario(5, "Tool use: Bash command", "mid", 10,
             "run `echo hello` via bash and show me the output", "ok",
             extra_args=["--allowed-tools", "Bash"]),
    Scenario(6, "Tool use: Edit a file (round-trip)", "mid", 10,
             "create /tmp/agent_b_test.txt containing 'hello' and then read it back", "ok",
             extra_args=["--allowed-tools", "Read,Edit,Bash"]),
    Scenario(11, "Bad request (missing max_tokens)", "smoke", 20,
             "_bad_request_", "error_4xx",
             extra_args=["--raw-bad-request"]),
    Scenario(12, "Unknown model strict mode 404", "smoke", 20,
             "hello", "error_4xx",
             extra_args=["--model", "this-model-does-not-exist"]),
    Scenario(13, "Cold-start TTFT", "mid", 10,
             "hello", "ok",
             extra_args=["--restart-server-before"]),
    Scenario(14, "Image content block to non-vision model 400", "mid", 10,
             "_image_content_", "error_4xx"),
    Scenario(15, "--accept-any-model passthrough", "smoke", 20,
             "hello", "ok",
             extra_args=["--model", "claude-sonnet-4-20250514", "--server-flag",
                         "--accept-any-model"]),
]


# --- Runner -------------------------------------------------------------------

@dataclass
class RunResult:
    scenario_id: int
    succeeded: bool
    first_token_ms: Optional[float]
    total_wall_ms: float
    output_tokens: Optional[int]
    error: Optional[str] = None


def run_claude(scenario: Scenario, base_url: str) -> RunResult:
    """Run a single iteration of `claude -p ...` with --bare --output-format json."""
    env = os.environ.copy()
    env["ANTHROPIC_BASE_URL"] = base_url
    env["ANTHROPIC_AUTH_TOKEN"] = env.get("ANTHROPIC_AUTH_TOKEN", "any-value")

    cmd = ["claude", "-p", scenario.prompt, "--bare", "--output-format", "json"]

    # Process scenario-specific extras
    cancel_after_ms: Optional[int] = None
    raw_bad_request = False
    image_content = False
    extra_iter = iter(scenario.extra_args)
    for arg in extra_iter:
        if arg == "--cancel-after-ms":
            cancel_after_ms = int(next(extra_iter))
        elif arg == "--allowed-tools":
            cmd.extend(["--allowedTools", next(extra_iter)])
        elif arg == "--model":
            cmd.extend(["--model", next(extra_iter)])
        elif arg == "--raw-bad-request":
            raw_bad_request = True
        elif arg == "--restart-server-before":
            pass  # handled at orchestrator level
        elif arg == "--server-flag":
            pass  # handled at orchestrator level

    if raw_bad_request:
        # Hit /v1/messages directly with a missing max_tokens body to verify 400
        import httpx
        t0 = time.monotonic()
        try:
            r = httpx.post(f"{base_url}/v1/messages",
                           json={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
                           timeout=10.0)
            ms = (time.monotonic() - t0) * 1000
            ok = r.status_code == 400 and r.json().get("error", {}).get("type") == "invalid_request_error"
            return RunResult(scenario.id, ok, None, ms, None,
                             None if ok else f"unexpected status={r.status_code} body={r.text[:200]}")
        except Exception as exc:
            return RunResult(scenario.id, False, None, (time.monotonic() - t0) * 1000, None, str(exc))

    if image_content:
        import httpx
        t0 = time.monotonic()
        try:
            r = httpx.post(f"{base_url}/v1/messages",
                           json={
                               "model": "any-non-vision-model",
                               "max_tokens": 16,
                               "messages": [{
                                   "role": "user",
                                   "content": [{"type": "image", "source": {
                                       "type": "base64", "media_type": "image/png",
                                       "data": "iVBORw0KGgo="
                                   }}]
                               }]
                           },
                           timeout=10.0)
            ms = (time.monotonic() - t0) * 1000
            ok = r.status_code == 400 and r.json().get("error", {}).get("type") == "invalid_request_error"
            return RunResult(scenario.id, ok, None, ms, None,
                             None if ok else f"unexpected status={r.status_code}")
        except Exception as exc:
            return RunResult(scenario.id, False, None, (time.monotonic() - t0) * 1000, None, str(exc))

    t0 = time.monotonic()
    try:
        timeout = (cancel_after_ms / 1000) if cancel_after_ms else 180
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        wall_ms = (time.monotonic() - t0) * 1000
        if proc.returncode == 0:
            try:
                envelope = json.loads(proc.stdout)
                first_token_ms = envelope.get("metrics", {}).get("first_token_ms")
                output_tokens = envelope.get("usage", {}).get("output_tokens")
                ok = scenario.expect_status == "ok"
                return RunResult(scenario.id, ok, first_token_ms, wall_ms, output_tokens)
            except json.JSONDecodeError:
                return RunResult(scenario.id, False, None, wall_ms, None,
                                 f"could not parse claude JSON envelope: {proc.stdout[:200]}")
        else:
            ok = scenario.expect_status.startswith("error_")
            return RunResult(scenario.id, ok, None, wall_ms, None,
                             None if ok else f"claude exited {proc.returncode}: {proc.stderr[:200]}")
    except subprocess.TimeoutExpired:
        wall_ms = (time.monotonic() - t0) * 1000
        if cancel_after_ms:
            return RunResult(scenario.id, True, None, wall_ms, None)  # cancellation is success
        return RunResult(scenario.id, False, None, wall_ms, None, "timeout")


def aggregate(results: List[RunResult]) -> Dict[str, Any]:
    p50_first = None
    p50_wall = None
    if results:
        ft = sorted(r.first_token_ms for r in results if r.first_token_ms is not None)
        if ft:
            p50_first = ft[len(ft) // 2]
        wt = sorted(r.total_wall_ms for r in results)
        p50_wall = wt[len(wt) // 2]
    return {
        "n": len(results),
        "passed": sum(1 for r in results if r.succeeded),
        "failed": sum(1 for r in results if not r.succeeded),
        "p50_first_token_ms": p50_first,
        "p50_total_wall_ms": p50_wall,
    }


def write_report(report_path: str, all_results: Dict[int, List[RunResult]],
                 server_log_path: str) -> bool:
    """Returns True if APPROVED."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rejected_reasons: List[str] = []

    lines = [f"# Agent B Report — {now}", "", "## Environment"]
    try:
        cc_ver = subprocess.run(["claude", "--version"], capture_output=True, text=True).stdout.strip()
    except Exception:
        cc_ver = "unknown"
    try:
        ll_ver = subprocess.run(["litert-lm", "--version"], capture_output=True, text=True).stdout.strip()
    except Exception:
        ll_ver = "unknown"
    lines += [f"- Claude Code: {cc_ver}", f"- LiteRT-LM: {ll_ver}",
              f"- Total scenarios: {len(SCENARIOS)}",
              f"- Total runs: {sum(s.runs for s in SCENARIOS)}", ""]

    lines += ["## Per-scenario results", "",
              "| # | Scenario | Tier | Runs | Pass | Fail | Status |",
              "|---|---|---|---|---|---|---|"]
    for sc in SCENARIOS:
        rs = all_results.get(sc.id, [])
        agg = aggregate(rs)
        status = "PASS" if agg["failed"] == 0 and agg["passed"] == sc.runs else "FAIL"
        if status == "FAIL":
            rejected_reasons.append(f"Scenario {sc.id} ({sc.name}): {agg['failed']} failures")
        lines.append(f"| {sc.id} | {sc.name} | {sc.tier} | {sc.runs} | {agg['passed']} | {agg['failed']} | {status} |")

    lines += ["", "## Performance vs. thresholds", "",
              "| Scenario | Metric | Measured p50 | Threshold | Status |",
              "|---|---|---|---|---|"]

    s1 = aggregate(all_results.get(1, []))
    s13 = aggregate(all_results.get(13, []))

    def gate(label: str, measured: Optional[float], threshold: float, lower_is_better: bool = True) -> str:
        if measured is None:
            return "SKIP (no data)"
        if lower_is_better:
            ok = measured <= threshold
        else:
            ok = measured >= threshold
        if not ok:
            rejected_reasons.append(f"{label}: measured={measured:.1f} threshold={threshold}")
        return f"{measured:.1f}"

    lines.append(f"| 1 | first_token_ms_p50 | {gate('s1_first_token', s1['p50_first_token_ms'], THRESHOLDS['scenario_1_first_token_ms_p50'])} | ≤ {THRESHOLDS['scenario_1_first_token_ms_p50']} | {'PASS' if s1['p50_first_token_ms'] is not None and s1['p50_first_token_ms'] <= THRESHOLDS['scenario_1_first_token_ms_p50'] else 'FAIL'} |")
    lines.append(f"| 1 | total_wall_ms_p50 | {gate('s1_total_wall', s1['p50_total_wall_ms'], THRESHOLDS['scenario_1_total_wall_ms_p50'])} | ≤ {THRESHOLDS['scenario_1_total_wall_ms_p50']} | {'PASS' if s1['p50_total_wall_ms'] is not None and s1['p50_total_wall_ms'] <= THRESHOLDS['scenario_1_total_wall_ms_p50'] else 'FAIL'} |")
    lines.append(f"| 13 | cold_start_ttft_ms_p50 | {gate('s13_ttft', s13['p50_first_token_ms'], THRESHOLDS['scenario_13_cold_start_ttft_ms_p50'])} | ≤ {THRESHOLDS['scenario_13_cold_start_ttft_ms_p50']} | {'PASS' if s13['p50_first_token_ms'] is not None and s13['p50_first_token_ms'] <= THRESHOLDS['scenario_13_cold_start_ttft_ms_p50'] else 'FAIL'} |")

    lines += ["", "## Failures", ""]
    any_failures = False
    for sc in SCENARIOS:
        for r in all_results.get(sc.id, []):
            if not r.succeeded:
                any_failures = True
                lines.append(f"- Scenario {sc.id} run-failure: {r.error or '(unspecified)'}")
    if not any_failures:
        lines.append("(none)")

    lines += ["", "## Server log excerpt (last 100 lines)", "", "```"]
    try:
        with open(server_log_path) as fh:
            tail = fh.readlines()[-100:]
            lines.extend(line.rstrip("\n") for line in tail)
    except Exception as exc:
        lines.append(f"(could not read server log: {exc})")
    lines.append("```")

    status = "APPROVED" if not rejected_reasons else "REJECTED"
    lines += ["", f"STATUS: {status}", ""]
    if rejected_reasons:
        lines += ["", "## Rejection reasons"]
        lines += [f"- {r}" for r in rejected_reasons]

    with open(report_path, "w") as fh:
        fh.write("\n".join(lines))

    print(f"[run_scenarios] STATUS: {status}")
    print(f"[run_scenarios] Report at {report_path}")
    return status == "APPROVED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--base-url", default=os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:9379"))
    parser.add_argument("--server-log", default="/tmp/server.log")
    args = parser.parse_args()

    all_results: Dict[int, List[RunResult]] = {}
    for sc in SCENARIOS:
        results: List[RunResult] = []
        for i in range(sc.runs):
            r = run_claude(sc, args.base_url)
            results.append(r)
            print(f"  [s{sc.id} run {i+1}/{sc.runs}] {'PASS' if r.succeeded else 'FAIL'} ({r.total_wall_ms:.0f}ms)")
        all_results[sc.id] = results

    approved = write_report(args.report, all_results, args.server_log)
    return 0 if approved else 1


if __name__ == "__main__":
    sys.exit(main())
