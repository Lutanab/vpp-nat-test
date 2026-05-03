from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any, Callable, ParamSpec, TypeVar

import rich_click as click

from .config import (
    DEFAULT_ACTUAL_PPS_TOLERANCE,
    DEFAULT_BASE_SRC_PORT,
    DEFAULT_BIND_IP,
    DEFAULT_CONFIG_PATH,
    DEFAULT_SEARCH_PRESET_NAME,
    DEFAULT_DRAIN_DURATION_SEC,
    DEFAULT_LATENCY_SAMPLE_SIZE,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_SERVER_IP,
    DEFAULT_SERVER_MODE,
    DEFAULT_SERVER_PORT,
    TrafficTestConfig,
    ensure_valid_nat_mode,
    ensure_valid_server_mode,
    load_search_preset,
    load_simple_yaml,
)
from .results import ensure_directory
from .search import BoundarySearchError, run_boundary_search

P = ParamSpec("P")
R = TypeVar("R")


def stderr_log(prefix: str, log_path: Path | None = None) -> Callable[[str], None]:
    if log_path is not None:
        ensure_directory(log_path.parent)
        log_path.write_text("", encoding="utf-8")

    def emit(message: str) -> None:
        if log_path is not None:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        click.echo(f"[{prefix}] {message}", err=True)

    return emit


def load_command_config(config_path_arg: Path | None) -> tuple[dict[str, object], Path | None]:
    config_path = config_path_arg
    config_values: dict[str, object] = {}
    if config_path is None and DEFAULT_CONFIG_PATH.exists():
        config_path = DEFAULT_CONFIG_PATH
    if config_path is not None:
        config_values = load_simple_yaml(config_path)
        config_path = config_path.resolve()

    search_preset = str(config_values.get("search_preset", DEFAULT_SEARCH_PRESET_NAME))
    preset_values, preset_path = load_search_preset(search_preset)
    merged = {**preset_values, **config_values, "search_preset": search_preset, "search_preset_file": str(preset_path)}
    return merged, config_path


def resolve_setting(
    cli_value: object | None,
    config_values: dict[str, object],
    *config_keys: str,
    default: object | None = None,
    required: bool = False,
) -> object:
    if cli_value is not None:
        return cli_value
    for key in config_keys:
        if key in config_values and config_values[key] is not None:
            return config_values[key]
    if required:
        raise ValueError(f"не хватает обязательного параметра: {', '.join(config_keys)}")
    return default


def resolve_optional_int_setting(
    cli_value: int | None,
    config_values: dict[str, object],
    *config_keys: str,
) -> int | None:
    value = resolve_setting(cli_value, config_values, *config_keys, default=None)
    if value is None:
        return None
    return int(value)


def resolve_path_setting(
    cli_value: Path | None,
    config_values: dict[str, object],
    *config_keys: str,
    config_path: Path | None,
    default: Path | None = None,
) -> Path | None:
    value = resolve_setting(cli_value, config_values, *config_keys, default=default)
    if value is None:
        return None
    path = value if isinstance(value, Path) else Path(str(value))
    if path.is_absolute():
        return path
    if config_path is not None:
        return (config_path.parent / path).resolve()
    return path


def config_option(func: Callable[P, R]) -> Callable[P, R]:
    return click.option(
        "--config",
        type=click.Path(path_type=Path, dir_okay=False),
        help="Path to the YAML config. Defaults to testing/configs/test_config.yaml when present.",
    )(func)


