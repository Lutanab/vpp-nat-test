from __future__ import annotations

import re
from ast import literal_eval
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "test_config.yaml"
DEFAULT_CONFIG_TEMPLATE_PATH = PROJECT_ROOT / "configs" / "test_config.yaml.template"
DEFAULT_SEARCH_PRESET_DIR = PROJECT_ROOT / "virtual_machines" / "host_mounts" / "user_vm_1" / "testing" / "presets" / "search"
DEFAULT_RESULTS_ROOT = PROJECT_ROOT / "results"
DEFAULT_SEARCH_PRESET = "standart"
DEFAULT_USER_VM_SSH_TARGET = "zero@10.8.2.11"
DEFAULT_USER_VM_SSH_PORT = 22
DEFAULT_USER_VM_TESTING_DIR = "/mnt/host/testing"
DEFAULT_EXTERNAL_VM_SSH_TARGET = "zero@10.8.2.10"
DEFAULT_EXTERNAL_VM_SSH_PORT = 22
DEFAULT_SOCKPERF_SERVER_SERVICE = "sockperf-server.service"
DEFAULT_SERVER_IP = "10.8.0.2"
DEFAULT_SERVER_PORT_BASE = 5001
DEFAULT_REPLY_EVERY = 100
DEFAULT_VPP_SERVICE_NAME = "vpp.service"
DEFAULT_SCRAPE_INTERVAL_SEC = 1.0
DEFAULT_TEST_NAME = "sockperf_nat_boundary"
VALID_NAT_MODES = ("none", "nat44", "nat_fo")


@dataclass(slots=True)
class SearchPreset:
    name: str
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
    search_preset: str
    packet_size: int
    n_flows: int
    target_loss_rate: float
    user_vm_ssh_target: str
    user_vm_ssh_port: int
    user_vm_testing_dir: str
    external_vm_ssh_target: str
    external_vm_ssh_port: int
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


def load_host_config(config_path: Path | None) -> tuple[HostTestConfig, SearchPreset, Path]:
    resolved_config_path = config_path or DEFAULT_CONFIG_PATH
    if not resolved_config_path.exists():
        raise FileNotFoundError(
            f"Config file not found: {resolved_config_path}. "
            f"Start from template: {DEFAULT_CONFIG_TEMPLATE_PATH}"
        )

    raw_config = load_simple_yaml(resolved_config_path)
    search_preset_name = str(raw_config.get("search_preset", DEFAULT_SEARCH_PRESET))
    preset = load_search_preset(search_preset_name)

    config = HostTestConfig(
        nat_mode=ensure_valid_nat_mode(str(require(raw_config, "nat_mode"))),
        search_preset=search_preset_name,
        packet_size=int(require(raw_config, "packet_size")),
        n_flows=int(require(raw_config, "n_flows")),
        target_loss_rate=float(require(raw_config, "target_loss_rate")),
        user_vm_ssh_target=DEFAULT_USER_VM_SSH_TARGET,
        user_vm_ssh_port=DEFAULT_USER_VM_SSH_PORT,
        user_vm_testing_dir=DEFAULT_USER_VM_TESTING_DIR,
        external_vm_ssh_target=DEFAULT_EXTERNAL_VM_SSH_TARGET,
        external_vm_ssh_port=DEFAULT_EXTERNAL_VM_SSH_PORT,
        sockperf_server_service=DEFAULT_SOCKPERF_SERVER_SERVICE,
        server_ip=DEFAULT_SERVER_IP,
        server_port_base=DEFAULT_SERVER_PORT_BASE,
        reply_every=DEFAULT_REPLY_EVERY,
        vpp_service_name=DEFAULT_VPP_SERVICE_NAME,
        scrape_interval_sec=DEFAULT_SCRAPE_INTERVAL_SEC,
        results_root=DEFAULT_RESULTS_ROOT,
        test_name=DEFAULT_TEST_NAME,
    )
    validate_config(config, preset)
    return config, preset, resolved_config_path


def load_search_preset(name: str) -> SearchPreset:
    path = DEFAULT_SEARCH_PRESET_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Search preset not found: {path}")
    raw = load_simple_yaml(path)
    return SearchPreset(
        name=name,
        warmup_sec=int(require(raw, "warmup_sec")),
        measurement_sec=int(require(raw, "measurement_sec")),
        search_initial_pps=int(require(raw, "search_initial_pps")),
        search_max_pps=int(require(raw, "search_max_pps")),
        search_relative_precision=float(require(raw, "search_relative_precision")),
    )


def validate_config(config: HostTestConfig, preset: SearchPreset) -> None:
    if config.packet_size <= 0:
        raise ValueError("packet_size must be positive")
    if config.n_flows <= 0:
        raise ValueError("n_flows must be positive")
    if config.target_loss_rate < 0:
        raise ValueError("target_loss_rate must be non-negative")
    if config.reply_every <= 0:
        raise ValueError("reply_every must be positive")
    if config.scrape_interval_sec <= 0:
        raise ValueError("scrape_interval_sec must be positive")
    if preset.search_initial_pps <= 0 or preset.search_max_pps <= 0:
        raise ValueError("search PPS values must be positive")
    if preset.search_initial_pps > preset.search_max_pps:
        raise ValueError("search_initial_pps must be <= search_max_pps")
    if preset.search_relative_precision <= 0:
        raise ValueError("search_relative_precision must be positive")


def build_results_dir(config: HostTestConfig, preset: SearchPreset) -> Path:
    segments = (
        ("test_name", config.test_name),
        ("target_loss_rate", config.target_loss_rate),
        ("search_preset", preset.name),
        ("packet_size", config.packet_size),
        ("n_flows", config.n_flows),
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
    if lowered in {"null", "~", "none"}:
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
