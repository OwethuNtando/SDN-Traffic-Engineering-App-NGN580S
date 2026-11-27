"""LSP provisioning, teardown and verification workflow.

State model shown to the operator (T3 s3 / s5 evidence threshold):

    INTENDED      path chosen in the GUI, request validated, not yet sent
    REQUESTED     add-lsp RPC sent
    ACCEPTED      controller answered without an RPC failure  (NOT proof of an LSP)
    REPORTED      LSP present in the PCEP LSP-DB (reported-lsp) but not up
    OPERATIONAL   reported LSP with operational = up/active   <- only success state
    FAILED        RPC rejected / controller unreachable
    TIMEOUT       accepted but never reported operational within verify_timeout_s

    WITHDRAW_REQUESTED -> WITHDRAWN (verified absent from LSP-DB) | WITHDRAW_UNVERIFIED

The path displayed for an LSP is resolved from the controller's RRO (recorded
route, preferred) or reported ERO - never from what the GUI asked for.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .controller.base import (ControllerAdapter, ControllerError, ControllerRejected,
                              ControllerSnapshot, ControllerUnreachable, ReportedLsp)
from .model import Topology

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

# PCEP error types/values (RFC 5440 / RFC 8231 / RFC 8281) that the routers return
# through OpenDaylight, translated into plain words for the operator.
PCEP_ERRORS = {
    (19, 1): "the router says this LSP is NOT DELEGATED to the PCE, so the controller may not change "
             "or remove it (the router keeps control). Remove it on the router, or re-delegate it first.",
    (19, 9): "the router says the LSP was not created by this PCE.",
    (24, 1): "the router could not set up the LSP (unacceptable instantiation parameters).",
    (24, 2): "the router has no free resources for a new PCE-initiated LSP.",
}


def explain_pcep_error(text: str) -> str:
    """Return ' -> <plain explanation>' for a known PCEP error object found in text, else ''."""
    m = re.search(r'"type":\s*(\d+).*?"value":\s*(\d+)', text or "")
    if not m:
        if "no-ack" in (text or ""):
            return " -> the router did not acknowledge the request in time; try again."
        return ""
    why = PCEP_ERRORS.get((int(m.group(1)), int(m.group(2))))
    return f" -> PCEP error {m.group(1)}/{m.group(2)}: {why}" if why else \
        f" -> PCEP error type {m.group(1)} value {m.group(2)}"


@dataclass
class OpStep:
    ts: float
    state: str
    message: str


@dataclass
class LspOperation:
    kind: str                 # provision | teardown
    name: str
    pcc: str
    intended_nodes: list = field(default_factory=list)
    ero: list = field(default_factory=list)
    state: str = "INTENDED"
    steps: list = field(default_factory=list)
    observed_nodes: list = field(default_factory=list)
    observed_source: str = ""
    path_match: bool | None = None
    simulated: bool = False

    def step(self, state: str, msg: str) -> None:
        self.state = state
        self.steps.append(OpStep(time.time(), state, msg))

    @property
    def success(self) -> bool:
        return self.state in ("OPERATIONAL", "WITHDRAWN", "SYNCED")


@dataclass
class ResolvedPath:
    nodes: list
    link_ids: list
    source: str               # RRO | ERO | none
    unresolved_hops: list = field(default_factory=list)


def resolve_lsp_path(topo: Topology, lsp: ReportedLsp) -> ResolvedPath:
    """Map the LSP's recorded (RRO) or explicit (ERO) hops onto topology links."""
    head = topo.node_for_pcc(lsp.pcc)
    p = lsp.primary
    hops, source = (p.rro, "RRO") if p.rro else ((p.ero, "ERO") if p.ero else ([], "none"))
    nodes = [head] if head else []
    unresolved = []
    for ip in hops:
        node, _ = topo.node_for_ip(ip)
        if node is None:
            unresolved.append(ip)
            continue
        if not nodes or nodes[-1] != node:
            nodes.append(node)
    if p.destination:
        tail, _ = topo.node_for_ip(p.destination)
        if tail and nodes and nodes[-1] != tail and source == "none":
            nodes.append(tail)
    links = []
    for u, v in zip(nodes, nodes[1:]):
        l = topo.link_between(u, v, include_down=True)
        if l is None:
            unresolved.append(f"{u}-{v}")
        else:
            links.append(l.id)
    return ResolvedPath(nodes, links, source, unresolved)