def common_client_options(func: Callable[P, R]) -> Callable[P, R]:
    options = [
        click.option("--nat-mode", required=True, callback=validate_nat_mode_option, help="Target NAT mode label."),
        click.option("--server-ip", help="Traffic server IP address."),
        click.option("--server-port", type=int, help="Traffic server UDP port."),
        click.option("--server-mode", callback=validate_server_mode_option, help="Server mode: sink or echo."),
        click.option("--packet-size", type=int, help="UDP payload size in bytes."),
        click.option("--flow-count", type=int, help="Number of flows/source ports."),
        click.option("--duration", type=int, help="Measurement duration in seconds."),
        click.option("--warmup", type=int, help="Warmup duration in seconds."),
        click.option("--loss-threshold", type=float, help="Allowed packet loss threshold."),
        click.option("--nat-pid", type=int, help="PID for process-level resource monitoring."),
        click.option("--bind-ip", help="Client bind IP."),
        click.option("--base-src-port", type=int, help="Base source UDP port."),
        click.option("--drain-duration", type=int, help="Drain replies after send phase, seconds."),
        click.option("--latency-sample-size", type=int, help="Reservoir sample size for latency."),
        click.option("--actual-pps-tolerance", type=float, help="Allowed relative PPS deviation."),
    ]
    wrapped = func
    for option in reversed(options):
        wrapped = option(wrapped)
    return wrapped


def validate_nat_mode_option(
    _ctx: click.Context,
    _param: click.Parameter,
    value: str | None,
) -> str | None:
    if value is None:
        return None
    return ensure_valid_nat_mode(value)


def validate_server_mode_option(
    _ctx: click.Context,
    _param: click.Parameter,
    value: str | None,
) -> str | None:
    if value is None:
        return None
    return ensure_valid_server_mode(value)


def build_traffic_config(kwargs: dict[str, Any], config_values: dict[str, object]) -> tuple[TrafficTestConfig, Path | None, int | None]:
    config_path = kwargs["config_path"]
    config = TrafficTestConfig(
        nat_mode=ensure_valid_nat_mode(str(kwargs["nat_mode"])),
        server_mode=ensure_valid_server_mode(
            str(resolve_setting(kwargs.get("server_mode"), config_values, "server_mode", default=DEFAULT_SERVER_MODE))
        ),
        server_ip=str(resolve_setting(kwargs.get("server_ip"), config_values, "server_ip", default=DEFAULT_SERVER_IP)),
        server_port=int(resolve_setting(kwargs.get("server_port"), config_values, "server_port", default=DEFAULT_SERVER_PORT)),
        packet_size_bytes=int(
            resolve_setting(kwargs.get("packet_size"), config_values, "packet_size_bytes", "packet_size", required=True)
        ),
        flow_count=int(resolve_setting(kwargs.get("flow_count"), config_values, "flow_count", required=True)),
        loss_threshold=float(
            resolve_setting(kwargs.get("loss_threshold"), config_values, "loss_threshold", required=True)
        ),
        warmup_duration_sec=int(
            resolve_setting(kwargs.get("warmup"), config_values, "warmup_duration_sec", "warmup", required=True)
        ),
        measurement_duration_sec=int(
            resolve_setting(
                kwargs.get("duration"),
                config_values,
                "measurement_duration_sec",
                "duration",
                required=True,
            )
        ),
        search_preset=str(resolve_setting(None, config_values, "search_preset", default=DEFAULT_SEARCH_PRESET_NAME)),
        base_src_port=int(
            resolve_setting(
                kwargs.get("base_src_port"),
                config_values,
                "base_src_port",
                default=DEFAULT_BASE_SRC_PORT,
            )
        ),
        bind_ip=str(resolve_setting(kwargs.get("bind_ip"), config_values, "bind_ip", default=DEFAULT_BIND_IP)),
        drain_duration_sec=int(
            resolve_setting(
                kwargs.get("drain_duration"),
                config_values,
                "drain_duration_sec",
                "drain_duration",
                default=DEFAULT_DRAIN_DURATION_SEC,
            )
        ),
        latency_sample_size=int(
            resolve_setting(
                kwargs.get("latency_sample_size"),
                config_values,
                "latency_sample_size",
                default=DEFAULT_LATENCY_SAMPLE_SIZE,
            )
        ),
        actual_pps_tolerance=float(
            resolve_setting(
                kwargs.get("actual_pps_tolerance"),
                config_values,
                "actual_pps_tolerance",
                default=DEFAULT_ACTUAL_PPS_TOLERANCE,
            )
        ),
        test_name=str(resolve_setting(None, config_values, "test_name", default="udp_forwarding_capacity")),
    )
    nat_pid = resolve_optional_int_setting(kwargs.get("nat_pid"), config_values, "nat_pid")
    return config, config_path, nat_pid


