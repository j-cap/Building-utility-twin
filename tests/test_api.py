import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from building_utility_twin.api import create_app
from building_utility_twin.persistent_backend import PersistentBackend
from building_utility_twin.synthetic_portfolio import generate_portfolio
from tests.test_synthetic_portfolio import small_config


class ApiTests(unittest.TestCase):
    def test_read_endpoints_expose_persisted_portfolio(self) -> None:
        portfolio = generate_portfolio(small_config())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backend.sqlite3"
            backend = PersistentBackend.for_path(path)
            backend.load_portfolio(portfolio)
            backend.close()
            app = create_app(f"sqlite:///{path}")
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                summary = client.get("/api/v1/portfolio").json()
                self.assertEqual(summary["building_count"], 2)
                buildings = client.get("/api/v1/buildings").json()
                self.assertEqual(len(buildings), 2)
                meters = client.get(
                    f"/api/v1/buildings/{buildings[0]['building_id']}/meters"
                ).json()
                response = client.get(
                    f"/api/v1/meters/{meters[0]['meter_id']}/measurements",
                    params={"limit": 3},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.json()), 3)
                self.assertEqual(len(client.get("/api/v1/imports").json()), 1)

    def test_unknown_resources_return_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(f"sqlite:///{Path(directory) / 'empty.sqlite3'}")
            with TestClient(app) as client:
                self.assertEqual(
                    client.get("/api/v1/buildings/missing/meters").status_code, 404
                )
                self.assertEqual(
                    client.get("/api/v1/meters/missing/measurements").status_code,
                    404,
                )


if __name__ == "__main__":
    unittest.main()
