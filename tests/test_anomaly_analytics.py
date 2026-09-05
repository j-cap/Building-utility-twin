from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import unittest

from building_utility_twin.anomaly_analytics import (
    Experiment4Config,
    simulate_experiment_4,
)


ROOT = Path(__file__).resolve().parents[1]


class BuildingAnomalyAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = Experiment4Config.from_json_file(
            ROOT / "config/experiment_4.json"
        )
        self.result = simulate_experiment_4(self.config)

    def test_all_meter_discontinuities_are_recovered(self) -> None:
        maximum_error_l = 0.0
        for channel in self.result.meter_channels:
            detected_rollovers = sum(
                "rollover" in item.adjustment for item in channel.reconciled
            )
            detected_resets = sum(
                "reset" in item.adjustment for item in channel.reconciled
            )
            self.assertEqual(detected_rollovers, channel.simulated_rollover_count)
            self.assertEqual(
                detected_resets, len(channel.telemetry.reset_offsets_seconds)
            )
            stride = (
                channel.telemetry.readout_interval_seconds
                // self.config.building.physical_template.step_seconds
            )
            for item in channel.reconciled:
                boundary = item.observation.sequence_number * stride
                maximum_error_l = max(
                    maximum_error_l,
                    abs(
                        item.cumulative_m3
                        - channel.registered_cumulative_m3[boundary]
                    )
                    * 1000.0,
                )
        self.assertLessEqual(
            maximum_error_l,
            self.config.analytics.maximum_telemetry_reconstruction_error_l,
        )

    def test_water_balance_detects_both_nonidentifiable_sources(self) -> None:
        start = self.result.building.timestamps[0]
        leak_start = start + timedelta(
            seconds=self.config.water_leak.window.start_offset_seconds
        )
        leak_end = start + timedelta(
            seconds=self.config.water_leak.window.end_offset_seconds
        )
        bias_start = start + timedelta(
            seconds=(
                self.config.meter_underregistration.window.start_offset_seconds
            )
        )
        bias_end = start + timedelta(
            seconds=self.config.meter_underregistration.window.end_offset_seconds
        )
        leak_alarms = [
            item
            for item in self.result.water_windows
            if item.alarm and item.start < leak_end and item.end > leak_start
        ]
        bias_alarms = [
            item
            for item in self.result.water_windows
            if item.alarm and item.start < bias_end and item.end > bias_start
        ]
        self.assertTrue(leak_alarms)
        self.assertTrue(bias_alarms)
        self.assertTrue(
            all(item.leak_component_l_min > 0.0 for item in leak_alarms)
        )
        self.assertTrue(
            all(item.meter_bias_component_l_min > 0.0 for item in bias_alarms)
        )
        self.assertFalse(
            any(
                item.alarm and not item.expected_alarm
                for item in self.result.water_windows
            )
        )

    def test_excess_storage_loss_closes_balance_and_is_detected(self) -> None:
        tank = self.result.building.shared_tank
        stored_change = tank.stored_energy_j[-1] - tank.stored_energy_j[0]
        residual = stored_change - (
            tank.boiler_output_energy_j[-1]
            - tank.dhw_output_energy_j[-1]
            - tank.standing_loss_energy_j[-1]
        )
        self.assertLessEqual(abs(residual), self.config.building.energy_tolerance_j)
        self.assertTrue(any(item.alarm for item in self.result.thermal_windows))
        self.assertFalse(
            any(
                item.alarm and not item.expected_alarm
                for item in self.result.thermal_windows
            )
        )

    def test_unknown_underregistering_meter_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "identify an apartment"):
            replace(
                self.config,
                meter_underregistration=replace(
                    self.config.meter_underregistration,
                    meter_id="apartment-does-not-exist",
                ),
            )


if __name__ == "__main__":
    unittest.main()
