from __future__ import annotations

import re
from ast import literal_eval
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from manage_nat.config import VPP_FIXED_MEMIF_PAIRS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOAD_CONFIG_PATH = PROJECT_ROOT / "configs" / "loadtest" / "load" / "test_config.yaml"
DEFAULT_LOAD_CONFIG_TEMPLATE_PATH = PROJECT_ROOT / "configs" / "loadtest" / "load" / "test_config.yaml.template"
DEFAULT_SEARCH_CONFIG_PATH = PROJECT_ROOT / "configs" / "loadtest" / "search" / "test_config.yaml"
DEFAULT_SEARCH_CONFIG_TEMPLATE_PATH = PROJECT_ROOT / "configs" / "loadtest" / "search" / "test_config.yaml.template"
DEFAULT_LATENCY_LOAD_CONFIG_PATH = (
    PROJECT_ROOT / "configs" / "loadtest" / "latency" / "load" / "test_config.yaml"
)
DEFAULT_LATENCY_LOAD_CONFIG_TEMPLATE_PATH = (
    PROJECT_ROOT / "configs" / "loadtest" / "latency" / "load" / "test_config.yaml.template"
)
DEFAULT_LATENCY_SEARCH_CONFIG_PATH = (
    PROJECT_ROOT / "configs" / "loadtest" / "latency" / "search" / "test_config.yaml"
)
DEFAULT_LATENCY_SEARCH_CONFIG_TEMPLATE_PATH = (
    PROJECT_ROOT / "configs" / "loadtest" / "latency" / "search" / "test_config.yaml.template"
)
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "results"
DEFAULT_USER_VM_SSH_TARGET = "zero@10.8.2.11"
DEFAULT_USER_VM_SSH_PORT = 22
DEFAULT_USER_VM_TESTING_DIR = "/mnt/host/testing"
DEFAULT_USER_VM_TEST_IP = "10.8.1.2"
DEFAULT_EXTERNAL_VM_SSH_TARGET = "zero@10.8.2.10"
DEFAULT_EXTERNAL_VM_SSH_PORT = 22
DEFAULT_EXTERNAL_VM_TEST_IP = "10.8.0.2"
DEFAULT_SOCKPERF_SERVER_SERVICE = "sockperf-server.service"
DEFAULT_SERVER_IP = "10.8.0.2"
DEFAULT_SERVER_PORT_BASE = 5001
DEFAULT_REPLY_EVERY = 100
DEFAULT_VPP_SERVICE_NAME = "vpp.service"
DEFAULT_SCRAPE_INTERVAL_SEC = 1.0
DEFAULT_TEST_NAME = "trex_nat_boundary"
DEFAULT_FLOW_COUNT = 1
UDP_SPORT_RANGE_START = 1
UDP_SPORT_RANGE_END = 65535
VALID_NAT_MODES = ("none", "nat44", "nat_fo")


@dataclass(slots=True)
class SearchConfig:
    warmup_sec: int
    measurement_sec: int
    search_initial_pps: int
    search_max_pps: int
    search_relative_precision: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HostTestConfig:
    nat_mode: str
    n_workers: int
    flow_count: int
    packet_size: int
    target_loss_rate: float
    user_vm_ssh_target: str
    user_vm_ssh_port: int
    user_vm_testing_dir: str
    user_vm_test_ip: str
    external_vm_ssh_target: str
    external_vm_ssh_port: int
    external_vm_test_ip: str
    sockperf_server_service: str
    server_ip: str
    server_port_base: int
    reply_every: int
    vpp_service_name: str
    scrape_interval_sec: float
    results_root: Path
    test_name: str = DEFAULT_TEST_NAME

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results_root"] = str(self.results_root)
        return payload


@dataclass(slots=True)
class LatencyLoadConfig:
    nat_mode: str
    n_workers: int
    target_pps: int
    flow_count: int
    packet_size: int
    results_root: Path
    test_name: str = DEFAULT_TEST_NAME

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results_root"] = str(self.results_root)
        return payload


