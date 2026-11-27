"""
NGN580S T3 Final Summative - SDN Traffic Engineering & Network Management Application
Student 222313625

HOW TO RUN (no .bat file needed)
    python te_app.py              -> LIVE mode against the GA5 lab (OpenDaylight + GNS3)
    python te_app.py --offline    -> labelled OFFLINE SIMULATION (core TE demo only)
    python te_app.py --test       -> run the automated tests (needs pytest)

You can also open this file in Spyder / IDLE / VS Code and press Run (F5).
After the window opens, click "ODL login" and type the OpenDaylight username and
password. They are kept in memory only and are never written to disk.

Source layout (see README.md):
    te_app.py               this launcher
    teapp/gui/              GUI (Tkinter): app.py, topology_view.py, history_view.py
    teapp/te_engine.py      traffic-engineering calculation (utilisation, MLU, path choice)
    teapp/lsp_manager.py    LSP add / remove workflow with operational verification
    teapp/controller/odl.py OpenDaylight RESTCONF adapter (PCEP topology, BGP state, add-lsp)
    teapp/gns3.py           reads the live R1/R2/R3 topology from the GNS3 server
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)          # so "import teapp" works from any working directory


def _check_python() -> None:
    """Fail early with a clear message instead of a stack trace."""
    if sys.version_info < (3, 9):
        sys.exit("Python 3.9 or newer is needed (you have %s)." % sys.version.split()[0])
    try:
        import tkinter  # noqa: F401
    except ImportError:
        sys.exit("Tkinter is missing. Install Python from python.org or Anaconda (both include it); "
                 "on Ubuntu: sudo apt install python3-tk")
    try:
        import requests  # noqa: F401
    except ImportError:
        sys.exit("The 'requests' package is missing. Run:  pip install -r requirements.txt")


def run_tests() -> int:
    try:
        import pytest
    except ImportError:
        print("pytest is not installed:  pip install pytest")
        return 1
    return pytest.main(["-q", os.path.join(HERE, "tests")])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NGN580S SDN TE & network management application (222313625)")
    ap.add_argument("--offline", action="store_true", help="labelled offline simulation instead of the live lab")
    ap.add_argument("--config", help="config JSON file (default: config/config.json)")
    ap.add_argument("--test", action="store_true", help="run the automated tests and exit")
    # parse_known_args: IDEs such as Spyder sometimes pass their own arguments
    args, _unknown = ap.parse_known_args(argv)

    if args.test:
        return run_tests()
    _check_python()

    from teapp.config import ConfigError, credentials, load_config
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError, ConfigError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    cfg["mode"] = "offline" if args.offline else "live"

    from teapp.service import AppService
    svc = AppService(cfg)
    if cfg["mode"] == "live" and credentials(cfg["controller"])[0] is None:
        svc.log.add("INFO", "controller", "Click 'ODL login' and enter the OpenDaylight username/password")

    from teapp.gui.app import main as gui_main
    gui_main(svc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
