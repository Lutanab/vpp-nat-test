from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import LatencyLoadConfig, LatencySearchConfig, build_latency_results_dir
from .load_runner import (
    LOG_SEPARATOR,
    clear_nat_sessions,
    clear_vpp_runtime_counters,
    ensure_nat_mode,
    ensure_trex_ready,
    ensure_vpp_topology,
    format_number,
    reset_results_dir,
)
from .results import RunLogger, write_json
from .trex.run.udp import UdpLatencyRunResult, run_udp_latency_measurement

LOG_FILE_NAME = "program.log"
HISTORY_FILE_NAME = "history.json"
RESULT_FILE_NAME = "result.json"


def run_latency_test(
    config: LatencyLoadConfig,
    search: LatencySearchConfig,
    load_config_path: Path,
    search_config_path: Path,
) -> dict[str, Any]:
    """Запускает single-shot latency тест и пишет артефакты результата."""
    results_dir = build_latency_results_dir(config)
    reset_results_dir(results_dir)
    logger = RunLogger(results_dir / LOG_FILE_NAME)
    log_start_block(
        logger=logger,
        config=config,
        search=search,
        load_config_path=load_config_path,
        search_config_path=search_config_path,
        results_dir=results_dir,
    )

    try:
        topology_changed = ensure_nat_mode(config.nat_mode, config.n_workers, logger)
        ensure_vpp_topology(logger)
        ensure_trex_ready(topology_changed, logger)
        clear_nat_sessions(config.nat_mode, logger)
        clear_vpp_runtime_counters(logger)
        logger.begin(f"latency step: target_pps={format_number(config.target_pps)}")
        try:
            measurement = run_udp_latency_measurement(
                target_pps=config.target_pps,
                duration=search.measurement_sec,
                packet_size=config.packet_size,
                flow_count=config.flow_count,
                warmup_sec=search.warmup_sec,
            )
        except Exception as exc:
            logger.finish(f" -> error={exc}")
            raise
        logger.finish(
            " -> "
            f"p50={render_optional_int(measurement.summary.p50_usec)}, "
            f"p95={render_optional_int(measurement.summary.p95_usec)}, "
            f"p99={render_optional_int(measurement.summary.p99_usec)}, "
            f"avg={render_optional_float(measurement.summary.average_usec)}, "
            f"load_loss={measurement.total_load_loss_percent:.6f}%"
        )
        log_pair_counters(logger, measurement)
        payload = build_history_payload(config=config, search=search, measurement=measurement)
        persist_payloads(payload=payload, results_dir=results_dir, logger=logger)
        logger(f"Готово: result={results_dir / RESULT_FILE_NAME}, history={results_dir / HISTORY_FILE_NAME}")
        return build_final_result_payload(payload)
    except Exception as exc:
        logger(f"Ошибка: {exc}")
        raise


def log_start_block(
    logger: RunLogger,
    config: LatencyLoadConfig,
    search: LatencySearchConfig,
    load_config_path: Path,
    search_config_path: Path,
    results_dir: Path,
) -> None:
    logger(
        "\n".join(
            (
                LOG_SEPARATOR,
                "НАЧАЛО TRex latency-test",
                "",
                f"load_config: {load_config_path}",
                f"search_config: {search_config_path}",
                f"results_dir: {results_dir}",
                "",
                f"nat_mode: {config.nat_mode}",
                f"n_workers: {format_number(config.n_workers)}",
                f"target_pps: {format_number(config.target_pps)}",
                f"flow_count: {format_number(config.flow_count)}",
                f"packet_size: {format_number(config.packet_size)}",
                "",
                f"warmup_sec: {format_number(search.warmup_sec)}",
                f"measurement_sec: {format_number(search.measurement_sec)}",
                "",
            )
        )
    )


def build_history_payload(
    config: LatencyLoadConfig,
    search: LatencySearchConfig,
    measurement: UdpLatencyRunResult,
) -> dict[str, Any]:
    return {
        "params": {
            "load_params": {
                "test_name": config.test_name,
                "nat_mode": config.nat_mode,
                "n_workers": config.n_workers,
                "target_pps": config.target_pps,
                "flow_count": config.flow_count,
                "packet_size": config.packet_size,
            },
            "search_params": {
                "warmup_sec": search.warmup_sec,
                "measurement_sec": search.measurement_sec,
            },
        },
        "result": [build_measurement_payload(measurement)],
        "_final_result": build_final_measurement_payload(measurement),
    }


