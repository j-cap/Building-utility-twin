#!/usr/bin/env python3
"""Run Experiment 0 from a source checkout without installing the package."""

from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from building_utility_twin.experiment import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

