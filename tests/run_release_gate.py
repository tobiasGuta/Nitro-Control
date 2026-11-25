"""Run unittest qualification checks and fail on skips or an empty suite."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main() -> int:
    loader = unittest.defaultTestLoader
    suite = (loader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1 else
             loader.discover(str(Path(__file__).resolve().parent)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.skipped:
        print("Required release gate skipped tests. Resolve the dependencies above.", file=sys.stderr)
    if not result.testsRun:
        print("Required release gate ran no tests.", file=sys.stderr)
    return 0 if result.wasSuccessful() and result.testsRun and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
