#!/usr/bin/env python3
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOAD_CONFIG_PATH = REPO_ROOT / "configs" / "loadtest" / "load" / "test_config.yaml"
TEST_NAT = REPO_ROOT / ".venv" / "bin" / "test-nat"
WORKER_COUNTS = range(1, 5)
N_WORKERS_RE = re.compile(r"^(\s*n_workers\s*:\s*)\d+(\s*)$", re.MULTILINE)
NAT_MODE_RE = re.compile(r"^(\s*nat_mode\s*:\s*)\S+(\s*)$", re.MULTILINE)
NAT_MODE = "none"


def set_n_workers(n_workers: int) -> None:
    content = LOAD_CONFIG_PATH.read_text(encoding="utf-8")
    updated, worker_replacements = N_WORKERS_RE.subn(rf"\g<1>{n_workers}\2", content, count=1)
    if worker_replacements != 1:
        raise RuntimeError(f"Expected exactly one n_workers entry in {LOAD_CONFIG_PATH}")
    updated, nat_mode_replacements = NAT_MODE_RE.subn(
        rf"\g<1>{NAT_MODE}\2",
        updated,
        count=1,
    )
    if nat_mode_replacements != 1:
        raise RuntimeError(f"Expected exactly one nat_mode entry in {LOAD_CONFIG_PATH}")
    LOAD_CONFIG_PATH.write_text(updated, encoding="utf-8")


def run_load_test(n_workers: int) -> int:
    print(f"\n=== test-nat load: nat_mode={NAT_MODE}, n_workers={n_workers} ===", flush=True)
    set_n_workers(n_workers)
    completed = subprocess.run([str(TEST_NAT), "load"], cwd=REPO_ROOT)
    return completed.returncode


def main() -> int:
    if not TEST_NAT.exists():
        print(f"error: {TEST_NAT} not found", file=sys.stderr)
        return 1

    for n_workers in WORKER_COUNTS:
        returncode = run_load_test(n_workers)
        if returncode != 0:
            print(
                f"\nStopped after failure: n_workers={n_workers}, exit_code={returncode}",
                file=sys.stderr,
                flush=True,
            )
            return returncode

    print("\nAll load tests completed successfully.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