@dataclass(slots=True)
class LatencySearchConfig:
    warmup_sec: int
    measurement_sec: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_test_configs(
    load_config_path: Path | None,
    search_config_path: Path | None,
) -> tuple[HostTestConfig, SearchConfig, Path, Path]:
    resolved_load_config_path = load_config_path or DEFAULT_LOAD_CONFIG_PATH
    resolved_search_config_path = search_config_path or DEFAULT_SEARCH_CONFIG_PATH

    if not resolved_load_config_path.exists():
        raise FileNotFoundError(
            f"Load config file not found: {resolved_load_config_path}. "
            f"Start from template: {DEFAULT_LOAD_CONFIG_TEMPLATE_PATH}"
        )
    if not resolved_search_config_path.exists():
        raise FileNotFoundError(
            f"Search config file not found: {resolved_search_config_path}. "
            f"Start from template: {DEFAULT_SEARCH_CONFIG_TEMPLATE_PATH}"
        )

    raw_load_config = load_simple_yaml(resolved_load_config_path)
    raw_search_config = load_simple_yaml(resolved_search_config_path)

    config = HostTestConfig(
        nat_mode=ensure_valid_nat_mode(str(require(raw_load_config, "nat_mode"))),
        n_workers=int(raw_load_config.get("n_workers", 0)),
        flow_count=int(raw_load_config.get("flow_count", DEFAULT_FLOW_COUNT)),
        packet_size=int(require(raw_load_config, "packet_size")),
        target_loss_rate=float(require(raw_load_config, "target_loss_rate")),
        user_vm_ssh_target=DEFAULT_USER_VM_SSH_TARGET,
        user_vm_ssh_port=DEFAULT_USER_VM_SSH_PORT,
        user_vm_testing_dir=DEFAULT_USER_VM_TESTING_DIR,
        user_vm_test_ip=DEFAULT_USER_VM_TEST_IP,
        external_vm_ssh_target=DEFAULT_EXTERNAL_VM_SSH_TARGET,
        external_vm_ssh_port=DEFAULT_EXTERNAL_VM_SSH_PORT,
        external_vm_test_ip=DEFAULT_EXTERNAL_VM_TEST_IP,
        sockperf_server_service=DEFAULT_SOCKPERF_SERVER_SERVICE,
        server_ip=DEFAULT_SERVER_IP,
        server_port_base=DEFAULT_SERVER_PORT_BASE,
        reply_every=DEFAULT_REPLY_EVERY,
        vpp_service_name=DEFAULT_VPP_SERVICE_NAME,
        scrape_interval_sec=DEFAULT_SCRAPE_INTERVAL_SEC,
        results_root=DEFAULT_RESULTS_ROOT,
        test_name=str(raw_load_config.get("test_name") or DEFAULT_TEST_NAME),
    )
    search = SearchConfig(
        warmup_sec=int(raw_search_config.get("warmup_sec", 0)),
        measurement_sec=int(require(raw_search_config, "measurement_sec")),
        search_initial_pps=int(require(raw_search_config, "search_initial_pps")),
        search_max_pps=int(require(raw_search_config, "search_max_pps")),
        search_relative_precision=float(require(raw_search_config, "search_relative_precision")),
    )
    validate_config(config, search)
    return config, search, resolved_load_config_path, resolved_search_config_path


def load_latency_test_configs(
    load_config_path: Path | None,
    search_config_path: Path | None,
) -> tuple[LatencyLoadConfig, LatencySearchConfig, Path, Path]:
    resolved_load_config_path = load_config_path or DEFAULT_LATENCY_LOAD_CONFIG_PATH
    resolved_search_config_path = search_config_path or DEFAULT_LATENCY_SEARCH_CONFIG_PATH

    if not resolved_load_config_path.exists():
        raise FileNotFoundError(
            f"Load config file not found: {resolved_load_config_path}. "
            f"Start from template: {DEFAULT_LATENCY_LOAD_CONFIG_TEMPLATE_PATH}"
        )
    if not resolved_search_config_path.exists():
        raise FileNotFoundError(
            f"Search config file not found: {resolved_search_config_path}. "
            f"Start from template: {DEFAULT_LATENCY_SEARCH_CONFIG_TEMPLATE_PATH}"
        )

    raw_load_config = load_simple_yaml(resolved_load_config_path)
    raw_search_config = load_simple_yaml(resolved_search_config_path)

    config = LatencyLoadConfig(
        nat_mode=ensure_valid_nat_mode(str(require(raw_load_config, "nat_mode"))),
        n_workers=int(raw_load_config.get("n_workers", 0)),
        target_pps=int(require(raw_load_config, "target_pps")),
        flow_count=int(raw_load_config.get("flow_count", DEFAULT_FLOW_COUNT)),
        packet_size=int(require(raw_load_config, "packet_size")),
        results_root=DEFAULT_RESULTS_ROOT,
        test_name=str(raw_load_config.get("test_name") or DEFAULT_TEST_NAME),
    )
    search = LatencySearchConfig(
        warmup_sec=int(raw_search_config.get("warmup_sec", 0)),
        measurement_sec=int(require(raw_search_config, "measurement_sec")),
    )
    validate_latency_config(config, search)
    return config, search, resolved_load_config_path, resolved_search_config_path


