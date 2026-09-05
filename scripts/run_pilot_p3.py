#!/usr/bin/env python3
"""Run Pilot Preparation P3 from a repository checkout."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from building_utility_twin.pilot_p3 import main

if __name__ == "__main__":
    raise SystemExit(main())
