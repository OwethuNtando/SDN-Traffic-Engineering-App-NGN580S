"""Controller adapter interface shared by the live OpenDaylight adapter and the
offline simulator. The GUI and LSP manager only depend on this interface.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class ControllerError(Exception):
    """Transport or protocol failure talking to the controller."""


class ControllerUnreachable(ControllerError):
    """Controller did not answer (connection refused / timeout / DNS)."""


class ControllerRejected(ControllerError):
    """Controller answered but refused the request (HTTP 4xx/5xx or RPC failure)."""

    def __init__(self, msg: str, status: int | None = None, body: str = ""):
        super().__init__(msg)
        self.status = status
        self.body = body


@dataclass
class ApiRecord:
    """One request/response, kept for the API inspector and the evidence folder."""
    method: str
    url: str
    request_body: str = ""
    status: int | None = None
    response_body: str = ""
    elapsed_ms: float = 0.0
    error: str = ""
    ts: float = field(default_factory=time.time)
    simulated: bool = False


@dataclass
class LspPath:
    lsp_id: str = ""
    plsp_id: str = ""
    operational: str = "unknown"   # up / active / down / going-up / going-down / unknown
    administrative: bool | None = None
    delegate: bool | None = None
    ero: list = field(default_factory=list)   # hop IPs as signalled/requested
    rro: list = field(default_factory=list)   # hop IPs recorded (actual path)
    source: str = ""
    destination: str = ""


@dataclass
class ReportedLsp:
    name: str
    pcc: str               # pcc://x.x.x.x
    paths: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def primary(self) -> LspPath:
        return self.paths[0] if self.paths else LspPath()

    @property
    def operational(self) -> str:
        return self.primary.operational

    @property
    def is_up(self) -> bool:
        return self.operational.lower() in ("up", "active")


@dataclass
class PccSession:
    pcc: str
    ip: str = ""
    state_sync: str = "unknown"     # synchronized / initial-resync / ...
    lsps: list = field(default_factory=list)


@dataclass
class ControllerSnapshot:
    reachable: bool
    pccs: list = field(default_factory=list)      # [PccSession]
    bgpls_nodes: list = field(default_factory=list)
    bgpls_links: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    bgp_peer_state: str = "n/a"                   # from bgp-rib peer (GA5 RIB bgp-3625)
    ts: float = field(default_factory=time.time)
    simulated: bool = False

    def all_lsps(self) -> list:
        return [l for p in self.pccs for l in p.lsps]

    def find_lsp(self, name: str, pcc: str | None = None) -> ReportedLsp | None:
        for l in self.all_lsps():
            if l.name == name and (pcc is None or l.pcc == pcc):
                return l
        return None


class ControllerAdapter:
    """Abstract adapter. Implementations must never claim success they did not observe."""
    simulated = False
    name = "abstract"

    def __init__(self):
        self.history: list[ApiRecord] = []

    def snapshot(self) -> ControllerSnapshot:           # GET operational state
        raise NotImplementedError

    def add_lsp(self, pcc: str, name: str, src_ip: str, dst_ip: str, ero: list) -> ApiRecord:
        raise NotImplementedError

    def remove_lsp(self, pcc: str, name: str) -> ApiRecord:
        raise NotImplementedError

    def trigger_sync(self, pcc: str, name: str | None = None) -> ApiRecord:
        raise NotImplementedError

    def _record(self, rec: ApiRecord) -> ApiRecord:
        self.history.append(rec)
        del self.history[:-200]
        return rec
