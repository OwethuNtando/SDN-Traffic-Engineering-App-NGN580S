"""Tolerant parsers for OpenDaylight RFC 8040 JSON (pcep-topology and BGP-LS topology).

RFC 8040 JSON prefixes augmented nodes with their module name
(e.g. "network-topology-pcep:path-computation-client",
"odl-pcep-ietf-stateful:lsp"). Releases differ in which keys carry a prefix, so
lookups here match either the bare name or "<module>:<name>". Anything that
cannot be parsed is left out rather than guessed.
"""
from __future__ import annotations

from typing import Any

from .base import LspPath, ReportedLsp, PccSession


def g(d: Any, key: str, default=None):
    """Get `key` from dict d, accepting an optional 'module:' prefix."""
    if not isinstance(d, dict):
        return default
    if key in d:
        return d[key]
    for k, v in d.items():
        if k.split(":")[-1] == key:
            return v
    return default


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def hop_ips(obj: Any) -> list:
    """Extract the hop IPv4 addresses from an ERO/RRO container (skips label subobjects)."""
    out = []
    for sub in _as_list(g(obj, "subobject")):
        pref = g(sub, "ip-prefix")
        if isinstance(pref, dict):
            pref = g(pref, "ip-prefix")
        if isinstance(pref, str):
            out.append(pref.split("/")[0])
            continue
        unnum = g(sub, "router-id")   # unnumbered interface subobject
        if isinstance(unnum, (str, int)):
            out.append(str(unnum))
    return out


def parse_lsp_path(p: dict) -> LspPath:
    lsp = g(p, "lsp") or {}
    tlvs = g(lsp, "tlvs") or {}
    ids = g(tlvs, "lsp-identifiers") or {}
    v4 = g(ids, "ipv4") or {}
    op = g(lsp, "operational")
    return LspPath(
        lsp_id=str(g(p, "lsp-id", "")),
        plsp_id=str(g(lsp, "plsp-id", "")),
        operational=str(op).lower() if op is not None else "unknown",
        administrative=g(lsp, "administrative"),
        delegate=g(lsp, "delegate"),
        ero=hop_ips(g(p, "ero")),
        rro=hop_ips(g(p, "rro")),
        source=str(g(v4, "ipv4-tunnel-sender-address", "")),
        destination=str(g(v4, "ipv4-tunnel-endpoint-address", "")),
    )


def _topology_list(doc: dict) -> list:
    """Accept {'network-topology:topology': [...]}, {'topology': [...]}, or a full tree."""
    top = g(doc, "topology")
    if top is None:
        nt = g(doc, "network-topology")
        top = g(nt, "topology") if nt else None
    return _as_list(top)


def parse_pcep_topology(doc: dict) -> list:
    sessions = []
    for topo in _topology_list(doc):
        for node in _as_list(g(topo, "node")):
            pcc_id = str(g(node, "node-id", ""))
            pcc = g(node, "path-computation-client") or {}
            sess = PccSession(pcc=pcc_id, ip=str(g(pcc, "ip-address", "")),
                              state_sync=str(g(pcc, "state-sync", "unknown")))
            for rl in _as_list(g(pcc, "reported-lsp")):
                sess.lsps.append(ReportedLsp(
                    name=str(g(rl, "name", "")), pcc=pcc_id,
                    paths=[parse_lsp_path(p) for p in _as_list(g(rl, "path"))], raw=rl))
            sessions.append(sess)
    return sessions


def _find_numeric(d: Any, names: tuple) -> float | None:
    """Depth-first search for the first numeric value under any of `names`."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k.split(":")[-1] in names:
                try:
                    return float(v if not isinstance(v, list) else v[0])
                except (TypeError, ValueError):
                    pass
            r = _find_numeric(v, names)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _find_numeric(v, names)
            if r is not None:
                return r
    return None


def parse_bgpls_topology(doc: dict) -> tuple[list, list]:
    """Return (nodes, links) as plain dicts: router-ids, endpoints, metric, max bandwidth."""
    nodes, links = [], []
    for topo in _topology_list(doc):
        for n in _as_list(g(topo, "node")):
            attrs = g(n, "igp-node-attributes") or {}
            rids = [str(r) for r in _as_list(g(attrs, "router-id"))]
            nodes.append({"node_id": str(g(n, "node-id", "")), "router_ids": rids,
                          "name": str(g(attrs, "name", ""))})
        for l in _as_list(g(topo, "link")):
            attrs = g(l, "igp-link-attributes") or {}
            links.append({
                "link_id": str(g(l, "link-id", "")),
                "src": str(g(g(l, "source") or {}, "source-node", "")),
                "dst": str(g(g(l, "destination") or {}, "dest-node", "")),
                "metric": _find_numeric(attrs, ("metric",)),
                "te_metric": _find_numeric(attrs, ("te-default-metric",)),
                "max_bw": _find_numeric(attrs, ("max-link-bandwidth",)),
            })
    return nodes, links
