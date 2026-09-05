import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from building_utility_twin.analytics_test_bench import run_analytics_campaign
from building_utility_twin.api import create_app
from building_utility_twin.persistent_backend import PersistentBackend
from building_utility_twin.synthetic_portfolio import generate_portfolio
from tests.test_synthetic_portfolio import small_config


class AnalyticsApiTests(unittest.TestCase):
    def test_analytics_routes_expose_summary_and_filtered_evidence(self) -> None:
        portfolio = generate_portfolio(small_config())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backend.sqlite3"
            backend = PersistentBackend.for_path(path)
            backend.load_portfolio(portfolio)
            backend.store_analytics_campaign(run_analytics_campaign(portfolio))
            backend.close()
            with TestClient(create_app(f"sqlite:///{path}")) as client:
                summary = client.get("/api/v1/analytics/summary")
                self.assertEqual(summary.status_code, 200)
                self.assertEqual(summary.json()["evidence_count"], 14)
                diagnostic = client.get(
                    "/api/v1/analytics/evidence",
                    params={"evidence_level": "diagnostic_research"},
                )
                self.assertEqual(diagnostic.status_code, 200)
                self.assertEqual(len(diagnostic.json()), 3)
                self.assertEqual(
                    client.get(
                        "/api/v1/analytics/evidence",
                        params={"evidence_level": "unsupported"},
                    ).status_code,
                    422,
                )


if __name__ == "__main__":
    unittest.main()
