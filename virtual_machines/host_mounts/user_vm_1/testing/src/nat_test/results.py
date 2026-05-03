from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import as_yaml_lines


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def next_attempt_path(results_dir: Path) -> Path:
    ensure_directory(results_dir)
    existing = sorted(results_dir.glob("attempt_*.json"))
    next_index = len(existing) + 1
    return results_dir / f"attempt_{next_index:04d}.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_directory(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    ensure_directory(path.parent)
    path.write_text("\n".join(as_yaml_lines(payload)) + "\n", encoding="utf-8")
