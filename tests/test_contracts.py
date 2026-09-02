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


if __name__ == "__main__":
    unittest.main()

