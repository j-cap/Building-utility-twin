from dataclasses import replace
from pathlib import Path
import unittest

from building_utility_twin.building_system import (
    Experiment3Config,
    simulate_building,
)


ROOT = Path(__file__).resolve().parents[1]


class MultiApartmentAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Experiment3Config.from_json_file(
            ROOT / "config/experiment_3.json"
        )
        self.result = simulate_building(self.config)

    def test_apartment_targets_and_building_aggregation(self) -> None:
        template = self.config.physical_template
        for apartment in self.result.apartments:
            expected_total_l = (
                apartment.spec.occupants
                * apartment.spec.demand_scale
                * template.target_total_l_per_person_day
            )
            expected_hot_l = (
                apartment.spec.occupants
                * apartment.spec.demand_scale
                * template.target_hot_l_per_person_day
            )
            self.assertAlmostEqual(
                apartment.demand.total_volume_m3[-1] * 1000.0,
                expected_total_l,
                places=9,
            )
            self.assertAlmostEqual(
                apartment.demand.hot_volume_m3[-1] * 1000.0,
                expected_hot_l,
                places=9,
            )

        for index in range(len(self.result.total_flow_m3_s)):
            self.assertAlmostEqual(
                self.result.total_flow_m3_s[index],
                sum(
                    item.demand.total_flow_m3_s[index]
                    for item in self.result.apartments
                ),
                places=15,
            )
            self.assertAlmostEqual(
                self.result.total_flow_m3_s[index],
                self.result.cold_flow_m3_s[index]
                + self.result.hot_flow_m3_s[index],
                places=15,
            )
        self.assertAlmostEqual(
            self.result.total_volume_m3[-1],
            sum(
                item.demand.total_volume_m3[-1]
                for item in self.result.apartments
            ),
            places=12,
        )

    def test_shared_tank_energy_balance_and_bounds(self) -> None:
        tank = self.result.shared_tank
        delta_stored = tank.stored_energy_j[-1] - tank.stored_energy_j[0]
        residual = delta_stored - (
            tank.boiler_output_energy_j[-1]
            - tank.dhw_output_energy_j[-1]
            - tank.standing_loss_energy_j[-1]
        )
        self.assertLessEqual(abs(residual), self.config.energy_tolerance_j)
        self.assertGreaterEqual(
            min(tank.temperature_k) - 273.15,
            self.config.shared_tank.minimum_service_temperature_c,
        )
        self.assertLessEqual(
            max(tank.temperature_k) - 273.15,
            self.config.shared_tank.thermostat_upper_c + 1e-12,
        )
        self.assertLessEqual(
            max(tank.boiler_output_power_w),
            self.config.shared_tank.boiler_max_thermal_power_kw * 1000.0,
        )
        self.assertGreater(tank.standing_loss_energy_j[-1], 0.0)

    def test_duplicate_apartment_identifier_is_rejected(self) -> None:
        duplicate = replace(
            self.config.apartments[1],
            apartment_id=self.config.apartments[0].apartment_id,
        )
        with self.assertRaisesRegex(ValueError, "identifiers"):
            replace(
                self.config,
                apartments=(self.config.apartments[0], duplicate),
            )

    def test_standing_loss_multiplier_validation_and_nominal_equivalence(self) -> None:
        interval_count = len(self.result.total_flow_m3_s)
        nominal = simulate_building(
            self.config, standing_loss_multipliers=(1.0,) * interval_count
        )
        self.assertEqual(nominal, self.result)
        with self.assertRaisesRegex(ValueError, "match"):
            simulate_building(
                self.config, standing_loss_multipliers=(1.0,) * 2
            )
        with self.assertRaisesRegex(ValueError, "non-negative"):
            simulate_building(
                self.config,
                standing_loss_multipliers=(-1.0,) * interval_count,
            )


if __name__ == "__main__":
    unittest.main()
