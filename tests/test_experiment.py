import json
from pathlib import Path
import tempfile
import unittest

from building_utility_twin.experiment import run_experiment
from building_utility_twin.simulation import ExperimentConfig


ROOT = Path(__file__).resolve().parents[1]


class ExperimentRunnerTests(unittest.TestCase):
    def test_same_seed_produces_identical_data_and_summary(self) -> None:
        config = ExperimentConfig.from_json_file(ROOT / "config/experiment_0.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_experiment(config, root / "left")
            right = run_experiment(config, root / "right")
            self.assertEqual(
                left.measurements_path.read_bytes(), right.measurements_path.read_bytes()
            )
            self.assertEqual(left.timeseries_path.read_bytes(), right.timeseries_path.read_bytes())
            self.assertEqual(left.summary_path.read_bytes(), right.summary_path.read_bytes())
            summary = json.loads(left.summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["conservation"]["passed"])
            self.assertTrue(summary["results"]["meter_monotonic"])


if __name__ == "__main__":
    unittest.main()

