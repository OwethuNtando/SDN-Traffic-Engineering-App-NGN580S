"""Application logic layer between the GUI and the adapters.

The GUI never talks to OpenDaylight or the routers directly; it calls this
service (from worker threads) and renders the returned state. This keeps the
backend testable without a display.
"""
from __future__ import annotations

import threading
import time

from . import telemetry as tel
from .audit import EventLog, History, export_csv
from .config import credentials, data_path, resolve
from .controller import ControllerError, ControllerRejected, ControllerSnapshot, ControllerUnreachable, make_adapter
from .lsp_manager import LspManager, resolve_lsp_path
from .model import Link, Load, Prov, Topology
from .te_engine import compute, link_states


class AppService:
    def __init__(self, cfg: dict, adapter=None):
        self.cfg = cfg
        self.student = cfg["_student"]
        self.topo = Topology.from_config(cfg)
        self.adapter = adapter or make_adapter(cfg)
        c = cfg["controller"]
        self.lsp = LspManager(self.adapter, self.topo, float(c.get("verify_poll_s", 2)),
                              float(c.get("verify_timeout_s", 30)))
        self.log = EventLog(resolve(cfg, "audit_log"))
        self.history = History(int(cfg["gui"].get("history_points", 120)))
        self.lock = threading.RLock()

        te = cfg["te"]
        self.src, self.dst = te["src"], te["dst"]
        self.demand = float(te["demand_mbps"])
        self.demand_prov = Prov.DECLARED
        self.threshold = float(te["threshold_pct"])
        self.policy = te.get("policy", "min_mlu")
        self.telemetry_source = cfg["telemetry"].get("source", "declared")
        self.subtract_flow = False

        self.snapshot: ControllerSnapshot | None = None
        self.controller_status = "never contacted"
        self.last_refresh: float | None = None
        self.last_error = ""
        self.te_result = None
        self.selected_lsp: tuple | None = None    # (pcc, name)

        self.gns3 = None
        self.gns3_snapshot = None
        gcfg = cfg.get("gns3", {})
        if gcfg.get("enabled"):
            from .gns3 import Gns3Client
            self.gns3 = Gns3Client(gcfg)
        self._discovery_done = False

        self.replay_steps = []
        self.replay_idx = -1
        self.cli = None

        mode = "LIVE" if not self.adapter.simulated else "OFFLINE SIMULATION"
        self.log.add("INFO", "app", f"Started in {mode} mode, config {cfg['_source_file']}",
                     student=self.student.student_number)
        if cfg["_derived_fields"]:
            self.log.add("INFO", "student", "Derived from student number: " + ", ".join(cfg["_derived_fields"]))

    # ---------------------------------------------------------------- state
    @property
    def simulated(self) -> bool:
        return self.adapter.simulated

    def refresh(self) -> None:
        """Read controller state and link loads, then recompute TE. Never raises."""
        with self.lock:
            self._refresh_gns3()
            self._refresh_controller()
            self._refresh_telemetry()
            self.last_refresh = time.time()
            self.recompute(log=False)

    # ------------------------------------------------------------- GNS3
    def _refresh_gns3(self) -> None:
        if self.gns3 is None:
            return
        snap = self.gns3.snapshot()
        first = self.gns3_snapshot is None
        self.gns3_snapshot = snap
        if not snap.ok:
            for n in self.topo.nodes.values():
                n.gns3_status = "unknown"
            self.log.add("ERROR", "gns3", snap.error)
            return
        routers = {k: v for k, v in snap.nodes.items() if k in self.topo.nodes}
        xs = [v.x for v in routers.values()] or [0]
        ys = [v.y for v in routers.values()] or [0]
        spanx, spany = max(max(xs) - min(xs), 1), max(max(ys) - min(ys), 1)
        for name, node in self.topo.nodes.items():
            g = routers.get(name)
            if g is None:
                node.gns3_status = "not in GNS3"
                continue
            node.gns3_status = g.status
            node.x = 0.15 + 0.70 * (g.x - min(xs)) / spanx
            node.y = 0.30 + 0.38 * (g.y - min(ys)) / spany
        seen = set()
        for gl in snap.links:
            link = self.topo.link_between(gl.a, gl.b, include_down=True)
            if link is None:
                if gl.a in self.topo.nodes and gl.b in self.topo.nodes:
                    lid = "L" + "".join(sorted([gl.a[1:], gl.b[1:]]))
                    link = Link(lid, gl.a, gl.b, "0.0.0.0", "0.0.0.0", gl.a_if, gl.b_if, 10.0, 10, 1.0)
                    for u, v in ((gl.a, gl.b), (gl.b, gl.a)):
                        link.loads[f"{u}>{v}"] = Load(0.0, Prov.DECLARED, detail="GNS3-discovered link")
                    self.topo.links.append(link)
                    self.log.add("WARN", "gns3", f"Link {gl.a}-{gl.b} found in GNS3 but not in config: "
                                                 f"added; set its IPs in config.json")
                else:
                    continue
            seen.add(link.id)
            if gl.a == link.a:
                link.a_if, link.b_if = gl.a_if or link.a_if, gl.b_if or link.b_if
            else:
                link.a_if, link.b_if = gl.b_if or link.a_if, gl.a_if or link.b_if
            link.gns3 = "suspended" if gl.suspended else "present"
        for link in self.topo.links:
            if link.id not in seen:
                link.gns3 = "absent"
        started = sum(1 for n in routers.values() if n.status == "started")
        msg = (f"GNS3 project '{snap.project}': {started}/{len(routers)} routers started, "
               f"{len(snap.links)} router links")
        if first:
            self.log.add("OK", "gns3", msg + " - topology taken from GNS3")
        elif started < len(routers):
            self.log.add("WARN", "gns3", msg)

    def _discover_controller(self) -> bool:
        sub = self.cfg["controller"].get("auto_discover_subnet")
        if self._discovery_done or not sub or self.simulated:
            return False
        self._discovery_done = True
        from .gns3 import discover_controller
        port = int(self.cfg["controller"].get("discover_port", 8181))
        self.log.add("INFO", "controller", f"Controller not reachable at configured address; "
                                           f"scanning {sub}.0/24 for port {port}...")
        found = discover_controller(sub, port)
        if not found:
            self.log.add("ERROR", "controller", f"No host on {sub}.0/24 answers on port {port}")
            return False
        url = f"http://{found[0]}:{port}"
        self.adapter.base = url + self.cfg["controller"].get("restconf_root", "/rests")
        self.adapter.c["base_url"] = url
        self.log.add("OK", "controller", f"Found controller candidate(s) {found}; using {url}")
        return True

    def _refresh_controller(self) -> None:
        try:
            try:
                snap = self.adapter.snapshot()
            except ControllerUnreachable:
                if not self._discover_controller():
                    raise
                snap = self.adapter.snapshot()
        except ControllerUnreachable as exc:
            self.controller_status = "DISCONNECTED"
            self.last_error = str(exc)
            self.snapshot = None
            for n in self.topo.nodes.values():
                n.pcep_state = "unknown (controller down)"
            self.log.add("ERROR", "controller", str(exc))
            return
        except ControllerRejected as exc:
            self.controller_status = f"ERROR {exc.status or ''}".strip()
            self.last_error = str(exc)
            self.snapshot = None
            self.log.add("ERROR", "controller", str(exc))
            return
        self.snapshot = snap
        self.controller_status = "CONNECTED" + (" (SIMULATED)" if snap.simulated else "")
        self.last_error = "; ".join(snap.errors)
        seen = {}
        for s in snap.pccs:
            node = self.topo.node_for_pcc(s.pcc) or self.topo.node_for_pcc(f"pcc://{s.ip}")
            if node:
                seen[node] = s.state_sync
        for name, n in self.topo.nodes.items():
            n.pcep_state = seen.get(name, "no session")
        unmapped = [f"{s.pcc}" for s in snap.pccs
                    if not (self.topo.node_for_pcc(s.pcc) or self.topo.node_for_pcc(f"pcc://{s.ip}"))]
        if unmapped:
            self.log.add("WARN", "controller", f"PCEP sessions not matched to a router loopback in config: "
                                               f"{unmapped} - set nodes.Rn.loopback/pcc_id in config.json")
        r1 = self.topo.nodes.get(self.src)
        if r1 is not None:
            r1.bgp_state = snap.bgp_peer_state if snap.bgp_peer_state != "n/a" else (
                f"BGP-LS: {len(snap.bgpls_nodes)} nodes/{len(snap.bgpls_links)} links"
                if snap.bgpls_nodes else "BGP-LS: no data")
        for e in snap.errors:
            if e not in getattr(self, "_seen_errors", set()):
                self.log.add("WARN", "controller", e)
        self._seen_errors = set(snap.errors)
        if snap.bgp_peer_state != getattr(self, "_last_bgp", None):
            lvl = "OK" if snap.bgp_peer_state.startswith("ESTABLISHED") else "WARN"
            self.log.add(lvl, "bgp", f"R1-ODL BGP: {snap.bgp_peer_state}")
            self._last_bgp = snap.bgp_peer_state
        self.log.add("OK", "controller", f"Refreshed: {len(snap.pccs)} PCC(s), "
                                         f"{len(snap.all_lsps())} LSP(s)" + (" [SIMULATED]" if snap.simulated else ""))

    def _refresh_telemetry(self) -> None:
        src = self.telemetry_source
        if src == "declared":
            tel.apply_declared(self.topo, self.cfg)
        elif src == "replay":
            self.replay_next()
        elif src == "cli":
            if self.cli is None:
                u, p = credentials(self.cfg["telemetry"]["cli"])
                self.cli = tel.CliTelemetry(self.cfg, u, p)
            for err in self.cli.poll(self.topo):
                self.log.add("WARN", "telemetry", err)
            if self.subtract_flow and self.te_result and self.te_result.baseline:
                tel.subtract_test_flow(self.topo, self.te_result.baseline.nodes, self.demand)
        # 'manual' keeps operator-entered values untouched

    # ---------------------------------------------------------- telemetry ops
    def set_telemetry_source(self, src: str) -> None:
        if src not in ("declared", "manual", "replay", "cli"):
            raise ValueError(src)
        self.telemetry_source = src
        if src == "replay":
            self.replay_steps = tel.load_replay(data_path(self.cfg["telemetry"]["replay_csv"]))
            self.replay_idx = -1
        self.log.add("INFO", "telemetry", f"Load source set to {src}")

    def replay_next(self) -> None:
        if not self.replay_steps:
            self.replay_steps = tel.load_replay(data_path(self.cfg["telemetry"]["replay_csv"]))
        self.replay_idx = (self.replay_idx + 1) % len(self.replay_steps)
        st = self.replay_steps[self.replay_idx]
        tel.apply_replay(self.topo, st)
        self.demand, self.demand_prov = st.demand_mbps, Prov.REPLAY
        self.log.add("INFO", "replay", f"Profile step {st.step} (t={st.elapsed_s:g}s): demand "
                                       f"{st.demand_mbps} Mb/s - {st.note}")

    def set_manual_load(self, link_id: str, u: str, v: str, mbps: float) -> None:
        tel.set_manual(self.topo, link_id, u, v, mbps)
        self.telemetry_source = "manual"
        self.log.add("INFO", "telemetry", f"Manual load {link_id} {u}>{v} = {mbps} Mb/s")

    # ------------------------------------------------------------------- TE
    def set_inputs(self, demand: float, threshold: float, policy: str) -> None:
        if not 0 <= demand <= 10_000:
            raise ValueError("Demand must be between 0 and 10000 Mb/s")
        if not 1 <= threshold <= 100:
            raise ValueError("Threshold must be between 1 and 100 %")
        if demand != self.demand:
            self.demand_prov = Prov.MANUAL
        self.demand, self.threshold, self.policy = demand, threshold, policy

    def recompute(self, log: bool = True):
        with self.lock:
            res = compute(self.topo, self.src, self.dst, self.demand, self.threshold, self.policy)
            prev = self.te_result
            self.te_result = res
            self.history.add(res.before_links)
            if log or prev is None or (prev.recommended and res.recommended and
                                       prev.recommended.name != res.recommended.name):
                lvl = "WARN" if res.baseline and res.baseline.congested else "OK"
                self.log.add(lvl, "te", f"D={res.demand:.3f} Mb/s T={res.threshold:.0f}%: "
                                        f"MLU before {res.mlu_before:.1f}% -> after {res.mlu_after:.1f}%. "
                                        f"{res.reason}",
                             demand=res.demand, mlu_before=res.mlu_before, mlu_after=res.mlu_after,
                             recommended=res.recommended.name if res.recommended else None)
                for w in res.warnings:
                    self.log.add("WARN", "te", w)
            return res

    def set_link_up(self, link_id: str, up: bool) -> None:
        self.topo.link(link_id).up = up
        self.log.add("WARN" if not up else "INFO", "what-if",
                     f"{link_id} marked {'UP' if up else 'DOWN'} (what-if analysis only - router not changed)")

    def set_link_params(self, link_id: str, capacity: float, cost: float, delay: float) -> None:
        if capacity <= 0 or cost <= 0 or delay < 0:
            raise ValueError("Capacity and cost must be > 0, delay >= 0")
        l = self.topo.link(link_id)
        l.capacity_mbps, l.igp_cost, l.delay_ms = capacity, cost, delay
        self.log.add("INFO", "what-if", f"{link_id}: capacity={capacity} Mb/s cost={cost} delay={delay} ms "
                                        f"(TE model only)")

    # ------------------------------------------------------------------ LSPs
    def lsp_name_for(self, nodes: list) -> str:
        tag = "DIRECT" if len(nodes) == 2 else "VIA-" + "-".join(nodes[1:-1])
        return self.student.lsp_name(nodes[0], nodes[-1], tag)

    def provision(self, name: str, nodes: list, progress=None):
        op = self.lsp.provision(name, nodes, self.snapshot, progress)
        lvl = "OK" if op.state == "OPERATIONAL" else ("WARN" if op.state in ("TIMEOUT", "REPORTED") else "ERROR")
        self.log.add(lvl, "lsp", f"Provision {name}: {op.state} - {op.steps[-1].message}",
                     steps=[(s.state, s.message) for s in op.steps], simulated=op.simulated)
        self.refresh()
        if op.state in ("OPERATIONAL", "REPORTED", "TIMEOUT"):
            self.selected_lsp = (op.pcc, name)
        return op

    def teardown(self, pcc: str, name: str, progress=None):
        op = self.lsp.teardown(name, pcc, progress, self.snapshot)
        lvl = "OK" if op.state == "WITHDRAWN" else "ERROR"
        self.log.add(lvl, "lsp", f"Teardown {name}: {op.state} - {op.steps[-1].message}",
                     steps=[(s.state, s.message) for s in op.steps], simulated=op.simulated)
        self.refresh()
        if self.selected_lsp == (pcc, name) and op.state == "WITHDRAWN":
            self.selected_lsp = None
        return op

    def resync(self, pcc: str, progress=None):
        op = self.lsp.resync(pcc, progress)
        lvl = "OK" if op.state == "SYNCED" else "ERROR"
        self.log.add(lvl, "lsp", f"Re-sync {pcc}: {op.state} - {op.steps[-1].message}",
                     steps=[(s.state, s.message) for s in op.steps], simulated=op.simulated)
        self.refresh()
        return op

    def selected_lsp_view(self):
        """(ReportedLsp, ResolvedPath) for the selected LSP from the LAST controller read, or None."""
        if not self.selected_lsp or not self.snapshot:
            return None
        lsp = self.snapshot.find_lsp(self.selected_lsp[1], self.selected_lsp[0])
        if lsp is None:
            return None
        return lsp, resolve_lsp_path(self.topo, lsp)

    # --------------------------------------------------------------- export
    def export(self):
        path = resolve(self.cfg, "export_dir") / time.strftime("te_export_%Y%m%d_%H%M%S.csv")
        export_csv(path, self.history, self.te_result)
        self.log.add("OK", "export", f"Exported {len(self.history.samples)} samples to {path}")
        return path

    def link_states_now(self):
        return link_states(self.topo, self.threshold)
