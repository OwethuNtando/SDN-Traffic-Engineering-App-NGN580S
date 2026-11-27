"""Live topology source: the GNS3 server REST API (v2).

Reads the OPENED GNS3 project (e.g. "Tunzi GA5") and returns its routers, the
router-to-router links with the interface labels shown on the GNS3 canvas, node
run state (started/stopped) and canvas positions. All of it is tagged GNS3 in
the GUI.

    GET {url}/v2/projects                      -> pick status == "opened" (or name match)
    GET {url}/v2/projects/{id}/nodes           -> name, status, x, y, console
    GET {url}/v2/projects/{id}/links           -> nodes[].node_id + label.text ("Gi0/0/0/1")

Authentication: GNS3 2.2's local server may require HTTP Basic auth. The app
uses GNS3_USER/GNS3_PASS if set, otherwise reads the user/password GNS3 itself
stored in %APPDATA%\\GNS3\\<ver>\\gns3_server.ini on this PC (never written to
our config or logs).
"""
from __future__ import annotations

import configparser
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests


@dataclass
class Gns3Node:
    node_id: str
    name: str
    status: str
    x: float
    y: float
    node_type: str = ""
    console: str = ""


@dataclass
class Gns3Link:
    a: str
    b: str
    a_if: str
    b_if: str
    suspended: bool = False


@dataclass
class Gns3Snapshot:
    project: str = ""
    project_status: str = ""
    nodes: dict = field(default_factory=dict)     # name -> Gns3Node (all nodes)
    links: list = field(default_factory=list)     # router-router links only
    ts: float = field(default_factory=time.time)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def expand_if(label: str) -> str:
    """'Gi0/0/0/1' -> 'GigabitEthernet0/0/0/1' (GNS3 canvas labels are abbreviated)."""
    label = (label or "").strip()
    m = re.match(r"^(Gi|Gig|GigabitEthernet|Te|Fa|Eth|e)\s*([\d/]+)$", label, re.I)
    if not m:
        return label
    pref = {"gi": "GigabitEthernet", "gig": "GigabitEthernet", "gigabitethernet": "GigabitEthernet",
            "te": "TenGigE", "fa": "FastEthernet", "eth": "Ethernet", "e": "Ethernet"}[m.group(1).lower()]
    return pref + m.group(2)


def _gns3_ini_credentials() -> tuple[str | None, str | None]:
    base = Path(os.environ.get("APPDATA", "")) / "GNS3"
    if not base.exists():
        return None, None
    for ini in sorted(base.glob("*/gns3_server.ini"), reverse=True):
        cp = configparser.ConfigParser()
        try:
            cp.read(ini, encoding="utf-8")
        except (configparser.Error, OSError):
            continue
        if cp.has_section("Server") and cp.getboolean("Server", "auth", fallback=False):
            return cp.get("Server", "user", fallback=None), cp.get("Server", "password", fallback=None)
    return None, None


class Gns3Client:
    def __init__(self, gcfg: dict, session: requests.Session | None = None):
        self.url = gcfg.get("url", "http://localhost:3080").rstrip("/")
        self.project_name = gcfg.get("project", "")
        self.router_prefix = gcfg.get("router_prefix", "R")
        self.timeout = float(gcfg.get("timeout_s", 3))
        self.s = session or requests.Session()
        user = os.environ.get("GNS3_USER")
        pwd = os.environ.get("GNS3_PASS")
        if not user:
            user, pwd = _gns3_ini_credentials()
        if user:
            self.s.auth = (user, pwd or "")

    def _get(self, path: str):
        r = self.s.get(self.url + path, timeout=self.timeout)
        if r.status_code == 401:
            raise PermissionError("GNS3 server requires auth (set GNS3_USER/GNS3_PASS)")
        r.raise_for_status()
        return r.json()

    def _is_router(self, n: dict) -> bool:
        name = n.get("name", "")
        return bool(re.match(rf"^{re.escape(self.router_prefix)}\d+$", name))

    def snapshot(self) -> Gns3Snapshot:
        snap = Gns3Snapshot()
        try:
            projects = self._get("/v2/projects")
            proj = None
            for p in projects:
                if self.project_name and p.get("name") == self.project_name:
                    proj = p
                    break
            if proj is None:
                opened = [p for p in projects if p.get("status") == "opened"]
                proj = opened[0] if opened else None
            if proj is None:
                snap.error = "No opened GNS3 project" + (f" named '{self.project_name}'" if self.project_name else "")
                return snap
            snap.project, snap.project_status = proj.get("name", ""), proj.get("status", "")
            pid = proj["project_id"]
            nodes = self._get(f"/v2/projects/{pid}/nodes")
            by_id = {}
            for n in nodes:
                gn = Gns3Node(n["node_id"], n.get("name", ""), n.get("status", "unknown"),
                              float(n.get("x", 0)), float(n.get("y", 0)), n.get("node_type", ""),
                              f"{n.get('console_host', '')}:{n.get('console', '')}")
                by_id[gn.node_id] = (gn, self._is_router(n))
                snap.nodes[gn.name] = gn
            for l in self._get(f"/v2/projects/{pid}/links"):
                ends = l.get("nodes", [])
                if len(ends) != 2:
                    continue
                (na, ra), (nb, rb) = by_id.get(ends[0]["node_id"], (None, False)), \
                    by_id.get(ends[1]["node_id"], (None, False))
                if not (ra and rb):
                    continue
                la = expand_if((ends[0].get("label") or {}).get("text", ""))
                lb = expand_if((ends[1].get("label") or {}).get("text", ""))
                snap.links.append(Gns3Link(na.name, nb.name, la, lb, bool(l.get("suspend", False))))
        except PermissionError as exc:
            snap.error = str(exc)
        except (requests.RequestException, ValueError, KeyError) as exc:
            snap.error = f"GNS3 API {self.url} unreachable/invalid: {type(exc).__name__}"
        return snap

    @staticmethod
    def routers(snap: Gns3Snapshot, prefix: str = "R") -> dict:
        return {k: v for k, v in snap.nodes.items() if re.match(rf"^{re.escape(prefix)}\d+$", k)}


def discover_controller(subnet_prefix: str, port: int = 8181, timeout: float = 0.3,
                        hosts=range(2, 255)) -> list:
    """TCP-probe <prefix>.N:port in parallel; return addresses that accept a connection."""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def probe(h):
        ip = f"{subnet_prefix}.{h}"
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return ip
        except OSError:
            return None
    with ThreadPoolExecutor(max_workers=64) as ex:
        return [ip for ip in ex.map(probe, hosts) if ip]
