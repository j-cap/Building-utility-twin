import unittest

from building_utility_twin.meters import IdealCumulativeMeter


class IdealCumulativeMeterTests(unittest.TestCase):
    def test_integrates_interval_average_rates(self) -> None:
        meter = IdealCumulativeMeter(initial_register=2.0)
        self.assertEqual(meter.readings((1.0, 2.0, 0.0), 10), (2.0, 12.0, 32.0, 32.0))

    def test_rejects_invalid_step(self) -> None:
        with self.assertRaises(ValueError):
            IdealCumulativeMeter().readings((1.0,), 0)


if __name__ == "__main__":
    unittest.main()

