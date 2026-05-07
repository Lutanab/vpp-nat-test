from __future__ import annotations

import json
import shlex
import signal
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from manage_nat._nat_mode import parse_managed_nat_mode

from .config import HostTestConfig, SearchPreset, build_results_dir
from .results import RunLogger, ensure_directory, parse_utc_iso, utc_now_iso, write_json

LOG_FILE_NAME = "program.log"
HISTORY_FILE_NAME = "history.json"
RESULT_FILE_NAME = "result.json"
WARMUP_STRATEGY = "per_step"
SSH_OPTIONS = (
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
    "-o",
    "ConnectTimeout=10",
)
CLEAR_NAT_COMMANDS = {
    "none": None,
    "nat44": "sudo vppctl clear nat44 ed sessions",
    "nat_fo": "sudo vppctl nat_fo clear sessions",
}


class BoundarySearchError(RuntimeError):
    """Raised when boundary search cannot produce a passing/failing interval."""


@dataclass(slots=True)
class StepRecord:
    phase: str
    phase_ru: str
    step_index: int
    target_pps: int
    passed: bool
    loss_rate: float | None
    actual_sent_pps: float | None
    worker_result: dict[str, Any]
    vpp_resources: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_load_search(config: HostTestConfig, preset: SearchPreset, config_path: Path) -> dict[str, Any]:
    results_dir = build_results_dir(config, preset)
    ensure_directory(results_dir)
    logger = RunLogger(results_dir / LOG_FILE_NAME)

    logger(
        "Старт нагрузочного теста: "
        f"config={config_path}, results_dir={results_dir}, nat_mode={config.nat_mode}, "
        f"search_preset={preset.name}, packet_size={config.packet_size}, n_flows={config.n_flows}, "
        f"loss_threshold={config.target_loss_rate}"
    )
    logger(
        "Стратегия warmup: warmup выполняется на каждой ступеньке. "
        "Так каждая точка PPS измеряется после стабилизации именно на своей нагрузке."
    )

    try:
        ensure_nat_mode(config.nat_mode, logger)
        ensure_external_sockperf_server(config, logger)
        return run_boundary_search(config=config, preset=preset, results_dir=results_dir, logger=logger)
    except BoundarySearchError:
        raise
    except Exception as exc:
        logger(f"Тест завершен с ошибкой: {exc}")
        raise


def ensure_external_sockperf_server(config: HostTestConfig, logger: RunLogger) -> None:
    logger(
        "Проверяем sockperf-server на external_vm: "
        f"{config.external_vm_ssh_target}:{config.external_vm_ssh_port}, service={config.sockperf_server_service}"
    )
    command = f"systemctl is-active {shlex.quote(config.sockperf_server_service)}"
    result = run_ssh_command(config.external_vm_ssh_target, config.external_vm_ssh_port, command, check=False)
    service_state = (result.stdout or result.stderr).strip() or "unknown"
    if result.returncode != 0:
        raise RuntimeError(
            f"{config.sockperf_server_service} на external_vm не активен: "
            f"systemctl is-active вернул code={result.returncode}, state={service_state!r}. "
            "Если code=4/state='unknown', unit, скорее всего, не установлен. "
            "Установите/запустите его на external_vm командой: "
            "'sudo python3 /mnt/host/sockperf_server/manage_sockperf_server.py install --bind 10.8.0.2 --port 5001'."
        )
    logger("sockperf-server на external_vm активен")


def ensure_nat_mode(target_mode: str, logger: RunLogger) -> None:
    current_mode = parse_managed_nat_mode()
    logger(f"Текущий NAT-режим на хосте: {current_mode}")
    if current_mode == target_mode:
        logger("NAT-режим уже соответствует конфигу")
        return

    logger(f"NAT-режим отличается от требуемого, переключаем: {current_mode} -> {target_mode}")
    command = [sys.executable, "-m", "manage_nat", "switch", target_mode]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.stdout:
        for line in result.stdout.splitlines():
            logger(f"manage-nat stdout: {line}")
    if result.stderr:
        for line in result.stderr.splitlines():
            logger(f"manage-nat stderr: {line}")
    if result.returncode != 0:
        raise RuntimeError(f"manage-nat switch failed with code {result.returncode}")
    logger(f"NAT-режим переключен на {target_mode}")


