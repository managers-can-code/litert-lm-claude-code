#!/usr/bin/env python3
# Copyright 2026 ram <tenheadedram@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Control script for a local litert-lm serve --api anthropic process.

Subcommands:
    start --model PATH [--port N] [--host H]   spawn server in background
    stop                                       kill the running server
    status [--print-env]                       probe /v1/models, optionally
                                               print the env vars Claude Code
                                               needs
    switch --model PATH [--port N] [--host H]  stop, then start with new model
    list-models                                ask the running server what is
                                               loaded

State is kept under ``~/.litert-lm/``:

    server.pid       PID of the spawned litert-lm process
    server.log       stdout+stderr of the server
    config.json      last-used model path / port / host
    last-model-id    model id resolved from /v1/models

Designed to work on Linux and macOS with stock Python 3.10+. Has no
runtime dependencies outside the stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

STATE_DIR = Path.home() / ".litert-lm"
PID_FILE = STATE_DIR / "server.pid"
LOG_FILE = STATE_DIR / "server.log"
CONFIG_FILE = STATE_DIR / "config.json"
LAST_MODEL_ID_FILE = STATE_DIR / "last-model-id"

DEFAULT_PORT = 9379
DEFAULT_HOST = "127.0.0.1"
HEALTHCHECK_TIMEOUT_SECONDS = 30
HEALTHCHECK_POLL_INTERVAL = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class ServerConfig:
    """The bits we persist about the last server we started."""

    model: str
    port: int
    host: str


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> ServerConfig | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return ServerConfig(
            model=str(data["model"]),
            port=int(data.get("port", DEFAULT_PORT)),
            host=str(data.get("host", DEFAULT_HOST)),
        )
    except (KeyError, TypeError, ValueError):
        return None


def save_config(cfg: ServerConfig) -> None:
    ensure_state_dir()
    CONFIG_FILE.write_text(json.dumps(cfg.__dict__, indent=2) + "\n")


def read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def write_pid(pid: int) -> None:
    ensure_state_dir()
    PID_FILE.write_text(f"{pid}\n")


def clear_pid_file() -> None:
    try:
        PID_FILE.unlink()
    except FileNotFoundError:
        pass


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect((host, port))
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False
    return True


def find_free_port(host: str, start: int) -> int:
    for candidate in range(start, start + 20):
        if not port_in_use(host, candidate):
            return candidate
    raise RuntimeError(
        f"no free port in range {start}-{start + 19} on {host}"
    )


def litert_lm_binary() -> str:
    binary = shutil.which("litert-lm")
    if binary is None:
        raise FileNotFoundError(
            "litert-lm binary not found on PATH. Install with "
            "'uv tool install litert-lm' or 'pipx install litert-lm', "
            "then ensure the install location is on your PATH."
        )
    return binary


def http_get_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8"))


def resolve_model_id(host: str, port: int) -> str | None:
    """Ask the server which model id it's serving."""
    url = f"http://{host}:{port}/v1/models"
    try:
        data = http_get_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None
    # Accept Anthropic-shaped or OpenAI-shaped responses.
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        for entry in data["data"]:
            if isinstance(entry, dict) and "id" in entry:
                return str(entry["id"])
    if isinstance(data, dict) and "model" in data:
        return str(data["model"])
    if isinstance(data, list) and data and "id" in data[0]:
        return str(data[0]["id"])
    return None


def wait_for_healthy(host: str, port: int) -> str | None:
    """Block until /v1/models is reachable; return the model id."""
    deadline = time.monotonic() + HEALTHCHECK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        model_id = resolve_model_id(host, port)
        if model_id:
            ensure_state_dir()
            LAST_MODEL_ID_FILE.write_text(model_id + "\n")
            return model_id
        time.sleep(HEALTHCHECK_POLL_INTERVAL)
    return None


