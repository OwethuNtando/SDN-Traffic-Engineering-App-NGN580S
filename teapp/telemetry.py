"""Link-load sources. Each source tags the values it writes with its provenance.

    declared : documented assumptions from config.json            -> DECLARED
    manual   : values typed by the operator in the link panel      -> MANUAL
    replay   : documented load profile (CSV) stepped per refresh   -> REPLAY (simulated)
    cli      : IOS XRv 'show interfaces' output rate via SSH       -> MEASURED

The 'cli' source needs the optional dependency netmiko and SSH reachability to
the routers' management addresses. If it fails, the error is reported and the
previous values keep their old provenance and timestamp (never silently relabelled).
"""
from __future__ import annotations

import csv
import re
import time
from dataclasses import dataclass

from .model import Load, Prov, Topology

RATE_RE = re.compile(r"(\d+)\s+(second|minute)s?\s+output rate\s+(\d+)\s+bits/sec", re.I)


@dataclass
class ReplayStep:
    step: int
    elapsed_s: float
    demand_mbps: float
    background: dict          # "R1>R3" -> Mb/s
    note: str


def load_replay(path) -> list:
    steps = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(r for r in fh if not r.startswith("#")):
            bg = {k[3:]: float(v) for k, v in row.items() if k.startswith("bg_") and v not in ("", None)}
            d = float(row["demand_mbps"])
            if d < 0 or any(v < 0 for v in bg.values()):
                raise ValueError(f"Negative value in replay row {row}")
            steps.append(ReplayStep(int(row["step"]), float(row["elapsed_s"]), d, bg, row.get("note", "")))
    if not steps:
        raise ValueError("Replay profile is empty")
    return steps


def parse_output_rate_bps(text: str) -> float | None:
    m = RATE_RE.search(text or "")
    return float(m.group(3)) if m else None


def apply_declared(topo: Topology, cfg: dict) -> None:
    for lc in cfg["links"]:
        link = topo.link(lc["id"])
        for key, val in lc.get("background_mbps", {}).items():
            link.loads[key] = Load(float(val or 0.0), Prov.DECLARED, detail="config assumption")


def apply_replay(topo: Topology, step: ReplayStep) -> None:
    for key, mbps in step.background.items():
        u, v = key.split(">")
        link = topo.link_between(u, v, include_down=True)
        if link:
            link.loads[key] = Load(mbps, Prov.REPLAY, detail=f"profile step {step.step}: {step.note}")


def set_manual(topo: Topology, link_id: str, u: str, v: str, mbps: float) -> None:
    if mbps < 0:
        raise ValueError("Load must be >= 0 Mb/s")
    link = topo.link(link_id)
    if mbps > 10 * link.capacity_mbps:
        raise ValueError(f"{mbps} Mb/s is implausible for a {link.capacity_mbps} Mb/s link")
    link.loads[f"{u}>{v}"] = Load(mbps, Prov.MANUAL, detail="operator entry")


def subtract_test_flow(topo: Topology, path_nodes: list, demand: float) -> None:
    """Measured counters already contain the R1->R3 test flow on its current path.
    Remove it so the TE engine can re-place the demand (background = measured - demand)."""
    for u, v in zip(path_nodes, path_nodes[1:]):
        link = topo.link_between(u, v, include_down=True)
        if link:
            cur = link.load(u, v)
            link.loads[f"{u}>{v}"] = Load(max(0.0, cur.mbps - demand), cur.prov, cur.ts,
                                          cur.detail + f" minus test flow {demand:.3f}")


class CliTelemetry:
    """Reads 'show interfaces <if>' output rate from each router over SSH (netmiko)."""

    def __init__(self, cfg: dict, username: str | None, password: str | None):
        self.cfg = cfg
        self.user, self.pwd = username, password

    def poll(self, topo: Topology) -> list:
        try:
            from netmiko import ConnectHandler  # optional dependency
        except ImportError:
            return ["netmiko not installed: pip install netmiko (CLI telemetry disabled)"]
        if not self.user:
            return ["XR_USER / XR_PASS not set: CLI telemetry disabled"]
        errors = []
        tcfg = self.cfg["telemetry"]["cli"]
        for node in topo.nodes.values():
            if not node.mgmt_ip:
                errors.append(f"{node.name}: no mgmt_ip configured")
                continue
            try:
                conn = ConnectHandler(device_type=tcfg.get("device_type", "cisco_xr"), host=node.mgmt_ip,
                                      username=self.user, password=self.pwd,
                                      timeout=tcfg.get("timeout_s", 10))
            except Exception as exc:  # netmiko raises many types
                errors.append(f"{node.name} SSH failed: {type(exc).__name__}")
                continue
            try:
                for link in topo.links:
                    if node.name not in link.ends():
                        continue
                    ifname = link.if_of(node.name)
                    out = conn.send_command(f"show interfaces {ifname} | include rate")
                    bps = parse_output_rate_bps(out)
                    if bps is None:
                        errors.append(f"{node.name} {ifname}: rate not found in output")
                        continue
                    peer = link.other(node.name)
                    link.loads[f"{node.name}>{peer}"] = Load(
                        bps / 1e6, Prov.MEASURED, time.time(), f"{node.name} {ifname} output rate")
            finally:
                conn.disconnect()
        return errors