def run_boundary_search(
    config: HostTestConfig,
    preset: SearchPreset,
    results_dir: Path,
    logger: RunLogger,
) -> dict[str, Any]:
    initial_pps = preset.search_initial_pps
    max_pps = preset.search_max_pps
    precision_delta = max(1, int(initial_pps * preset.search_relative_precision))

    logger(
        "Параметры поиска: "
        f"search_initial_pps={initial_pps}, search_max_pps={max_pps}, "
        f"search_relative_precision={preset.search_relative_precision}, "
        f"search_pps_precision_delta={precision_delta}"
    )

    steps: list[StepRecord] = []
    started_at = utc_now_iso()
    step_index = 0

    def execute_step(phase: str, target_pps: int) -> StepRecord:
        nonlocal step_index
        step_index += 1
        logger(f"Запуск ступеньки #{step_index}: phase={phase_label(phase)}, target_pps={target_pps}")
        step = run_and_measure_step(
            config=config,
            preset=preset,
            phase=phase,
            step_index=step_index,
            target_pps=target_pps,
            logger=logger,
        )
        verdict = "ниже_или_равно_порогу" if step.passed else "выше_порога"
        logger(
            "Результат ступеньки: "
            f"step_index={step.step_index}, phase={step.phase_ru}, target_pps={step.target_pps}, "
            f"actual_sent_pps={step.actual_sent_pps}, loss_rate={step.loss_rate}, verdict={verdict}"
        )
        steps.append(step)
        return step

    logger("Фаза 1/2: экспоненциальный поиск правой границы")
    left_step = execute_step("exponential", initial_pps)
    if not left_step.passed:
        message = (
            "Уже на search_initial_pps потери выше порога. "
            "Опустите search_initial_pps либо поднимите допустимый loss_threshold."
        )
        payload = build_history_payload(
            config=config,
            preset=preset,
            precision_delta=precision_delta,
            status="initial_pps_above_threshold",
            message=message,
            recommendations=[
                "Уменьшите search_initial_pps в search preset.",
                "Либо увеличьте target_loss_rate, если такой уровень потерь допустим.",
            ],
            steps=steps,
            left_step=None,
            right_step=left_step,
            results_dir=results_dir,
            started_at=started_at,
        )
        persist_payloads(payload, results_dir, logger)
        raise BoundarySearchError(message)

    right_step: StepRecord | None = None
    current_left = left_step
    current_pps = left_step.target_pps
    while current_pps < max_pps:
        next_pps = min(current_pps * 2, max_pps)
        candidate = execute_step("exponential", next_pps)
        if candidate.passed:
            current_left = candidate
            current_pps = candidate.target_pps
            if current_pps == max_pps:
                break
            continue
        right_step = candidate
        break

    if right_step is None:
        message = (
            "Не удалось найти правую границу: даже на search_max_pps потери не превысили порог. "
            "Поднимите search_max_pps и повторите поиск."
        )
        payload = build_history_payload(
            config=config,
            preset=preset,
            precision_delta=precision_delta,
            status="upper_bound_not_found",
            message=message,
            recommendations=[
                "Увеличьте search_max_pps в search preset.",
                "Либо сделайте target_loss_rate строже, если границу нужно искать раньше.",
            ],
            steps=steps,
            left_step=current_left,
            right_step=None,
            results_dir=results_dir,
            started_at=started_at,
        )
        persist_payloads(payload, results_dir, logger)
        raise BoundarySearchError(message)

    logger(
        "Фаза 2/2: бинарный поиск. "
        f"Начальный интервал: left_pps={current_left.target_pps}, right_pps={right_step.target_pps}"
    )
    while right_step.target_pps - current_left.target_pps > precision_delta:
        mid_pps = (current_left.target_pps + right_step.target_pps) // 2
        if mid_pps in (current_left.target_pps, right_step.target_pps):
            logger("Бинарный поиск остановлен: mid_pps совпал с границей интервала")
            break
        candidate = execute_step("binary", mid_pps)
        if candidate.passed:
            current_left = candidate
        else:
            right_step = candidate

    message = (
        "Граница найдена. "
        f"Итоговый target_pps={current_left.target_pps}, первый failing target_pps={right_step.target_pps}."
    )
    payload = build_history_payload(
        config=config,
        preset=preset,
        precision_delta=precision_delta,
        status="boundary_found",
        message=message,
        recommendations=[],
        steps=steps,
        left_step=current_left,
        right_step=right_step,
        results_dir=results_dir,
        started_at=started_at,
    )
    persist_payloads(payload, results_dir, logger)
    logger(
        "Поиск завершен успешно: "
        f"result_json={results_dir / RESULT_FILE_NAME}, history_json={results_dir / HISTORY_FILE_NAME}"
    )
    return build_final_result_payload(payload)


