from __future__ import annotations

from pathlib import Path

TREX_DOWNLOAD_URL = "https://trex-tgn.cisco.com/trex/release/latest"
TREX_INSTALL_BASE_DIR = Path("/opt/trex")
TREX_SERVER_BINARY_NAME = "t-rex-64"
TREX_CONSOLE_BINARY_NAME = "trex-console"
TREX_SERVER_LINK_PATH = Path("/usr/local/bin") / TREX_SERVER_BINARY_NAME
TREX_CONSOLE_LINK_PATH = Path("/usr/local/bin") / TREX_CONSOLE_BINARY_NAME
