"""CLI and GUI entry point."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys

from . import __version__
from .demo import demo_snapshot
from .hardware import HardwareReader


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nitro Control — Fedora hardware dashboard")
    parser.add_argument("--demo", action="store_true", help="show clearly labeled example readings")
    parser.add_argument("--tray", action="store_true", help="start optional read-only system-tray indicator")
    parser.add_argument("--once", action="store_true", help="print a single JSON snapshot, without GTK")
    parser.add_argument("--rgb-probe", action="store_true", help="read-only RGB backend probe (JSON; no GTK)")
    parser.add_argument("--version", action="version", version=f"Nitro Control {__version__}")
    args = parser.parse_args(argv)
    if args.tray:
        if args.demo or args.once or args.rgb_probe:
            parser.error("--tray cannot be combined with --demo, --once or --rgb-probe")
        try:
            # GTK3 AppIndicator runs in this separate process. It must never
            # be imported into the GTK4/Libadwaita dashboard process.
            from .tray_indicator import run_tray
        except (ImportError, ValueError) as exc:
            print(
                "Tray unavailable. On Fedora: sudo dnf install "
                "python3-gobject gtk3 libayatana-appindicator-gtk3",
                file=sys.stderr,
            )
            print(f"Details: {exc}", file=sys.stderr)
            return 2
        return run_tray()
    if args.rgb_probe:
        from .rgb_hardware import RGBHardware
        print(json.dumps(asdict(RGBHardware().probe()), indent=2))
        return 0
    if args.once:
        print((demo_snapshot() if args.demo else HardwareReader().collect()).to_json())
        return 0
    try:
        from .ui import run_ui
    except (ImportError, ValueError) as exc:
        print("GTK4 / Libadwaita / Cairo unavailable. On Fedora: sudo dnf install python3-gobject python3-cairo gtk4 libadwaita", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        return 2
    return run_ui(demo=args.demo)


if __name__ == "__main__":
    raise SystemExit(main())
