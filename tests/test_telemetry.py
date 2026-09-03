from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from building_utility_twin.telemetry import (
    Experiment2Config,
    MeterObservation,
    TelemetryFaultConfig,
    reconcile_observations,
    simulate_imperfect_telemetry,
)


ROOT = Path(__file__).resolve().parents[1]


def _fault_config() -> TelemetryFaultConfig:
    return TelemetryFaultConfig(
        readout_interval_seconds=300,
        register_resolution_l=0.1,
        increment_noise_std_fraction=0.0,
        register_modulus_l=100.0,
        packet_loss_probability=0.0,
        max_packet_delay_seconds=0,
        reset_offsets_seconds=(),
        seed=1,
        rollover_drop_threshold_fraction=0.25,
    )


class RegisterReconciliationTests(unittest.TestCase):
    def test_reorders_and_recovers_rollover_and_declared_reset(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)

        def observation(
            sequence: int,
            raw_l: float,
            true_l: float,
            *,
            event: str = "none",
            pre_reset_l: float | None = None,
        ) -> MeterObservation:
            timestamp = start + timedelta(seconds=sequence * 300)
            return MeterObservation(
                sequence_number=sequence,
                observed_at=timestamp,
                true_cumulative_m3=true_l / 1000.0,
                raw_register_m3=raw_l / 1000.0,
                event=event,
                pre_reset_register_m3=(
                    None if pre_reset_l is None else pre_reset_l / 1000.0
                ),
                dropped=False,
                received_at=timestamp,
            )

        observations = (
            observation(0, 0.0, 0.0),
            observation(2, 10.0, 110.0),
            observation(1, 90.0, 90.0),
            observation(3, 0.0, 140.0, event="reset", pre_reset_l=40.0),
        )
        result = reconcile_observations(observations, _fault_config())
        self.assertEqual(
            [round(item.cumulative_m3 * 1000.0, 9) for item in result],
            [0.0, 90.0, 110.0, 140.0],
        )
        self.assertEqual(
            [item.adjustment for item in result],
            ["none", "none", "rollover", "reset"],
        )

    def test_reset_requires_pre_reset_register(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        observations = (
            MeterObservation(0, start, 0.0, 0.0, "none", None, False, start),
            MeterObservation(
                1,
                start + timedelta(seconds=300),
                0.0,
                0.0,
                "reset",
                None,
                False,
                start + timedelta(seconds=300),
            ),
        )
        with self.assertRaisesRegex(ValueError, "pre-reset"):
            reconcile_observations(observations, _fault_config())


class ImperfectTelemetryTests(unittest.TestCase):
    def test_reference_scenario_exercises_and_recovers_faults(self) -> None:
        config = Experiment2Config.from_json_file(ROOT / "config/experiment_2.json")
        result = simulate_imperfect_telemetry(config)
        self.assertEqual(len(result.observations), 289)
        self.assertEqual(sum(item.dropped for item in result.observations), 37)
        self.assertEqual(len(result.packets_by_arrival), 252)
        self.assertEqual(result.simulated_rollover_count, 3)
        self.assertEqual(
            sum("rollover" in item.adjustment for item in result.reconciled), 3
        )
        self.assertEqual(
            sum("reset" in item.adjustment for item in result.reconciled), 1
        )
        self.assertTrue(
            all(
                right.cumulative_m3 >= left.cumulative_m3
                for left, right in zip(result.reconciled, result.reconciled[1:])
            )
        )
        errors_l = [
            abs(
                item.cumulative_m3 - item.observation.true_cumulative_m3
            )
            * 1000.0
            for item in result.reconciled
        ]
        self.assertLessEqual(max(errors_l), config.maximum_reconstruction_error_l)
        reset_observation = next(
            item for item in result.observations if item.event == "reset"
        )
        self.assertFalse(reset_observation.dropped)


if __name__ == "__main__":
    unittest.main()
