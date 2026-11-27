"""Entry point:  python -m teapp [--config FILE] [--live | --offline]"""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, credentials, load_config


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NGN580S SDN TE & network management application")
    ap.add_argument("--config", help="path to config JSON (default config/config.json, else the example)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="talk to the real OpenDaylight controller")
    mode.add_argument("--offline", action="store_true", help="labelled offline simulation mode")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.live:
            cfg["mode"] = "live"
        elif args.offline:
            cfg["mode"] = "offline"
    except (OSError, ValueError, ConfigError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    from .service import AppService
    svc = AppService(cfg)
    if cfg["mode"] == "live" and credentials(cfg["controller"])[0] is None:
        svc.log.add("WARN", "controller", "ODL_USER/ODL_PASS not set - use the Credentials... button")

    from .gui.app import main as gui_main
    gui_main(svc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
