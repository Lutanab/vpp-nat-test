from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..config import VM_MIN_DISK_SIZE
from ..helpers import run_command
from .spec import VmSpec, base_image_path, disk_image_path, vm_storage_dir


def size_to_bytes(value: str) -> int:
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    value = value.strip()
    if value.isdigit():
        return int(value)
    suffix = value[-1].upper()
    if suffix not in units or not value[:-1].isdigit():
        raise ValueError(f"Unsupported size: {value}")
    return int(value[:-1]) * units[suffix]


def ensure_disk(project_root: Path, spec: VmSpec) -> None:
    storage_dir = vm_storage_dir(project_root, spec)
    storage_dir.mkdir(parents=True, exist_ok=True)
    base_image = base_image_path(project_root)
    disk_image = disk_image_path(project_root, spec)
    if not base_image.is_file():
        raise FileNotFoundError(f"Base VM image not found: {base_image}")
    if not disk_image.exists():
        run_command(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base_image), str(disk_image)])

    info = subprocess.run(
        ["qemu-img", "info", "--output=json", "--force-share", str(disk_image)],
        text=True,
        capture_output=True,
        check=True,
    )
    current_size = int(json.loads(info.stdout)["virtual-size"])
    if current_size < size_to_bytes(VM_MIN_DISK_SIZE):
        run_command(["qemu-img", "resize", str(disk_image), VM_MIN_DISK_SIZE])