def build_measurement_payload(measurement: UdpLatencyRunResult) -> dict[str, Any]:
    return {
        "measurement_start_epoch": measurement.measurement_start_epoch,
        "measurement_end_epoch": measurement.measurement_end_epoch,
        "pair_count": measurement.pair_count,
        "target_pps": measurement.target_pps,
        "duration_sec": measurement.duration_sec,
        "warmup_sec": measurement.warmup_sec,
        "packet_size": measurement.packet_size,
        "flow_count": measurement.flow_count,
        "total_load_expected_packets": measurement.total_load_expected_packets,
        "total_load_tx_packets": measurement.total_load_tx_packets,
        "total_load_rx_packets": measurement.total_load_rx_packets,
        "total_load_lost_packets": measurement.total_load_lost_packets,
        "total_load_loss_rate": measurement.total_load_loss_rate,
        "total_load_loss_percent": measurement.total_load_loss_percent,
        "total_load_actual_sent_pps": measurement.total_load_actual_sent_pps,
        "total_latency_tx_packets": measurement.total_latency_tx_packets,
        "total_latency_rx_packets": measurement.total_latency_rx_packets,
        "total_latency_samples": measurement.total_latency_samples,
        "latency": {
            "average_usec": measurement.summary.average_usec,
            "jitter_usec": measurement.summary.jitter_usec,
            "total_min_usec": measurement.summary.total_min_usec,
            "total_max_usec": measurement.summary.total_max_usec,
            "last_max_usec": measurement.summary.last_max_usec,
            "p50_usec": measurement.summary.p50_usec,
            "p95_usec": measurement.summary.p95_usec,
            "p99_usec": measurement.summary.p99_usec,
            "percentile_source": measurement.summary.percentile_source,
            "sample_count": measurement.summary.sample_count,
            "histogram": measurement.summary.histogram,
            "err_cntrs": measurement.summary.err_cntrs,
        },
        "pairs": [
            {
                "pair_index": pair.pair_index,
                "inside_port_id": pair.inside_port_id,
                "outside_port_id": pair.outside_port_id,
                "inside_ip": pair.inside_ip,
                "outside_ip": pair.outside_ip,
                "flow_count": pair.flow_count,
                "load_target_pps": pair.load_target_pps,
                "latency_target_pps": pair.latency_target_pps,
                "load_pg_id": pair.load_pg_id,
                "latency_pg_id": pair.latency_pg_id,
                "load_expected_packets": pair.load_expected_packets,
                "load_tx_packets": pair.load_tx_packets,
                "load_rx_packets": pair.load_rx_packets,
                "load_lost_packets": pair.load_lost_packets,
                "load_loss_rate": pair.load_loss_rate,
                "load_loss_percent": pair.load_loss_rate * 100,
                "load_actual_sent_pps": pair.load_actual_sent_pps,
                "latency_tx_packets": pair.latency_tx_packets,
                "latency_rx_packets": pair.latency_rx_packets,
                "latency_sample_count": pair.latency_sample_count,
                "latency_average_usec": pair.latency_average_usec,
                "latency_jitter_usec": pair.latency_jitter_usec,
                "latency_total_min_usec": pair.latency_total_min_usec,
                "latency_total_max_usec": pair.latency_total_max_usec,
                "latency_last_max_usec": pair.latency_last_max_usec,
                "latency_p50_usec": pair.latency_p50_usec,
                "latency_p95_usec": pair.latency_p95_usec,
                "latency_p99_usec": pair.latency_p99_usec,
                "latency_percentile_source": pair.latency_percentile_source,
                "latency_histogram": pair.latency_histogram,
                "latency_err_cntrs": pair.latency_err_cntrs,
            }
            for pair in measurement.pairs
        ],
    }


def build_final_measurement_payload(measurement: UdpLatencyRunResult) -> dict[str, Any]:
    return {
        "target_pps": measurement.target_pps,
        "pair_count": measurement.pair_count,
        "total_load_actual_sent_pps": measurement.total_load_actual_sent_pps,
        "total_load_loss_percent": measurement.total_load_loss_percent,
        "latency_avg_usec": measurement.summary.average_usec,
        "latency_p50_usec": measurement.summary.p50_usec,
        "latency_p95_usec": measurement.summary.p95_usec,
        "latency_p99_usec": measurement.summary.p99_usec,
        "latency_percentile_source": measurement.summary.percentile_source,
        "latency_total_max_usec": measurement.summary.total_max_usec,
        "latency_sample_count": measurement.summary.sample_count,
    }


def persist_payloads(payload: dict[str, Any], results_dir: Path, logger: RunLogger) -> None:
    history_payload = {key: value for key, value in payload.items() if key != "_final_result"}
    write_json(results_dir / HISTORY_FILE_NAME, history_payload)
    write_json(results_dir / RESULT_FILE_NAME, build_final_result_payload(payload))
    logger(f"Результаты сохранены: {results_dir / HISTORY_FILE_NAME}, {results_dir / RESULT_FILE_NAME}")


def build_final_result_payload(history_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "params": history_payload["params"],
        "result": history_payload.get("_final_result", {}),
    }


def log_pair_counters(logger: RunLogger, measurement: UdpLatencyRunResult) -> None:
    lines = ["latency per-pair counters"]
    for pair in measurement.pairs:
        lines.append(
            "  "
            f"pair={pair.pair_index}: "
            f"load_pps={format_number(pair.load_target_pps)}, "
            f"load_loss={pair.load_loss_rate * 100:.6f}%, "
            f"lat_avg={render_optional_float(pair.latency_average_usec)}, "
            f"lat_p99={render_optional_int(pair.latency_p99_usec)}, "
            f"samples={format_number(pair.latency_sample_count)}"
        )
    logger("\n".join(lines))


def render_optional_int(value: int | None) -> str:
    return str(value) if value is not None else "n/a"


def render_optional_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"
