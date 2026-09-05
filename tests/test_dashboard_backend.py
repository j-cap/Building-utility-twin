import tempfile
import unittest
from pathlib import Path

from building_utility_twin.persistent_backend import PersistentBackend
from building_utility_twin.synthetic_portfolio import generate_portfolio
from tests.test_synthetic_portfolio import small_config


class DashboardBackendTests(unittest.TestCase):
    def test_overview_balance_and_meter_profile_are_consistent(self) -> None:
        portfolio = generate_portfolio(small_config())
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentBackend.for_path(Path(directory) / "backend.sqlite3")
            backend.load_portfolio(portfolio)
            overview = backend.portfolio_overview()
            self.assertEqual(len(overview["building_cards"]), 2)
            first_building = portfolio.buildings[0].building_id
            balance = backend.building_balance(first_building)
            self.assertEqual(
                balance["water_balance"]["classification"],
                "within_rounding_tolerance",
            )
            self.assertLessEqual(abs(balance["water_balance"]["residual_l"]), 0.01)
            self.assertGreater(balance["matched_boundary_count"], 0)
            profile = backend.meter_profile(portfolio.meters[0].meter_id)
            self.assertEqual(
                profile["summary"]["observed_reading_count"],
                len(profile["measurements"]),
            )
            self.assertEqual(profile["period"]["expected_boundary_count"], 49)
            backend.close()

    def test_review_status_and_note_persist(self) -> None:
        portfolio = generate_portfolio(small_config())
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentBackend.for_path(Path(directory) / "backend.sqlite3")
            backend.load_portfolio(portfolio)
            issues = backend.list_issues(include_runtime_fields=False)
            self.assertGreater(len(issues), 0)
            backend.load_portfolio(portfolio)
            self.assertEqual(
                backend.list_issues(include_runtime_fields=False), issues
            )
            self.assertTrue(
                all(item["evidence"]["classification"] == "data_quality" for item in issues)
            )
            issue_id = issues[0]["issue_id"]
            updated = backend.update_issue(
                issue_id,
                status="investigating",
                operator_note="Check the source export cadence.",
            )
            self.assertEqual(updated["status"], "investigating")
            self.assertEqual(updated["operator_note"], "Check the source export cadence.")
            backend.close()
            backend = PersistentBackend.for_path(Path(directory) / "backend.sqlite3")
            filtered = backend.list_issues(status="investigating")
            self.assertEqual([item["issue_id"] for item in filtered], [issue_id])
            backend.close()


if __name__ == "__main__":
    unittest.main()
