from __future__ import annotations

import re
from pathlib import Path

VALID_NAT_MODES = ("none", "nat44", "nat_fo")
STARTUP_CONF_PATH = Path("/etc/vpp/startup.conf")
MANAGED_BLOCK_BEGIN = "# BEGIN VPP_NAT_TEST_MANAGED_PLUGINS"
MANAGED_BLOCK_END = "# END VPP_NAT_TEST_MANAGED_PLUGINS"

NAT_FO_PLUGIN_RE = re.compile(r"plugin\s+nat_fo_plugin\.so\s+\{\s*(enable|disable)\s*\}")
NAT44_PLUGIN_RE = re.compile(r"plugin\s+nat_plugin\.so\s+\{\s*(enable|disable)\s*\}")


def ensure_valid_nat_mode(value: str) -> str:
    """Проверяет, что значение NAT-режима входит в поддерживаемый список."""
    if value not in VALID_NAT_MODES:
        raise ValueError(f"Unsupported NAT mode '{value}'. Expected one of: {', '.join(VALID_NAT_MODES)}")
    return value


def parse_plugin_states(startup_conf_path: Path) -> tuple[str | None, str | None]:
    """Считывает `enable/disable` для nat_fo и nat44 из managed-блока startup.conf."""
    lines = startup_conf_path.read_text(encoding="utf-8", errors="replace").splitlines()
    in_block = False
    nat_fo_state: str | None = None
    nat44_state: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if line == MANAGED_BLOCK_BEGIN:
            in_block = True
            continue
        if line == MANAGED_BLOCK_END:
            break
        if not in_block:
            continue

        nat_fo_match = NAT_FO_PLUGIN_RE.search(line)
        if nat_fo_match is not None:
            nat_fo_state = nat_fo_match.group(1)

        nat44_match = NAT44_PLUGIN_RE.search(line)
        if nat44_match is not None:
            nat44_state = nat44_match.group(1)

    return nat_fo_state, nat44_state


def parse_managed_nat_mode(startup_conf_path: Path = STARTUP_CONF_PATH) -> str | None:
    """Определяет текущий NAT-режим по managed-блоку startup.conf."""
    if not startup_conf_path.exists():
        return None

    nat_fo_state, nat44_state = parse_plugin_states(startup_conf_path)

    if nat_fo_state == "enable" and nat44_state == "disable":
        return "nat_fo"
    if nat_fo_state == "disable" and nat44_state == "enable":
        return "nat44"
    if nat_fo_state == "disable" and nat44_state == "disable":
        return "none"
    return None
