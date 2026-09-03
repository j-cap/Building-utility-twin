from datetime import datetime, timezone
import math
import unittest

from building_utility_twin.contracts import Measurement, Quantity


class MeasurementContractTests(unittest.TestCase):
    def test_round_trip_is_lossless_and_identifier_is_deterministic(self) -> None:
        arguments = {
            "asset_id": "pipe-001",
            "channel": "outlet_flow",
            "quantity": Quantity.VOLUMETRIC_FLOW_RATE,
            "timestamp": datetime(2026, 1, 15, tzinfo=timezone.utc),
            "value": 0.0001,
            "source": "test",
            "duration_seconds": 60,
        }
        left = Measurement.create(**arguments)
        right = Measurement.create(**arguments)
        self.assertEqual(left.measurement_id, right.measurement_id)
        self.assertEqual(left, Measurement.from_json(left.to_json()))

    def test_wrong_unit_is_rejected(self) -> None:
        measurement = Measurement.create(
            asset_id="pipe-001",
            channel="outlet_flow",
            quantity=Quantity.VOLUMETRIC_FLOW_RATE,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            value=0.0001,
            source="test",
            duration_seconds=60,
        )
        payload = measurement.to_dict()
        payload["unit"] = "L/min"
        with self.assertRaises(ValueError):
            Measurement.from_dict(payload)

    def test_naive_timestamp_and_non_finite_value_are_rejected(self) -> None:
        common = {
            "asset_id": "pipe-001",
            "channel": "outlet_flow",
            "quantity": Quantity.VOLUMETRIC_FLOW_RATE,
            "source": "test",
            "duration_seconds": 60,
        }
        with self.assertRaises(ValueError):
            Measurement.create(
                **common, timestamp=datetime(2026, 1, 15), value=0.0001
            )
        with self.assertRaises(ValueError):
            Measurement.create(
                **common,
                timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
                value=math.nan,
            )

    def test_thermal_power_requires_duration_and_cumulative_energy_does_not(self) -> None:
        timestamp = datetime(2026, 1, 15, tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            Measurement.create(
                asset_id="boiler-001",
                channel="useful_power",
                quantity=Quantity.THERMAL_POWER,
                timestamp=timestamp,
                value=1000.0,
                source="test",
            )
        energy = Measurement.create(
            asset_id="heat-meter-001",
            channel="energy_register",
            quantity=Quantity.CUMULATIVE_ENERGY,
            timestamp=timestamp,
            value=3_600_000.0,
            source="test",
        )
        self.assertEqual(energy.unit, "J")

    def test_temperature_uses_kelvin(self) -> None:
        temperature = Measurement.create(
            asset_id="boiler-001",
            channel="supply_temperature",
            quantity=Quantity.TEMPERATURE,
            timestamp=datetime(2026, 1, 15, tzinfo=timezone.utc),
            value=333.15,
            source="test",
        )
        self.assertEqual(temperature.unit, "K")
        self.assertIsNone(temperature.duration_seconds)


if __name__ == "__main__":
    unittest.main()
