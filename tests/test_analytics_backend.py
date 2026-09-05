import tempfile
import unittest
from pathlib import Path

from building_utility_twin.analytics_test_bench import run_analytics_campaign
from building_utility_twin.persistent_backend import PersistentBackend
from building_utility_twin.synthetic_portfolio import generate_portfolio
from tests.test_synthetic_portfolio import small_config


class AnalyticsBackendTests(unittest.TestCase):
    def test_campaign_is_idempotent_filterable_and_linked_to_review_queue(self) -> None:
        portfolio = generate_portfolio(small_config())
        campaign = run_analytics_campaign(portfolio)
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentBackend.for_path(Path(directory) / "backend.sqlite3")
            backend.load_portfolio(portfolio)
            first = backend.store_analytics_campaign(campaign)
            replay = backend.store_analytics_campaign(campaign)
            self.assertEqual(first.accepted_count, 14)
            self.assertEqual(replay.accepted_count, 0)
            self.assertEqual(replay.duplicate_count, 14)
            self.assertTrue(replay.replayed)
            summary = backend.analytics_summary()
            self.assertEqual(summary["evidence_count"], 14)
            self.assertEqual(summary["mechanism_agreement_count"], 14)
            self.assertEqual(summary["operational_claim_count"], 0)
            diagnostic = backend.list_analytics_evidence(
                evidence_level="diagnostic_research"
            )
            self.assertEqual(len(diagnostic), 3)
            self.assertTrue(all(item["outcome"] == "research_only" for item in diagnostic))
            analytics_issues = [
                item for item in backend.list_issues() if item["category"] == "analytics"
            ]
            self.assertEqual(len(analytics_issues), 10)
            self.assertTrue(
                all(not item["evidence"]["operational_claim_allowed"] for item in analytics_issues)
            )
            backend.close()

    def test_campaign_requires_an_existing_portfolio(self) -> None:
        campaign = run_analytics_campaign(generate_portfolio(small_config()))
        with tempfile.TemporaryDirectory() as directory:
            backend = PersistentBackend.for_path(Path(directory) / "backend.sqlite3")
            with self.assertRaises(KeyError):
                backend.store_analytics_campaign(campaign)
            backend.close()


if __name__ == "__main__":
    unittest.main()
