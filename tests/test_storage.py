from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from building_utility_twin.contracts import Measurement, Quantity
from building_utility_twin.storage import JsonLinesMeasurementStore


class JsonLinesMeasurementStoreTests(unittest.TestCase):
    def test_file_round_trip_and_query(self) -> None:
        start = datetime(2026, 1, 15, tzinfo=timezone.utc)
        records = [
            Measurement.create(
                asset_id="pipe-001",
                channel="outlet_flow",
                quantity=Quantity.VOLUMETRIC_FLOW_RATE,
                timestamp=start + timedelta(minutes=index),
                value=index / 1_000_000.0,
                source="test",
                duration_seconds=60,
            )
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as directory:
            store = JsonLinesMeasurementStore(Path(directory) / "measurements.jsonl")
            self.assertEqual(store.replace(reversed(records)), 3)
            self.assertEqual(store.read_all(), records)
            self.assertEqual(
                store.query(
                    asset_id="pipe-001",
                    quantity=Quantity.VOLUMETRIC_FLOW_RATE,
                    start=start + timedelta(minutes=1),
                ),
                records[1:],
            )


if __name__ == "__main__":
    unittest.main()

