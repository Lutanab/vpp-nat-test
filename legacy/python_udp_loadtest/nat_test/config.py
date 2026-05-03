from __future__ import annotations

import platform
import re
import socket
import sys
from ast import literal_eval
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_NAT_MODES = ("none", "nat44", "nat_fo")
VALID_SERVER_MODES = ("sink", "echo")
TEST_NAME = "udp_forwarding_capacity"
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_ROOT = PACKAGE_ROOT / "results"
DEFAULT_CONFIG_PATH = PACKAGE_ROOT / "configs" / "test_config.yaml"
DEFAULT_CONFIG_TEMPLATE_PATH = PACKAGE_ROOT / "configs" / "test_config.yaml.template"
DEFAULT_SEARCH_PRESET_DIR = PACKAGE_ROOT / "presets" / "search"
DEFAULT_SEARCH_PRESET_NAME = "standart"
DEFAULT_SERVER_IP = "10.8.0.2"
DEFAULT_SERVER_PORT = 9000
DEFAULT_SERVER_MODE = "echo"
DEFAULT_BIND_IP = "0.0.0.0"
DEFAULT_BASE_SRC_PORT = 20000
DEFAULT_DRAIN_DURATION_SEC = 2
DEFAULT_LATENCY_SAMPLE_SIZE = 50_000
DEFAULT_ACTUAL_PPS_TOLERANCE = 0.02


@dataclass(slots=True)
class MachineMetadata:
    hostname: str
    kernel_version: str
    python_version: str


@dataclass(slots=True)
class TrafficTestConfig:
    nat_mode: str
    server_mode: str
    server_ip: str
    server_port: int
    packet_size_bytes: int
    flow_count: int
    loss_threshold: float
    warmup_duration_sec: int
    measurement_duration_sec: int
    search_preset: str = DEFAULT_SEARCH_PRESET_NAME
    base_src_port: int = 20000
    bind_ip: str = "0.0.0.0"
    drain_duration_sec: int = 2
    latency_sample_size: int = 50_000
    actual_pps_tolerance: float = 0.02
    socket_send_buffer_bytes: int = 4 * 1024 * 1024
    socket_receive_buffer_bytes: int = 4 * 1024 * 1024
    test_name: str = TEST_NAME

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_valid_nat_mode(value: str) -> str:
    if value not in VALID_NAT_MODES:
        raise ValueError(f"Unsupported nat mode '{value}'. Expected one of: {', '.join(VALID_NAT_MODES)}")
    return value


def ensure_valid_server_mode(value: str) -> str:
    if value not in VALID_SERVER_MODES:
        raise ValueError(
            f"Unsupported server mode '{value}'. Expected one of: {', '.join(VALID_SERVER_MODES)}"
        )
    return value


def validate_traffic_config(config: TrafficTestConfig) -> None:
    ensure_valid_nat_mode(config.nat_mode)
    ensure_valid_server_mode(config.server_mode)
    if config.packet_size_bytes < 32:
        raise ValueError("packet_size_bytes must be at least 32 bytes to fit the test packet header")
    if config.flow_count <= 0:
        raise ValueError("flow_count must be positive")
    if config.measurement_duration_sec <= 0:
        raise ValueError("measurement_duration_sec must be positive")
    if config.warmup_duration_sec < 0:
        raise ValueError("warmup_duration_sec must be non-negative")
    if config.base_src_port <= 0:
        raise ValueError("base_src_port must be positive")
    if config.base_src_port + config.flow_count - 1 > 65535:
        raise ValueError("base_src_port + flow_count exceeds UDP port range")
    if config.loss_threshold < 0:
        raise ValueError("loss_threshold must be non-negative")
    if config.drain_duration_sec < 0:
        raise ValueError("drain_duration_sec must be non-negative")
    if config.latency_sample_size <= 0:
        raise ValueError("latency_sample_size must be positive")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def collect_machine_metadata() -> MachineMetadata:
    return MachineMetadata(
        hostname=socket.gethostname(),
        kernel_version=platform.release(),
        python_version=sys.version.split()[0],
    )


def machine_metadata_dict() -> dict[str, Any]:
    return asdict(collect_machine_metadata())