def run_and_measure_step(
    config: HostTestConfig,
    preset: SearchPreset,
    phase: str,
    step_index: int,
    target_pps: int,
    logger: RunLogger,
) -> StepRecord:
    clear_nat_sessions(config.nat_mode, logger)
    with tempfile.NamedTemporaryFile(prefix="vpp-cgroup-", suffix=".jsonl", delete=False) as handle:
        scrape_path = Path(handle.name)

    scraper = start_vpp_scraper(config, scrape_path, logger)
    worker_result: dict[str, Any]
    try:
        try:
            worker_result = run_worker_step(config=config, preset=preset, target_pps=target_pps, logger=logger)
        finally:
            stop_vpp_scraper(scraper, logger)
        resources = summarize_vpp_scrape(scrape_path, worker_result.get("timestamps", {}))
    finally:
        scrape_path.unlink(missing_ok=True)

    aggregate = worker_result.get("aggregate", {})
    loss_rate = aggregate.get("loss_rate")
    actual_sent_pps = aggregate.get("actual_sent_pps")
    passed = worker_result.get("status") == "ok" and loss_rate is not None and float(loss_rate) <= config.target_loss_rate
    return StepRecord(
        phase=phase,
        phase_ru=phase_label(phase),
        step_index=step_index,
        target_pps=target_pps,
        passed=passed,
        loss_rate=float(loss_rate) if loss_rate is not None else None,
        actual_sent_pps=float(actual_sent_pps) if actual_sent_pps is not None else None,
        worker_result=worker_result,
        vpp_resources=resources,
    )


def clear_nat_sessions(nat_mode: str, logger: RunLogger) -> None:
    command = CLEAR_NAT_COMMANDS[nat_mode]
    if command is None:
        logger("Очистка NAT-сессий пропущена: nat_mode=none")
        return
    logger(f"Очищаем NAT-сессии: {command}")
    result = subprocess.run(shlex.split(command), text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Не удалось очистить NAT-сессии: {result.stderr.strip() or result.stdout.strip()}")


def start_vpp_scraper(config: HostTestConfig, scrape_path: Path, logger: RunLogger) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        "-m",
        "test_nat.vpp_scraper",
        "--service",
        config.vpp_service_name,
        "--interval-sec",
        str(config.scrape_interval_sec),
        "--output",
        str(scrape_path),
    ]
    logger(f"Запускаем VPP cgroup scraper: file={scrape_path}")
    return subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def stop_vpp_scraper(process: subprocess.Popen[str], logger: RunLogger) -> None:
    process.send_signal(signal.SIGINT)
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
    if stdout:
        for line in stdout.splitlines():
            logger(f"vpp-scraper stdout: {line}")
    if stderr:
        for line in stderr.splitlines():
            logger(f"vpp-scraper stderr: {line}")


