from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from manage_nat.nat_mode import parse_configured_workers, parse_managed_nat_mode

from .config import HostTestConfig, SearchConfig, build_results_dir
from .results import RunLogger, ensure_directory, write_json
from .trex.run.udp import UdpRunResult, run_udp_measurement
from .trex.setup import configure_vpp_memif_rx_placement, setup_trex_server

LOG_FILE_NAME = "program.log"
HISTORY_FILE_NAME = "history.json"
RESULT_FILE_NAME = "result.json"
LOG_SEPARATOR = "#" * 50
CLEAR_NAT_COMMANDS = {
    "none": None,
    "nat44": "sudo vppctl clear nat44 ed sessions",
    "nat_fo": "sudo vppctl nat_fo clear sessions",
}


def manage_nat_command() -> str:
    """Возвращает путь к CLI `manage-nat` из текущего virtualenv, если он есть."""
    local_command = Path(sys.executable).with_name("manage-nat")
    if local_command.exists():
        return str(local_command)
    return "manage-nat"


class LoadSearchError(RuntimeError):
    """Ошибка поиска рабочей границы PPS."""


@dataclass(frozen=True)
class LoadStep:
    """Одна точка PPS в поиске границы."""

    phase: str
    step_index: int
    target_pps: int
    passed: bool
    measurement: UdpRunResult


def run_load_test(
    config: HostTestConfig,
    search: SearchConfig,
    load_config_path: Path,
    search_config_path: Path,
) -> dict[str, Any]:
    """Запускает TRex load-test: exponential search, затем binary search."""
    results_dir = build_results_dir(config)
    reset_results_dir(results_dir)
    logger = RunLogger(results_dir / LOG_FILE_NAME)
    precision_delta = max(1, int(search.search_initial_pps * search.search_relative_precision))

    log_start_block(
        logger=logger,
        config=config,
        search=search,
        precision_delta=precision_delta,
        load_config_path=load_config_path,
        search_config_path=search_config_path,
        results_dir=results_dir,
    )

    try:
        topology_changed = ensure_nat_mode(config.nat_mode, config.n_workers, logger)
        ensure_vpp_topology(logger)
        ensure_trex_ready(topology_changed, logger)
        payload = run_boundary_search(
            config=config,
            search=search,
            precision_delta=precision_delta,
            results_dir=results_dir,
            logger=logger,
        )
        logger(f"Готово: result={results_dir / RESULT_FILE_NAME}, history={results_dir / HISTORY_FILE_NAME}")
        return payload
    except Exception as exc:
        logger(f"Ошибка: {exc}")
        raise


def log_start_block(
    logger: RunLogger,
    config: HostTestConfig,
    search: SearchConfig,
    precision_delta: int,
    load_config_path: Path,
    search_config_path: Path,
    results_dir: Path,
) -> None:
    """Печатает стартовый блок с параметрами эксперимента."""
    logger(
        "\n".join(
            (
                LOG_SEPARATOR,
                "НАЧАЛО TRex load-test",
                "",
                f"load_config: {load_config_path}",
                f"search_config: {search_config_path}",
                f"results_dir: {results_dir}",
                "",
                f"nat_mode: {config.nat_mode}",
                f"n_workers: {format_number(config.n_workers)}",
                f"flow_count: {format_number(config.flow_count)}",
                f"packet_size: {format_number(config.packet_size)}",
                f"target_loss_rate: {format_percent(config.target_loss_rate)}",
                "",
                f"measurement_sec: {format_number(search.measurement_sec)}",
                f"search_initial_pps: {format_number(search.search_initial_pps)}",
                f"search_max_pps: {format_number(search.search_max_pps)}",
                f"search_relative_precision: {format_percent(search.search_relative_precision)}",
                f"search_pps_precision_delta: {format_number(precision_delta)}",
                "",
            )
        )
    )


def reset_results_dir(results_dir: Path) -> None:
    """Очищает три файла результата перед новым запуском."""
    ensure_directory(results_dir)
    for file_name in (LOG_FILE_NAME, HISTORY_FILE_NAME, RESULT_FILE_NAME):
        (results_dir / file_name).unlink(missing_ok=True)


