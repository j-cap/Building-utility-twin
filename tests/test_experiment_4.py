import json
from pathlib import Path
import tempfile
import unittest

from building_utility_twin.anomaly_analytics import Experiment4Config
from building_utility_twin.experiment_4 import run_experiment_4


ROOT = Path(__file__).resolve().parents[1]


class Experiment4RunnerTests(unittest.TestCase):
    def test_outputs_are_reproducible_and_anomalies_are_detected(self) -> None:
        config = Experiment4Config.from_json_file(ROOT / "config/experiment_4.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_experiment_4(config, root / "left")
            right = run_experiment_4(config, root / "right")
            for left_path, right_path in (
                (left.measurements_path, right.measurements_path),
                (left.telemetry_path, right.telemetry_path),
                (left.water_balance_path, right.water_balance_path),
                (left.thermal_balance_path, right.thermal_balance_path),
                (left.alarms_path, right.alarms_path),
                (left.summary_path, right.summary_path),
            ):
                self.assertEqual(left_path.read_bytes(), right_path.read_bytes())
            summary = json.loads(left.summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["acceptance"]["passed"])
            self.assertEqual(summary["results"]["stored_measurement_count"], 3163)
            self.assertTrue(summary["water_balance"]["leak_event"]["detected"])
            self.assertTrue(
                summary["water_balance"]["meter_underregistration_event"][
                    "detected"
                ]
            )
            self.assertFalse(
                summary["water_balance"]["source_identifiable_from_balance_alone"]
            )
            self.assertEqual(summary["thermal_balance"]["precision"], 1.0)


if __name__ == "__main__":
    unittest.main()
