"""Live OpenDaylight adapter (RESTCONF RFC 8040, ODL 0.20.x / bgpcep).

Northbound calls used (all paths configurable in config.json -> controller):
    GET  {root}/data/network-topology:network-topology/topology={pcep}?content=nonconfig
    GET  {root}/data/network-topology:network-topology/topology={bgpls}?content=nonconfig
    POST {root}/operations/network-topology-pcep:add-lsp
    POST {root}/operations/network-topology-pcep:remove-lsp
    POST {root}/operations/network-topology-pcep:trigger-sync   (re-sync LSP state from a PCC)

An HTTP 200/204 from add-lsp only means the controller ACCEPTED the request.
The LSP manager separately verifies the LSP in the PCEP LSP-DB (reported-lsp)
and its operational flag before anything is shown as operational.
"""
from __future__ import annotations

import json
import time

import requests

from .base import (ApiRecord, ControllerAdapter, ControllerRejected, ControllerSnapshot,
                   ControllerUnreachable)
from .parsers import g, parse_bgpls_topology, parse_pcep_topology


class OdlAdapter(ControllerAdapter):
    name = "OpenDaylight RESTCONF"
    simulated = False

    def __init__(self, ccfg: dict, username: str | None, password: str | None,
                 session: requests.Session | None = None):
        super().__init__()
        self.c = ccfg
        self.base = ccfg["base_url"].rstrip("/") + ccfg.get("restconf_root", "/rests")
        self.timeout = float(ccfg.get("timeout_s", 5))
        self.s = session or requests.Session()
        self.s.headers.update({"Accept": "application/yang-data+json",
                               "Content-Type": "application/yang-data+json"})
        self.s.verify = bool(ccfg.get("verify_tls", False))
        self.set_credentials(username, password)

    def set_credentials(self, username: str | None, password: str | None) -> None:
        self.s.auth = (username, password) if username else None

    # ------------------------------------------------------------------ http
    def _request(self, method: str, path: str, body: dict | None = None,
                 ok_missing: bool = False, timeout: float | None = None) -> tuple[ApiRecord, dict | None]:
        url = self.base + path
        payload = json.dumps(body, indent=2) if body is not None else ""
        rec = ApiRecord(method=method, url=url, request_body=payload)
        t0 = time.perf_counter()
        try:
            resp = self.s.request(method, url, data=payload or None, timeout=timeout or self.timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            rec.error = f"{type(exc).__name__}: {exc}"
            rec.elapsed_ms = (time.perf_counter() - t0) * 1000
            self._record(rec)
            raise ControllerUnreachable(f"Controller unreachable at {self.c['base_url']} ({type(exc).__name__})") from exc
        except requests.RequestException as exc:
            rec.error = str(exc)
            self._record(rec)
            raise ControllerUnreachable(str(exc)) from exc
        rec.elapsed_ms = (time.perf_counter() - t0) * 1000
        rec.status = resp.status_code
        rec.response_body = resp.text[:200_000]
        self._record(rec)
        if ok_missing and resp.status_code in (404, 409):
            return rec, None
        if resp.status_code == 401:
            raise ControllerRejected("401 Unauthorized - check ODL_USER / ODL_PASS", 401, resp.text)
        if resp.status_code >= 400:
            raise ControllerRejected(f"HTTP {resp.status_code} from {method} {path}: "
                                     f"{_error_text(resp.text)}", resp.status_code, resp.text)
        if not resp.text.strip():
            return rec, {}
        try:
            return rec, resp.json()
        except ValueError:
            raise ControllerRejected(f"Non-JSON response from {path}", resp.status_code, resp.text)

    # ----------------------------------------------------------------- reads
    def _topo_path(self, topo_id: str) -> str:
        return f"/data/network-topology:network-topology/topology={topo_id}?content=nonconfig"

    def snapshot(self) -> ControllerSnapshot:
        snap = ControllerSnapshot(reachable=False)
        _, doc = self._request("GET", self._topo_path(self.c["pcep_topology_id"]), ok_missing=True)
        snap.reachable = True
        if doc is None:
            snap.errors.append(f"PCEP topology '{self.c['pcep_topology_id']}' not present "
                               f"(odl-bgpcep-pcep feature / PCCs not connected?)")
        else:
            snap.pccs = parse_pcep_topology(doc)
        snap.bgp_peer_state = self._bgp_state()
        if not self.c.get("bgpls_topology_id"):
            return snap
        try:
            _, ls = self._request("GET", self._topo_path(self.c["bgpls_topology_id"]), ok_missing=True)
            if ls is None:
                snap.errors.append(f"BGP-LS topology '{self.c['bgpls_topology_id']}' not present")
            else:
                snap.bgpls_nodes, snap.bgpls_links = parse_bgpls_topology(ls)
        except ControllerRejected as exc:
            snap.errors.append(f"BGP-LS read failed: {exc}")
        return snap

    def _bgp_state(self) -> str:
        """R1-ODL BGP session state.

        1st choice: OpenConfig neighbor state (same data as Karaf
        'bgp:operational-state'), field session-state, e.g. ESTABLISHED.
        Fallback: presence of the peer in the BGP RIB.
        """
        rib, peer = self.c.get("bgp_rib_id"), self.c.get("bgp_peer")
        if not (rib and peer):
            return "n/a"
        from urllib.parse import quote
        oc = (f"/data/openconfig-network-instance:network-instances/network-instance=global-bgp/"
              f"openconfig-network-instance:protocols/protocol=openconfig-policy-types%3ABGP,{rib}/"
              f"bgp-openconfig-extensions:bgp/neighbors/neighbor={peer}/state")
        try:
            _, doc = self._request("GET", oc, ok_missing=True)
            st = _find_key(doc, "session-state") if doc is not None else None
            if st:
                st = str(st).split(":")[-1].upper()
                afis = sorted({str(a).split(":")[-1] for a in _find_all(doc, "afi-safi-name")})
                extra = f", {'/'.join(afis)}" if afis else ""
                return f"{st} (peer {peer}, AS {self.c.get('bgp_as', '4200003625')}{extra})"
        except ControllerRejected:
            pass
        try:
            _, pdoc = self._request("GET", f"/data/bgp-rib:bgp-rib/rib={rib}/peer="
                                           f"{quote('bgp://' + peer, safe='')}?content=nonconfig",
                                    ok_missing=True)
            if pdoc is None:
                return f"NOT ESTABLISHED (no peer {peer} in RIB {rib})"
            return f"ESTABLISHED (peer {peer} present in RIB {rib})"
        except ControllerRejected as exc:
            return f"UNKNOWN (read failed {exc.status})"

    # ---------------------------------------------------------------- writes
    def _topo_ref(self) -> str:
        return ('/network-topology:network-topology/network-topology:topology'
                f'[network-topology:topology-id="{self.c["pcep_topology_id"]}"]')

    def build_add_lsp(self, pcc: str, name: str, src_ip: str, dst_ip: str, ero: list) -> dict:
        return {self.c.get("rpc_input_key", "input"): {
            "node": pcc,
            "name": name,
            "network-topology-ref": self._topo_ref(),
            "arguments": {
                self.c.get("lsp_augment_key", "odl-pcep-ietf-stateful:lsp"):
                    {"delegate": True, "administrative": True},
                "endpoints-obj": {"ipv4": {"source-ipv4-address": src_ip,
                                           "destination-ipv4-address": dst_ip}},
                "ero": {"subobject": [{"loose": False, "ip-prefix": {"ip-prefix": f"{ip}/32"}}
                                      for ip in ero]},
            },
        }}

    def build_remove_lsp(self, pcc: str, name: str) -> dict:
        return {self.c.get("rpc_input_key", "input"): {
            "node": pcc, "name": name, "network-topology-ref": self._topo_ref()}}

    def _rpc(self, op: str, body: dict) -> ApiRecord:
        # ODL answers add/remove-lsp only after the PCC reports back, so allow longer
        rec, doc = self._request("POST", f"/operations/network-topology-pcep:{op}", body,
                                 timeout=float(self.c.get("rpc_timeout_s", 45)))
        out = g(doc or {}, "output") or {}
        failure = g(out, "failure")
        if failure:
            errs = g(out, "error") or []
            raise ControllerRejected(f"{op} RPC returned failure='{failure}' {json.dumps(errs)[:300]}",
                                     rec.status, rec.response_body)
        return rec

    def add_lsp(self, pcc: str, name: str, src_ip: str, dst_ip: str, ero: list) -> ApiRecord:
        return self._rpc("add-lsp", self.build_add_lsp(pcc, name, src_ip, dst_ip, ero))

    def remove_lsp(self, pcc: str, name: str) -> ApiRecord:
        return self._rpc("remove-lsp", self.build_remove_lsp(pcc, name))

    def trigger_sync(self, pcc: str, name: str | None = None) -> ApiRecord:
        """Ask the PCC to re-send its LSP state (RFC 8232 re-synchronisation, ODL 'trigger-sync')."""
        body = {self.c.get("rpc_input_key", "input"): {"node": pcc, "network-topology-ref": self._topo_ref()}}
        if name:
            body[self.c.get("rpc_input_key", "input")]["name"] = name
        return self._rpc("trigger-sync", body)


def _error_text(body: str) -> str:
    try:
        doc = json.loads(body)
        errs = g(g(doc, "errors") or {}, "error") or []
        if isinstance(errs, list) and errs:
            return "; ".join(str(e.get("error-message", e.get("error-tag", e))) for e in errs)[:300]
    except (ValueError, AttributeError):
        pass
    return body[:200]


def _find_key(obj, key):
    """First value whose key (ignoring a 'module:' prefix) equals key, searched depth-first."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.split(":")[-1] == key:
                return v
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def _find_all(obj, key):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.split(":")[-1] == key and not isinstance(v, (dict, list)):
                out.append(v)
            else:
                out.extend(_find_all(v, key))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_find_all(v, key))
    return out
