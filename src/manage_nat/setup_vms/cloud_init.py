from __future__ import annotations

import hashlib
from pathlib import Path

from ..helpers import run_command
from .spec import VmSpec, seed_image_path, vm_config_dir


def write_text_if_changed(path: Path, content: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def content_instance_id(spec: VmSpec, user_data: str, network_config: str) -> str:
    digest = hashlib.sha256(f"{user_data}\n{network_config}".encode("utf-8")).hexdigest()[:12]
    return f"{spec.name}-{digest}"


def write_cloud_init(project_root: Path, spec: VmSpec) -> bool:
    config_dir = vm_config_dir(project_root, spec)
    config_dir.mkdir(parents=True, exist_ok=True)
    user_data = f"""#cloud-config
hostname: {spec.hostname}
users:
  - name: zero
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: [sudo]
    shell: /bin/bash
    lock_passwd: false
    plain_text_passwd: "orez1234"
    ssh_authorized_keys: []
ssh_pwauth: true
disable_root: true
bootcmd:
  - mkdir -p /mnt/host
mounts:
  - [hostshare, /mnt/host, 9p, "trans=virtio,version=9p2000.L,rw,nofail,_netdev", "0", "0"]
runcmd:
  - modprobe 9pnet_virtio || true
  - mount /mnt/host || true
"""
    network_config = f"""version: 2
ethernets:
  data0:
    match:
      macaddress: "{spec.dataplane_mac}"
    set-name: data0
    dhcp4: false
    addresses: [{spec.dataplane_ip_cidr}]
    routes:
      - to: {spec.dataplane_peer_cidr}
        via: {spec.dataplane_gateway}
  mgmt0:
    match:
      macaddress: "{spec.management_mac}"
    set-name: mgmt0
    dhcp4: true
    optional: true
"""
    meta_data = (
        f"instance-id: {content_instance_id(spec, user_data, network_config)}\n"
        f"local-hostname: {spec.hostname}\n"
    )
    changed = any(
        (
            write_text_if_changed(config_dir / "user-data.yaml", user_data),
            write_text_if_changed(config_dir / "network-config.yaml", network_config),
            write_text_if_changed(config_dir / "meta-data.yaml", meta_data),
            not seed_image_path(project_root, spec).exists(),
        )
    )
    if not changed:
        return False

    seed_image_path(project_root, spec).unlink(missing_ok=True)
    run_command(
        [
            "cloud-localds",
            f"--network-config={config_dir / 'network-config.yaml'}",
            str(seed_image_path(project_root, spec)),
            str(config_dir / "user-data.yaml"),
            str(config_dir / "meta-data.yaml"),
        ]
    )
    return True
