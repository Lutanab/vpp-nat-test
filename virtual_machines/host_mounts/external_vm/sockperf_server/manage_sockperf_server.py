#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SERVICE_TEMPLATE_PATH = SCRIPT_DIR / "sockperf-server.service.template"
SERVICE_NAME = "sockperf-server.service"
SYSTEMD_UNIT_PATH = Path("/etc/systemd/system") / SERVICE_NAME
ENV_DIR = Path("/etc/sockperf-server")
ENV_FILE_PATH = ENV_DIR / "sockperf-server.env"
DEFAULT_BIND = "10.8.0.2"
DEFAULT_PORT = 5001
DEFAULT_MSG_SIZE = 1024


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install and manage sockperf server systemd unit.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install/update and start sockperf-server.service.")
    add_server_options(install)
    install.set_defaults(handler=install_command)

    restart = subparsers.add_parser("restart", help="Update configuration and restart sockperf-server.service.")
    add_server_options(restart)
    restart.set_defaults(handler=restart_command)

    status = subparsers.add_parser("status", help="Show sockperf-server.service status.")
    status.set_defaults(handler=status_command)

    stop = subparsers.add_parser("stop", help="Stop sockperf-server.service.")
    stop.set_defaults(handler=stop_command)

    return parser


def add_server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bind", default=DEFAULT_BIND, help=f"IP address to bind sockperf server to. Default: {DEFAULT_BIND}.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"UDP port. Default: {DEFAULT_PORT}.")
    parser.add_argument(
        "--msg-size",
        type=int,
        default=DEFAULT_MSG_SIZE,
        help=f"Maximum sockperf message size. Default: {DEFAULT_MSG_SIZE}.",
    )
    parser.add_argument(
        "--no-gap-detection",
        action="store_true",
        help="Disable sockperf --gap-detection.",
    )
    parser.add_argument(
        "--extra-args",
        default="",
        help="Extra arguments appended to sockperf server command.",
    )


def install_command(args: argparse.Namespace) -> int:
    require_root()
    sockperf_bin = require_sockperf()
    render_unit(sockperf_bin=sockperf_bin)
    write_env(args)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", SERVICE_NAME])
    run(["systemctl", "restart", SERVICE_NAME])
    print(f"{SERVICE_NAME} is installed and running")
    print(f"unit: {SYSTEMD_UNIT_PATH}")
    print(f"env:  {ENV_FILE_PATH}")
    return 0


def restart_command(args: argparse.Namespace) -> int:
    require_root()
    sockperf_bin = require_sockperf()
    render_unit(sockperf_bin=sockperf_bin)
    write_env(args)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "restart", SERVICE_NAME])
    print(f"{SERVICE_NAME} restarted")
    return 0


def status_command(_args: argparse.Namespace) -> int:
    require_sockperf()
    return run(["systemctl", "status", SERVICE_NAME, "--no-pager"], check=False).returncode


def stop_command(_args: argparse.Namespace) -> int:
    require_root()
    return run(["systemctl", "stop", SERVICE_NAME]).returncode


def require_root() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("this command writes to /etc and must be run with sudo")


def require_sockperf() -> str:
    sockperf_bin = shutil.which("sockperf")
    if sockperf_bin is None:
        raise RuntimeError("sockperf is not installed or is not in PATH")
    version = subprocess.run([sockperf_bin, "--version"], text=True, capture_output=True, check=False)
    version_text = (version.stdout or version.stderr).strip()
    if version_text:
        print(version_text.splitlines()[0])
    return sockperf_bin


def render_unit(sockperf_bin: str) -> None:
    if not SERVICE_TEMPLATE_PATH.exists():
        raise RuntimeError(f"service template not found: {SERVICE_TEMPLATE_PATH}")
    rendered = SERVICE_TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = rendered.replace("__SOCKPERF_BIN__", sockperf_bin)
    rendered = rendered.replace("__ENV_FILE__", str(ENV_FILE_PATH))
    SYSTEMD_UNIT_PATH.write_text(rendered, encoding="utf-8")


def write_env(args: argparse.Namespace) -> None:
    if args.port <= 0 or args.port > 65535:
        raise RuntimeError("--port must be in UDP port range 1..65535")
    if args.msg_size <= 0:
        raise RuntimeError("--msg-size must be positive")

    ENV_DIR.mkdir(parents=True, exist_ok=True)
    gap_detection = "" if args.no_gap_detection else "--gap-detection"
    payload = "\n".join(
        [
            f"SOCKPERF_BIND={systemd_env_quote(args.bind)}",
            f"SOCKPERF_PORT={args.port}",
            f"SOCKPERF_MSG_SIZE={args.msg_size}",
            f"SOCKPERF_GAP_DETECTION={systemd_env_quote(gap_detection)}",
            f"SOCKPERF_EXTRA_ARGS={systemd_env_quote(args.extra_args)}",
            "",
        ]
    )
    ENV_FILE_PATH.write_text(payload, encoding="utf-8")


def systemd_env_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("\"", "\\\"")
    return f"\"{escaped}\""


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, check=check)


if __name__ == "__main__":
    sys.exit(main())
