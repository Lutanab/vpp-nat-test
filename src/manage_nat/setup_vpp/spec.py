from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import (
    VPP_PRIMARY_API_PREFIX,
    VPP_PRIMARY_LOG_PATH,
    VPP_PRIMARY_NAME,
    VPP_PRIMARY_RUN_DIR,
    VPP_PRIMARY_SERVICE_NAME,
    VPP_PRIMARY_STARTUP_CONF_PATH,
    VPP_SECONDARY_API_PREFIX,
    VPP_SECONDARY_LOG_PATH,
    VPP_SECONDARY_NAME,
    VPP_SECONDARY_RUN_DIR,
    VPP_SECONDARY_SERVICE_NAME,
    VPP_SECONDARY_STARTUP_CONF_PATH,
)


@dataclass(frozen=True, slots=True)
class VppInstance:
    name: str
    service: str
    startup_conf: Path
    run_dir: Path
    log_path: Path
    api_prefix: str
    main_core: int
    worker_core_start: int

    @property
    def cli_socket(self) -> Path:
        return self.run_dir / "cli.sock"

    @property
    def api_socket(self) -> Path:
        return self.run_dir / "api.sock"

    @property
    def stats_socket(self) -> Path:
        return self.run_dir / "stats.sock"

    @property
    def pidfile(self) -> Path:
        return self.run_dir / "vpp.pid"


PRIMARY = VppInstance(
    name=VPP_PRIMARY_NAME,
    service=VPP_PRIMARY_SERVICE_NAME,
    startup_conf=VPP_PRIMARY_STARTUP_CONF_PATH,
    run_dir=VPP_PRIMARY_RUN_DIR,
    log_path=VPP_PRIMARY_LOG_PATH,
    api_prefix=VPP_PRIMARY_API_PREFIX,
    main_core=7,
    worker_core_start=8,
)
SECONDARY = VppInstance(
    name=VPP_SECONDARY_NAME,
    service=VPP_SECONDARY_SERVICE_NAME,
    startup_conf=VPP_SECONDARY_STARTUP_CONF_PATH,
    run_dir=VPP_SECONDARY_RUN_DIR,
    log_path=VPP_SECONDARY_LOG_PATH,
    api_prefix=VPP_SECONDARY_API_PREFIX,
    main_core=10,
    worker_core_start=11,
)
VPP_INSTANCES = (PRIMARY, SECONDARY)
VPP_TARGETS = ("all", "primary", "secondary")


def instances_for_target(target: str) -> tuple[VppInstance, ...]:
    if target == "all":
        return VPP_INSTANCES
    if target == "primary":
        return (PRIMARY,)
    if target == "secondary":
        return (SECONDARY,)
    raise ValueError(f"unknown VPP target: {target}")
