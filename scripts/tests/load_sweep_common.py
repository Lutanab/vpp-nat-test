from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
LOAD_CONFIG_PATH = REPO_ROOT / "configs" / "loadtest" / "load" / "test_config.yaml"
TEST_NAT = REPO_ROOT / ".venv" / "bin" / "test-nat"
DEFAULT_NAT_MODES = ("none", "nat44", "nat_fo")
VALID_NAT_MODES = ("none", "nat44", "nat_fo")


@dataclass(frozen=True)
class LoadCase:
    test_name: str
    nat_mode: str
    n_workers: int
    flow_count: int
    packet_size: int


def format_list(values: Iterable[int]) -> str:
    return " ".join(str(value) for value in values)


def validate_positive(name: str, values: Iterable[int]) -> None:
    if any(value <= 0 for value in values):
        raise ValueError(f"{name} must contain positive integers")


def write_load_config(path: Path, case: LoadCase) -> None:
    content = path.read_text(encoding="utf-8")
    updates = {
        "test_name": case.test_name,
        "nat_mode": case.nat_mode,
        "n_workers": case.n_workers,
        "flow_count": case.flow_count,
        "packet_size": case.packet_size,
    }
    for key, value in updates.items():
        content = set_yaml_scalar(content, key, value)
    path.write_text(content, encoding="utf-8")


def set_yaml_scalar(content: str, key: str, value: str | int) -> str:
    rendered = str(value)
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*:\s*).*$", re.MULTILINE)
    updated, replacements = pattern.subn(rf"\g<1>{rendered}", content, count=1)
    if replacements == 1:
        return updated
    suffix = "" if content.endswith("\n") else "\n"
    return f"{content}{suffix}{key}: {rendered}\n"


def run_load_test(case: LoadCase, load_config_path: Path) -> int:
    print(
        "\n=== test-nat load: "
        f"test_name={case.test_name}, "
        f"nat_mode={case.nat_mode}, "
        f"n_workers={case.n_workers}, "
        f"flow_count={case.flow_count}, "
        f"packet_size={case.packet_size} ===",
        flush=True,
    )
    write_load_config(load_config_path, case)
    completed = subprocess.run([str(TEST_NAT), "load"], cwd=REPO_ROOT)
    return completed.returncode


def run_cases(cases: list[LoadCase], load_config_path: Path, dry_run: bool, keep_going: bool) -> int:
    if not TEST_NAT.exists():
        print(f"error: {TEST_NAT} not found", file=sys.stderr)
        return 1
    if not load_config_path.exists():
        print(f"error: {load_config_path} not found", file=sys.stderr)
        return 1

    if dry_run:
        for case in cases:
            print(
                f"{case.test_name}\t{case.nat_mode}\t"
                f"n_workers={case.n_workers}\tflow_count={case.flow_count}\tpacket_size={case.packet_size}"
            )
        return 0

    failures: list[tuple[LoadCase, int]] = []
    for case in cases:
        returncode = run_load_test(case, load_config_path)
        if returncode != 0:
            failures.append((case, returncode))
            print(
                f"\nFAILED: {case.nat_mode}, flow_count={case.flow_count}, "
                f"packet_size={case.packet_size}, exit_code={returncode}",
                file=sys.stderr,
                flush=True,
            )
            if not keep_going:
                return returncode

    if failures:
        print("\nFailed cases:", file=sys.stderr)
        for case, returncode in failures:
            print(
                f"- {case.test_name}: nat_mode={case.nat_mode}, "
                f"n_workers={case.n_workers}, flow_count={case.flow_count}, "
                f"packet_size={case.packet_size}, exit_code={returncode}",
                file=sys.stderr,
            )
        return 1

    print("\nAll selected load tests completed successfully.", flush=True)
    return 0