def stop_pid(pid: int, *, signal_first: int = signal.SIGTERM) -> None:
    """Send SIGTERM, then SIGKILL after a grace period if needed."""
    try:
        os.kill(pid, signal_first)
    except ProcessLookupError:
        return
    for _ in range(20):  # ~5 seconds
        if not process_is_alive(pid):
            return
        time.sleep(0.25)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> int:
    model_path: str | None = args.model
    port: int = args.port
    host: str = args.host

    # Idempotent: if the server is already running, just report it.
    existing = read_pid()
    if existing and process_is_alive(existing):
        cfg = load_config()
        cfg_port = cfg.port if cfg else port
        cfg_host = cfg.host if cfg else host
        print(
            f"litert-lm already running pid={existing} "
            f"url=http://{cfg_host}:{cfg_port}"
        )
        return 0

    # Stale PID file: clean it up.
    if existing and not process_is_alive(existing):
        clear_pid_file()

    # Resolve the model path if the user didn't pass one.
    if model_path is None:
        cfg = load_config()
        if cfg is None:
            print(
                "error: no model path supplied and no last-used config at "
                f"{CONFIG_FILE}. Run "
                "'/litert-lm-start /absolute/path/to/your-model.litertlm'.",
                file=sys.stderr,
            )
            return 2
        model_path = cfg.model
        port = port if port != DEFAULT_PORT else cfg.port
        host = host if host != DEFAULT_HOST else cfg.host

    model_path_p = Path(model_path).expanduser()
    if not model_path_p.is_file():
        print(
            f"error: model file not found at {model_path_p}. "
            "Pass an absolute path to a .litertlm file.",
            file=sys.stderr,
        )
        return 2

    if port_in_use(host, port):
        free = find_free_port(host, port + 1)
        print(
            f"error: port {port} is already in use on {host}. "
            f"Try '--port {free}'.",
            file=sys.stderr,
        )
        return 2

    try:
        binary = litert_lm_binary()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    ensure_state_dir()
    log_fh = open(LOG_FILE, "ab", buffering=0)
    cmd = [
        binary,
        "serve",
        "--api",
        "anthropic",
        "--model",
        str(model_path_p),
        "--host",
        host,
        "--port",
        str(port),
    ]
    log_fh.write(
        f"\n--- starting {' '.join(cmd)} at {time.ctime()} ---\n".encode()
    )
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_pid(proc.pid)
    save_config(ServerConfig(model=str(model_path_p), port=port, host=host))

    model_id = wait_for_healthy(host, port)
    if model_id is None:
        # The server didn't answer in time. Don't kill it; the user may
        # want to inspect the log. But report the failure.
        print(
            f"error: server pid={proc.pid} did not become healthy within "
            f"{HEALTHCHECK_TIMEOUT_SECONDS}s. Check {LOG_FILE} for errors.",
            file=sys.stderr,
        )
        return 1

    print(
        f"litert-lm started pid={proc.pid} "
        f"url=http://{host}:{port} "
        f"model_id={model_id}"
    )
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    pid = read_pid()
    if pid is None:
        print("litert-lm is not running (no pid file)")
        return 0
    if not process_is_alive(pid):
        clear_pid_file()
        print(f"litert-lm is not running (stale pid file pid={pid} cleared)")
        return 0
    stop_pid(pid)
    clear_pid_file()
    print(f"litert-lm stopped pid={pid}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    host = cfg.host if cfg else DEFAULT_HOST
    port = cfg.port if cfg else DEFAULT_PORT

    pid = read_pid()
    pid_alive = pid is not None and process_is_alive(pid)
    if pid is not None and not pid_alive:
        clear_pid_file()
        pid = None

    model_id = resolve_model_id(host, port)
    reachable = model_id is not None

    # Best-effort uptime: when /proc is available (Linux) we can read the
    # start time of the pid; on macOS we fall back to the mtime of the pid
    # file as a proxy.
    uptime_seconds: float | None = None
    if pid_alive and pid is not None:
        proc_stat = Path(f"/proc/{pid}/stat")
        if proc_stat.exists():
            try:
                clk_tck = os.sysconf("SC_CLK_TCK")
                fields = proc_stat.read_text().rsplit(") ", 1)[-1].split()
                start_time_jiffies = int(fields[19])
                with open("/proc/uptime") as fh:
                    system_uptime = float(fh.read().split()[0])
                started_at = system_uptime - (start_time_jiffies / clk_tck)
                uptime_seconds = started_at
            except (OSError, ValueError, IndexError):
                pass
        if uptime_seconds is None and PID_FILE.exists():
            try:
                uptime_seconds = max(
                    0.0, time.time() - PID_FILE.stat().st_mtime
                )
            except OSError:
                pass

    # Approximate recent-request count: number of POST /v1/messages lines
    # in the last 4kB of the log file.
    recent_requests = 0
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 4096))
                tail = fh.read().decode("utf-8", errors="replace")
            recent_requests = sum(
                1 for line in tail.splitlines()
                if "/v1/messages" in line and "POST" in line
            )
        except OSError:
            pass

    if args.print_env:
        if not reachable:
            print(
                "litert-lm is not reachable. Start it first with "
                "'/litert-lm-start <model-path>'.",
                file=sys.stderr,
            )
            return 1
        print(f"export ANTHROPIC_BASE_URL=http://{host}:{port}")
        print("export ANTHROPIC_AUTH_TOKEN=any-value")
        print("# example:")
        print(f'claude -p "what is 2+2?" --model {model_id}')
        return 0

    print("litert-lm status")
    print(f"  pid              : {pid if pid_alive else 'not running'}")
    print(f"  url              : http://{host}:{port}")
    print(f"  reachable        : {'yes' if reachable else 'no'}")
    print(f"  model_id         : {model_id or 'unknown'}")
    if uptime_seconds is not None:
        print(f"  uptime_seconds   : {uptime_seconds:.0f}")
    print(f"  recent_requests  : {recent_requests}")
    print(f"  log              : {LOG_FILE}")
    return 0 if reachable else 1


