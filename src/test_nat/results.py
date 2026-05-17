from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: str) -> float:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).timestamp()


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_directory(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class RunLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        ensure_directory(log_path.parent)
        self.log_path.write_text("", encoding="utf-8")

    def __call__(self, message: str) -> None:
        line = f"{utc_now_iso()} {message}"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        for output_line in message.splitlines() or [""]:
            print(f"[test-nat] {output_line}", flush=True)

    def begin(self, message: str) -> None:
        line = f"{utc_now_iso()} {message}"
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        print(f"[test-nat] {message}", end="", flush=True)

    def finish(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")
        print(message, flush=True)
