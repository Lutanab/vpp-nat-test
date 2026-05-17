from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Sequence

import rich_click as click

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def with_privileges(command: Sequence[str]) -> list[str]:
    """Добавляет `sudo`, если команда запускается не от root."""
    if os.geteuid() == 0:
        return list(command)
    return ["sudo", *command]


def run_command(command: Sequence[str], cwd: Path | None = None) -> None:
    """Запускает команду и печатает её в читаемом виде."""
    rendered = shlex.join(command)
    if cwd is not None:
        click.echo(f"\n==> {rendered} (cwd: {cwd})")
    else:
        click.echo(f"\n==> {rendered}")
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def run_shell_script(
    script: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture_output: bool = True,
) -> str:
    """Выполняет shell-скрипт в `bash -lc`.

    Если `capture_output=True`, возвращает stdout.
    Если `capture_output=False`, вывод транслируется напрямую в терминал.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        ["bash", "-lc", script],
        cwd=str(cwd),
        env=merged_env,
        text=True,
        capture_output=capture_output,
        check=True,
    )
    return result.stdout if capture_output else ""
