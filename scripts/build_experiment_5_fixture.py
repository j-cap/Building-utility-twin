#!/usr/bin/env python3
"""Build the frozen representative vendor export from Experiment 4 evidence."""

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/experiment_4/telemetry.csv"
TARGET = ROOT / "fixtures/experiment_5/vendor_meter_export.csv"
IDS = {"building": "BLDG-H2O-001", "apartment-101": "APT-101-H2O", "apartment-102": "APT-102-H2O", "apartment-201": "APT-201-H2O", "apartment-202": "APT-202-H2O"}

TARGET.parent.mkdir(parents=True, exist_ok=True)
with SOURCE.open(newline="", encoding="utf-8") as source, TARGET.open("w", newline="", encoding="utf-8") as target:
    reader = csv.DictReader(source)
    writer = csv.writer(target, delimiter=";", lineterminator="\n")
    writer.writerow(("Zaehlernummer", "Zeitstempel", "Zaehlerstand_L", "Status"))
    for row in reader:
        if row["status"] != "delivered":
            continue
        utc = datetime.fromisoformat(row["observed_at_utc"].replace("Z", "+00:00"))
        local = utc.astimezone(ZoneInfo("Europe/Vienna"))
        value = f'{float(row["reconciled_cumulative_l"]):.1f}'.replace(".", ",")
        status = "OK" if row["quality"] == "good" else "GESCHAETZT"
        writer.writerow((IDS[row["meter_id"]], local.strftime("%d.%m.%Y %H:%M:%S"), value, status))
