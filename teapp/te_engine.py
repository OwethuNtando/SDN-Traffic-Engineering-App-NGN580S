"""Traffic-engineering computation.

Problem: an R1->R3 demand D (Mb/s) is offered. The IGP sends it on the
least-cost path. If placing D on that path pushes any traversed link above the
congestion threshold T (% of declared capacity), the engine evaluates every
alternative simple path and proposes the one that minimises the maximum link
utilisation (MLU), breaking ties on IGP cost then delay.

Definitions (per direction u->v of a link):
    background(u,v)   load already on the link (measured / declared / replayed)
    util(u,v)         (background + demand placed) / capacity * 100 %
    available(u,v)    capacity - background
    path bottleneck   min available over the path's links
    path MLU          max util over the path's links with D placed on it
    congested         util >= T          overloaded   util > 100 %

All outputs of this module are COMPUTED values; the inputs keep their own
provenance tag so the GUI can show where each number came from.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .model import Topology, Prov

POLICIES = {
    "igp": "Least IGP cost (conventional shortest path)",
    "min_mlu": "TE: keep shortest path unless congested, else minimise max link utilisation",
    "min_delay": "Lowest assumed delay among paths below threshold",
}


@dataclass
class HopEval:
    link_id: str
    u: str
    v: str
    capacity: float
    background: float
    placed: float
    bg_prov: str

    @property
    def total(self) -> float:
        return self.background + self.placed

    @property
    def util_pct(self) -> float:
        return 100.0 * self.total / self.capacity

    @property
    def available(self) -> float:
        return self.capacity - self.background


@dataclass
class PathEval:
    nodes: list
    hops: list = field(default_factory=list)
    cost: float = 0.0
    delay_ms: float = 0.0
    demand: float = 0.0
    threshold: float = 75.0

    @property
    def name(self) -> str:
        return "-".join(self.nodes)

    @property
    def tag(self) -> str:
        """Short path tag used in the LSP naming rule (DIRECT / VIAR2 / VIAR2R4...)."""
        mid = self.nodes[1:-1]
        return "DIRECT" if not mid else "VIA-" + "-".join(mid)

    @property
    def link_ids(self) -> list:
        return [h.link_id for h in self.hops]

    @property
    def bottleneck_available(self) -> float:
        return min(h.available for h in self.hops) if self.hops else 0.0

    @property
    def mlu(self) -> float:
        return max(h.util_pct for h in self.hops) if self.hops else 0.0

    @property
    def bottleneck_link(self) -> str:
        return max(self.hops, key=lambda h: h.util_pct).link_id if self.hops else "-"

    @property
    def feasible(self) -> bool:
        return self.demand <= self.bottleneck_available + 1e-9

    @property
    def congested(self) -> bool:
        return self.mlu >= self.threshold

    @property
    def headroom_pct(self) -> float:
        return self.threshold - self.mlu


@dataclass
class LinkState:
    link_id: str
    u: str
    v: str
    capacity: float
    load: float
    prov: str
    threshold: float
    up: bool = True

    @property
    def util_pct(self) -> float:
        return 100.0 * self.load / self.capacity if self.capacity else 0.0

    @property
    def status(self) -> str:
        if not self.up:
            return "DOWN (what-if)"
        if self.util_pct > 100:
            return "OVERLOADED"
        if self.util_pct >= self.threshold:
            return "CONGESTED"
        return "OK"


@dataclass
class SplitResult:
    paths: list            # [PathEval, PathEval]
    fractions: list        # [x, 1-x]
    mlu: float


@dataclass
class TEResult:
    src: str
    dst: str
    demand: float
    threshold: float
    policy: str
    candidates: list
    baseline: PathEval | None
    recommended: PathEval | None
    reason: str
    before_links: list
    after_links: list
    split: SplitResult | None = None
    warnings: list = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def mlu_before(self) -> float:
        return max((s.util_pct for s in self.before_links if s.up), default=0.0)

    @property
    def mlu_after(self) -> float:
        return max((s.util_pct for s in self.after_links if s.up), default=0.0)


# ---------------------------------------------------------------------------
def enumerate_paths(topo: Topology, src: str, dst: str, max_hops: int = 6) -> list:
    """All loop-free paths src->dst over links that are up (DFS)."""
    out, stack = [], [(src, [src])]
    while stack:
        node, path = stack.pop()
        if node == dst:
            out.append(path)
            continue
        if len(path) > max_hops:
            continue
        for nb in sorted(topo.neighbours(node), reverse=True):
            if nb not in path:
                stack.append((nb, path + [nb]))
    out.sort(key=lambda p: (len(p), p))
    return out


def evaluate_path(topo: Topology, nodes: list, demand: float, threshold: float,
                  extra: dict | None = None) -> PathEval:
    """Place `demand` on `nodes` and evaluate every hop. `extra` adds other placed load."""
    ev = PathEval(nodes=list(nodes), demand=demand, threshold=threshold)
    for u, v in zip(nodes, nodes[1:]):
        link = topo.link_between(u, v)
        if link is None:
            raise ValueError(f"No operational link {u}-{v}")
        bg = link.load(u, v)
        placed = demand + (extra or {}).get((link.id, u, v), 0.0)
        ev.hops.append(HopEval(link.id, u, v, link.capacity_mbps, bg.mbps, placed, bg.prov.value))
        ev.cost += link.igp_cost
        ev.delay_ms += link.delay_ms
    return ev


def link_states(topo: Topology, threshold: float, placements: dict | None = None) -> list:
    """Directional state of every link; placements maps (link_id,u,v)->extra Mb/s."""
    placements = placements or {}
    out = []
    for l in topo.links:
        for u, v in ((l.a, l.b), (l.b, l.a)):
            bg = l.load(u, v)
            extra = placements.get((l.id, u, v), 0.0)
            prov = bg.prov.value if extra == 0 else f"{bg.prov.value}+{Prov.COMPUTED.value}"
            out.append(LinkState(l.id, u, v, l.capacity_mbps, bg.mbps + extra, prov, threshold, l.up))
    return out


def _placement(path: PathEval | None, amount: float | None = None) -> dict:
    if path is None:
        return {}
    amt = path.demand if amount is None else amount
    return {(h.link_id, h.u, h.v): amt for h in path.hops}


def best_split(topo: Topology, a: PathEval, b: PathEval, demand: float, threshold: float,
               step: float = 0.01) -> SplitResult:
    """Fraction x on path a, (1-x) on b, minimising MLU (grid search, handles shared links)."""
    best = None
    n = int(round(1 / step))
    for i in range(n + 1):
        x = i * step
        pl: dict = {}
        for k, val in _placement(a, demand * x).items():
            pl[k] = pl.get(k, 0.0) + val
        for k, val in _placement(b, demand * (1 - x)).items():
            pl[k] = pl.get(k, 0.0) + val
        mlu = max(s.util_pct for s in link_states(topo, threshold, pl) if s.up)
        if best is None or mlu < best[1] - 1e-9:
            best = (x, mlu)
    return SplitResult([a, b], [round(best[0], 2), round(1 - best[0], 2)], best[1])


def compute(topo: Topology, src: str, dst: str, demand: float, threshold: float,
            policy: str = "min_mlu") -> TEResult:
    if demand < 0:
        raise ValueError("Demand must be >= 0 Mb/s")
    if not 1 <= threshold <= 100:
        raise ValueError("Threshold must be within 1..100 %")
    if policy not in POLICIES:
        raise ValueError(f"Unknown policy {policy!r}")

    paths = enumerate_paths(topo, src, dst)
    cands = [evaluate_path(topo, p, demand, threshold) for p in paths]
    warnings: list = []
    if not cands:
        return TEResult(src, dst, demand, threshold, policy, [], None, None,
                        f"No operational path {src}->{dst} (check link states).",
                        link_states(topo, threshold), link_states(topo, threshold),
                        warnings=["Destination unreachable"])

    by_cost = sorted(cands, key=lambda c: (c.cost, c.delay_ms, len(c.nodes)))
    baseline = by_cost[0]
    by_mlu = sorted(cands, key=lambda c: (round(c.mlu, 6), c.cost, c.delay_ms))
    split = None

    if policy == "igp":
        rec = baseline
        reason = (f"IGP policy: least-cost path {baseline.name} (cost {baseline.cost:g}) is used "
                  f"regardless of load; MLU {baseline.mlu:.1f} %.")
    elif policy == "min_delay":
        ok = [c for c in cands if not c.congested]
        pool = ok or by_mlu
        rec = min(pool, key=lambda c: (c.delay_ms, c.mlu)) if ok else by_mlu[0]
        reason = (f"Lowest-delay path below {threshold:.0f} %: {rec.name} "
                  f"({rec.delay_ms:.1f} ms, MLU {rec.mlu:.1f} %)." if ok else
                  f"No path stays below {threshold:.0f} %; falling back to minimum MLU {rec.name}.")
    else:  # min_mlu
        if not baseline.congested:
            rec = baseline
            reason = (f"Shortest path {baseline.name} stays below the {threshold:.0f} % threshold "
                      f"(MLU {baseline.mlu:.1f} %, headroom {baseline.headroom_pct:.1f} pp): "
                      f"no re-route needed.")
        else:
            rec = by_mlu[0]
            if rec is baseline:
                reason = (f"Shortest path {baseline.name} is congested (MLU {baseline.mlu:.1f} %) "
                          f"and no alternative is better.")
            else:
                reason = (f"Shortest path {baseline.name} would reach {baseline.mlu:.1f} % on "
                          f"{baseline.bottleneck_link} (>= {threshold:.0f} %). Path {rec.name} gives "
                          f"MLU {rec.mlu:.1f} % (bottleneck {rec.bottleneck_link}, "
                          f"available {rec.bottleneck_available:.2f} Mb/s) at +{rec.cost - baseline.cost:g} "
                          f"IGP cost and +{rec.delay_ms - baseline.delay_ms:.1f} ms assumed delay.")
            if rec.congested:
                warnings.append(f"No single path keeps utilisation below {threshold:.0f} %.")
    if not rec.feasible:
        warnings.append(f"Recommended path cannot carry {demand:.2f} Mb/s "
                        f"(bottleneck available {rec.bottleneck_available:.2f} Mb/s).")
    if len(cands) >= 2 and (rec.congested or not rec.feasible):
        split = best_split(topo, by_mlu[0], by_mlu[1], demand, threshold)
        warnings.append(f"Load split {split.fractions[0]*100:.0f}/{split.fractions[1]*100:.0f} over "
                        f"{split.paths[0].name} / {split.paths[1].name} would give MLU {split.mlu:.1f} %.")

    before = link_states(topo, threshold, _placement(baseline))
    after = link_states(topo, threshold, _placement(rec))
    return TEResult(src, dst, demand, threshold, policy, cands, baseline, rec, reason,
                    before, after, split, warnings)
