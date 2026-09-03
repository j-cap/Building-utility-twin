import json
from pathlib import Path
import tempfile
import unittest

from building_utility_twin.domestic_hot_water import Experiment1Config
from building_utility_twin.experiment_1 import run_experiment_1


ROOT = Path(__file__).resolve().parents[1]


class Experiment1RunnerTests(unittest.TestCase):
    def test_outputs_are_reproducible_and_conservative(self) -> None:
        config = Experiment1Config.from_json_file(ROOT / "config/experiment_1.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_experiment_1(config, root / "left")
            right = run_experiment_1(config, root / "right")
            for left_path, right_path in (
                (left.measurements_path, right.measurements_path),
                (left.timeseries_path, right.timeseries_path),
                (left.events_path, right.events_path),
                (left.summary_path, right.summary_path),
            ):
                self.assertEqual(left_path.read_bytes(), right_path.read_bytes())
            summary = json.loads(left.summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["conservation"]["passed"])
            self.assertEqual(summary["results"]["stored_measurement_count"], 17_285)


if __name__ == "__main__":
    unittest.main()

