from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_ROOT = REPO_ROOT / "results"
NAT_MODES = ("none", "nat44", "nat_fo")
NAT_MODE_COLORS = {
    "none": "#4e79a7",
    "nat44": "#f28e2b",
    "nat_fo": "#59a14f",
}


@dataclass(frozen=True)
class ResultRow:
    path: Path
    test_name: str | None
    nat_mode: str
    n_workers: int
    flow_count: int
    packet_size: int
    target_loss_rate: float | None
    result: dict[str, Any]


def resolve_results_dir(results_root: Path, test_name: str | None) -> Path:
    if test_name is None:
        return results_root
    candidate = results_root / f"test_name_{test_name}"
    if candidate.exists():
        return candidate
    return results_root / test_name


def iter_result_rows(results_root: Path, test_name: str | None = None) -> Iterable[ResultRow]:
    results_dir = resolve_results_dir(results_root, test_name)
    for result_path in results_dir.rglob("result.json"):
        payload = load_json(result_path)
        load_params = payload.get("params", {}).get("load_params", {})
        result = payload.get("result", {})

        nat_mode = load_params.get("nat_mode")
        n_workers = as_int(load_params.get("n_workers"))
        flow_count = as_int(load_params.get("flow_count"), default=1)
        packet_size = as_int(load_params.get("packet_size"))
        if nat_mode not in NAT_MODES or n_workers is None or flow_count is None or packet_size is None:
            continue
        if not isinstance(result, dict):
            continue

        yield ResultRow(
            path=result_path,
            test_name=extract_test_name(result_path),
            nat_mode=nat_mode,
            n_workers=n_workers,
            flow_count=flow_count,
            packet_size=packet_size,
            target_loss_rate=as_float(load_params.get("target_loss_rate")),
            result=result,
        )


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    return default


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def extract_test_name(path: Path) -> str | None:
    for part in path.parts:
        if part.startswith("test_name_"):
            return part.removeprefix("test_name_")
    return None


def result_pps(row: ResultRow, pps_key: str) -> float | None:
    value = row.result.get(pps_key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def pps_to_metric(pps: float, packet_size: int, metric: str) -> float:
    if metric == "mpps":
        return pps / 1_000_000
    if metric == "gbps":
        return pps * packet_size * 8 / 1_000_000_000
    raise ValueError(f"Unsupported metric: {metric}")


def metric_label(metric: str) -> str:
    if metric == "mpps":
        return "Throughput, Mpps"
    if metric == "gbps":
        return "Throughput, Gbit/s"
    raise ValueError(f"Unsupported metric: {metric}")


def metric_suffix(metric: str) -> str:
    if metric == "mpps":
        return "Mpps"
    if metric == "gbps":
        return "Gbit/s"
    raise ValueError(f"Unsupported metric: {metric}")


def configure_plot_style() -> None:
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "figure.figsize": (10, 6),
            "axes.grid": True,
            "grid.linestyle": "--",
            "grid.alpha": 0.35,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def format_float(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.12g}"
