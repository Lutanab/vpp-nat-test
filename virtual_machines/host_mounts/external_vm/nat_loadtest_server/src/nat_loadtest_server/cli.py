from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import rich_click as click

from .l34_udp import VALID_SERVER_MODES, run_l34_udp_server

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD_TEMPLATE_PATH = PACKAGE_ROOT / "nat_loadtest_server.service"
SYSTEMD_UNIT_DIR = Path("/etc/systemd/system")
SERVER_ENV_DIR = Path("/etc/nat-loadtest-server")


@dataclass(frozen=True)
class ServerSpec:
    name: str
    unit_name: str
    env_path: Path
    bind_var: str
    port_var: str
    mode_var: str


L34_UDP_SPEC = ServerSpec(
    name="l34-udp",
    unit_name="nat-loadtest-server-l34-udp.service",
    env_path=SERVER_ENV_DIR / "l34-udp.env",
    bind_var="L34_UDP_BIND",
    port_var="L34_UDP_PORT",
    mode_var="L34_UDP_MODE",
)

KNOWN_SERVERS = (L34_UDP_SPEC,)


def with_privileges(command: Sequence[str]) -> list[str]:
    if os.geteuid() == 0:
        return list(command)
    return ["sudo", *command]


def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=True)


def run_command_no_check(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def run_command_live(command: Sequence[str]) -> None:
    rendered = shlex.join(command)
    click.echo(f"==> {rendered}")
    subprocess.run(command, check=True)


def systemctl_show(unit_name: str) -> dict[str, str]:
    result = run_command_no_check(
        [
            "systemctl",
            "show",
            unit_name,
            "--property",
            "LoadState,ActiveState,SubState,UnitFileState",
        ]
    )
    if result.returncode != 0:
        return {
            "LoadState": "not-found",
            "ActiveState": "inactive",
            "SubState": "dead",
            "UnitFileState": "not-found",
        }
    data: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value
    return data


def write_server_env(spec: ServerSpec, bind: str, port: int, mode: str) -> None:
    lines = [
        f'{spec.bind_var}="{bind}"',
        f"{spec.port_var}={port}",
        f'{spec.mode_var}="{mode}"',
    ]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(lines) + "\n")
        temp_path = Path(handle.name)
    run_command_live(with_privileges(["mkdir", "-p", str(spec.env_path.parent)]))
    run_command_live(with_privileges(["install", "-m", "0644", str(temp_path), str(spec.env_path)]))
    temp_path.unlink(missing_ok=True)


def install_systemd_unit(spec: ServerSpec) -> Path:
    if not SYSTEMD_TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"systemd template not found: {SYSTEMD_TEMPLATE_PATH}")

    uv_bin = shutil.which("uv")
    if uv_bin is None:
        raise RuntimeError("uv binary not found in PATH")

    rendered = (
        SYSTEMD_TEMPLATE_PATH.read_text(encoding="utf-8")
        .replace("__SERVER_DIR__", str(PACKAGE_ROOT))
        .replace("__UV_BIN__", uv_bin)
        .replace("__ENV_FILE__", str(spec.env_path))
    )
    target_path = SYSTEMD_UNIT_DIR / spec.unit_name
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(rendered)
        temp_path = Path(handle.name)
    run_command_live(with_privileges(["install", "-m", "0644", str(temp_path), str(target_path)]))
    temp_path.unlink(missing_ok=True)
    return target_path


def format_server_status(spec: ServerSpec) -> str:
    status = systemctl_show(spec.unit_name)
    env_values = read_env_file(spec.env_path)
    lines = [
        spec.name,
        f"  unit: {spec.unit_name}",
        f"  load_state: {status.get('LoadState', 'unknown')}",
        f"  active_state: {status.get('ActiveState', 'unknown')}",
        f"  sub_state: {status.get('SubState', 'unknown')}",
        f"  unit_file_state: {status.get('UnitFileState', 'unknown')}",
    ]
    if env_values:
        lines.extend(
            [
                f"  bind: {env_values.get(spec.bind_var, 'unknown')}",
                f"  port: {env_values.get(spec.port_var, 'unknown')}",
                f"  mode: {env_values.get(spec.mode_var, 'unknown')}",
            ]
        )
    else:
        lines.append("  config: not configured")
    return "\n".join(lines)


def read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Standalone L3/L4 server package for NAT load tests."""


@app.group("run")
def run_group() -> None:
    """Configure and start one of the available loadtest server services."""


@app.command("status")
def status_command() -> None:
    """Show which loadtest servers are configured/running and with which parameters."""
    click.echo("\n\n".join(format_server_status(spec) for spec in KNOWN_SERVERS))


@run_group.command("l34-udp")
@click.option("--bind", default="0.0.0.0", show_default=True, help="Bind address.")
@click.option("--port", type=int, required=True, help="UDP listen port.")
@click.option(
    "--mode",
    type=click.Choice(VALID_SERVER_MODES),
    default="echo",
    show_default=True,
    help="sink receives only, echo sends the same payload back.",
)
def run_l34_udp_service_command(bind: str, port: int, mode: str) -> None:
    """Install/update and start the systemd unit for the UDP L3/L4 test server."""
    write_server_env(L34_UDP_SPEC, bind=bind, port=port, mode=mode)
    unit_path = install_systemd_unit(L34_UDP_SPEC)
    click.echo(f"Configured env file: {L34_UDP_SPEC.env_path}")
    click.echo(f"Installed unit: {unit_path}")
    run_command_live(with_privileges(["systemctl", "daemon-reload"]))
    run_command_live(with_privileges(["systemctl", "enable", L34_UDP_SPEC.unit_name]))
    run_command_live(with_privileges(["systemctl", "restart", L34_UDP_SPEC.unit_name]))
    click.echo("")
    click.echo(format_server_status(L34_UDP_SPEC))


@app.group("serve", hidden=True)
def serve_group() -> None:
    """Foreground server entrypoints used internally by systemd."""


@serve_group.command("l34-udp", hidden=True)
@click.option("--bind", default="0.0.0.0", show_default=True)
@click.option("--port", type=int, required=True)
@click.option("--mode", type=click.Choice(VALID_SERVER_MODES), default="echo", show_default=True)
@click.option("--output-json", type=click.Path(path_type=Path, dir_okay=False))
def serve_l34_udp_command(bind: str, port: int, mode: str, output_json: Path | None) -> None:
    """Run the UDP L3/L4 server in the foreground."""
    exit_code = run_l34_udp_server(bind=bind, port=port, mode=mode, output_path=output_json)
    if exit_code != 0:
        raise click.exceptions.Exit(exit_code)


def main() -> int:
    try:
        app.main(standalone_mode=False)
        return 0
    except subprocess.CalledProcessError as exc:
        click.echo(exc.stdout, err=True)
        click.echo(exc.stderr, err=True)
        return exc.returncode
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except (OSError, RuntimeError, ValueError) as exc:
        click.echo(f"error: {exc}", err=True)
        return 1
    except click.Abort:
        click.echo("Aborted", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
