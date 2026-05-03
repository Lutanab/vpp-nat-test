from __future__ import annotations

import re
from pathlib import Path

VALID_NAT_MODES = ("none", "nat44", "nat_fo")
STARTUP_CONF_PATH = Path("/etc/vpp/startup.conf")


def ensure_valid_nat_mode(value: str) -> str:
    if value not in VALID_NAT_MODES:
        raise ValueError(f"Unsupported NAT mode '{value}'. Expected one of: {', '.join(VALID_NAT_MODES)}")
    return value


def parse_managed_nat_mode(startup_conf_path: Path = STARTUP_CONF_PATH) -> str | None:
    if not startup_conf_path.exists():
        return None

    lines = startup_conf_path.read_text(encoding="utf-8", errors="replace").splitlines()
    in_block = False
    nat_fo_state: str | None = None
    nat44_state: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped == "# BEGIN VPP_NAT_TEST_MANAGED_PLUGINS":
            in_block = True
            continue
        if stripped == "# END VPP_NAT_TEST_MANAGED_PLUGINS":
            break
        if not in_block:
            continue

        nat_fo_match = re.search(r"plugin\s+nat_fo_plugin\.so\s+\{\s*(enable|disable)\s*\}", stripped)
        if nat_fo_match:
            nat_fo_state = nat_fo_match.group(1)

        nat44_match = re.search(r"plugin\s+nat_plugin\.so\s+\{\s*(enable|disable)\s*\}", stripped)
        if nat44_match:
            nat44_state = nat44_match.group(1)

    if nat_fo_state == "enable" and nat44_state == "disable":
        return "nat_fo"
    if nat_fo_state == "disable" and nat44_state == "enable":
        return "nat44"
    if nat_fo_state == "disable" and nat44_state == "disable":
        return "none"
    return None
