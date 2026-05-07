from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape VPP systemd cgroup CPU/RAM usage into JSONL.")
    parser.add_argument("--service", required=True)
    parser.add_argument("--interval-sec", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        scrape(service=args.service, interval_sec=args.interval_sec, output_path=args.output)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        write_sample(args.output, {"timestamp": utc_now_iso(), "error": str(exc)})
        return 1
    return 0


def scrape(service: str, interval_sec: float, output_path: Path) -> None:
    cgroup_path = resolve_systemd_cgroup_path(service)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        sample = read_cgroup_sample(cgroup_path)
        sample["timestamp"] = utc_now_iso()
        sample["epoch"] = time.time()
        sample["service"] = service
        sample["cgroup_path"] = str(cgroup_path)
        write_sample(output_path, sample)
        time.sleep(interval_sec)


def resolve_systemd_cgroup_path(service: str) -> Path:
    result = subprocess.run(
        ["systemctl", "show", service, "--property=ControlGroup", "--value"],
        text=True,
        capture_output=True,
        check=True,
    )
    control_group = result.stdout.strip()
    if not control_group:
        raise RuntimeError(f"systemd did not return ControlGroup for {service}")
    return Path("/sys/fs/cgroup") / control_group.lstrip("/")


def read_cgroup_sample(cgroup_path: Path) -> dict[str, Any]:
    cpu_stat = read_key_value_file(cgroup_path / "cpu.stat")
    memory_current = read_int_file(cgroup_path / "memory.current")
    return {
        "cpu_usage_usec": int(cpu_stat["usage_usec"]),
        "memory_current_bytes": memory_current,
    }


def read_key_value_file(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        key, raw_value = raw_line.split(maxsplit=1)
        values[key] = int(raw_value)
    return values


def read_int_file(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def write_sample(path: Path, sample: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample, ensure_ascii=False) + "\n")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    sys.exit(main())