def cmd_switch(args: argparse.Namespace) -> int:
    if args.model is None:
        print(
            "error: switch requires a --model PATH argument.",
            file=sys.stderr,
        )
        return 2
    rc = cmd_stop(argparse.Namespace())
    if rc != 0:
        return rc
    return cmd_start(args)


def cmd_list_models(_args: argparse.Namespace) -> int:
    cfg = load_config()
    host = cfg.host if cfg else DEFAULT_HOST
    port = cfg.port if cfg else DEFAULT_PORT
    url = f"http://{host}:{port}/v1/models"
    try:
        data = http_get_json(url)
    except Exception as exc:  # noqa: BLE001 - reporting only
        print(f"error: cannot reach {url}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(data, indent=2))
    return 0


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litert_lm_control",
        description=(
            "Manage a local 'litert-lm serve --api anthropic' server "
            "for use with Claude Code."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="start the server in background")
    start.add_argument("--model", help="absolute path to .litertlm file")
    start.add_argument(
        "model_positional",
        nargs="?",
        help="alias for --model when invoked from a slash command",
    )
    start.add_argument("--port", type=int, default=DEFAULT_PORT)
    start.add_argument("--host", default=DEFAULT_HOST)
    start.set_defaults(func=_run_start)

    sub.add_parser("stop", help="stop the running server").set_defaults(
        func=cmd_stop
    )

    status = sub.add_parser("status", help="probe /v1/models and report")
    status.add_argument(
        "--print-env",
        action="store_true",
        help="print 'export ANTHROPIC_BASE_URL=...' lines for the user",
    )
    status.set_defaults(func=cmd_status)

    switch = sub.add_parser(
        "switch", help="stop, then start with a different model"
    )
    switch.add_argument("--model", help="absolute path to .litertlm file")
    switch.add_argument("model_positional", nargs="?")
    switch.add_argument("--port", type=int, default=DEFAULT_PORT)
    switch.add_argument("--host", default=DEFAULT_HOST)
    switch.set_defaults(func=_run_switch)

    sub.add_parser(
        "list-models", help="curl /v1/models and pretty-print the response"
    ).set_defaults(func=cmd_list_models)

    return parser


def _coalesce_model_arg(args: argparse.Namespace) -> argparse.Namespace:
    """Allow ``start /path/to/model.litertlm`` as well as ``--model``."""
    if getattr(args, "model", None) is None:
        positional = getattr(args, "model_positional", None)
        if positional:
            args.model = positional
    return args


def _run_start(args: argparse.Namespace) -> int:
    return cmd_start(_coalesce_model_arg(args))


def _run_switch(args: argparse.Namespace) -> int:
    return cmd_switch(_coalesce_model_arg(args))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
