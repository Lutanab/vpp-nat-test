#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


########
# CONSTANTS
########

ROOT = Path(__file__).resolve().parent
LOAD_CONFIG = ROOT / "configs/loadtest/load/test_config.yaml"
LOAD_TEMPLATE = ROOT / "configs/loadtest/load/test_config.yaml.template"
SEARCH_CONFIG = ROOT / "configs/loadtest/search/test_config.yaml"
SEARCH_TEMPLATE = ROOT / "configs/loadtest/search/test_config.yaml.template"
PPS_PLOT = ROOT / "scripts/viz/plot_pps_vs_workers.py"
RESULTS_ROOT = ROOT / "results"
NAT_MODE = "none"
WORKER_COUNTS = range(3, 6)


########
# HELPERS
########

def run(cmd: list[str], cwd: Path = ROOT) -> None:
    """Печатает и выполняет команду."""
    print(f"\n==> {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def copy_if_missing(source: Path, target: Path) -> None:
    """Создает файл из темплейта, если его еще нет."""
    if target.exists():
        print(f"{target.relative_to(ROOT)} уже есть.", flush=True)
        return

    if not source.exists():
        raise RuntimeError(f"template not found: {source}")

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"Создан {target.relative_to(ROOT)}.", flush=True)


def load_flat_config(path: Path) -> dict[str, str]:
    """Читает простой YAML-конфиг вида `key: value`."""
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.split("#", 1)[0].strip()
    return result


def set_config_values(path: Path, values: dict[str, object]) -> None:
    """Идемпотентно выставляет значения в простом YAML-конфиге."""
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    seen: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        key = ""
        if ":" in line and not line.lstrip().startswith("#"):
            key = line.split(":", 1)[0].strip()
        if key in values:
            if key in seen:
                raise RuntimeError(f"duplicate config key: {key}")
            updated_lines.append(f"{key}: {values[key]}")
            seen.add(key)
        else:
            updated_lines.append(line)

    for key, value in values.items():
        if key not in seen:
            updated_lines.append(f"{key}: {value}")

    updated = "\n".join(updated_lines) + "\n"
    if updated == content:
        print(f"{path.relative_to(ROOT)} уже актуален.", flush=True)
        return

    path.write_text(updated, encoding="utf-8")
    print(f"Обновлен {path.relative_to(ROOT)}.", flush=True)


def slugify_value(value: str) -> str:
    """Приводит значение к формату имени директории результатов."""
    rendered = value.replace(".", "p").replace("-", "m")
    return re.sub(r"[^A-Za-z0-9_]+", "_", rendered).strip("_") or "value"


########
# TEST STEPS
########

def ensure_test_configs() -> None:
    """Идемпотентно создает load/search конфиги из темплейтов."""
    print("Готовлю load/search конфиги.", flush=True)
    copy_if_missing(LOAD_TEMPLATE, LOAD_CONFIG)
    copy_if_missing(SEARCH_TEMPLATE, SEARCH_CONFIG)


def current_results_dir() -> Path:
    """Возвращает директорию результатов текущего эксперимента."""
    test_name = load_flat_config(LOAD_CONFIG).get("test_name")
    if not test_name:
        raise RuntimeError(f"missing test_name in {LOAD_CONFIG}")
    return RESULTS_ROOT / f"test_name_{slugify_value(test_name)}"


def run_worker_sweep() -> None:
    """Запускает load-тест для workers 1..5 в режиме none."""
    set_config_values(LOAD_CONFIG, {"nat_mode": NAT_MODE})
    for n_workers in WORKER_COUNTS:
        print(f"Запускаю load-тест: workers={n_workers}.", flush=True)
        set_config_values(LOAD_CONFIG, {"n_workers": n_workers})
        run(["uv", "run", "test-nat", "run", "load"])


def plot_pps_vs_workers() -> None:
    """Строит PPS-график для текущего test_name."""
    results_dir = current_results_dir()
    run(
        [
            "uv",
            "run",
            "python",
            str(PPS_PLOT),
            "--results-dir",
            str(results_dir),
            "--output",
            str(results_dir / "pps_vs_workers.png"),
        ]
    )


def run_tests() -> None:
    """Запускает тесты подготовленного стенда."""
    ensure_test_configs()
    run_worker_sweep()
    plot_pps_vs_workers()


########
# ENTRYPOINT
########

def main() -> int:
    """Запускает полный набор тестов."""
    print("Старт full tests workflow.", flush=True)
    try:
        run_tests()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
