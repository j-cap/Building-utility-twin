from pathlib import Path
import unittest

from building_utility_twin.domestic_hot_water import (
    CentralBoiler,
    Experiment1Config,
    generate_fixture_demand,
    simulate_domestic_hot_water,
)


ROOT = Path(__file__).resolve().parents[1]


class FixtureDemandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Experiment1Config.from_json_file(ROOT / "config/experiment_1.json")

    def test_reference_targets_and_interval_balance(self) -> None:
        result = generate_fixture_demand(self.config)
        expected_total_l = (
            self.config.occupants * self.config.target_total_l_per_person_day
        )
        expected_hot_l = self.config.occupants * self.config.target_hot_l_per_person_day
        self.assertAlmostEqual(result.total_volume_m3[-1] * 1000.0, expected_total_l, places=10)
        self.assertAlmostEqual(result.hot_volume_m3[-1] * 1000.0, expected_hot_l, places=10)
        self.assertAlmostEqual(
            result.cold_volume_m3[-1] + result.hot_volume_m3[-1],
            result.total_volume_m3[-1],
            places=14,
        )
        for total, cold, hot in zip(
            result.total_flow_m3_s, result.cold_flow_m3_s, result.hot_flow_m3_s
        ):
            self.assertAlmostEqual(total, cold + hot, places=16)

    def test_same_seed_reproduces_events_and_flows(self) -> None:
        left = generate_fixture_demand(self.config)
        right = generate_fixture_demand(self.config)
        self.assertEqual(left.events, right.events)
        self.assertEqual(left.total_flow_m3_s, right.total_flow_m3_s)


class CentralBoilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Experiment1Config.from_json_file(ROOT / "config/experiment_1.json")

    def test_zero_hot_flow_requires_zero_power(self) -> None:
        boiler = CentralBoiler(
            cold_temperature_k=283.15,
            hot_temperature_k=333.15,
            efficiency=0.9,
            water_density_kg_m3=997.0,
            water_heat_capacity_j_kg_k=4180.0,
        )
        self.assertEqual(boiler.power_from_hot_flow(0.0), (0.0, 0.0))

    def test_daily_energy_matches_volume_and_efficiency(self) -> None:
        result = simulate_domestic_hot_water(self.config)
        expected_useful = (
            self.config.water_density_kg_m3
            * self.config.water_heat_capacity_j_kg_k
            * result.demand.hot_volume_m3[-1]
            * (self.config.hot_supply_temperature_c - self.config.cold_supply_temperature_c)
        )
        self.assertAlmostEqual(result.useful_energy_j[-1], expected_useful, places=5)
        self.assertAlmostEqual(
            result.boiler_input_energy_j[-1] * self.config.boiler_efficiency,
            result.useful_energy_j[-1],
            places=5,
        )


if __name__ == "__main__":
    unittest.main()

