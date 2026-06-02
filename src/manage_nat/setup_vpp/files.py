from __future__ import annotations

import subprocess

from ..helpers import with_privileges


def read_privileged_file(path: str) -> str | None:
    result = subprocess.run(
        with_privileges(["cat", path]),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def write_privileged_file_if_changed(path: str, content: str, mode: str | None = None) -> bool:
    changed = read_privileged_file(path) != content
    if changed:
        subprocess.run(
            with_privileges(["tee", path]),
            text=True,
            input=content,
            capture_output=True,
            check=True,
        )
    if mode is not None:
        subprocess.run(with_privileges(["chmod", mode, path]), check=True)
    return changed
