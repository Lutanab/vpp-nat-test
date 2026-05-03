from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from statistics import mean
from typing import Any


@dataclass(slots=True)
class LatencySummary:
    avg: float | None
    p50: float | None
    p95: float | None
    p99: float | None
    sample_count: int
    observed_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "avg": self.avg,
            "p50": self.p50,
            "p95": self.p95,
            "p99": self.p99,
            "sample_count": self.sample_count,
            "observed_count": self.observed_count,
        }


class ReservoirSampler:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._samples: list[int] = []
        self._count = 0
        self._rng = random.Random()

    @property
    def count(self) -> int:
        return self._count

    @property
    def samples(self) -> list[int]:
        return list(self._samples)

    def add(self, value: int) -> None:
        self._count += 1
        if len(self._samples) < self.capacity:
            self._samples.append(value)
            return
        index = self._rng.randrange(self._count)
        if index < self.capacity:
            self._samples[index] = value


def summarize_latencies_ns(latencies_ns: ReservoirSampler) -> LatencySummary:
    samples = latencies_ns.samples
    if not samples:
        return LatencySummary(avg=None, p50=None, p95=None, p99=None, sample_count=0, observed_count=0)

    samples.sort()
    return LatencySummary(
        avg=round(mean(samples) / 1_000_000, 6),
        p50=round(percentile(samples, 0.50) / 1_000_000, 6),
        p95=round(percentile(samples, 0.95) / 1_000_000, 6),
        p99=round(percentile(samples, 0.99) / 1_000_000, 6),
        sample_count=len(samples),
        observed_count=latencies_ns.count,
    )


def percentile(sorted_values: list[int], ratio: float) -> float:
    if not sorted_values:
        raise ValueError("percentile() requires at least one value")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    index = ratio * (len(sorted_values) - 1)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = index - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction


class ResourceMonitor:
    def __init__(
        self,
        pid: int | None,
        interval_sec: float = 1.0,
    ) -> None:
        self.pid = pid
        self.interval_sec = interval_sec
        self.scope = "process" if pid is not None else "host"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._warnings: list[str] = []
        self._cpu_samples: list[float] = []
        self._rss_samples: list[int] = []
        self._prev_process: tuple[float, float, int] | None = None
        self._prev_host: tuple[int, int, int] | None = None

    @property
    def warnings(self) -> list[str]:
        return list(self._warnings)

    def start(self) -> None:
        if self.scope == "process":
            self._prev_process = self._read_process_sample()
        else:
            self._prev_host = self._read_host_sample()
        self._thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()

    def summary(self) -> dict[str, Any]:
        if not self._cpu_samples and not self._rss_samples:
            return {
                "resource_metric_scope": "not_collected" if self._warnings else self.scope,
                "cpu_percent_avg": None,
                "cpu_percent_max": None,
                "rss_memory_bytes_avg": None,
                "rss_memory_bytes_max": None,
            }

        return {
            "resource_metric_scope": self.scope,
            "cpu_percent_avg": round(mean(self._cpu_samples), 4) if self._cpu_samples else None,
            "cpu_percent_max": round(max(self._cpu_samples), 4) if self._cpu_samples else None,
            "rss_memory_bytes_avg": int(round(mean(self._rss_samples))) if self._rss_samples else None,
            "rss_memory_bytes_max": max(self._rss_samples) if self._rss_samples else None,
        }

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval_sec):
            try:
                if self.scope == "process":
                    self._sample_process()
                else:
                    self._sample_host()
            except Exception as exc:
                self._warnings.append(f"resource monitor sample failed: {exc}")
                return

    def _sample_process(self) -> None:
        if self._prev_process is None:
            return
        current = self._read_process_sample()
        if current is None:
            self._warnings.append(f"pid {self.pid} disappeared during measurement")
            return
        prev_wall, prev_cpu, _ = self._prev_process
        wall, cpu, rss = current
        wall_delta = wall - prev_wall
        cpu_delta = cpu - prev_cpu
        if wall_delta > 0:
            self._cpu_samples.append(max(cpu_delta / wall_delta * 100.0, 0.0))
        self._rss_samples.append(rss)
        self._prev_process = current

    def _sample_host(self) -> None:
        if self._prev_host is None:
            return
        current = self._read_host_sample()
        prev_total, prev_idle, _ = self._prev_host
        total, idle, mem_used = current
        total_delta = total - prev_total
        idle_delta = idle - prev_idle
        if total_delta > 0:
            cpu_percent = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
            self._cpu_samples.append(cpu_percent)
        self._rss_samples.append(mem_used)
        self._prev_host = current

    def _read_process_sample(self) -> tuple[float, float, int] | None:
        if self.pid is None:
            return None
        stat_path = f"/proc/{self.pid}/stat"
        statm_path = f"/proc/{self.pid}/statm"
        if not os.path.exists(stat_path):
            return None

        with open(stat_path, "r", encoding="utf-8") as handle:
            stat_raw = handle.read().strip()
        close_paren = stat_raw.rfind(")")
        fields = stat_raw[close_paren + 2 :].split()
        if len(fields) < 22:
            raise RuntimeError(f"unexpected /proc stat format for pid {self.pid}")

        utime = int(fields[11])
        stime = int(fields[12])
        page_size = os.sysconf("SC_PAGE_SIZE")
        with open(statm_path, "r", encoding="utf-8") as handle:
            statm_fields = handle.read().split()
        rss_pages = int(statm_fields[1])
        cpu_seconds = (utime + stime) / os.sysconf("SC_CLK_TCK")
        rss_bytes = rss_pages * page_size
        return (time.monotonic(), cpu_seconds, rss_bytes)

    def _read_host_sample(self) -> tuple[int, int, int]:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            cpu_fields = handle.readline().split()[1:]
        values = [int(field) for field in cpu_fields]
        idle = values[3] + values[4]
        total = sum(values)

        meminfo: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, raw_value = line.split(":", 1)
                meminfo[key] = int(raw_value.strip().split()[0]) * 1024
        mem_used = meminfo["MemTotal"] - meminfo["MemAvailable"]
        return total, idle, mem_used
