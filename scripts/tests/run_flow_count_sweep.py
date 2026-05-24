#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from load_sweep_common import DEFAULT_NAT_MODES, LOAD_CONFIG_PATH, VALID_NAT_MODES, LoadCase
from load_sweep_common import format_list, run_cases, validate_positive


DEFAULT_TEST_NAME = "flow_count_sweep"
DEFAULT_FLOW_COUNTS = (1, 64, 512, 4096)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run single-worker flow-count NAT load-test sweep. The script rewrites "
            "configs/loadtest/load/test_config.yaml before each run."
        ),
    )
    parser.add_argument(
        "--nat-modes",
        nargs="+",
        choices=VALID_NAT_MODES,
        default=list(DEFAULT_NAT_MODES),
        help=f"NAT modes to test (default: {' '.join(DEFAULT_NAT_MODES)}).",
    )
    parser.add_argument(
        "--flow-counts",
        nargs="+",
        type=int,
        default=list(DEFAULT_FLOW_COUNTS),
        help=f"Flow counts to test (default: {format_list(DEFAULT_FLOW_COUNTS)}).",
    )
    parser.add_argument(
        "--n-workers",
        type=int,
        default=1,
        help="VPP worker count (default: 1).",
    )
    parser.add_argument(
        "--packet-size",
        type=int,
        default=64,
        help="Fixed packet_size for this sweep (default: 64).",
    )
    parser.add_argument(
        "--test-name",
        default=DEFAULT_TEST_NAME,
        help=f"test_name used for results (default: {DEFAULT_TEST_NAME}).",
    )
    parser.add_argument(
        "--load-config",
        type=Path,
        default=LOAD_CONFIG_PATH,
        help=f"Working load YAML to rewrite (default: {LOAD_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned cases without changing config or running tests.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue after failed load-test runs and report failures at the end.",
    )
    return parser.parse_args()


def build_cases(args: argparse.Namespace) -> list[LoadCase]:
    return [
        LoadCase(
            test_name=args.test_name,
            nat_mode=nat_mode,
            n_workers=args.n_workers,
            flow_count=flow_count,
            packet_size=args.packet_size,
        )
        for flow_count in args.flow_counts
        for nat_mode in args.nat_modes
    ]


def validate_args(args: argparse.Namespace) -> None:
    if args.n_workers < 0:
        raise ValueError("--n-workers must be non-negative")
    if args.packet_size <= 0:
        raise ValueError("--packet-size must be positive")
    validate_positive("--flow-counts", args.flow_counts)


def main() -> int:
    args = parse_args()
    try:
        validate_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return run_cases(build_cases(args), args.load_config, args.dry_run, args.keep_going)


if __name__ == "__main__":
    raise SystemExit(main())
