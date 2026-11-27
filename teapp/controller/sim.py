"""OFFLINE SIMULATED controller - for TE demonstration, development and tests.

Everything this adapter returns is flagged simulated=True and the GUI shows an
OFFLINE / SIMULATED banner. Per the T3 brief (s8, s15) it demonstrates core TE
logic only and must NOT be presented as live controller or router evidence.
"""
from __future__ import annotations

import json
import time

from .base import (ApiRecord, ControllerAdapter, ControllerRejected, ControllerSnapshot,
                   ControllerUnreachable, LspPath, PccSession, ReportedLsp)


class SimAdapter(ControllerAdapter):
    name = "OFFLINE SIMULATOR"
    simulated = True

    def __init__(self, nodes: dict, setup_delay_s: float = 2.0, teardown_delay_s: float = 1.5):
        super().__init__()
        self.nodes = nodes                         # name -> {"pcc_id", "loopback"}
        self.setup_delay = setup_delay_s
        self.teardown_delay = teardown_delay_s
        self.fail_mode = "none"                    # none | unreachable | reject
        self._lsps: dict = {}                      # (pcc, name) -> dict(state, t, ero, src, dst)

    def _rec(self, method: str, op: str, body: dict | None, status: int, resp: dict | str = "") -> ApiRecord:
        return self._record(ApiRecord(method=method, url=f"sim://controller/{op}",
                                      request_body=json.dumps(body, indent=2) if body else "",
                                      status=status, response_body=json.dumps(resp) if resp else "",
                                      simulated=True))

    def _check_fail(self, op: str):
        if self.fail_mode == "unreachable":
            self._record(ApiRecord("GET", f"sim://controller/{op}", error="simulated outage", simulated=True))
            raise ControllerUnreachable("SIMULATED controller outage (fail_mode=unreachable)")

    def _advance(self):
        now = time.time()
        for key in list(self._lsps):
            l = self._lsps[key]
            if l["state"] == "going-up" and now - l["t"] >= self.setup_delay:
                l["state"] = "up"
            elif l["state"] == "going-down" and now - l["t"] >= self.teardown_delay:
                del self._lsps[key]

    def snapshot(self) -> ControllerSnapshot:
        self._check_fail("snapshot")
        self._advance()
        snap = ControllerSnapshot(reachable=True, simulated=True)
        snap.bgp_peer_state = "ESTABLISHED (SIMULATED peer 192.168.56.11)"
        for name, n in self.nodes.items():
            s = PccSession(pcc=n["pcc_id"], ip=n["loopback"], state_sync="synchronized")
            for (pcc, lname), l in self._lsps.items():
                if pcc == n["pcc_id"]:
                    s.lsps.append(ReportedLsp(lname, pcc, [LspPath(
                        lsp_id="1", plsp_id=str(abs(hash(lname)) % 1000), operational=l["state"],
                        administrative=True, delegate=True, ero=list(l["ero"]),
                        rro=list(l["ero"]) if l["state"] == "up" else [],
                        source=l["src"], destination=l["dst"])], raw={"simulated": True}))
            snap.pccs.append(s)
        self._rec("GET", "pcep-topology", None, 200, {"pccs": len(snap.pccs)})
        return snap

    def add_lsp(self, pcc: str, name: str, src_ip: str, dst_ip: str, ero: list) -> ApiRecord:
        self._check_fail("add-lsp")
        body = {"input": {"node": pcc, "name": name, "ero": ero}}
        if self.fail_mode == "reject":
            self._rec("POST", "add-lsp", body, 200, {"output": {"failure": "failed"}})
            raise ControllerRejected("SIMULATED add-lsp failure='failed'", 200)
        if pcc not in {n["pcc_id"] for n in self.nodes.values()}:
            self._rec("POST", "add-lsp", body, 200, {"output": {"failure": "unsent"}})
            raise ControllerRejected(f"SIMULATED: unknown PCC {pcc}", 200)
        if (pcc, name) in self._lsps:
            self._rec("POST", "add-lsp", body, 200, {"output": {"failure": "failed",
                                                                "error": "LSP name in use"}})
            raise ControllerRejected(f"SIMULATED: LSP {name} already exists", 200)
        self._lsps[(pcc, name)] = {"state": "going-up", "t": time.time(), "ero": list(ero),
                                   "src": src_ip, "dst": dst_ip}
        return self._rec("POST", "add-lsp", body, 200, {"output": {}})

    def trigger_sync(self, pcc: str, name: str | None = None) -> ApiRecord:
        self._check_fail("trigger-sync")
        body = {"input": {"node": pcc, "name": name}}
        # a re-sync makes the PCC report its real state: LSPs that never came up are dropped
        for key in [k for k, l in self._lsps.items() if k[0] == pcc and l["state"] == "down"
                    and (name is None or k[1] == name)]:
            del self._lsps[key]
        return self._rec("POST", "trigger-sync", body, 200, {"output": {}})

    def remove_lsp(self, pcc: str, name: str) -> ApiRecord:
        self._check_fail("remove-lsp")
        body = {"input": {"node": pcc, "name": name}}
        if self.fail_mode == "reject" or (pcc, name) not in self._lsps:
            self._rec("POST", "remove-lsp", body, 200, {"output": {"failure": "failed"}})
            raise ControllerRejected(f"SIMULATED remove-lsp failure for {name}", 200)
        self._lsps[(pcc, name)].update(state="going-down", t=time.time())
        return self._rec("POST", "remove-lsp", body, 200, {"output": {}})
