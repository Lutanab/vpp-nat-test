from __future__ import annotations

from ..nat_mode import MANAGED_BLOCK_BEGIN, MANAGED_BLOCK_END, ensure_valid_nat_mode
from .spec import VppInstance


def plugin_block(nat_mode: str) -> str:
    mode = ensure_valid_nat_mode(nat_mode)
    nat_fo_state = "enable" if mode == "nat_fo" else "disable"
    nat44_state = "enable" if mode == "nat44" else "disable"
    return f"""{MANAGED_BLOCK_BEGIN}
plugins {{
  plugin nat_fo_plugin.so {{ {nat_fo_state} }}
  plugin nat_plugin.so {{ {nat44_state} }}
}}
{MANAGED_BLOCK_END}"""


def worker_corelist(instance: VppInstance, n_workers: int) -> str | None:
    if n_workers <= 0:
        return None
    end_core = instance.worker_core_start + n_workers - 1
    return f"{instance.worker_core_start}-{end_core}"


def render_startup_conf(instance: VppInstance, nat_mode: str, n_workers: int) -> str:
    corelist = worker_corelist(instance, n_workers)
    worker_line = f"  corelist-workers {corelist}\n" if corelist else ""
    return f"""unix {{
  nodaemon
  log {instance.log_path}
  full-coredump
  cli-listen {instance.cli_socket}
  pidfile {instance.pidfile}
  gid vpp
}}

api-trace {{
  on
}}

api-segment {{
  prefix {instance.api_prefix}
  gid vpp
}}

socksvr {{
  socket-name {instance.api_socket}
}}

statseg {{
  socket-name {instance.stats_socket}
}}

cpu {{
  main-core {instance.main_core}
{worker_line}}}

{plugin_block(nat_mode)}
"""
