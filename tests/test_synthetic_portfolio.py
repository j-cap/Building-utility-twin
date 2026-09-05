import unittest
from dataclasses import replace
from datetime import UTC, datetime

from building_utility_twin.synthetic_portfolio import (
    PortfolioConfig,
    generate_portfolio,
)


def small_config() -> PortfolioConfig:
    return PortfolioConfig(
        portfolio_id="test-portfolio",
        portfolio_name="Test Portfolio",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        days=2,
        interval_minutes=60,
        building_count=2,
        apartments_per_building=3,
        minimum_occupants=1,
        maximum_occupants=3,
        daily_liters_per_person=125.0,
        apartment_missing_probability=0.05,
        building_missing_probability=0.02,
        suspect_probability=0.02,
        seed=17,
    )


class SyntheticPortfolioTests(unittest.TestCase):
    def test_generation_is_deterministic_and_topology_is_complete(self) -> None:
        left = generate_portfolio(small_config())
        right = generate_portfolio(small_config())
        self.assertEqual(left, right)
        self.assertEqual(left.digest, right.digest)
        self.assertEqual(len(left.buildings), 2)
        self.assertEqual(len(left.apartments), 6)
        self.assertEqual(len(left.meters), 8)

    def test_building_end_register_equals_sum_of_apartments(self) -> None:
        portfolio = generate_portfolio(small_config())
        for building in portfolio.buildings:
            meters = [item for item in portfolio.meters if item.building_id == building.building_id]
            building_asset = next(item.asset_id for item in meters if item.role == "building")
            apartment_assets = {item.asset_id for item in meters if item.role == "apartment"}
            building_end = max(
                (item for item in portfolio.measurements if item.asset_id == building_asset),
                key=lambda item: item.timestamp,
            ).value
            apartment_end = sum(
                max(
                    (item for item in portfolio.measurements if item.asset_id == asset),
                    key=lambda item: item.timestamp,
                ).value
                for asset in apartment_assets
            )
            self.assertAlmostEqual(building_end, apartment_end, places=6)

    def test_invalid_probability_and_naive_start_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, r"\[0, 1\)"):
            replace(small_config(), suspect_probability=1.0)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            replace(small_config(), start=datetime(2026, 1, 1))  # noqa: DTZ001


if __name__ == "__main__":
    unittest.main()
