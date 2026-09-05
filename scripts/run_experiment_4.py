#!/usr/bin/env python3
"""Run Iteration E / Experiment 4 from a repository checkout."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from building_utility_twin.experiment_4 import main


if __name__ == "__main__":
    raise SystemExit(main())
