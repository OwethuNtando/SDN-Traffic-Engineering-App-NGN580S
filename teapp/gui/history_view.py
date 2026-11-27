"""Utilisation history graph (Tk canvas, no extra dependency)."""
from __future__ import annotations

import time
import tkinter as tk

PALETTE = ["#d1352b", "#1c7ed6", "#2e9d57", "#8e24aa", "#e67700", "#0b7285"]


class HistoryView(tk.Canvas):
    def __init__(self, master, service, **kw):
        super().__init__(master, bg="white", highlightthickness=0, **kw)
        self.svc = service
        self.keys = []          # series shown; empty = default (forward direction of every link)
        self.bind("<Configure>", lambda _e: self.redraw())

    def redraw(self):
        self.delete("all")
        hist = self.svc.history
        w, h = max(self.winfo_width(), 300), max(self.winfo_height(), 200)
        L, R, T, B = 50, 150, 20, 30
        keys = self.keys or [k for k in hist.keys() if k.split()[1].split(">")[0] < k.split()[1].split(">")[1]]
        samples = list(hist.samples)
        self.create_text(L, 8, anchor="w", font=("Segoe UI", 9, "bold"),
                         text="Utilisation history (%), demand placed on IGP path - each point is one refresh/recompute")
        if len(samples) < 1:
            self.create_text(w / 2, h / 2, text="No samples yet - press Refresh", fill="#888")
            return
        ymax = max(110.0, max((d[k][0] for _, d in samples for k in keys if k in d), default=0) * 1.1)
        t0, t1 = samples[0][0], samples[-1][0]
        span = max(t1 - t0, 1.0)

        def X(t):
            return L + (t - t0) / span * (w - L - R)

        def Y(u):
            return h - B - u / ymax * (h - T - B)

        for u in range(0, int(ymax) + 1, 25):
            self.create_line(L, Y(u), w - R, Y(u), fill="#eee")
            self.create_text(L - 6, Y(u), text=f"{u}", anchor="e", font=("Segoe UI", 8))
        thr = self.svc.threshold
        self.create_line(L, Y(thr), w - R, Y(thr), fill="#d1352b", dash=(6, 4))
        self.create_text(w - R + 4, Y(thr), text=f"threshold {thr:.0f}%", anchor="w", fill="#d1352b",
                         font=("Segoe UI", 8))
        self.create_line(L, Y(100), w - R, Y(100), fill="#555", dash=(2, 4))
        for i, k in enumerate(keys):
            pts = [(X(ts), Y(d[k][0])) for ts, d in samples if k in d]
            col = PALETTE[i % len(PALETTE)]
            if len(pts) >= 2:
                self.create_line(*[c for p in pts for c in p], fill=col, width=2)
            for x, y in pts[-1:]:
                self.create_oval(x - 3, y - 3, x + 3, y + 3, fill=col, outline="")
            self.create_text(w - R + 4, T + 14 + 16 * i, text=k, anchor="w", fill=col, font=("Segoe UI", 8, "bold"))
        self.create_text(L, h - 10, anchor="w", font=("Segoe UI", 8),
                         text=time.strftime("%H:%M:%S", time.localtime(t0)))
        self.create_text(w - R, h - 10, anchor="e", font=("Segoe UI", 8),
                         text=time.strftime("%H:%M:%S", time.localtime(t1)))
