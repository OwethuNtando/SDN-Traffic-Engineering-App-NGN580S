"""Controller adapters: live OpenDaylight (odl.OdlAdapter) and offline simulator (sim.SimAdapter)."""
from __future__ import annotations

from ..config import credentials
from .base import (ApiRecord, ControllerAdapter, ControllerError, ControllerRejected,
                   ControllerSnapshot, ControllerUnreachable, ReportedLsp)


def make_adapter(cfg: dict) -> ControllerAdapter:
    if cfg["mode"] == "live":
        from .odl import OdlAdapter
        user, pwd = credentials(cfg["controller"])
        return OdlAdapter(cfg["controller"], user, pwd)
    from .sim import SimAdapter
    return SimAdapter({k: {"pcc_id": v.get("pcc_id", f"pcc://{v['loopback']}"), "loopback": v["loopback"]}
                       for k, v in cfg["nodes"].items()})


__all__ = ["make_adapter", "ApiRecord", "ControllerAdapter", "ControllerError",
           "ControllerRejected", "ControllerSnapshot", "ControllerUnreachable", "ReportedLsp"]
