import json
from pathlib import Path
import tempfile
import unittest

from building_utility_twin.experiment_2 import run_experiment_2
from building_utility_twin.telemetry import Experiment2Config


ROOT = Path(__file__).resolve().parents[1]


class Experiment2RunnerTests(unittest.TestCase):
    def test_outputs_are_reproducible_and_faults_are_recovered(self) -> None:
        config = Experiment2Config.from_json_file(ROOT / "config/experiment_2.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_experiment_2(config, root / "left")
            right = run_experiment_2(config, root / "right")
            for left_path, right_path in (
                (left.measurements_path, right.measurements_path),
                (left.timeseries_path, right.timeseries_path),
                (left.telemetry_path, right.telemetry_path),
                (left.summary_path, right.summary_path),
            ):
                self.assertEqual(left_path.read_bytes(), right_path.read_bytes())
            summary = json.loads(left.summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["acceptance"]["passed"])
            self.assertEqual(summary["results"]["stored_measurement_count"], 504)
            self.assertEqual(
                summary["reconciliation"]["detected_rollover_count"], 3
            )
            self.assertEqual(summary["reconciliation"]["detected_reset_count"], 1)


if __name__ == "__main__":
    unittest.main()
