#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
LATENCY_LOAD_CONFIG_PATH = REPO_ROOT / "configs" / "loadtest" / "latency" / "load" / "test_config.yaml"
LATENCY_SEARCH_CONFIG_PATH = REPO_ROOT / "configs" / "loadtest" / "latency" / "search" / "test_config.yaml"
TEST_NAT = REPO_ROOT / ".venv" / "bin" / "test-nat"
NAT_MODES = ("none", "nat44", "nat_fo")

# Hardcoded target PPS grid for latency runs.
#TARGET_PPS_GRID = (
#    100_000,
#    200_000,
#    300_000,
#    400_000,
#    425_000,
#    450_000,
#    475_000,
#    500_000,
#    525_000,
#    550_000,
#    575_000,
#    600_000,
#    650_000,
#    700_000,
#    800_000,
#    1_000_000,
#)

TARGET_PPS_GRID = (
    100_000,
    200_000,
    300_000,
    500_000,
    600_000,
    650_000,
    700_000,
    800_000,
    1_000_000,
    1_100_000,
    1_200_000,
    1_300_000,
    1_400_000,
    1_500_000,
    1_600_000,
    1_700_000,
    1_800_000,
    1_900_000,
    2_000_000
)

@dataclass(frozen=True)
class LatencyConfigBase:
    test_name: str
    n_workers: int
    flow_count: int
    packet_size: int


@dataclass(frozen=True)
class LatencyCase:
    test_name: str
    nat_mode: str
    n_workers: int
    flow_count: int
    packet_size: int
    target_pps: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run latency sweeps over hardcoded target_pps for all NAT modes, "
            "keeping test_name/n_workers/flow_count/packet_size fixed from latency load config."
        ),
    )
    parser.add_argument(
        "--load-config",
        type=Path,
        default=LATENCY_LOAD_CONFIG_PATH,
        help=f"Latency load config to rewrite (default: {LATENCY_LOAD_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--search-config",
        type=Path,
        default=LATENCY_SEARCH_CONFIG_PATH,
        help=f"Latency search config to pass to runner (default: {LATENCY_SEARCH_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned cases without changing config or running tests.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue after failed latency runs and report failures at the end.",
    )
    return parser.parse_args()


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
        value_text = raw_value.strip().split("#", 1)[0].strip()
        if re.fullmatch(r"[+-]?[0-9]+", value_text):
            data[key] = int(value_text)
            continue
        data[key] = value_text
    return data


def read_latency_base_config(path: Path) -> LatencyConfigBase:
    raw = load_simple_yaml(path)
    test_name = raw.get("test_name")
    n_workers = raw.get("n_workers")
    flow_count = raw.get("flow_count")
    packet_size = raw.get("packet_size")

    if not isinstance(test_name, str) or not test_name.strip():
        raise ValueError(f"{path}: missing or invalid test_name")
    if not isinstance(n_workers, int) or n_workers < 0:
        raise ValueError(f"{path}: n_workers must be non-negative int")
    if not isinstance(flow_count, int) or flow_count <= 0:
        raise ValueError(f"{path}: flow_count must be positive int")
    if not isinstance(packet_size, int) or packet_size <= 0:
        raise ValueError(f"{path}: packet_size must be positive int")

    return LatencyConfigBase(
        test_name=test_name.strip(),
        n_workers=n_workers,
        flow_count=flow_count,
        packet_size=packet_size,
    )


def set_yaml_scalar(content: str, key: str, value: str | int) -> str:
    rendered = str(value)
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*:\s*).*$", re.MULTILINE)
    updated, replacements = pattern.subn(rf"\g<1>{rendered}", content, count=1)
    if replacements == 1:
        return updated
    suffix = "" if content.endswith("\n") else "\n"
    return f"{content}{suffix}{key}: {rendered}\n"


def write_latency_load_config(path: Path, case: LatencyCase) -> None:
    content = path.read_text(encoding="utf-8")
    updates = {
        "test_name": case.test_name,
        "nat_mode": case.nat_mode,
        "n_workers": case.n_workers,
        "flow_count": case.flow_count,
        "packet_size": case.packet_size,
        "target_pps": case.target_pps,
    }
    for key, value in updates.items():
        content = set_yaml_scalar(content, key, value)
    path.write_text(content, encoding="utf-8")


def build_cases(base: LatencyConfigBase) -> list[LatencyCase]:
    return [
        LatencyCase(
            test_name=base.test_name,
            nat_mode=nat_mode,
            n_workers=base.n_workers,
            flow_count=base.flow_count,
            packet_size=base.packet_size,
            target_pps=target_pps,
        )
        for nat_mode in NAT_MODES
        for target_pps in TARGET_PPS_GRID
    ]


def run_latency_case(case: LatencyCase, load_config_path: Path, search_config_path: Path) -> int:
    print(
        "\n=== test-nat run latency: "
        f"test_name={case.test_name}, "
        f"nat_mode={case.nat_mode}, "
        f"n_workers={case.n_workers}, "
        f"flow_count={case.flow_count}, "
        f"packet_size={case.packet_size}, "
        f"target_pps={case.target_pps} ===",
        flush=True,
    )
    write_latency_load_config(load_config_path, case)
    completed = subprocess.run(
        [
            str(TEST_NAT),
            "run",
            "latency",
            "--load-config",
            str(load_config_path),
            "--search-config",
            str(search_config_path),
        ],
        cwd=REPO_ROOT,
    )
    return completed.returncode


def run_cases(
    cases: list[LatencyCase],
    load_config_path: Path,
    search_config_path: Path,
    dry_run: bool,
    keep_going: bool,
) -> int:
    if not TEST_NAT.exists():
        print(f"error: {TEST_NAT} not found", file=sys.stderr)
        return 1
    if not load_config_path.exists():
        print(f"error: {load_config_path} not found", file=sys.stderr)
        return 1
    if not search_config_path.exists():
        print(f"error: {search_config_path} not found", file=sys.stderr)
        return 1

    if dry_run:
        for case in cases:
            print(
                f"{case.test_name}\t{case.nat_mode}\t"
                f"n_workers={case.n_workers}\tflow_count={case.flow_count}\t"
                f"packet_size={case.packet_size}\ttarget_pps={case.target_pps}"
            )
        return 0

    failures: list[tuple[LatencyCase, int]] = []
    for case in cases:
        returncode = run_latency_case(case, load_config_path, search_config_path)
        if returncode != 0:
            failures.append((case, returncode))
            print(
                f"\nFAILED: nat_mode={case.nat_mode}, target_pps={case.target_pps}, "
                f"exit_code={returncode}",
                file=sys.stderr,
                flush=True,
            )
            if not keep_going:
                return returncode

    if failures:
        print("\nFailed cases:", file=sys.stderr)
        for case, returncode in failures:
            print(
                f"- {case.test_name}: nat_mode={case.nat_mode}, n_workers={case.n_workers}, "
                f"flow_count={case.flow_count}, packet_size={case.packet_size}, "
                f"target_pps={case.target_pps}, exit_code={returncode}",
                file=sys.stderr,
            )
        return 1

    print("\nAll selected latency tests completed successfully.", flush=True)
    return 0


def main() -> int:
    args = parse_args()
    try:
        base = read_latency_base_config(args.load_config)
    except (OSError, ValueError) as exc:
        print(f"error: failed to read latency base config: {exc}", file=sys.stderr)
        return 2

    return run_cases(
        cases=build_cases(base),
        load_config_path=args.load_config,
        search_config_path=args.search_config,
        dry_run=args.dry_run,
        keep_going=args.keep_going,
    )


if __name__ == "__main__":
    raise SystemExit(main())