def run_worker_step(
    config: HostTestConfig,
    preset: SearchPreset,
    target_pps: int,
    logger: RunLogger,
) -> dict[str, Any]:
    worker_command = [
        "uv",
        "run",
        "--no-config",
        "test-nat-worker",
        "run-step",
        "--target-pps",
        str(target_pps),
        "--packet-size",
        str(config.packet_size),
        "--n-flows",
        str(config.n_flows),
        "--warmup-sec",
        str(preset.warmup_sec),
        "--measurement-sec",
        str(preset.measurement_sec),
        "--server-ip",
        config.server_ip,
        "--server-port-base",
        str(config.server_port_base),
        "--reply-every",
        str(config.reply_every),
    ]
    remote_command = f"cd {shlex.quote(config.user_vm_testing_dir)} && {shlex.join(worker_command)}"
    logger(f"Запускаем worker run-step на user_vm_1: target_pps={target_pps}")
    result = run_ssh_command(config.user_vm_ssh_target, config.user_vm_ssh_port, remote_command, check=False)
    if result.stderr:
        for line in result.stderr.splitlines():
            logger(f"worker stderr: {line}")
    try:
        worker_result = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        if result.returncode != 0:
            raise RuntimeError(f"worker run-step failed with code {result.returncode}: {result.stderr.strip()}") from exc
        raise RuntimeError(f"worker stdout is not valid JSON: {result.stdout[:500]}") from exc
    if result.returncode != 0:
        logger(f"worker вернул non-zero exit_code={result.returncode}, но JSON результата получен")
        worker_result.setdefault("warnings", []).append(f"worker_exit_code={result.returncode}")
    return worker_result


def run_ssh_command(ssh_target: str, ssh_port: int, remote_command: str, check: bool) -> subprocess.CompletedProcess[str]:
    command = ["ssh", *SSH_OPTIONS, "-p", str(ssh_port), ssh_target, remote_command]
    return subprocess.run(command, text=True, capture_output=True, check=check)


def summarize_vpp_scrape(scrape_path: Path, timestamps: dict[str, Any]) -> dict[str, Any]:
    samples = read_scrape_samples(scrape_path)
    measurement_start = timestamps.get("measurement_started_at")
    measurement_finish = timestamps.get("measurement_finished_at")
    if measurement_start is not None and measurement_finish is not None:
        start_ts = parse_utc_iso(str(measurement_start))
        finish_ts = parse_utc_iso(str(measurement_finish))
        samples = [sample for sample in samples if start_ts <= sample.get("epoch", 0) <= finish_ts]

    valid_samples = [sample for sample in samples if "error" not in sample]
    errors = [sample["error"] for sample in samples if "error" in sample]
    if len(valid_samples) < 2:
        return {
            "status": "not_enough_samples",
            "sample_count": len(valid_samples),
            "cpu_percent_avg": None,
            "cpu_percent_max": None,
            "memory_current_bytes_avg": None,
            "memory_current_bytes_max": None,
            "warnings": errors,
        }

    cpu_percents: list[float] = []
    for previous, current in zip(valid_samples, valid_samples[1:]):
        wall_delta = float(current["epoch"]) - float(previous["epoch"])
        cpu_delta = int(current["cpu_usage_usec"]) - int(previous["cpu_usage_usec"])
        if wall_delta > 0:
            cpu_percents.append(max(0.0, cpu_delta / 1_000_000 / wall_delta * 100.0))
    memory_values = [int(sample["memory_current_bytes"]) for sample in valid_samples]
    return {
        "status": "ok",
        "sample_count": len(valid_samples),
        "cpu_percent_avg": round(mean(cpu_percents), 4) if cpu_percents else None,
        "cpu_percent_max": round(max(cpu_percents), 4) if cpu_percents else None,
        "memory_current_bytes_avg": int(round(mean(memory_values))),
        "memory_current_bytes_max": max(memory_values),
        "warnings": errors,
    }


