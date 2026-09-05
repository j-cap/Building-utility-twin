import tempfile
from pathlib import Path
import unittest

from building_utility_twin.contracts import Quality
from building_utility_twin.file_adapter import MeterMapping, VendorCsvConfig, import_vendor_csv


def config() -> VendorCsvConfig:
    return VendorCsvConfig(";", "%d.%m.%Y %H:%M:%S", "Europe/Vienna", ",", "L", "test-vendor", {"meter_id": "meter", "timestamp": "time", "value": "value", "status": "status"}, {"OK": Quality.GOOD, "EST": Quality.SUSPECT}, {"M1": MeterMapping("meter-1", "building")})


class VendorCsvAdapterTests(unittest.TestCase):
    def _import(self, body: str):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"
            path.write_text(body, encoding="utf-8")
            return import_vendor_csv(path, config())

    def test_local_decimal_comma_litres_and_quality_are_normalized(self) -> None:
        result = self._import("meter;time;value;status\nM1;15.01.2026 01:00:00;1,5;OK\nM1;15.01.2026 01:05:00;2,0;EST\n")
        self.assertEqual(len(result.measurements), 2)
        self.assertEqual(result.measurements[0].timestamp.isoformat(), "2026-01-15T00:00:00+00:00")
        self.assertEqual(result.measurements[0].value, .0015)
        self.assertIs(result.measurements[1].quality, Quality.SUSPECT)

    def test_exact_duplicate_is_deduplicated_deterministically(self) -> None:
        row = "M1;15.01.2026 01:00:00;1,5;OK\n"
        result = self._import("meter;time;value;status\n" + row + row + "M1;15.01.2026 01:05:00;2,0;OK\n")
        self.assertEqual(len(result.measurements), 2)
        self.assertEqual(result.audit[1].action, "duplicate")

    def test_malformed_and_conflicting_rows_are_rejected(self) -> None:
        invalid = (
            "meter;time;value;status\nM1;15.01.2026 01:00:00;1,5;OK\n"
            "M1;15.01.2026 01:00:00;2,5;OK\n"
        )
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self._import(invalid)
        with self.assertRaisesRegex(ValueError, "unknown status"):
            self._import("meter;time;value;status\nM1;15.01.2026 01:00:00;1,5;NOPE\nM1;15.01.2026 01:05:00;2,0;OK\n")
        with self.assertRaisesRegex(ValueError, "unknown meter"):
            self._import("meter;time;value;status\nM2;15.01.2026 01:00:00;1,5;OK\nM2;15.01.2026 01:05:00;2,0;OK\n")
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            self._import("meter;time;value;status\nM1;15.01.2026 01:00:00;-1,5;OK\nM1;15.01.2026 01:05:00;2,0;OK\n")


if __name__ == "__main__":
    unittest.main()
