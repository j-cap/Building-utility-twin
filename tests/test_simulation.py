from pathlib import Path
import unittest

from building_utility_twin.simulation import ExperimentConfig, simulate_one_pipe_day


ROOT = Path(__file__).resolve().parents[1]


class OnePipeSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ExperimentConfig.from_json_file(ROOT / "config/experiment_0.json")
        self.result = simulate_one_pipe_day(self.config)

    def test_exactly_one_day_is_simulated(self) -> None:
        self.assertEqual(len(self.result.flow_m3_s), 1440)
        self.assertEqual(len(self.result.timestamps), 1441)
        self.assertEqual(
            (self.result.timestamps[-1] - self.result.timestamps[0]).total_seconds(),
            86_400,
        )

    def test_virtual_meter_increments_match_flow(self) -> None:
        for index, rate in enumerate(self.result.flow_m3_s):
            increment = (
                self.result.cumulative_volume_m3[index + 1]
                - self.result.cumulative_volume_m3[index]
            )
            self.assertAlmostEqual(increment, rate * self.config.step_seconds, places=14)

    def test_one_pipe_conserves_water(self) -> None:
        inlet_volume = sum(
            rate * self.config.step_seconds for rate in self.result.flow_m3_s
        )
        outlet_volume = sum(
            rate * self.config.step_seconds for rate in self.result.flow_m3_s
        )
        meter_delta = (
            self.result.cumulative_volume_m3[-1]
            - self.result.cumulative_volume_m3[0]
        )
        self.assertLessEqual(
            abs(inlet_volume - outlet_volume),
            self.config.conservation_tolerance_m3,
        )
        self.assertLessEqual(
            abs(inlet_volume - meter_delta),
            self.config.conservation_tolerance_m3,
        )


if __name__ == "__main__":
    unittest.main()