class LspManager:
    def __init__(self, adapter: ControllerAdapter, topo: Topology, poll_s: float = 2.0,
                 timeout_s: float = 30.0, sleep: Callable[[float], None] = time.sleep):
        self.adapter = adapter
        self.topo = topo
        self.poll_s = poll_s
        self.timeout_s = timeout_s
        self.sleep = sleep
        self.operations: list[LspOperation] = []

    # --------------------------------------------------------- validation
    def ero_for(self, nodes: list) -> list:
        """Strict ERO: ingress interface address of each next hop, ending with the
        tail-end loopback (IOS XR requires the ERO to finish at the tunnel endpoint,
        as in the GA5 add-lsp body that was verified on this lab)."""
        ero = []
        for u, v in zip(nodes, nodes[1:]):
            link = self.topo.link_between(u, v)
            if link is None:
                raise ValueError(f"No operational link {u}-{v} for ERO")
            ero.append(link.ip_of(v))
        if nodes:
            ero.append(self.topo.nodes[nodes[-1]].loopback)
        return ero

    def validate(self, name: str, nodes: list, snapshot: ControllerSnapshot | None) -> list:
        """Return a list of blocking problems (empty list = request is valid)."""
        problems = []
        if not NAME_RE.match(name or ""):
            problems.append("LSP name must be 1-64 characters: letters, digits, '-', '_' or '.'")
        if len(nodes) < 2 or len(set(nodes)) != len(nodes):
            problems.append("Path must contain at least two distinct nodes and no loops")
        for n in nodes:
            if n not in self.topo.nodes:
                problems.append(f"Unknown node {n}")
        if not problems:
            for u, v in zip(nodes, nodes[1:]):
                if self.topo.link_between(u, v) is None:
                    problems.append(f"Link {u}-{v} is down or absent")
        if snapshot is not None and not problems:
            pcc = self.topo.nodes[nodes[0]].pcc_id
            if not any(s.pcc == pcc for s in snapshot.pccs):
                problems.append(f"Head-end {nodes[0]} ({pcc}) has no PCEP session in the controller")
            if snapshot.find_lsp(name, pcc):
                problems.append(f"An LSP named {name} already exists on {nodes[0]}")
        return problems

    # ---------------------------------------------------------- provision
    def provision(self, name: str, nodes: list, snapshot: ControllerSnapshot | None = None,
                  progress: Callable[[LspOperation], None] | None = None) -> LspOperation:
        head, tail = nodes[0], nodes[-1]
        pcc = self.topo.nodes[head].pcc_id
        op = LspOperation("provision", name, pcc, list(nodes), simulated=self.adapter.simulated)
        self.operations.append(op)
        notify = progress or (lambda _o: None)

        problems = self.validate(name, nodes, snapshot)
        if problems:
            op.step("FAILED", "Request not sent - validation: " + "; ".join(problems))
            notify(op)
            return op
        op.ero = self.ero_for(nodes)
        op.step("INTENDED", f"{name}: {'-'.join(nodes)}  ERO {op.ero}")
        notify(op)

        src_ip = self.topo.nodes[head].loopback
        dst_ip = self.topo.nodes[tail].loopback
        op.step("REQUESTED", f"POST add-lsp node={pcc} name={name}")
        notify(op)
        try:
            rec = self.adapter.add_lsp(pcc, name, src_ip, dst_ip, op.ero)
        except ControllerUnreachable as exc:
            op.step("FAILED", f"Controller unreachable: {exc}")
            notify(op)
            return op
        except ControllerRejected as exc:
            op.step("FAILED", f"Controller rejected request: {exc}{explain_pcep_error(str(exc))}")
            notify(op)
            return op
        op.step("ACCEPTED", f"HTTP {rec.status}, no RPC failure. Verifying in PCEP LSP-DB "
                            f"(accepted is NOT operational)...")
        notify(op)
        return self._verify_up(op, notify)

    def _verify_up(self, op: LspOperation, notify) -> LspOperation:
        deadline = time.time() + self.timeout_s
        reported_once = False
        while True:
            try:
                snap = self.adapter.snapshot()
            except ControllerError as exc:
                op.step(op.state, f"Verification read failed: {exc}")
                notify(op)
                snap = None
            lsp = snap.find_lsp(op.name, op.pcc) if snap else None
            if lsp is not None:
                if not reported_once:
                    reported_once = True
                    op.step("REPORTED", f"Present in LSP-DB of {op.pcc}: operational={lsp.operational}")
                    notify(op)
                if lsp.is_up:
                    rp = resolve_lsp_path(self.topo, lsp)
                    op.observed_nodes, op.observed_source = rp.nodes, rp.source
                    op.path_match = rp.nodes == op.intended_nodes if rp.source != "none" else None
                    note = {True: "matches intended path", False: "DIFFERS from intended path",
                            None: "no ERO/RRO reported - path not verifiable"}[op.path_match]
                    op.step("OPERATIONAL", f"operational={lsp.operational}; observed path "
                                           f"{'-'.join(rp.nodes) or '?'} from {rp.source} ({note})")
                    notify(op)
                    return op
            if time.time() >= deadline:
                op.step("TIMEOUT", f"Not operational after {self.timeout_s:.0f}s "
                                   f"({'reported but not up' if reported_once else 'never reported'}). "
                                   f"Check PCC: 'show mpls traffic-eng tunnels' / 'show pce lsp'.")
                notify(op)
                return op
            self.sleep(self.poll_s)

    # ----------------------------------------------------------- teardown
    def teardown(self, name: str, pcc: str,
                 progress: Callable[[LspOperation], None] | None = None,
                 snapshot: ControllerSnapshot | None = None) -> LspOperation:
        op = LspOperation("teardown", name, pcc, simulated=self.adapter.simulated)
        self.operations.append(op)
        notify = progress or (lambda _o: None)
        snapshot_lsp = snapshot.find_lsp(name, pcc) if snapshot is not None else None
        if not NAME_RE.match(name or ""):
            op.step("FAILED", "Invalid LSP name - nothing sent")
            notify(op)
            return op
        if snapshot_lsp is not None and not snapshot_lsp.is_up:
            op.step("FAILED", f"{name} is '{snapshot_lsp.operational}', not up - a tunnel that is down cannot be "
                              f"withdrawn. Press RE-SYNC LSPs first so the router and controller agree, then try again. "
                              f"Nothing was sent.")
            notify(op)
            return op
        op.step("WITHDRAW_REQUESTED", f"POST remove-lsp node={pcc} name={name}")
        notify(op)
        try:
            rec = self.adapter.remove_lsp(pcc, name)
        except ControllerError as exc:
            op.step("FAILED", f"remove-lsp failed: {exc}{explain_pcep_error(str(exc))}")
            notify(op)
            return op
        op.step("WITHDRAW_REQUESTED", f"HTTP {rec.status} accepted; verifying absence from LSP-DB...")
        notify(op)
        deadline = time.time() + self.timeout_s
        while True:
            try:
                snap = self.adapter.snapshot()
                lsp = snap.find_lsp(name, pcc)
                if lsp is None:
                    op.step("WITHDRAWN", f"{name} absent from LSP-DB of {pcc} (verified)")
                    notify(op)
                    return op
                state = lsp.operational
            except ControllerError as exc:
                state = f"read failed: {exc}"
            if time.time() >= deadline:
                op.step("WITHDRAW_UNVERIFIED", f"{name} still reported ({state}) after "
                                               f"{self.timeout_s:.0f}s - NOT confirmed withdrawn")
                notify(op)
                return op
            self.sleep(self.poll_s)

    # ------------------------------------------------------------ re-sync
    def resync(self, pcc: str, progress: Callable[[LspOperation], None] | None = None) -> LspOperation:
        """Re-synchronise the LSP database of one PCC (e.g. R1) with the controller.

        Used to clean up 'abandoned' tunnels (add-lsp attempts that never came up). The app compares the
        LSP list before and after and reports exactly what changed - it never assumes success.
        """
        op = LspOperation("resync", "all LSPs", pcc, simulated=self.adapter.simulated)
        self.operations.append(op)
        notify = progress or (lambda _o: None)
        try:
            before = self.adapter.snapshot()
        except ControllerError as exc:
            op.step("FAILED", f"cannot read LSP-DB before re-sync: {exc}")
            notify(op)
            return op
        b = {l.name: l.operational for l in before.all_lsps() if l.pcc == pcc}
        down = sorted(n for n, s in b.items() if s.lower() not in ("up", "active"))
        op.step("SYNC_REQUESTED", f"POST trigger-sync node={pcc}  (before: {len(b)} LSPs, {len(down)} not up"
                                  f"{': ' + ', '.join(down) if down else ''})")
        notify(op)
        try:
            rec = self.adapter.trigger_sync(pcc)
        except ControllerError as exc:
            op.step("FAILED", f"trigger-sync failed: {exc}{explain_pcep_error(str(exc))} - the router may not "
                              f"support re-sync; see README section 'Re-sync abandoned tunnels'.")
            notify(op)
            return op
        op.step("SYNC_ACCEPTED", f"HTTP {rec.status}; reading the LSP-DB again...")
        notify(op)
        self.sleep(max(self.poll_s, 0) * 2)
        try:
            after = self.adapter.snapshot()
        except ControllerError as exc:
            op.step("FAILED", f"cannot read LSP-DB after re-sync: {exc}")
            notify(op)
            return op
        a = {l.name: l.operational for l in after.all_lsps() if l.pcc == pcc}
        gone = sorted(set(b) - set(a))
        changed = sorted(n for n in set(a) & set(b) if a[n] != b[n])
        still_down = sorted(n for n, s in a.items() if s.lower() not in ("up", "active"))
        op.step("SYNCED", f"after: {len(a)} LSPs. Removed by re-sync: {', '.join(gone) or 'none'}. "
                          f"State changed: {', '.join(f'{n} {b[n]}->{a[n]}' for n in changed) or 'none'}. "
                          f"Still not up: {', '.join(still_down) or 'none'}.")
        notify(op)
        return op

