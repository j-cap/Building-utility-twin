import json
import tempfile
import unittest
from pathlib import Path

from building_utility_twin.pilot_p1 import run_pilot_p1
from building_utility_twin.synthetic_portfolio import PortfolioConfig

ROOT = Path(__file__).resolve().parents[1]


class PilotP1RunnerTests(unittest.TestCase):
    def test_outputs_are_reproducible_and_acceptance_passes(self) -> None:
        config = PortfolioConfig.from_json_file(ROOT / "config/pilot_p1.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_pilot_p1(config, root / "left")
            right = run_pilot_p1(config, root / "right")
            self.assertEqual(
                left.api_snapshot_path.read_bytes(), right.api_snapshot_path.read_bytes()
            )
            self.assertEqual(left.summary_path.read_bytes(), right.summary_path.read_bytes())
            summary = json.loads(left.summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["acceptance"]["passed"])
            self.assertEqual(summary["generated"]["building_count"], 6)
            self.assertEqual(summary["generated"]["apartment_count"], 72)
            self.assertEqual(
                summary["persistence"]["replay"]["accepted_count"], 0
            )


if __name__ == "__main__":
    unittest.main()