def validate_config(config: HostTestConfig, search: SearchConfig) -> None:
    if config.packet_size <= 0:
        raise ValueError("packet_size must be positive")
    if config.n_workers < 0:
        raise ValueError("n_workers must be non-negative")
    if config.n_workers > VPP_FIXED_MEMIF_PAIRS:
        raise ValueError(f"n_workers must be <= {VPP_FIXED_MEMIF_PAIRS}")
    validate_flow_count(config.flow_count)
    if config.target_loss_rate < 0:
        raise ValueError("target_loss_rate must be non-negative")
    if config.reply_every <= 0:
        raise ValueError("reply_every must be positive")
    if config.scrape_interval_sec <= 0:
        raise ValueError("scrape_interval_sec must be positive")
    if search.warmup_sec < 0:
        raise ValueError("warmup_sec must be non-negative")
    if search.measurement_sec <= 0:
        raise ValueError("measurement_sec must be positive")
    if search.search_initial_pps <= 0 or search.search_max_pps <= 0:
        raise ValueError("search PPS values must be positive")
    if search.search_initial_pps > search.search_max_pps:
        raise ValueError("search_initial_pps must be <= search_max_pps")
    if search.search_relative_precision <= 0:
        raise ValueError("search_relative_precision must be positive")


def validate_latency_config(config: LatencyLoadConfig, search: LatencySearchConfig) -> None:
    if config.packet_size <= 0:
        raise ValueError("packet_size must be positive")
    if config.n_workers < 0:
        raise ValueError("n_workers must be non-negative")
    if config.n_workers > VPP_FIXED_MEMIF_PAIRS:
        raise ValueError(f"n_workers must be <= {VPP_FIXED_MEMIF_PAIRS}")
    if config.target_pps <= 0:
        raise ValueError("target_pps must be positive")
    validate_flow_count(config.flow_count)
    if search.warmup_sec < 0:
        raise ValueError("warmup_sec must be non-negative")
    if search.measurement_sec <= 0:
        raise ValueError("measurement_sec must be positive")


def validate_flow_count(flow_count: int) -> None:
    if flow_count <= 0:
        raise ValueError("flow_count must be positive")
    max_supported_flow_count = UDP_SPORT_RANGE_END - UDP_SPORT_RANGE_START + 1
    if flow_count > max_supported_flow_count:
        raise ValueError(
            "flow_count is too high for configured UDP sport range "
            f"{UDP_SPORT_RANGE_START}-{UDP_SPORT_RANGE_END}; "
            f"max supported flow_count is {max_supported_flow_count}"
        )


def build_results_dir(config: HostTestConfig) -> Path:
    segments = (
        ("test_name", config.test_name),
        ("flow_count", config.flow_count),
        ("n_workers", config.n_workers),
        ("target_loss_rate", config.target_loss_rate),
        ("packet_size", config.packet_size),
        ("nat_mode", config.nat_mode),
    )
    path = config.results_root
    for key, value in segments:
        path /= f"{key}_{slugify_value(value)}"
    return path


def build_latency_results_dir(config: LatencyLoadConfig) -> Path:
    segments = (
        ("test_name", config.test_name),
        ("flow_count", config.flow_count),
        ("n_workers", config.n_workers),
        ("target_pps", config.target_pps),
        ("packet_size", config.packet_size),
        ("nat_mode", config.nat_mode),
    )
    path = config.results_root
    for key, value in segments:
        path /= f"{key}_{slugify_value(value)}"
    return path


def ensure_valid_nat_mode(value: str) -> str:
    if value not in VALID_NAT_MODES:
        raise ValueError(f"Unsupported NAT mode '{value}'. Expected one of: {', '.join(VALID_NAT_MODES)}")
    return value


def require(data: dict[str, Any], key: str) -> Any:
    if key not in data or data[key] is None:
        raise ValueError(f"missing required config key: {key}")
    return data[key]


def slugify_value(value: Any) -> str:
    rendered = str(value)
    rendered = rendered.replace(".", "p")
    rendered = rendered.replace("-", "m")
    rendered = re.sub(r"[^A-Za-z0-9_]+", "_", rendered)
    return rendered.strip("_") or "value"


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
        return None
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
