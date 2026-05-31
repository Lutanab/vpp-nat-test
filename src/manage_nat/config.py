from __future__ import annotations

from pathlib import Path

TREX_DOWNLOAD_URL = "https://trex-tgn.cisco.com/trex/release/latest"
TREX_INSTALL_BASE_DIR = Path("/opt/trex")
TREX_SERVER_BINARY_NAME = "t-rex-64"
TREX_CONSOLE_BINARY_NAME = "trex-console"
TREX_SERVER_LINK_PATH = Path("/usr/local/bin") / TREX_SERVER_BINARY_NAME
TREX_CONSOLE_LINK_PATH = Path("/usr/local/bin") / TREX_CONSOLE_BINARY_NAME


# ============================================================================
# Machine-local VPP CPU pinning
#
# When moving this test rig to another machine, this is the block to adjust:
# - VPP_CPU_MAIN_CORE is written as `main-core`.
# - VPP worker cores are allocated from VPP_CPU_MAIN_CORE + 1 upward.
# - VPP_CPU_MAX_WORKERS limits how many consecutive worker cores may be used.
# ============================================================================
VPP_CPU_MAIN_CORE = 320
VPP_CPU_MAX_WORKERS = 20


# memif ring-size for `create interface memif ... ring-size <size> ...`
MEMIF_RING_SIZE_DEFAULT = 16384