def ensure_nat_mode(target_mode: str, target_workers: int, logger: RunLogger) -> bool:
    """Проверяет NAT-режим/число workers и возвращает True, если топология пересоздавалась."""
    current_mode = parse_managed_nat_mode()
    current_workers = parse_configured_workers()
    if current_mode == target_mode and current_workers == target_workers:
        return False

    logger(
        "Переключаем параметры NAT/VPP: "
        f"mode {current_mode} -> {target_mode}, "
        f"n_workers {current_workers} -> {target_workers}"
    )
    result = subprocess.run(
        [
            manage_nat_command(),
            "switch",
            target_mode,
            "--n_workers",
            str(target_workers),
            "--restart",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        log_failed_subprocess("manage-nat", result, logger)
        raise RuntimeError(f"manage-nat switch failed with code {result.returncode}")
    return True


def ensure_vpp_topology(logger: RunLogger) -> None:
    """Проверяет, что runtime-топология VPP поднята."""
    result = subprocess.run(
        ["sudo", "vppctl", "show", "interface"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        log_failed_subprocess("vppctl", result, logger)
        raise RuntimeError("Не удалось выполнить `sudo vppctl show interface`")
    if not has_nonlocal_vpp_interface(result.stdout):
        raise RuntimeError("VPP-топология не поднята: `show interface` содержит только local0")


def has_nonlocal_vpp_interface(show_interface_output: str) -> bool:
    """Проверяет, что в VPP есть интерфейсы кроме local0."""
    for raw_line in show_interface_output.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("Name") or stripped.startswith("local0"):
            continue
        return True
    return False


def ensure_trex_ready(topology_changed: bool, logger: RunLogger) -> None:
    """Перезапускает TRex, если VPP-топология была пересоздана или link down."""
    if topology_changed:
        restart_trex_server("VPP topology changed after NAT mode switch", logger)
        return
    if not trex_links_are_up():
        restart_trex_server("TRex memif links are down", logger)
        return
    configure_vpp_memif_rx_placement()


def restart_trex_server(reason: str, logger: RunLogger) -> None:
    """Перезапускает TRex server под текущие VPP memif-сокеты."""
    logger(f"Перезапускаем TRex server: {reason}")
    setup_trex_server()


def trex_links_are_up() -> bool:
    """Проверяет, что оба TRex-порта видят link UP."""
    from .trex.run.udp import CLIENT_PORT, SERVER_PORT, TREX_SERVER_HOST, load_trex_stl_api

    api = load_trex_stl_api()
    client = api.STLClient(server=TREX_SERVER_HOST)
    try:
        client.connect()
        for port in (CLIENT_PORT, SERVER_PORT):
            if client.get_port_attr(port).get("link") != "UP":
                return False
        return True
    except Exception:
        return False
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def run_boundary_search(
    config: HostTestConfig,
    search: SearchConfig,
    precision_delta: int,
    results_dir: Path,
    logger: RunLogger,
) -> dict[str, Any]:
    """Ищет максимальный passing PPS."""
    steps: list[LoadStep] = []
    step_index = 0

    def execute_step(phase: str, target_pps: int) -> LoadStep:
        nonlocal step_index
        step_index += 1
        clear_nat_sessions(config.nat_mode, logger)
        clear_vpp_runtime_counters(logger)
        logger.begin(f"step #{step_index}: target_pps={format_number(target_pps)}")
        try:
            measurement = run_udp_measurement(
                target_pps=target_pps,
                duration=search.measurement_sec,
                packet_size=config.packet_size,
                flow_count=config.flow_count,
                warmup_sec=search.warmup_sec,
            )
        except Exception as exc:
            logger.finish(f" -> error={exc}")
            raise
        passed = measurement.loss_rate <= config.target_loss_rate
        logger.finish(
            " -> "
            f"loss={measurement.loss_percent:.6f}%, "
            f"expected={format_number(measurement.expected_packets)}, "
            f"received={format_number(measurement.received_packets)}, "
            f"loss={format_number(measurement.lost_packets)} {format_verdict(passed)}"
        )
        step = LoadStep(
            phase=phase,
            step_index=step_index,
            target_pps=target_pps,
            passed=passed,
            measurement=measurement,
        )
        steps.append(step)
        return step

    log_phase(logger, "PHASE 1/2: exponential search")
    left_step = execute_step("exponential", search.search_initial_pps)
    if not left_step.passed:
        payload = build_history_payload(config, search, precision_delta, steps, None)
        persist_payloads(payload, results_dir, logger)
        raise LoadSearchError("search_initial_pps уже выше допустимого loss threshold")

    current_left = left_step
    right_step: LoadStep | None = None
    current_pps = left_step.target_pps
    while current_pps < search.search_max_pps:
        next_pps = min(current_pps * 2, search.search_max_pps)
        candidate = execute_step("exponential", next_pps)
        if candidate.passed:
            current_left = candidate
            current_pps = candidate.target_pps
            if current_pps == search.search_max_pps:
                break
            continue
        right_step = candidate
        break

    if right_step is None:
        payload = build_history_payload(config, search, precision_delta, steps, current_left)
        persist_payloads(payload, results_dir, logger)
        raise LoadSearchError("Даже search_max_pps не превысил loss threshold")

    log_phase(
        logger,
        "PHASE 2/2: binary search; "
        f"left_pps={format_number(current_left.target_pps)}, right_pps={format_number(right_step.target_pps)}",
    )
    while right_step.target_pps - current_left.target_pps > precision_delta:
        mid_pps = (current_left.target_pps + right_step.target_pps) // 2
        if mid_pps in (current_left.target_pps, right_step.target_pps):
            break
        candidate = execute_step("binary", mid_pps)
        if candidate.passed:
            current_left = candidate
        else:
            right_step = candidate

    payload = build_history_payload(config, search, precision_delta, steps, current_left)
    persist_payloads(payload, results_dir, logger)
    return build_final_result_payload(payload)


def clear_nat_sessions(nat_mode: str, logger: RunLogger) -> None:
    """Очищает NAT-сессии перед очередной точкой."""
    command = CLEAR_NAT_COMMANDS[nat_mode]
    if command is None:
        return
    result = subprocess.run(command.split(), text=True, capture_output=True, check=False)
    if result.returncode != 0:
        log_failed_subprocess("clear-nat", result, logger)
        raise RuntimeError("Не удалось очистить NAT-сессии")


def clear_vpp_runtime_counters(logger: RunLogger) -> None:
    """Очищает VPP runtime counters перед очередной точкой."""
    result = subprocess.run(["sudo", "vppctl", "clear", "runtime"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        log_failed_subprocess("clear-runtime", result, logger)
        raise RuntimeError("Не удалось очистить VPP runtime counters")


def build_history_payload(
    config: HostTestConfig,
    search: SearchConfig,
    precision_delta: int,
    steps: list[LoadStep],
    final_step: LoadStep | None,
) -> dict[str, Any]:
    """Собирает history payload."""
    return {
        "params": {
            "load_params": {
                "nat_mode": config.nat_mode,
                "n_workers": config.n_workers,
                "flow_count": config.flow_count,
                "packet_size": config.packet_size,
                "target_loss_rate": config.target_loss_rate,
            },
            "search_params": {
                "measurement_sec": search.measurement_sec,
                "search_initial_pps": search.search_initial_pps,
                "search_max_pps": search.search_max_pps,
                "search_relative_precision": search.search_relative_precision,
                "search_pps_precision_delta": precision_delta,
                "warmup_sec": search.warmup_sec,
            },
        },
        "result": [build_step_result(step) for step in steps],
        "_final_result": build_final_step_result(final_step),
    }


def build_step_result(step: LoadStep) -> dict[str, Any]:
    """Собирает JSON для одной точки поиска."""
    measurement = step.measurement
    return {
        "step_index": step.step_index,
        "phase": step.phase,
        "target_pps": step.target_pps,
        "passed": step.passed,
        "measurement_start_epoch": measurement.measurement_start_epoch,
        "measurement_end_epoch": measurement.measurement_end_epoch,
        "actual_sent_pps": measurement.actual_sent_pps,
        "expected_packets": measurement.expected_packets,
        "received_packets": measurement.received_packets,
        "tx_packets": measurement.tx_packets,
        "rx_packets": measurement.rx_packets,
        "lost_packets": measurement.lost_packets,
        "loss_rate": measurement.loss_rate,
        "loss_percent": measurement.loss_percent,
    }


def build_final_step_result(step: LoadStep | None) -> dict[str, Any]:
    """Собирает итоговый result payload."""
    if step is None:
        return {}
    payload = build_step_result(step)
    payload.pop("step_index", None)
    payload.pop("phase", None)
    payload.pop("passed", None)
    payload.pop("measurement_start_epoch", None)
    payload.pop("measurement_end_epoch", None)
    return payload


def persist_payloads(payload: dict[str, Any], results_dir: Path, logger: RunLogger) -> None:
    """Пишет history.json и result.json."""
    history_payload = {key: value for key, value in payload.items() if key != "_final_result"}
    write_json(results_dir / HISTORY_FILE_NAME, history_payload)
    write_json(results_dir / RESULT_FILE_NAME, build_final_result_payload(payload))
    logger(f"Результаты сохранены: {results_dir / HISTORY_FILE_NAME}, {results_dir / RESULT_FILE_NAME}")


def build_final_result_payload(history_payload: dict[str, Any]) -> dict[str, Any]:
    """Собирает короткий result.json."""
    return {
        "params": history_payload["params"],
        "result": history_payload.get("_final_result", {}),
    }


def log_phase(logger: RunLogger, title: str) -> None:
    """Печатает заголовок фазы."""
    logger("\n".join((LOG_SEPARATOR, title, LOG_SEPARATOR)))


def format_percent(rate: float) -> str:
    """Форматирует долю как процент."""
    return f"{rate * 100:.6f}%"


def format_number(value: int | float) -> str:
    """Форматирует число с точками между тысячными триадами."""
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    rendered = f"{value:,.6f}".replace(",", ".")
    return rendered.rstrip("0").rstrip(".")


def format_verdict(passed: bool) -> str:
    """Форматирует итог ступеньки."""
    return "PASSED" if passed else "FAILED"


def log_failed_subprocess(name: str, result: subprocess.CompletedProcess[str], logger: RunLogger) -> None:
    """Пишет stdout/stderr subprocess только при ошибке."""
    details = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if details:
        logger(f"{name} error details: {details}")
