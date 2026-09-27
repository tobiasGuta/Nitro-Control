"""CLI and GUI entry point."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .demo import demo_snapshot
from .hardware import HardwareReader


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nitro Control — read-only Fedora hardware dashboard")
    parser.add_argument("--demo", action="store_true", help="show clearly labeled example readings")
    parser.add_argument("--once", action="store_true", help="print a single JSON snapshot, without GTK")
    parser.add_argument("--version", action="version", version=f"Nitro Control {__version__}")
    args = parser.parse_args(argv)
    if args.once:
        print((demo_snapshot() if args.demo else HardwareReader().collect()).to_json())
        return 0
    try:
        from .ui import run_ui
    except (ImportError, ValueError) as exc:
        print("GTK4 / Libadwaita unavailable. On Fedora: sudo dnf install python3-gobject gtk4 libadwaita", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        return 2
    return run_ui(demo=args.demo)


if __name__ == "__main__":
    raise SystemExit(main())
