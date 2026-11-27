"""Network model: nodes, links, directional loads and data provenance.

Every load value carries a Provenance tag so the GUI can always distinguish
observed state from assumptions, calculations and simulated data (T3 s2).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Prov(str, Enum):
    MEASURED = "MEASURED"      # read from a router (interface counters via CLI)
    CONTROLLER = "CONTROLLER"  # read from OpenDaylight operational datastore
    DECLARED = "DECLARED"      # documented assumption from config (no live telemetry)
    MANUAL = "MANUAL"          # typed in by the operator in the GUI
    REPLAY = "REPLAY"          # documented load profile replayed from CSV (simulated)
    SIMULATED = "SIMULATED"    # offline simulation mode
    COMPUTED = "COMPUTED"      # calculated by this application

    @property
    def observed(self) -> bool:
        return self in (Prov.MEASURED, Prov.CONTROLLER)


@dataclass
class Load:
    mbps: float
    prov: Prov
    ts: float = field(default_factory=time.time)
    detail: str = ""


@dataclass
class Node:
    name: str
    loopback: str
    pcc_id: str
    mgmt_ip: str = ""
    x: float = 0.5
    y: float = 0.5
    pcep_state: str = "unknown"   # filled from controller: synchronized / down / unknown
    bgp_state: str = "n/a"
    gns3_status: str = ""         # started / stopped / suspended (from GNS3 API), "" = not read


@dataclass
class Link:
    id: str
    a: str
    b: str
    a_ip: str
    b_ip: str
    a_if: str
    b_if: str
    capacity_mbps: float
    igp_cost: float
    delay_ms: float
    up: bool = True               # False = operator what-if failure
    gns3: str = ""                # "present" / "suspended" / "absent" (from GNS3 API), "" = not read
    loads: dict = field(default_factory=dict)   # "R1>R3" -> Load

    def ends(self) -> tuple[str, str]:
        return self.a, self.b

    def other(self, node: str) -> str:
        if node == self.a:
            return self.b
        if node == self.b:
            return self.a
        raise KeyError(f"{node} is not an end of {self.id}")

    @staticmethod
    def dkey(u: str, v: str) -> str:
        return f"{u}>{v}"

    def ip_of(self, node: str) -> str:
        return self.a_ip if node == self.a else self.b_ip

    def if_of(self, node: str) -> str:
        return self.a_if if node == self.a else self.b_if

    def load(self, u: str, v: str) -> Load:
        return self.loads.get(self.dkey(u, v), Load(0.0, Prov.DECLARED, detail="no data"))

    def label(self) -> str:
        return f"{self.id} ({self.a}-{self.b})"


class Topology:
    def __init__(self, nodes: dict[str, Node], links: list[Link]):
        self.nodes = nodes
        self.links = links

    @classmethod
    def from_config(cls, cfg: dict) -> "Topology":
        nodes = {
            name: Node(name=name, loopback=n["loopback"], pcc_id=n.get("pcc_id", f"pcc://{n['loopback']}"),
                       mgmt_ip=n.get("mgmt_ip", ""), x=float(n.get("x", 0.5)), y=float(n.get("y", 0.5)))
            for name, n in cfg["nodes"].items()
        }
        links = []
        for l in cfg["links"]:
            link = Link(id=l["id"], a=l["a"], b=l["b"], a_ip=l["a_ip"], b_ip=l["b_ip"],
                        a_if=l.get("a_if", ""), b_if=l.get("b_if", ""),
                        capacity_mbps=float(l["capacity_mbps"]), igp_cost=float(l["igp_cost"]),
                        delay_ms=float(l["delay_ms"]))
            for key, val in l.get("background_mbps", {}).items():
                link.loads[key] = Load(float(val or 0.0), Prov.DECLARED, detail="config assumption")
            for u, v in ((link.a, link.b), (link.b, link.a)):
                link.loads.setdefault(Link.dkey(u, v), Load(0.0, Prov.DECLARED, detail="default 0"))
            links.append(link)
        return cls(nodes, links)

    # --- lookups -----------------------------------------------------------
    def link(self, link_id: str) -> Link:
        for l in self.links:
            if l.id == link_id:
                return l
        raise KeyError(link_id)

    def link_between(self, u: str, v: str, include_down: bool = False) -> Link | None:
        for l in self.links:
            if {l.a, l.b} == {u, v} and (l.up or include_down):
                return l
        return None

    def neighbours(self, u: str) -> list[str]:
        return [l.other(u) for l in self.links if l.up and u in (l.a, l.b)]

    def node_for_ip(self, ip: str) -> tuple[str | None, Link | None]:
        """Map an ERO/RRO hop address to (node, link). Loopbacks map to a node only."""
        ip = ip.split("/")[0]
        for n in self.nodes.values():
            if n.loopback == ip:
                return n.name, None
        for l in self.links:
            if l.a_ip == ip:
                return l.a, l
            if l.b_ip == ip:
                return l.b, l
        return None, None

    def node_for_pcc(self, pcc_id: str) -> str | None:
        addr = pcc_id.replace("pcc://", "")
        for n in self.nodes.values():
            if n.pcc_id == pcc_id or n.loopback == addr or n.mgmt_ip == addr:
                return n.name
        return None
