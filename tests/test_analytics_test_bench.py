import unittest
from uuid import uuid4

from building_utility_twin.analytics_test_bench import (
    AnalyticsEvidence,
    EvidenceLevel,
    EvidenceOutcome,
    run_analytics_campaign,
)
from building_utility_twin.synthetic_portfolio import generate_portfolio
from tests.test_synthetic_portfolio import small_config


class AnalyticsTestBenchTests(unittest.TestCase):
    def test_reference_campaign_is_deterministic_and_covers_every_level(self) -> None:
        portfolio = generate_portfolio(small_config())
        left = run_analytics_campaign(portfolio)
        right = run_analytics_campaign(portfolio)
        self.assertEqual(left, right)
        self.assertEqual(left.digest, right.digest)
        self.assertEqual(len(left.evidence), 14)
        self.assertEqual(
            {level: sum(item.evidence_level is level for item in left.evidence) for level in EvidenceLevel},
            {
                EvidenceLevel.DATA_QUALITY: 6,
                EvidenceLevel.ACCOUNTING_PLAUSIBILITY: 5,
                EvidenceLevel.DIAGNOSTIC_RESEARCH: 3,
            },
        )
        self.assertTrue(all(item.mechanism_agrees for item in left.evidence))
        self.assertFalse(any(item.operational_claim_allowed for item in left.evidence))

    def test_diagnostic_contract_rejects_operational_disposition(self) -> None:
        with self.assertRaisesRegex(ValueError, "research_only"):
            AnalyticsEvidence(
                evidence_id=str(uuid4()),
                campaign_id=str(uuid4()),
                analytic_id="invalid_diagnostic",
                evidence_level=EvidenceLevel.DIAGNOSTIC_RESEARCH,
                outcome=EvidenceOutcome.REVIEW,
                expected_outcome=EvidenceOutcome.REVIEW,
                building_id=None,
                meter_id=None,
                title="Invalid diagnostic",
                interpretation="Invalid",
                observed={},
                thresholds={},
                provenance={},
            )


if __name__ == "__main__":
    unittest.main()
