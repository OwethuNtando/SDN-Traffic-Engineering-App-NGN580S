"""Interactive topology canvas: nodes, full-mesh links, controller sessions,
utilisation colouring, TE proposal and the VERIFIED LSP overlay."""
from __future__ import annotations

import math
import time
import tkinter as tk

COL = {
    "ok": "#2e9d57", "warn": "#e0a100", "bad": "#d1352b", "down": "#9a9a9a",
    "node": "#1f4e79", "node_sel": "#f2b134", "odl": "#5b3c88", "te": "#1c7ed6",
    "lsp_up": "#8e24aa", "lsp_other": "#e67700", "text": "#1d1d1d", "bg": "#fbfbfd",
}


def util_colour(util: float, threshold: float, up: bool = True) -> str:
    if not up:
        return COL["down"]
    if util > 100 or util >= threshold:
        return COL["bad"]
    if util >= 0.8 * threshold:
        return COL["warn"]
    return COL["ok"]


class TopologyView(tk.Canvas):
    def __init__(self, master, service, on_select, **kw):
        super().__init__(master, bg=COL["bg"], highlightthickness=0, **kw)
        self.svc = service
        self.on_select = on_select
        self.selected = None        # ("node", "R1") | ("link", "L13")
        self.bind("<Configure>", lambda _e: self.redraw())
        self.tag_bind("clickable", "<Button-1>", self._click)

    # -------------------------------------------------------------- helpers
    def _xy(self, name: str) -> tuple:
        n = self.svc.topo.nodes[name]
        w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 200)
        return n.x * w, n.y * h

    @staticmethod
    def _offset(x1, y1, x2, y2, d):
        dx, dy = x2 - x1, y2 - y1
        ln = math.hypot(dx, dy) or 1
        ox, oy = -dy / ln * d, dx / ln * d
        return x1 + ox, y1 + oy, x2 + ox, y2 + oy

    def _click(self, _ev):
        item = self.find_withtag("current")
        if not item:
            return
        for t in self.gettags(item[0]):
            if t.startswith(("node:", "link:")):
                kind, ident = t.split(":", 1)
                self.selected = (kind, ident)
                self.on_select(kind, ident)
                self.redraw()
                return

    # ----------------------------------------------------------------- draw
    def redraw(self):
        self.delete("all")
        svc = self.svc
        w, h = max(self.winfo_width(), 200), max(self.winfo_height(), 200)
        res = svc.te_result
        thr = svc.threshold
        states = {(s.link_id, s.u, s.v): s for s in (res.before_links if res else svc.link_states_now())}

        # controller box + sessions
        ox, oy = w * 0.5, h * 0.09
        reach = svc.snapshot is not None
        self.create_rectangle(ox - 120, oy - 22, ox + 120, oy + 22, fill="#efe8f7",
                              outline=COL["odl"] if reach else COL["bad"], width=2)
        self.create_text(ox, oy - 6, text="OpenDaylight 0.20.2 (PCE)", font=("Segoe UI", 10, "bold"),
                         fill=COL["odl"])
        self.create_text(ox, oy + 10, text=svc.controller_status, font=("Segoe UI", 8),
                         fill=COL["text"] if reach else COL["bad"])
        for name, node in svc.topo.nodes.items():
            x, y = self._xy(name)
            sync = node.pcep_state.lower()
            colour = COL["ok"] if sync == "synchronized" else (COL["warn"] if reach and "no session" not in sync
                                                               else COL["bad"])
            self.create_line(ox, oy + 22, x, y - 26, fill=colour, dash=(5, 4), width=1.5)
            mx, my = (ox + x) / 2, (oy + 22 + y - 26) / 2
            self.create_text(mx + (12 if x > ox else -12), my, text=f"PCEP {node.pcep_state}",
                             fill=colour, font=("Segoe UI", 7), anchor="w" if x > ox else "e")
        if svc.src in svc.topo.nodes:
            x, y = self._xy(svc.src)
            state = svc.topo.nodes[svc.src].bgp_state if reach else "unknown (controller down)"
            bgp_up = str(state).upper().startswith("ESTABLISHED")
            bcol = "#2f9e44" if bgp_up else COL["bad"]
            self.create_line(ox - 110, oy + 22, x - 18, y - 22, fill=bcol, width=3 if bgp_up else 1.5,
                             dash=() if bgp_up else (4, 3))
            ly = (oy + 22 + y - 22) / 2
            lx = max(124, (ox - 110 + x - 18) / 2 - 8)
            label = "BGP ESTABLISHED" if bgp_up else f"BGP {state.split(' (')[0]}"
            self.create_rectangle(lx - 118, ly - 18, lx + 2, ly + 18, fill="#e8f5e9" if bgp_up else "#ffebee",
                                  outline=bcol)
            self.create_text(lx - 58, ly - 6, text=label, fill=bcol, font=("Segoe UI", 9, "bold"))
            self.create_text(lx - 58, ly + 8, text="R1 - ODL, AS 4200003625", fill=bcol, font=("Segoe UI", 7))

        # links (two directions each, offset)
        for link in svc.topo.links:
            (x1, y1), (x2, y2) = self._xy(link.a), self._xy(link.b)
            tag = ("clickable", f"link:{link.id}")
            sel = self.selected == ("link", link.id)
            if not link.up:
                self.create_line(x1, y1, x2, y2, fill=COL["down"], width=4, dash=(8, 6), tags=tag)
                self.create_text((x1 + x2) / 2, (y1 + y2) / 2, text="X  DOWN (what-if)", fill=COL["bad"],
                                 font=("Segoe UI", 9, "bold"), tags=tag)
                continue
            for (u, v), off in (((link.a, link.b), 5), ((link.b, link.a), 5)):
                s = states.get((link.id, u, v))
                util = s.util_pct if s else 0.0
                ax1, ay1, ax2, ay2 = self._offset(x1, y1, x2, y2, off) if u == link.a else \
                    self._offset(x2, y2, x1, y1, off)
                self.create_line(ax1, ay1, ax2, ay2, fill=util_colour(util, thr), width=6 if sel else 4,
                                 arrow="last", arrowshape=(10, 12, 4), tags=tag)
            # label
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            fwd = states.get((link.id, link.a, link.b))
            rev = states.get((link.id, link.b, link.a))
            lines = [f"{link.id}  {link.capacity_mbps:g} Mb/s  cost {link.igp_cost:g}"]
            for s in (fwd, rev):
                if s:
                    lines.append(f"{s.u}>{s.v} {s.util_pct:5.1f}%  [{s.prov}]")
            txt = "\n".join(lines)
            dy = -34 if abs(y1 - y2) < 30 else 0
            dx = 0 if abs(y1 - y2) < 30 else (-95 if mx < w / 2 else 95)
            self.create_rectangle(mx + dx - 92, my + dy - 24, mx + dx + 92, my + dy + 24, fill="white",
                                  outline="#c8c8c8" if not sel else COL["node_sel"], tags=tag)
            self.create_text(mx + dx, my + dy, text=txt, font=("Consolas", 8), fill=COL["text"], tags=tag)

        # TE proposal (computed) - thin dashed blue
        if res and res.recommended and res.baseline and res.recommended is not res.baseline:
            pts = []
            for n in res.recommended.nodes:
                pts.extend(self._xy(n))
            self.create_line(*pts, fill=COL["te"], width=2, dash=(3, 3), smooth=False)

        # verified LSP overlay - only from controller-reported path
        view = svc.selected_lsp_view()
        if view:
            lsp, rp = view
            colour = COL["lsp_up"] if lsp.is_up else COL["lsp_other"]
            for u, v in zip(rp.nodes, rp.nodes[1:]):
                (x1, y1), (x2, y2) = self._xy(u), self._xy(v)
                ax1, ay1, ax2, ay2 = self._offset(x1, y1, x2, y2, 14)
                self.create_line(ax1, ay1, ax2, ay2, fill=colour, width=5, arrow="last",
                                 arrowshape=(14, 16, 6))
            src = "SIMULATED " if svc.simulated else ""
            self.create_text(10, h - 74, anchor="w", fill=colour, font=("Segoe UI", 9, "bold"),
                             text=f"LSP {lsp.name}: operational={lsp.operational}")
            self.create_text(10, h - 58, anchor="w", fill=colour, font=("Segoe UI", 9),
                             text=f"path {'-'.join(rp.nodes) or '?'}  (from {src}controller {rp.source})")
            if rp.unresolved_hops:
                self.create_text(10, h - 42, anchor="w", fill=COL["bad"], font=("Segoe UI", 8),
                                 text=f"Unresolved hops (not in topology config): {rp.unresolved_hops}")
        elif svc.selected_lsp:
            self.create_text(10, h - 58, anchor="w", fill=COL["bad"], font=("Segoe UI", 9, "bold"),
                             text=f"Selected LSP {svc.selected_lsp[1]} NOT present in last controller read")

        # nodes
        for name, node in svc.topo.nodes.items():
            x, y = self._xy(name)
            sel = self.selected == ("node", name)
            tag = ("clickable", f"node:{name}")
            self.create_oval(x - 26, y - 26, x + 26, y + 26, fill=COL["node"],
                             outline=COL["node_sel"] if sel else "white", width=4 if sel else 2, tags=tag)
            self.create_text(x, y - 4, text=name, fill="white", font=("Segoe UI", 12, "bold"), tags=tag)
            self.create_text(x, y + 11, text=node.loopback, fill="white", font=("Segoe UI", 6), tags=tag)
            if node.gns3_status:
                gc = COL["ok"] if node.gns3_status == "started" else COL["bad"]
                self.create_oval(x + 18, y - 26, x + 28, y - 16, fill=gc, outline="white", tags=tag)
                self.create_text(x, y + 36, text=f"GNS3: {node.gns3_status}", fill=gc,
                                 font=("Segoe UI", 8, "bold"), tags=tag)

        # topology source header
        g = svc.gns3_snapshot
        if g is not None and g.ok:
            src = (f"Topology LIVE from GNS3 project '{g.project}' "
                   f"@ {time.strftime('%H:%M:%S', time.localtime(g.ts))}")
            col = COL["ok"]
        elif g is not None:
            src, col = f"GNS3 read FAILED ({g.error}) - showing config topology", COL["bad"]
        else:
            src, col = "Topology from config file (GNS3 source disabled)", "#666"
        self.create_text(w - 8, h - 40, text=src, anchor="e", fill=col, font=("Segoe UI", 8, "bold"))
        for link in svc.topo.links:
            if link.gns3 == "absent":
                (x1, y1), (x2, y2) = self._xy(link.a), self._xy(link.b)
                self.create_text((x1 + x2) / 2, (y1 + y2) / 2 + 30, text="not in GNS3 project",
                                 fill=COL["bad"], font=("Segoe UI", 8, "italic"))

        # legend
        lx, ly = 10, h - 22
        for label, c in (("< 80% of T", COL["ok"]), ("approaching T", COL["warn"]),
                         (f">= T ({thr:.0f}%)", COL["bad"]), ("TE proposal", COL["te"]),
                         ("verified LSP", COL["lsp_up"])):
            self.create_rectangle(lx, ly, lx + 14, ly + 10, fill=c, outline="")
            self.create_text(lx + 18, ly + 5, text=label, anchor="w", font=("Segoe UI", 8))
            lx += 110
