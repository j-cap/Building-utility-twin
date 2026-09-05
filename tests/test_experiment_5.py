import json
from pathlib import Path
import tempfile
import unittest

from building_utility_twin.experiment_5 import run_experiment_5

ROOT = Path(__file__).resolve().parents[1]


class Experiment5RunnerTests(unittest.TestCase):
    def test_outputs_are_reproducible_and_adapter_substitution_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = run_experiment_5(ROOT / "config/experiment_5.json", root / "left")
            right = run_experiment_5(ROOT / "config/experiment_5.json", root / "right")
            for name in ("measurements_path", "import_audit_path", "water_balance_path", "alarms_path", "summary_path"):
                self.assertEqual(getattr(left, name).read_bytes(), getattr(right, name).read_bytes())
            summary = json.loads(left.summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["acceptance"]["passed"])
            self.assertFalse(summary["source"]["field_data"])
            self.assertEqual(summary["import"]["meter_count"], 5)
            self.assertTrue(
                all(
                    item["detected"]
                    for item in summary["water_balance"][
                        "reference_event_detection"
                    ].values()
                )
            )


if __name__ == "__main__":
    unittest.main()
