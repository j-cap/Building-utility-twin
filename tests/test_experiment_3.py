import json
from pathlib import Path
import tempfile
import unittest

from building_utility_twin.building_system import Experiment3Config
from building_utility_twin.experiment_3 import run_experiment_3


ROOT = Path(__file__).resolve().parents[1]


class Experiment3RunnerTests(unittest.TestCase):
    def test_outputs_are_reproducible_and_conservative(self) -> None:
        config = Experiment3Config.from_json_file(ROOT / "config/experiment_3.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_experiment_3(config, root / "left")
            right = run_experiment_3(config, root / "right")
            for left_path, right_path in (
                (left.measurements_path, right.measurements_path),
                (left.timeseries_path, right.timeseries_path),
                (left.apartments_path, right.apartments_path),
                (left.events_path, right.events_path),
                (left.summary_path, right.summary_path),
            ):
                self.assertEqual(left_path.read_bytes(), right_path.read_bytes())
            summary = json.loads(left.summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["acceptance"]["passed"])
            self.assertEqual(summary["results"]["stored_measurement_count"], 56_180)
            self.assertEqual(summary["building"]["apartment_count"], 4)
            self.assertLessEqual(
                abs(summary["conservation"]["energy"]["storage_balance_residual_j"]),
                config.energy_tolerance_j,
            )


if __name__ == "__main__":
    unittest.main()
