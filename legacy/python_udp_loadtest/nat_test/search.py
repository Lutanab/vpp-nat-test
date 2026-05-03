from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import TrafficTestConfig, build_search_results_dir, machine_metadata_dict, utc_now_iso
from .results import ensure_directory, write_json
from .udp_client import StepResult, run_udp_client_once

LOG_FILE_NAME = "program.log"
HISTORY_FILE_NAME = "history.json"
RESULT_FILE_NAME = "result.json"
WARMUP_STRATEGY = "per_step"


class BoundarySearchError(RuntimeError):
    """Raised when the search cannot establish a valid passing/failing interval."""


@dataclass(slots=True)
class SearchStep:
    phase: str
    step_index: int
    result: StepResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "phase_ru": _phase_label(self.phase),
            "step_index": self.step_index,
            **self.result.to_dict(),
        }


class SearchLogger:
    def __init__(self, log_path: Path, console_emit: Callable[[str], None] | None = None) -> None:
        self.log_path = log_path
        self.console_emit = console_emit
        ensure_directory(log_path.parent)
        self.log_path.write_text("", encoding="utf-8")

    def __call__(self, message: str) -> None:
        line = f"{utc_now_iso()} {message}"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self.console_emit is not None:
            self.console_emit(message)


def run_boundary_search(
    config: TrafficTestConfig,
    initial_pps: int,
    max_pps: int,
    pps_precision_delta: int,
    nat_pid: int | None = None,
    results_root: Path | None = None,
    explicit_results_dir: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    if config.server_mode != "echo":
        raise ValueError("команда run требует --server-mode echo, потому что sink не позволяет измерять потери")
    if initial_pps <= 0 or max_pps <= 0:
        raise ValueError("search_initial_pps и search_max_pps должны быть положительными")
    if initial_pps > max_pps:
        raise ValueError("search_initial_pps должен быть меньше либо равен search_max_pps")
    if pps_precision_delta <= 0:
        raise ValueError("search_pps_precision_delta должен быть положительным")

    results_dir = explicit_results_dir or build_search_results_dir(
        config=config,
        pps_precision_delta=pps_precision_delta,
        results_root=results_root,
    )
    ensure_directory(results_dir)
    logger = SearchLogger(results_dir / LOG_FILE_NAME, console_emit=progress)
    logger(
        "Старт поиска границы: "
        f"results_dir={results_dir}, "
        f"search_initial_pps={initial_pps}, "
        f"search_max_pps={max_pps}, "
        f"search_pps_precision_delta={pps_precision_delta}, "
        f"loss_threshold={config.loss_threshold}, "
        f"search_preset={config.search_preset}"
    )
    logger(
        "Стратегия warmup: отдельный warmup на каждой ступеньке. "
        "Так каждая точка измеряется после выхода на свой собственный режим нагрузки, "
        "а не наследует переходные эффекты от предыдущего PPS."
    )

    steps: list[SearchStep] = []
    started_at = utc_now_iso()
    step_index = 0

    def execute_step(phase: str, target_pps: int) -> SearchStep:
        nonlocal step_index
        step_index += 1
        logger(f"Запуск ступеньки #{step_index}: phase={_phase_label(phase)}, target_pps={target_pps}")
        result = run_udp_client_once(
            config=config,
            configured_pps=target_pps,
            nat_pid=nat_pid,
            output_path=None,
            progress=logger,
        )
        step = SearchStep(phase=phase, step_index=step_index, result=result)
        verdict = "ниже_или_равно_порогу" if result.passed else "выше_порога"
        logger(
            "Результат ступеньки: "
            f"step_index={step.step_index}, "
            f"phase={_phase_label(phase)}, "
            f"target_pps={result.target_pps}, "
            f"actual_sent_pps={result.actual_sent_pps}, "
            f"loss_rate={result.loss_rate}, "
            f"verdict={verdict}"
        )
        steps.append(step)
        return step

    logger("Фаза 1/2: экспоненциальный поиск правой границы")
    left_step = execute_step("exponential", initial_pps)
    if not left_step.result.passed:
        message = (
            "Уже на search_initial_pps потери выше порога. "
            "Опустите search_initial_pps либо поднимите допустимый loss_threshold."
        )
        payload = _build_result_payload(
            config=config,
            initial_pps=initial_pps,
            max_pps=max_pps,
            pps_precision_delta=pps_precision_delta,
            status="initial_pps_above_threshold",
            message=message,
            recommendations=[
                "Уменьшите search_initial_pps.",
                "Либо увеличьте loss_threshold, если такой уровень потерь допустим для вашей методики.",
            ],
            steps=steps,
            left_step=None,
            right_step=left_step,
            results_dir=results_dir,
            started_at=started_at,
        )
        write_json(results_dir / HISTORY_FILE_NAME, payload)
        final_payload = _build_final_result_payload(payload)
        write_json(results_dir / RESULT_FILE_NAME, final_payload)
        logger(f"Поиск завершен с ошибкой: {message}")
        raise BoundarySearchError(message)

    right_step: SearchStep | None = None
    current_left = left_step
    current_pps = left_step.result.target_pps

    while current_pps < max_pps:
        next_pps = min(current_pps * 2, max_pps)
        candidate = execute_step("exponential", next_pps)
        if candidate.result.passed:
            current_left = candidate
            current_pps = candidate.result.target_pps
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
        payload = _build_result_payload(
            config=config,
            initial_pps=initial_pps,
            max_pps=max_pps,
            pps_precision_delta=pps_precision_delta,
            status="upper_bound_not_found",
            message=message,
            recommendations=[
                "Увеличьте search_max_pps.",
                "Либо сделайте loss_threshold строже, если хотите искать границу по меньшему уровню потерь.",
            ],
            steps=steps,
            left_step=current_left,
            right_step=None,
            results_dir=results_dir,
            started_at=started_at,
        )
        write_json(results_dir / HISTORY_FILE_NAME, payload)
        final_payload = _build_final_result_payload(payload)
        write_json(results_dir / RESULT_FILE_NAME, final_payload)
        logger(f"Поиск завершен с ошибкой: {message}")
        raise BoundarySearchError(message)

    logger(
        "Фаза 2/2: бинарный поиск. "
        f"Начальный интервал: left_pps={current_left.result.target_pps}, right_pps={right_step.result.target_pps}"
    )
    while right_step.result.target_pps - current_left.result.target_pps > pps_precision_delta:
        left_pps = current_left.result.target_pps
        right_pps = right_step.result.target_pps
        mid_pps = (left_pps + right_pps) // 2
        if mid_pps in (left_pps, right_pps):
            logger("Бинарный поиск остановлен: mid_pps совпал с границей интервала.")
            break

        candidate = execute_step("binary", mid_pps)
        if candidate.result.passed:
            current_left = candidate
        else:
            right_step = candidate

    message = (
        "Граница найдена. "
        f"Итоговый target_pps={current_left.result.target_pps}, "
        f"первый failing target_pps={right_step.result.target_pps}."
    )
    payload = _build_result_payload(
        config=config,
        initial_pps=initial_pps,
        max_pps=max_pps,
        pps_precision_delta=pps_precision_delta,
        status="boundary_found",
        message=message,
        recommendations=[],
        steps=steps,
        left_step=current_left,
        right_step=right_step,
        results_dir=results_dir,
        started_at=started_at,
    )
    write_json(results_dir / HISTORY_FILE_NAME, payload)
    final_payload = _build_final_result_payload(payload)
    write_json(results_dir / RESULT_FILE_NAME, final_payload)
    logger(
        "Поиск завершен успешно: "
        f"boundary_pps={payload['result']['boundary_target_pps']}, "
        f"result_json={results_dir / RESULT_FILE_NAME}, "
        f"history_json={results_dir / HISTORY_FILE_NAME}, "
        f"log_file={results_dir / LOG_FILE_NAME}"
    )
    return final_payload


def _build_result_payload(
    config: TrafficTestConfig,
    initial_pps: int,
    max_pps: int,
    pps_precision_delta: int,
    status: str,
    message: str,
    recommendations: list[str],
    steps: list[SearchStep],
    left_step: SearchStep | None,
    right_step: SearchStep | None,
    results_dir: Path,
    started_at: str,
) -> dict[str, Any]:
    boundary_result = left_step.result if left_step is not None else None
    first_failing_result = right_step.result if right_step is not None else None
    return {
        "status": status,
        "message": message,
        "recommendations": recommendations,
        "search": {
            "test_name": config.test_name,
            "nat_mode": config.nat_mode,
            "packet_size_bytes": config.packet_size_bytes,
            "flow_count": config.flow_count,
            "loss_threshold": config.loss_threshold,
            "search_preset": config.search_preset,
            "search_initial_pps": initial_pps,
            "search_max_pps": max_pps,
            "search_pps_precision_delta": pps_precision_delta,
            "warmup_strategy": WARMUP_STRATEGY,
            "server_mode": config.server_mode,
            "server_ip": config.server_ip,
            "server_port": config.server_port,
            "results_dir": str(results_dir),
            "log_file": str(results_dir / LOG_FILE_NAME),
            "history_file": str(results_dir / HISTORY_FILE_NAME),
            "result_file": str(results_dir / RESULT_FILE_NAME),
        },
        "result": {
            "boundary_target_pps": boundary_result.target_pps if boundary_result is not None else None,
            "boundary_actual_sent_pps": boundary_result.actual_sent_pps if boundary_result is not None else None,
            "boundary_actual_gbps": boundary_result.actual_gbps if boundary_result is not None else None,
            "boundary_loss_rate": boundary_result.loss_rate if boundary_result is not None else None,
            "boundary_latency_ms": boundary_result.latency_ms if boundary_result is not None else None,
            "boundary_resources": boundary_result.resources if boundary_result is not None else None,
            "first_failing_target_pps": first_failing_result.target_pps if first_failing_result is not None else None,
            "first_failing_actual_sent_pps": first_failing_result.actual_sent_pps if first_failing_result is not None else None,
            "first_failing_loss_rate": first_failing_result.loss_rate if first_failing_result is not None else None,
            "left_pps": boundary_result.target_pps if boundary_result is not None else None,
            "right_pps": first_failing_result.target_pps if first_failing_result is not None else None,
        },
        "steps": [step.to_dict() for step in steps],
        "machine_metadata": machine_metadata_dict(),
        "timestamps": {
            "started_at": started_at,
            "finished_at": utc_now_iso(),
        },
    }


def _build_final_result_payload(history_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": history_payload["status"],
        "message": history_payload["message"],
        "recommendations": history_payload["recommendations"],
        "search": history_payload["search"],
        "result": history_payload["result"],
        "machine_metadata": history_payload["machine_metadata"],
        "timestamps": history_payload["timestamps"],
    }


def _phase_label(phase: str) -> str:
    labels = {
        "exponential": "экспоненциальный_поиск",
        "binary": "бинарный_поиск",
    }
    return labels.get(phase, phase)
