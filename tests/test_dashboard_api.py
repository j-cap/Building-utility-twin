import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from building_utility_twin.api import create_app
from building_utility_twin.persistent_backend import PersistentBackend
from building_utility_twin.synthetic_portfolio import generate_portfolio
from tests.test_synthetic_portfolio import small_config


class DashboardApiTests(unittest.TestCase):
    def test_operator_routes_and_review_write(self) -> None:
        portfolio = generate_portfolio(small_config())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backend.sqlite3"
            backend = PersistentBackend.for_path(path)
            backend.load_portfolio(portfolio)
            backend.close()
            app = create_app(f"sqlite:///{path}")
            with TestClient(app) as client:
                overview = client.get("/api/v1/portfolio/overview")
                self.assertEqual(overview.status_code, 200)
                self.assertEqual(len(overview.json()["building_cards"]), 2)
                building_id = portfolio.buildings[0].building_id
                balance = client.get(
                    f"/api/v1/buildings/{building_id}/water-balance"
                )
                self.assertEqual(balance.status_code, 200)
                meter_id = portfolio.meters[0].meter_id
                profile = client.get(f"/api/v1/meters/{meter_id}/profile")
                self.assertEqual(profile.status_code, 200)
                issues = client.get(
                    "/api/v1/issues",
                    params={"include_runtime_fields": False},
                ).json()
                issue_id = issues[0]["issue_id"]
                update = client.patch(
                    f"/api/v1/issues/{issue_id}",
                    json={
                        "status": "resolved",
                        "operator_note": "Synthetic review completed.",
                    },
                )
                self.assertEqual(update.status_code, 200)
                self.assertEqual(update.json()["status"], "resolved")
                self.assertEqual(
                    client.get(
                        "/api/v1/issues", params={"status": "resolved"}
                    ).json()[0]["operator_note"],
                    "Synthetic review completed.",
                )

    def test_operator_routes_validate_unknown_resources_and_empty_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(f"sqlite:///{Path(directory) / 'empty.sqlite3'}")
            with TestClient(app) as client:
                self.assertEqual(
                    client.get(
                        "/api/v1/buildings/missing/water-balance"
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    client.get("/api/v1/meters/missing/profile").status_code,
                    404,
                )
                self.assertEqual(
                    client.patch("/api/v1/issues/missing", json={}).status_code,
                    422,
                )


if __name__ == "__main__":
    unittest.main()
