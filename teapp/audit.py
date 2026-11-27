"""Event log, persistent audit trail (JSON lines), utilisation history and CSV export."""
from __future__ import annotations

import csv
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

LEVELS = ("INFO", "OK", "WARN", "ERROR")


@dataclass
class Event:
    ts: float
    level: str
    category: str
    message: str

    def line(self) -> str:
        return f"{time.strftime('%H:%M:%S', time.localtime(self.ts))} [{self.level:5}] {self.category}: {self.message}"


class EventLog:
    def __init__(self, audit_path: Path | None = None, maxlen: int = 2000):
        self.events: deque = deque(maxlen=maxlen)
        self.audit_path = audit_path
        self._lock = threading.Lock()
        self.listeners = []
        if audit_path:
            audit_path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, level: str, category: str, message: str, **data) -> Event:
        level = level if level in LEVELS else "INFO"
        ev = Event(time.time(), level, category, message)
        with self._lock:
            self.events.append(ev)
            if self.audit_path:
                rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ev.ts)),
                       "level": level, "category": category, "message": message}
                rec.update({k: v for k, v in data.items() if _jsonable(v)})
                with open(self.audit_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
        for cb in list(self.listeners):
            cb(ev)
        return ev


def _jsonable(v) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


class History:
    """Rolling utilisation history per directional link (for the history graph + export)."""

    def __init__(self, maxlen: int = 120):
        self.samples: deque = deque(maxlen=maxlen)   # (ts, {"L13 R1>R3": (util, prov)})

    def add(self, states) -> None:
        self.samples.append((time.time(), {f"{s.link_id} {s.u}>{s.v}": (s.util_pct, s.prov) for s in states}))

    def series(self, key: str) -> list:
        return [(ts, d[key][0]) for ts, d in self.samples if key in d]

    def keys(self) -> list:
        return sorted({k for _, d in self.samples for k in d})


def export_csv(path: Path, history: History, te_result=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["# NGN580S TE app export", time.strftime("%Y-%m-%d %H:%M:%S")])
        w.writerow(["timestamp", "link_dir", "utilisation_pct", "provenance"])
        for ts, d in history.samples:
            for key, (util, prov) in sorted(d.items()):
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), key, f"{util:.2f}", prov])
        if te_result is not None:
            w.writerow([])
            w.writerow(["# TE result (COMPUTED)", f"demand={te_result.demand} Mb/s",
                        f"threshold={te_result.threshold} %", f"policy={te_result.policy}"])
            w.writerow(["path", "igp_cost", "delay_ms", "bottleneck_available_mbps", "mlu_pct",
                        "feasible", "congested", "baseline", "recommended"])
            for c in te_result.candidates:
                w.writerow([c.name, c.cost, c.delay_ms, f"{c.bottleneck_available:.3f}", f"{c.mlu:.2f}",
                            c.feasible, c.congested, c is te_result.baseline, c is te_result.recommended])
            w.writerow(["reason", te_result.reason])
    return path