def read_scrape_samples(scrape_path: Path) -> list[dict[str, Any]]:
    if not scrape_path.exists():
        return []
    samples: list[dict[str, Any]] = []
    for raw_line in scrape_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            samples.append(json.loads(raw_line))
        except json.JSONDecodeError:
            samples.append({"error": f"invalid scraper line: {raw_line[:200]}"})
    return samples


def build_history_payload(
    config: HostTestConfig,
    preset: SearchPreset,
    precision_delta: int,
    status: str,
    message: str,
    recommendations: list[str],
    steps: list[StepRecord],
    left_step: StepRecord | None,
    right_step: StepRecord | None,
    results_dir: Path,
    started_at: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "recommendations": recommendations,
        "search": {
            "test_name": config.test_name,
            "nat_mode": config.nat_mode,
            "packet_size": config.packet_size,
            "n_flows": config.n_flows,
            "target_loss_rate": config.target_loss_rate,
            "search_preset": preset.name,
            "search_initial_pps": preset.search_initial_pps,
            "search_max_pps": preset.search_max_pps,
            "search_relative_precision": preset.search_relative_precision,
            "search_pps_precision_delta": precision_delta,
            "warmup_strategy": WARMUP_STRATEGY,
            "server_ip": config.server_ip,
            "server_port": config.server_port_base,
            "results_dir": str(results_dir),
            "log_file": str(results_dir / LOG_FILE_NAME),
            "history_file": str(results_dir / HISTORY_FILE_NAME),
            "result_file": str(results_dir / RESULT_FILE_NAME),
        },
        "result": {
            "boundary_target_pps": left_step.target_pps if left_step is not None else None,
            "boundary_actual_sent_pps": left_step.actual_sent_pps if left_step is not None else None,
            "boundary_loss_rate": left_step.loss_rate if left_step is not None else None,
            "boundary_latency_rtt": get_latency(left_step),
            "boundary_vpp_resources": left_step.vpp_resources if left_step is not None else None,
            "first_failing_target_pps": right_step.target_pps if right_step is not None else None,
            "first_failing_actual_sent_pps": right_step.actual_sent_pps if right_step is not None else None,
            "first_failing_loss_rate": right_step.loss_rate if right_step is not None else None,
            "left_pps": left_step.target_pps if left_step is not None else None,
            "right_pps": right_step.target_pps if right_step is not None else None,
        },
        "steps": [step.to_dict() for step in steps],
        "timestamps": {
            "started_at": started_at,
            "finished_at": utc_now_iso(),
        },
    }


def get_latency(step: StepRecord | None) -> dict[str, Any] | None:
    if step is None:
        return None
    aggregate = step.worker_result.get("aggregate", {})
    latency = aggregate.get("latency_rtt")
    return latency if isinstance(latency, dict) else None


def persist_payloads(payload: dict[str, Any], results_dir: Path, logger: RunLogger) -> None:
    write_json(results_dir / HISTORY_FILE_NAME, payload)
    write_json(results_dir / RESULT_FILE_NAME, build_final_result_payload(payload))
    logger(f"Результаты сохранены: {results_dir / RESULT_FILE_NAME}, {results_dir / HISTORY_FILE_NAME}")


def build_final_result_payload(history_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": history_payload["status"],
        "message": history_payload["message"],
        "recommendations": history_payload["recommendations"],
        "search": history_payload["search"],
        "result": history_payload["result"],
        "timestamps": history_payload["timestamps"],
    }


def phase_label(phase: str) -> str:
    labels = {
        "exponential": "экспоненциальный_поиск",
        "binary": "бинарный_поиск",
    }
    return labels.get(phase, phase)
