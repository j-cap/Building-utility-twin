#!/usr/bin/env python3
"""Run Iteration D / Experiment 3 from a source checkout."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from building_utility_twin.experiment_3 import main


if __name__ == "__main__":
    raise SystemExit(main())
