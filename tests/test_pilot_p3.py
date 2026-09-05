import tempfile
import unittest
from pathlib import Path

from building_utility_twin.pilot_p3 import run_pilot_p3
from tests.test_synthetic_portfolio import small_config


class PilotP3RunnerTests(unittest.TestCase):
    def test_outputs_are_reproducible_and_acceptance_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_pilot_p3(small_config(), root / "left")
            right = run_pilot_p3(small_config(), root / "right")
            self.assertEqual(
                left.analytics_snapshot_path.read_bytes(),
                right.analytics_snapshot_path.read_bytes(),
            )
            self.assertEqual(left.summary_path.read_bytes(), right.summary_path.read_bytes())
            self.assertTrue(left.summary["acceptance"]["passed"])
            self.assertEqual(left.summary["campaign"]["evidence_count"], 14)


if __name__ == "__main__":
    unittest.main()
