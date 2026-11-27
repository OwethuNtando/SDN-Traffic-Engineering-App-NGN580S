"""Configuration loading and validation.

Order of precedence: config/config.json (user copy) -> config/config.example.json.
Values left as null in the file are filled from the student-number derivation
(teapp.student), so the student-specific inputs are traceable.
Credentials are NEVER read from the file - only from environment variables or
an interactive prompt (see credentials()).
"""
from __future__ import annotations

import copy
import ipaddress
import json
import os
from pathlib import Path

from .student import derive, StudentParams

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(ValueError):
    """Raised when the configuration is invalid (input validation)."""


def _resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_config(path: str | None = None) -> dict:
    if path:
        cfg_path = Path(path)
    else:
        cfg_path = CONFIG_DIR / "config.json"
        if not cfg_path.exists():
            cfg_path = CONFIG_DIR / "config.example.json"
    with open(cfg_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    cfg = apply_student_defaults(raw)
    validate(cfg)
    cfg["_source_file"] = str(cfg_path)
    return cfg


def apply_student_defaults(raw: dict) -> dict:
    """Fill null TE inputs from the student derivation and record which were derived."""
    cfg = copy.deepcopy(raw)
    sp: StudentParams = derive(cfg.get("student_number", ""))
    cfg["_student"] = sp
    derived = []
    te = cfg.setdefault("te", {})
    if te.get("demand_mbps") is None:
        te["demand_mbps"] = sp.baseline_demand_mbps
        derived.append("te.demand_mbps")
    if te.get("threshold_pct") is None:
        te["threshold_pct"] = sp.threshold_pct
        derived.append("te.threshold_pct")
    for link in cfg.get("links", []):
        for key, val in list(link.get("background_mbps", {}).items()):
            if val is None:
                # Only R1->R3 is student-derived; any other null becomes 0.
                link["background_mbps"][key] = sp.background_r1r3_mbps if key == "R1>R3" else 0.0
                derived.append(f"links.{link['id']}.background_mbps.{key}")
    cfg["_derived_fields"] = derived
    return cfg


def validate(cfg: dict) -> None:
    nodes = cfg.get("nodes") or {}
    if len(nodes) < 2:
        raise ConfigError("At least two nodes are required")
    for name, n in nodes.items():
        try:
            ipaddress.IPv4Address(n["loopback"])
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"Node {name}: invalid loopback ({exc})") from exc
    seen = set()
    for link in cfg.get("links", []):
        lid = link.get("id")
        if not lid or lid in seen:
            raise ConfigError(f"Duplicate or missing link id: {lid!r}")
        seen.add(lid)
        for end in ("a", "b"):
            if link.get(end) not in nodes:
                raise ConfigError(f"Link {lid}: unknown node {link.get(end)!r}")
            try:
                ipaddress.IPv4Address(link[f"{end}_ip"])
            except (KeyError, ValueError) as exc:
                raise ConfigError(f"Link {lid}: invalid {end}_ip ({exc})") from exc
        if float(link.get("capacity_mbps", 0)) <= 0:
            raise ConfigError(f"Link {lid}: capacity must be > 0")
        if float(link.get("igp_cost", 0)) <= 0:
            raise ConfigError(f"Link {lid}: IGP cost must be > 0")
        if float(link.get("delay_ms", 0)) < 0:
            raise ConfigError(f"Link {lid}: delay must be >= 0")
    te = cfg.get("te", {})
    for end in ("src", "dst"):
        if te.get(end) not in nodes:
            raise ConfigError(f"te.{end} must be one of {sorted(nodes)}")
    if te["src"] == te["dst"]:
        raise ConfigError("te.src and te.dst must differ")
    thr = float(te.get("threshold_pct", 0))
    if not 1 <= thr <= 100:
        raise ConfigError("te.threshold_pct must be between 1 and 100")
    if cfg.get("mode") not in ("live", "offline"):
        raise ConfigError("mode must be 'live' or 'offline'")


def credentials(section: dict) -> tuple[str | None, str | None]:
    """Read credentials from the environment variables named in the config section."""
    user = os.environ.get(section.get("username_env", ""), None)
    pwd = os.environ.get(section.get("password_env", ""), None)
    return user, pwd


def resolve(cfg: dict, key: str) -> Path:
    return _resolve_path(cfg["paths"][key])


def data_path(p: str) -> Path:
    return _resolve_path(p)