def run_simple_test() -> int:
    script_path = Path(__file__).resolve().parents[2] / "simple_test.py"
    try:
        runpy.run_path(str(script_path), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        click.echo(code, err=True)
        return 1
    return 0


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def app() -> None:
    """Simple and load NAT test runner."""


@app.command("simple")
def simple_command() -> None:
    """Run simple_test.py."""
    exit_code = run_simple_test()
    if exit_code != 0:
        raise click.exceptions.Exit(exit_code)


@app.group("load")
def load_group() -> None:
    """Run load-test commands."""


@load_group.command("run")
@config_option
@common_client_options
@click.option("--initial-pps", type=int, help="Initial PPS for exponential ramp.")
@click.option("--max-pps", type=int, help="Maximum PPS to try.")
@click.option("--pps-precision-delta", type=int, help="Stop binary search when the PPS interval is below this value.")
@click.option("--out-dir", type=click.Path(path_type=Path, file_okay=False), help="Explicit result directory.")
@click.option("--results-root", type=click.Path(path_type=Path, file_okay=False), help="Root directory for results.")
def load_search_boundary_command(
    config: Path | None,
    nat_mode: str | None,
    server_ip: str | None,
    server_port: int | None,
    server_mode: str | None,
    packet_size: int | None,
    flow_count: int | None,
    duration: int | None,
    warmup: int | None,
    loss_threshold: float | None,
    nat_pid: int | None,
    bind_ip: str | None,
    base_src_port: int | None,
    drain_duration: int | None,
    latency_sample_size: int | None,
    actual_pps_tolerance: float | None,
    initial_pps: int | None,
    max_pps: int | None,
    pps_precision_delta: int | None,
    out_dir: Path | None,
    results_root: Path | None,
) -> None:
    """Запустить поиск границы PPS."""
    config_values, config_path = load_command_config(config)
    kwargs = locals() | {"config_path": config_path}
    traffic_config, resolved_config_path, resolved_nat_pid = build_traffic_config(kwargs, config_values)

    resolved_results_root = resolve_path_setting(
        results_root,
        config_values,
        "results_root",
        config_path=resolved_config_path,
        default=DEFAULT_RESULTS_ROOT,
    )
    progress = stderr_log("run")
    summary = run_boundary_search(
        config=traffic_config,
        initial_pps=int(
            resolve_setting(initial_pps, config_values, "search_initial_pps", "initial_pps", required=True)
        ),
        max_pps=int(resolve_setting(max_pps, config_values, "search_max_pps", "max_pps", required=True)),
        pps_precision_delta=int(
            resolve_setting(
                pps_precision_delta,
                config_values,
                "search_pps_precision_delta",
                "pps_precision_delta",
                required=True,
            )
        ),
        nat_pid=resolved_nat_pid,
        results_root=resolved_results_root,
        explicit_results_dir=resolve_path_setting(
            out_dir,
            config_values,
            "search_out_dir",
            config_path=resolved_config_path,
        ),
        progress=progress,
    )
    click.echo(json.dumps(summary, indent=2))


def main() -> int:
    try:
        app.main(standalone_mode=False)
        return 0
    except BoundarySearchError as exc:
        click.echo(f"ошибка: {exc}", err=True)
        return 2
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except (OSError, RuntimeError, ValueError) as exc:
        click.echo(f"ошибка: {exc}", err=True)
        return 1
    except click.Abort:
        click.echo("Прервано", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
