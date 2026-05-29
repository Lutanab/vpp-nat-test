from __future__ import annotations

import rich_click as click

from .base import (
    DEFAULT_FAILOVER_CONFIG_PATH,
    DEFAULT_FAILOVER_TIME_CONFIG_PATH,
    load_failover_config,
    load_failover_time_config,
    prepare_failover_runtime,
)
from .profile import run_failover_udp_profile
from .simple import run_failover_udp_mapping_check


def run_failover_simple() -> None:
    """Запускает failover simple: UDP mapping recovery после рестарта VPP."""
    config = load_failover_config(DEFAULT_FAILOVER_CONFIG_PATH)
    time_config = load_failover_time_config(DEFAULT_FAILOVER_TIME_CONFIG_PATH)
    click.echo(
        "failover simple: "
        f"mode={config.nat_mode}, flows={config.flow_count}, "
        f"warmup={time_config.warmup_sec:g}s, wait={time_config.waiting_sec:g}s"
    )
    prepare_failover_runtime(config)
    run_failover_udp_mapping_check(config, time_config)


def run_failover_profile() -> None:
    """Запускает failover profile: recovery polling и измерение t1/t2."""
    config = load_failover_config(DEFAULT_FAILOVER_CONFIG_PATH)
    time_config = load_failover_time_config(DEFAULT_FAILOVER_TIME_CONFIG_PATH)
    prepare_failover_runtime(config)
    run_failover_udp_profile(config, time_config)