def slugify_value(value: Any) -> str:
    rendered = str(value)
    rendered = rendered.replace(".", "p")
    rendered = rendered.replace("-", "m")
    rendered = re.sub(r"[^A-Za-z0-9_]+", "_", rendered)
    return rendered.strip("_") or "value"


def build_results_dir(config: TrafficTestConfig, results_root: Path | None = None) -> Path:
    root = results_root or DEFAULT_RESULTS_ROOT
    segments = (
        ("test_name", config.test_name),
        ("loss_threshold", config.loss_threshold),
        ("search_preset", config.search_preset),
        ("server_mode", config.server_mode),
        ("flow_count", config.flow_count),
        ("packet_size_bytes", config.packet_size_bytes),
        ("nat_mode", config.nat_mode),
    )
    path = root
    for key, value in segments:
        path /= f"{key}_{slugify_value(value)}"
    return path


def build_run_once_results_dir(
    config: TrafficTestConfig,
    configured_pps: int,
    results_root: Path | None = None,
) -> Path:
    root = results_root or DEFAULT_RESULTS_ROOT
    segments = (
        ("test_name", config.test_name),
        ("loss_threshold", config.loss_threshold),
        ("search_preset", config.search_preset),
        ("server_mode", config.server_mode),
        ("flow_count", config.flow_count),
        ("packet_size_bytes", config.packet_size_bytes),
        ("configured_pps", configured_pps),
        ("nat_mode", config.nat_mode),
    )
    path = root
    for key, value in segments:
        path /= f"{key}_{slugify_value(value)}"
    return path


def build_search_results_dir(
    config: TrafficTestConfig,
    pps_precision_delta: int,
    results_root: Path | None = None,
) -> Path:
    root = results_root or DEFAULT_RESULTS_ROOT
    segments = (
        ("test_name", config.test_name),
        ("loss_threshold", config.loss_threshold),
        ("search_preset", config.search_preset),
        ("server_mode", config.server_mode),
        ("flow_count", config.flow_count),
        ("packet_size_bytes", config.packet_size_bytes),
        ("nat_mode", config.nat_mode),
    )
    path = root
    for key, value in segments:
        path /= f"{key}_{slugify_value(value)}"
    return path


def as_yaml_lines(data: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(data, dict):
        lines: list[str] = []
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(as_yaml_lines(value, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {yaml_scalar(value)}")
        return lines
    if isinstance(data, list):
        lines = []
        for item in data:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(as_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {yaml_scalar(item)}")
        return lines
    return [f"{prefix}{yaml_scalar(data)}"]


def yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if text == "" or any(ch in text for ch in ":#[]{}&*!?|>'\"%@`"):
        escaped = text.replace("\\", "\\\\").replace("\"", "\\\"")
        return f"\"{escaped}\""
    return text


def load_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in raw_line:
            raise ValueError(f"{path}:{line_number}: expected 'key: value'")
        key, raw_value = raw_line.split(":", 1)
        key = key.strip()
        value_text = strip_inline_comment(raw_value).strip()
        if not key:
            raise ValueError(f"{path}:{line_number}: empty key is not allowed")
        data[key] = parse_yaml_scalar(value_text)
    return data


def load_search_preset(search_preset: str) -> tuple[dict[str, Any], Path]:
    preset_path = DEFAULT_SEARCH_PRESET_DIR / f"{search_preset}.yaml"
    if not preset_path.exists():
        raise ValueError(f"search preset '{search_preset}' not found: {preset_path}")
    return load_simple_yaml(preset_path), preset_path


def strip_inline_comment(raw_value: str) -> str:
    in_single = False
    in_double = False
    result_chars: list[str] = []
    for char in raw_value:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == "\"" and not in_single:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            break
        result_chars.append(char)
    return "".join(result_chars)


def parse_yaml_scalar(value_text: str) -> Any:
    if value_text == "":
        return ""
    lowered = value_text.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if (value_text.startswith("\"") and value_text.endswith("\"")) or (
        value_text.startswith("'") and value_text.endswith("'")
    ):
        return literal_eval(value_text)
    if re.fullmatch(r"[+-]?[0-9]+", value_text):
        return int(value_text)
    if re.fullmatch(r"[+-]?[0-9]*\.[0-9]+", value_text):
        return float(value_text)
    return value_text
