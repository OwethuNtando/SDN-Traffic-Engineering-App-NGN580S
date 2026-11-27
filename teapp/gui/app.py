"""Main window of the NGN580S TE network-management application (Python / Tkinter).

Simple layout:
  * status strip   - ODL, BGP R1-ODL, PCEP sessions, GNS3, last refresh, Refresh / Auto / ODL login
  * left           - live topology (click a router or link for details / what-if)
  * right, 3 tabs  - 1 Traffic engineering (T1-T3), 2 LSPs (add / remove), 3 History & tools
  * bottom         - event log (also written to logs/audit.jsonl)

Threading: every network call runs in a worker thread (run_bg); results come back
through a queue that the Tk main loop drains every 100 ms, so the GUI never freezes.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from .history_view import HistoryView
from .topology_view import TopologyView

FONT = ("Segoe UI", 10)
BOLD = ("Segoe UI", 10, "bold")
BAR = "#1f2a3a"
GREEN, AMBER, RED, GREY, BLUE = "#2e7d32", "#b26a00", "#c62828", "#607d8b", "#1565c0"
LEVEL_COL = {"INFO": "#333333", "OK": GREEN, "WARN": AMBER, "ERROR": RED}
STATE_COL = {"OPERATIONAL": GREEN, "WITHDRAWN": GREEN, "ACCEPTED": BLUE, "REQUESTED": BLUE,
             "REPORTED": AMBER, "INTENDED": "#555", "WITHDRAW_REQUESTED": BLUE, "SYNC_REQUESTED": BLUE, "SYNC_ACCEPTED": BLUE, "SYNCED": GREEN,
             "FAILED": RED, "TIMEOUT": RED, "WITHDRAW_UNVERIFIED": RED}


def bgp_established(state: str) -> bool:
    return str(state).upper().startswith("ESTABLISHED")


class App(tk.Tk):
    def __init__(self, service):
        super().__init__()
        self.svc = service
        self.q: queue.Queue = queue.Queue()
        self.busy = False
        self.auto_job = None
        self._api_key = None
        self.sel_link = None
        self.title(f"NGN580S SDN TE Manager - student {service.student.student_number}"
                   + ("  [OFFLINE SIMULATION]" if service.simulated else "  [LIVE]"))
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{min(1500, sw - 20)}x{min(930, sh - 80)}+5+5")
        self.minsize(1100, 650)
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("Treeview", rowheight=22, font=("Segoe UI", 9))
        st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        st.configure("TNotebook.Tab", font=BOLD, padding=(14, 5))
        self.svc.log.listeners.append(lambda ev: self.q.put(("event", ev)))

        self._build_statusbar()
        self._build_eventlog()                         # packed at the bottom, fixed height
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        self.topo = TopologyView(body, service, self.on_select, width=620)
        body.add(self.topo, weight=5)
        self.nb = ttk.Notebook(body)
        body.add(self.nb, weight=6)
        self._build_te_tab()
        self._build_lsp_tab()
        self._build_tools_tab()

        for ev in list(self.svc.log.events)[-200:]:
            self._append_event(ev)
        self.after(100, self._drain)
        self.after(1000, self._tick)
        if not service.simulated and getattr(service.adapter, "s", None) is not None \
                and service.adapter.s.auth is None:
            self.after(400, self.ask_credentials)
        else:
            self.after(200, self.do_refresh)

    # ================================================================ widgets
    @staticmethod
    def _btn(parent, text, cmd, bg="#e3e8ef", fg="black", **kw):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=bg,
                         font=BOLD, relief="raised", bd=1, padx=10, pady=3, cursor="hand2", **kw)

    def _chip(self, parent, text):
        lbl = tk.Label(parent, text=text, bg=GREY, fg="white", font=("Segoe UI", 9, "bold"), padx=8, pady=3)
        lbl.pack(side="left", padx=3, pady=6)
        return lbl

    def _tree(self, parent, cols, widths, height=6):
        frame = ttk.Frame(parent)
        tv = ttk.Treeview(frame, columns=cols, show="headings", height=height)
        for c, wd in zip(cols, widths):
            tv.heading(c, text=c)
            tv.column(c, width=wd, anchor="w", stretch=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, col in (("bad", "#ffe3e0"), ("warn", "#fff4d6"), ("good", "#e3f6e8"),
                         ("rec", "#dbeafe"), ("down", "#eeeeee")):
            tv.tag_configure(tag, background=col)
        return frame, tv

    # ================================================================ layout
    def _build_statusbar(self):
        bar = tk.Frame(self, bg=BAR)
        bar.pack(fill="x")
        sim = self.svc.simulated
        tk.Label(bar, text=" OFFLINE SIMULATION " if sim else " LIVE: GA5 network ",
                 bg=RED if sim else GREEN, fg="white", font=("Segoe UI", 11, "bold"),
                 padx=8, pady=4).pack(side="left", padx=6, pady=6)
        self.chip_odl = self._chip(bar, "ODL: -")
        self.chip_bgp = self._chip(bar, "BGP R1-ODL: -")
        self.chip_pcep = self._chip(bar, "PCEP: -")
        self.chip_gns3 = self._chip(bar, "GNS3: -")
        self.lbl_time = tk.Label(bar, text="Last refresh: never", bg=BAR, fg="#cfd8e3", font=("Segoe UI", 9))
        self.lbl_time.pack(side="left", padx=8)
        self.lbl_busy = tk.Label(bar, text="", bg=BAR, fg="#ffd54f", font=("Segoe UI", 9, "bold"))
        self.lbl_busy.pack(side="left", padx=6)

        right = tk.Frame(bar, bg=BAR)
        right.pack(side="right", padx=6)
        if not sim:
            self._btn(right, "ODL login", self.ask_credentials, bg="#ffd54f").pack(side="left", padx=3)
        self._btn(right, "Refresh", self.do_refresh, bg="#90caf9").pack(side="left", padx=3)
        self.var_auto = tk.BooleanVar(value=False)
        tk.Checkbutton(right, text="Auto every", variable=self.var_auto, command=self._auto_changed,
                       bg=BAR, fg="white", selectcolor=BAR, activebackground=BAR, activeforeground="white",
                       font=("Segoe UI", 9)).pack(side="left", padx=(10, 2))
        self.var_auto_s = tk.IntVar(value=int(self.svc.cfg["gui"].get("auto_refresh_s", 10)))
        ttk.Spinbox(right, from_=3, to=300, width=4, textvariable=self.var_auto_s).pack(side="left")
        tk.Label(right, text="s", bg=BAR, fg="white").pack(side="left", padx=(2, 4))

    def _build_te_tab(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="1  Traffic engineering")
        sp = self.svc.student
        f = ttk.LabelFrame(tab, text=f"Demand {self.svc.src} -> {self.svc.dst}", padding=6)
        f.pack(fill="x")
        self.var_demand = tk.StringVar(value=f"{self.svc.demand:g}")
        self.var_thr = tk.StringVar(value=f"{self.svc.threshold:g}")
        ttk.Label(f, text="Demand (Mb/s)", font=FONT).grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.var_demand, width=8, font=FONT).grid(row=0, column=1, padx=4)
        self.lbl_demand_prov = ttk.Label(f, text="", foreground="#666")
        self.lbl_demand_prov.grid(row=0, column=2, sticky="w")
        ttk.Label(f, text="Threshold (%)", font=FONT).grid(row=0, column=3, sticky="w", padx=(14, 0))
        ttk.Entry(f, textvariable=self.var_thr, width=5, font=FONT).grid(row=0, column=4, padx=4)
        b = ttk.Frame(f)
        b.grid(row=1, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self._btn(b, f"T1 baseline ({sp.baseline_demand_mbps:g})",
                  lambda: self.preset(sp.baseline_demand_mbps)).pack(side="left", padx=2)
        self._btn(b, f"T2 congestion ({sp.congestion_demand_mbps:g})",
                  lambda: self.preset(sp.congestion_demand_mbps), bg="#ffccbc").pack(side="left", padx=2)
        self._btn(b, "Calculate", self.do_recompute, bg="#90caf9").pack(side="left", padx=2)
        ttk.Label(f, text="Rule: keep the shortest path while it is under the threshold; otherwise use the path "
                          "with the lowest max link utilisation (MLU).", foreground="#555",
                  wraplength=560).grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))

        lf = ttk.LabelFrame(tab, text="Link utilisation with the demand on the shortest path", padding=4)
        lf.pack(fill="both", expand=True, pady=(8, 0))
        fr, self.tv_links = self._tree(lf, ("link", "direction", "capacity Mb/s", "load Mb/s", "util %",
                                            "status", "data source"), (50, 70, 85, 75, 60, 100, 150), 6)
        fr.pack(fill="both", expand=True)

        pf = ttk.LabelFrame(tab, text="Path comparison: direct R1-R3 vs R1-R2-R3", padding=4)
        pf.pack(fill="x", pady=(8, 0))
        fr, self.tv_paths = self._tree(pf, ("path", "IGP cost", "delay ms", "free on bottleneck", "MLU %",
                                            "feasible", "verdict"), (90, 60, 60, 115, 60, 60, 170), 2)
        fr.pack(fill="x")

        self.txt_reason = tk.Text(tab, height=4, wrap="word", font=FONT, bg="#f6f8fb", relief="flat")
        self.txt_reason.pack(fill="x", pady=(8, 0))
        self.txt_reason.tag_configure("good", foreground=GREEN, font=BOLD)
        self.txt_reason.tag_configure("bad", foreground=RED, font=BOLD)

    def _build_lsp_tab(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="2  LSPs (add / remove)")
        f = ttk.LabelFrame(tab, text="Add a PCE-initiated LSP through OpenDaylight", padding=6)
        f.pack(fill="x")
        ttk.Label(f, text="Path", font=FONT).grid(row=0, column=0, sticky="w")
        self.var_path = tk.StringVar()
        self.cb_path = ttk.Combobox(f, textvariable=self.var_path, state="readonly", width=34, font=FONT)
        self.cb_path.grid(row=0, column=1, sticky="w", padx=4)
        self.cb_path.bind("<<ComboboxSelected>>", lambda _e: self._path_changed())
        self._btn(f, "Use TE proposal", self.use_te_proposal).grid(row=0, column=2, padx=4)
        ttk.Label(f, text="LSP name", font=FONT).grid(row=1, column=0, sticky="w", pady=4)
        self.var_name = tk.StringVar()
        ttk.Entry(f, textvariable=self.var_name, width=36, font=FONT).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(f, text=f"rule: {self.svc.student.lsp_prefix}-<HEAD>-<TAIL>-<PATH>",
                  foreground="#555").grid(row=1, column=2, sticky="w")
        self.lbl_ero = ttk.Label(f, text="ERO: -", foreground="#555")
        self.lbl_ero.grid(row=2, column=0, columnspan=3, sticky="w")
        self.btn_add = self._btn(f, "ADD LSP", self.do_provision, bg="#43a047", fg="white")
        self.btn_add.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        inv = ttk.LabelFrame(tab, text="LSPs in the controller (PCEP LSP database) - click one to draw its path",
                             padding=4)
        inv.pack(fill="both", expand=True, pady=(8, 0))
        fr, self.tv_lsps = self._tree(inv, ("head-end", "name", "state", "delegated", "path",
                                            "hops (from controller)"), (125, 205, 50, 70, 70, 220), 5)
        fr.pack(fill="both", expand=True)
        self.tv_lsps.bind("<<TreeviewSelect>>", self._lsp_selected)
        bb = ttk.Frame(inv)
        bb.pack(fill="x", pady=(4, 0))
        self.btn_del = self._btn(bb, "WITHDRAW / REMOVE SELECTED LSP", self.do_teardown, bg="#e53935", fg="white")
        self.btn_del.pack(side="left", padx=2)
        self.btn_sync = self._btn(bb, "RE-SYNC LSPs", self.do_resync, bg="#ffb74d")
        self.btn_sync.pack(side="left", padx=6)
        ttk.Label(bb, text="Only an LSP that is UP can be withdrawn. Re-sync clears abandoned (down) tunnels.",
                  foreground="#777").pack(side="left", padx=4)

        of = ttk.LabelFrame(tab, text="Progress: requested -> accepted -> reported -> OPERATIONAL / WITHDRAWN",
                            padding=4)
        of.pack(fill="both", expand=True, pady=(8, 0))
        self.txt_op = tk.Text(of, height=8, wrap="word", font=("Consolas", 9))
        self.txt_op.pack(fill="both", expand=True)
        for s, col in STATE_COL.items():
            self.txt_op.tag_configure(s, foreground=col)
        if self.svc.simulated:
            ff = ttk.Frame(tab)
            ff.pack(fill="x")
            ttk.Label(ff, text="Simulated fault (T6):").pack(side="left")
            self.var_fail = tk.StringVar(value="none")
            cbf = ttk.Combobox(ff, textvariable=self.var_fail, values=["none", "unreachable", "reject"],
                               state="readonly", width=11)
            cbf.pack(side="left", padx=2)
            cbf.bind("<<ComboboxSelected>>", lambda _e: self._set_fail())

    def _build_tools_tab(self):
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="3  History & tools")
        sp = self.svc.student
        tk.Label(tab, anchor="w", justify="left", font=("Consolas", 8), fg="#444",
                 text=f"Student {sp.student_number}: last4 3625 -> LSP prefix {sp.lsp_prefix}, "
                      f"T1 {sp.baseline_demand_mbps:g} Mb/s, T2 {sp.congestion_demand_mbps:g} Mb/s, "
                      f"threshold {self.svc.threshold:g} %, AS 4200003625, RIB bgp-3625"
                 ).pack(side="bottom", fill="x", pady=(4, 0))
        self.hist = HistoryView(tab, self.svc, height=190)
        self.hist.pack(fill="x")
        b = ttk.Frame(tab)
        b.pack(fill="x", pady=4)
        self._btn(b, "Export CSV", self.do_export).pack(side="left", padx=2)
        ttk.Label(b, text="   Load data:").pack(side="left")
        self.var_source = tk.StringVar(value=self.svc.telemetry_source)
        cb = ttk.Combobox(b, textvariable=self.var_source, values=["declared", "replay", "cli"],
                          state="readonly", width=10)
        cb.pack(side="left", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda _e: self.change_source())
        self._btn(b, "Replay next step", self.replay_step).pack(side="left", padx=4)
        self.lbl_export = ttk.Label(b, text="", foreground="#555")
        self.lbl_export.pack(side="left", padx=8)

        af = ttk.LabelFrame(tab, text="API log: every request to OpenDaylight (click to see the body)", padding=4)
        af.pack(fill="both", expand=True, pady=(6, 0))
        fr, self.tv_api = self._tree(af, ("time", "method", "url", "status", "ms"), (65, 50, 420, 50, 45), 5)
        fr.pack(fill="both", expand=True)
        self.tv_api.bind("<<TreeviewSelect>>", self._api_selected)
        self.txt_api = tk.Text(af, height=9, wrap="none", font=("Consolas", 8))
        self.txt_api.pack(fill="both", expand=True, pady=(4, 0))


    def _build_eventlog(self):
        f = ttk.LabelFrame(self, text="Event log (also saved in logs/audit.jsonl)", padding=2)
        f.pack(side="bottom", fill="x")
        self.txt_log = tk.Text(f, height=6, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(f, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for lvl, col in LEVEL_COL.items():
            self.txt_log.tag_configure(lvl, foreground=col)

    # ============================================================ threading
    def run_bg(self, label, fn, done=None):
        if self.busy:
            self.lbl_busy.config(text=f"busy: {self._busy_label} - wait")
            return
        self.busy = True
        self._busy_label = label
        self.lbl_busy.config(text=f"... {label}")

        def work():
            try:
                res, err = fn(), None
            except Exception as exc:   # report, never crash the GUI
                res, err = None, exc
            self.q.put(("done", (done, res, err, label)))
        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "event":
                    self._append_event(payload)
                elif kind == "op":
                    self._show_op(payload)
                elif kind == "done":
                    done, res, err, label = payload
                    self.busy = False
                    self.lbl_busy.config(text="")
                    if err is not None:
                        self.svc.log.add("ERROR", "gui", f"{label} failed: {type(err).__name__}: {err}")
                    elif done:
                        done(res)
                    self.render()
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _tick(self):
        if self.svc.last_refresh:
            age = time.time() - self.svc.last_refresh
            stamp = time.strftime("%H:%M:%S", time.localtime(self.svc.last_refresh))
            self.lbl_time.config(text=f"Last refresh {stamp} ({age:.0f}s ago)",
                                 fg="#ffab91" if age > 3 * max(self.var_auto_s.get(), 5) else "#cfd8e3")
        self.after(1000, self._tick)

    def _auto_changed(self):
        if self.auto_job:
            self.after_cancel(self.auto_job)
            self.auto_job = None
        if self.var_auto.get():
            self._auto_fire()

    def _auto_fire(self):
        if not self.busy:
            self.do_refresh()
        try:
            secs = max(3, int(self.var_auto_s.get()))
        except (tk.TclError, ValueError):
            secs = 10
        self.auto_job = self.after(secs * 1000, self._auto_fire)

    # ============================================================== actions
    def do_refresh(self):
        self.run_bg("refreshing", self.svc.refresh)

    def _read_inputs(self) -> bool:
        try:
            self.svc.set_inputs(float(self.var_demand.get()), float(self.var_thr.get()), self.svc.policy)
            return True
        except ValueError as exc:
            messagebox.showerror("Invalid input", f"{exc}\nDemand: Mb/s greater than 0; threshold: 1-100 %.")
            return False

    def do_recompute(self):
        if self._read_inputs():
            self.svc.recompute()
            self.render()

    def preset(self, demand):
        self.var_demand.set(f"{demand:g}")
        self.do_recompute()

    def replay_step(self):
        try:
            if self.svc.telemetry_source != "replay":
                self.svc.set_telemetry_source("replay")
                self.var_source.set("replay")
            self.svc.replay_next()
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Replay", f"Cannot load replay profile: {exc}")
            return
        self.var_demand.set(f"{self.svc.demand:g}")
        self.svc.recompute()
        self.render()

    def change_source(self):
        try:
            self.svc.set_telemetry_source(self.var_source.get())
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("Load data", str(exc))
            self.var_source.set(self.svc.telemetry_source)
            return
        self.do_refresh()

    def ask_credentials(self):
        u = simpledialog.askstring("OpenDaylight login", "Username:", parent=self)
        if u is None:
            self.do_refresh()
            return
        p = simpledialog.askstring("OpenDaylight login", "Password (kept in memory only):", show="*", parent=self)
        if p is None:
            self.do_refresh()
            return
        self.svc.adapter.set_credentials(u, p)
        self.svc.log.add("INFO", "controller", "Credentials updated for this session (not stored)")
        self.do_refresh()

    def _set_fail(self):
        self.svc.adapter.fail_mode = self.var_fail.get()
        self.svc.log.add("WARN", "sim", f"Simulated controller fault set to '{self.var_fail.get()}'")

    # ---------------------------------------------------------------- LSPs
    def _path_nodes(self):
        res = self.svc.te_result
        sel = self.var_path.get().split("  ")[0]
        for c in (res.candidates if res else []):
            if c.name == sel:
                return c.nodes
        return None

    def _path_changed(self):
        nodes = self._path_nodes()
        if nodes:
            self.var_name.set(self.svc.lsp_name_for(nodes))
            try:
                self.lbl_ero.config(text=f"Strict ERO sent to R1: {' -> '.join(self.svc.lsp.ero_for(nodes))}")
            except ValueError as exc:
                self.lbl_ero.config(text=f"ERO: {exc}")

    def use_te_proposal(self):
        res = self.svc.te_result
        if res and res.recommended:
            for v in self.cb_path["values"]:
                if v.startswith(res.recommended.name + "  "):
                    self.var_path.set(v)
            self._path_changed()

    def do_provision(self):
        nodes = self._path_nodes()
        if not nodes:
            messagebox.showerror("Add LSP", "Choose a path first.")
            return
        name = self.var_name.get().strip()
        problems = self.svc.lsp.validate(name, nodes, self.svc.snapshot)
        if self.svc.snapshot is None:
            problems.append("No successful controller read yet - press Refresh first")
        if problems:
            messagebox.showerror("Add LSP - request NOT sent", "\n".join(problems))
            self.svc.log.add("WARN", "lsp", "Add blocked by validation: " + "; ".join(problems))
            return
        if not name.startswith(self.svc.student.lsp_prefix):
            if not messagebox.askyesno("Naming rule", f"{name} does not follow the naming rule "
                                                      f"({self.svc.student.lsp_prefix}-...). Continue?"):
                return
        self.txt_op.delete("1.0", "end")
        self.run_bg(f"adding {name}",
                    lambda: self.svc.provision(name, nodes, lambda op: self.q.put(("op", op))),
                    lambda op: self._op_finished(op))

    def do_teardown(self):
        sel = self.tv_lsps.selection()
        if not sel:
            messagebox.showerror("Remove LSP", "Click an LSP in the table first.")
            return
        vals = self.tv_lsps.item(sel[0], "values")
        pcc, name, state = vals[0], vals[1], vals[2]
        if str(state).lower() not in ("up", "active"):
            messagebox.showwarning("Withdraw LSP", f"{name} is '{state}', not up.\nA tunnel that is down cannot be "
                                                   f"withdrawn. Press RE-SYNC LSPs first, then try again.")
            self.svc.log.add("WARN", "lsp", f"Withdraw blocked: {name} is {state} (not up) - re-sync first")
            return
        if not name.startswith(self.svc.student.lsp_prefix):
            if not messagebox.askyesno("Remove LSP", f"{name} was not created by this application (PCE). "
                                                     f"The router may refuse to remove it. Send anyway?"):
                return
        elif not messagebox.askyesno("Withdraw LSP", f"Send remove-lsp to withdraw {name} from {pcc}?\n"
                                                     f"Success is only shown when it is gone from the LSP database."):
            return
        self.txt_op.delete("1.0", "end")
        self.run_bg(f"removing {name}",
                    lambda: self.svc.teardown(pcc, name, lambda op: self.q.put(("op", op))),
                    lambda op: self._op_finished(op))

    def do_resync(self):
        pcc = self.svc.topo.nodes[self.svc.src].pcc_id
        if not messagebox.askyesno("Re-sync LSPs", f"Ask {pcc} to re-send its LSP state to the controller "
                                                   f"(trigger-sync)?\nThe app will show which LSPs were removed or "
                                                   f"changed."):
            return
        self.txt_op.delete("1.0", "end")
        self.run_bg("re-syncing LSPs",
                    lambda: self.svc.resync(pcc, lambda op: self.q.put(("op", op))),
                    lambda op: self._op_finished(op))

    def _show_op(self, op):
        self.txt_op.delete("1.0", "end")
        tag = "SIMULATED " if op.simulated else ""
        self.txt_op.insert("end", f"{tag}{ {'provision': 'ADD', 'teardown': 'WITHDRAW'}.get(op.kind, 'RE-SYNC') } {op.name} @ {op.pcc}\n")
        for s in op.steps:
            self.txt_op.insert("end", f"{time.strftime('%H:%M:%S', time.localtime(s.ts))}  {s.state:<19} ", s.state)
            self.txt_op.insert("end", s.message + "\n")
        self.txt_op.see("end")

    def _op_finished(self, op):
        self._show_op(op)
        final = {"OPERATIONAL": "LSP is UP - verified in the controller LSP database",
                 "WITHDRAWN": "LSP is GONE - verified absent from the controller LSP database",
                 "SYNCED": "Re-sync done - LSP list above now shows the state reported by the router"}.get(op.state)
        if op.simulated and final:
            final += " (SIMULATED - not network evidence)"
        if final:
            self.txt_op.insert("end", "\nRESULT: " + final + "\n", op.state)
        else:
            self.txt_op.insert("end", f"\nRESULT: NOT successful ({op.state}). No success is claimed.\n", "FAILED")
        self.txt_op.see("end")

    def _lsp_selected(self, _e=None):
        sel = self.tv_lsps.selection()
        if sel:
            v = self.tv_lsps.item(sel[0], "values")
            self.svc.selected_lsp = (v[0], v[1])
            self.topo.redraw()

    # ------------------------------------------------ node / link pop-up
    def on_select(self, kind, ident):
        svc = self.svc
        win = tk.Toplevel(self)
        win.title(f"{'Router' if kind == 'node' else 'Link'} {ident}")
        win.transient(self)
        txt = tk.Text(win, width=78, height=14, font=("Consolas", 9), bg="#f6f8fb", relief="flat")
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        if kind == "node":
            n = svc.topo.nodes[ident]
            txt.insert("end", f"ROUTER {n.name}\n  Loopback0     {n.loopback}\n  PCC id        {n.pcc_id}\n"
                              f"  Management IP {n.mgmt_ip}\n  PCEP          {n.pcep_state}\n"
                              f"  BGP to ODL    {n.bgp_state if n.name == svc.src else 'not a BGP peer of ODL'}\n"
                              f"  GNS3 status   {n.gns3_status or 'not read'}\n\n  Interfaces:\n")
            for l in svc.topo.links:
                if ident in l.ends():
                    txt.insert("end", f"    {l.id} to {l.other(ident)}: {l.if_of(ident)} {l.ip_of(ident)}"
                                      f"  ({'up' if l.up else 'DOWN what-if'})\n")
            if svc.snapshot:
                lsps = [x for x in svc.snapshot.all_lsps() if x.pcc == n.pcc_id]
                txt.insert("end", f"\n  LSPs with this head-end: {len(lsps)}\n")
                for x in lsps:
                    txt.insert("end", f"    {x.name}: {x.operational}\n")
        else:
            l = svc.topo.link(ident)
            txt.insert("end", f"LINK {l.id}: {l.a} {l.a_if} {l.a_ip}  <->  {l.b} {l.b_if} {l.b_ip}\n"
                              f"  capacity {l.capacity_mbps:g} Mb/s [DECLARED], IGP cost {l.igp_cost:g}, "
                              f"delay {l.delay_ms:g} ms [ASSUMED]\n"
                              f"  state {'up' if l.up else 'DOWN (what-if)'}, GNS3 link: {l.gns3 or 'not read'}\n\n")
            for u, v in ((l.a, l.b), (l.b, l.a)):
                ld = l.load(u, v)
                txt.insert("end", f"  {u}>{v}: background {ld.mbps:.3f} Mb/s [{ld.prov.value}] "
                                  f"@ {time.strftime('%H:%M:%S', time.localtime(ld.ts))}\n")

            def toggle():
                self.svc.set_link_up(ident, not l.up)
                self.svc.recompute()
                self.render()
                win.destroy()
            self._btn(win, "What-if: restore link" if not l.up else "What-if: fail this link",
                      toggle, bg="#ffe0b2").pack(side="left", padx=6, pady=(0, 6))
            ttk.Label(win, text="What-if only changes the TE calculation, not the routers.",
                      foreground="#777").pack(side="left", pady=(0, 6))
        txt.configure(state="disabled")
        self._btn(win, "Close", win.destroy).pack(side="right", padx=6, pady=(0, 6))

    # -------------------------------------------------------------- export
    def do_export(self):
        try:
            p = self.svc.export()
            self.lbl_export.config(text=f"Saved {p}")
        except OSError as exc:
            messagebox.showerror("Export", str(exc))

    def _api_selected(self, _e=None):
        sel = self.tv_api.selection()
        if not sel:
            return
        rec = self.svc.adapter.history[int(sel[0])]
        self.txt_api.delete("1.0", "end")
        self.txt_api.insert("end", f"{rec.method} {rec.url}\nstatus={rec.status} {rec.elapsed_ms:.0f} ms "
                                   f"{'SIMULATED' if rec.simulated else ''}{('  ERROR: ' + rec.error) if rec.error else ''}"
                                   f"\n--- request ---\n{rec.request_body}\n--- response ---\n")
        body = rec.response_body
        try:
            body = json.dumps(json.loads(body), indent=2)
        except (ValueError, TypeError):
            pass
        self.txt_api.insert("end", (body or "")[:60000])

    # ============================================================== render
    def _append_event(self, ev):
        self.txt_log.insert("end", ev.line() + "\n", ev.level)
        lines = int(self.txt_log.index("end-1c").split(".")[0])
        if lines > 500:
            self.txt_log.delete("1.0", f"{lines - 500}.0")
        self.txt_log.see("end")

    @staticmethod
    def _set_chip(chip, text, colour):
        chip.config(text=text, bg=colour)

    def render(self):
        svc = self.svc
        ok = svc.snapshot is not None
        # ---- status strip
        self._set_chip(self.chip_odl, f"ODL: {svc.controller_status}",
                       GREEN if ok and not svc.last_error else (AMBER if ok else RED))
        r1 = svc.topo.nodes.get(svc.src)
        bgp = r1.bgp_state if (r1 and ok) else "unknown"
        short = bgp.split(" (")[0]
        self._set_chip(self.chip_bgp, f"BGP R1-ODL: {short}", GREEN if bgp_established(bgp) else (RED if ok else GREY))
        n_sync = sum(1 for n in svc.topo.nodes.values() if n.pcep_state.lower() == "synchronized")
        n_all = len(svc.topo.nodes)
        self._set_chip(self.chip_pcep, f"PCEP: {n_sync}/{n_all} synced",
                       GREEN if n_sync == n_all else (AMBER if n_sync else (RED if ok else GREY)))
        g = svc.gns3_snapshot
        if g is not None:
            started = sum(1 for n in svc.topo.nodes.values() if n.gns3_status == "started")
            self._set_chip(self.chip_gns3, f"GNS3: {started}/{n_all} started" if g.ok else "GNS3: not read",
                           GREEN if g.ok and started == n_all else AMBER)

        # ---- TE tab
        self.var_demand.set(f"{svc.demand:g}")
        self.lbl_demand_prov.config(text=f"[{svc.demand_prov.value}]")
        res = svc.te_result
        tv = self.tv_links
        tv.delete(*tv.get_children())
        for s in (res.before_links if res else svc.link_states_now()):
            tag = "down" if not s.up else ("bad" if s.status != "OK" else
                                           ("warn" if s.util_pct >= 0.8 * s.threshold else "good"))
            tv.insert("", "end", values=(s.link_id, f"{s.u}>{s.v}", f"{s.capacity:g}", f"{s.load:.3f}",
                                         f"{s.util_pct:.1f}", s.status, s.prov), tags=(tag,))
        tp = self.tv_paths
        tp.delete(*tp.get_children())
        values = []
        if res:
            for c in res.candidates:
                verdict = []
                if c is res.baseline:
                    verdict.append("shortest")
                if c.congested:
                    verdict.append("congested")
                if c is res.recommended:
                    verdict.append("RECOMMENDED")
                tag = "rec" if c is res.recommended else ("bad" if c.congested else "")
                tp.insert("", "end", values=(c.name, f"{c.cost:g}", f"{c.delay_ms:g}",
                                             f"{c.bottleneck_available:.2f} Mb/s", f"{c.mlu:.1f}",
                                             "yes" if c.feasible else "NO", ", ".join(verdict)), tags=(tag,))
                values.append(f"{c.name}  (cost {c.cost:g}, MLU {c.mlu:.1f}%)")
            r = self.txt_reason
            r.delete("1.0", "end")
            moved = res.recommended is not res.baseline
            r.insert("end", ("RE-ROUTE: " if moved else "NO CHANGE: "), "bad" if moved else "good")
            r.insert("end", res.reason + "\n")
            r.insert("end", f"MLU before {res.mlu_before:.1f} %  ->  after {res.mlu_after:.1f} %  "
                            f"({res.mlu_after - res.mlu_before:+.1f} points).")
            if moved:
                r.insert("end", "  Apply it with tab 2: 'Use TE proposal' then 'ADD LSP'.")
            for w in res.warnings:
                r.insert("end", "\nWARNING: " + w)
        cur = self.var_path.get()
        self.cb_path["values"] = values
        if values and not any(v.split("  ")[0] == cur.split("  ")[0] for v in values):
            self.var_path.set(values[0])
            self._path_changed()
        elif cur:
            for v in values:
                if v.split("  ")[0] == cur.split("  ")[0]:
                    self.var_path.set(v)

        # ---- LSP tab
        ti = self.tv_lsps
        keep = svc.selected_lsp
        ti.delete(*ti.get_children())
        if svc.snapshot:
            from ..lsp_manager import resolve_lsp_path
            for l in svc.snapshot.all_lsps():
                rp = resolve_lsp_path(svc.topo, l)
                p = l.primary
                hops = ("RRO " + ", ".join(p.rro)) if p.rro else ("ERO " + ", ".join(p.ero) if p.ero else "-")
                deleg = {True: "yes", False: "NO"}.get(p.delegate, "?")
                iid = ti.insert("", "end", values=(l.pcc, l.name, l.operational, deleg, "-".join(rp.nodes) or "?",
                                                   hops), tags=("good" if l.is_up else "warn",))
                if keep == (l.pcc, l.name):
                    ti.selection_set(iid)

        # ---- API log
        n = len(svc.adapter.history)
        key = (n, svc.adapter.history[-1].ts if n else 0)
        if key != self._api_key:
            self.tv_api.delete(*self.tv_api.get_children())
            for i, rec in enumerate(svc.adapter.history):
                self.tv_api.insert("", "end", iid=str(i), values=(
                    time.strftime("%H:%M:%S", time.localtime(rec.ts)), rec.method, rec.url,
                    rec.status if rec.status is not None else "ERR", f"{rec.elapsed_ms:.0f}"))
            if n:
                self.tv_api.see(str(n - 1))
            self._api_key = key

        self.topo.redraw()
        self.hist.redraw()


def main(service) -> None:
    App(service).mainloop()
