import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text

from building_utility_twin.persistent_backend import PersistentBackend
from building_utility_twin.synthetic_portfolio import generate_portfolio
from tests.test_synthetic_portfolio import small_config


class PersistentBackendTests(unittest.TestCase):
    def test_load_is_idempotent_and_queries_preserve_canonical_values(self) -> None:
        portfolio = generate_portfolio(small_config())
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentBackend.for_path(Path(directory) / "backend.sqlite3")
            first = backend.load_portfolio(portfolio)
            replay = backend.load_portfolio(portfolio)
            self.assertEqual(first.accepted_count, len(portfolio.measurements))
            self.assertFalse(first.replayed)
            self.assertTrue(replay.replayed)
            self.assertEqual(replay.accepted_count, 0)
            self.assertEqual(replay.duplicate_count, len(portfolio.measurements))
            self.assertEqual(backend.counts()["import_count"], 1)
            self.assertEqual(
                backend.counts()["measurement_count"], len(portfolio.measurements)
            )
            first_meter = portfolio.meters[0]
            stored = backend.meter_measurements(first_meter.meter_id, limit=10_000)
            expected = [
                item for item in portfolio.measurements if item.asset_id == first_meter.asset_id
            ]
            self.assertEqual(len(stored), len(expected))
            self.assertTrue(all(item["timestamp"].endswith("Z") for item in stored))
            self.assertEqual(stored[0]["value"], expected[0].value)
            backend.close()

    def test_schema_version_mismatch_fails_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backend.sqlite3"
            backend = PersistentBackend.for_path(path)
            with backend.engine.begin() as connection:
                connection.execute(
                    text("UPDATE schema_metadata SET value='999' WHERE key='schema_version'")
                )
            backend.close()
            with self.assertRaisesRegex(RuntimeError, "incompatible"):
                PersistentBackend.for_path(path)


if __name__ == "__main__":
    unittest.main()
