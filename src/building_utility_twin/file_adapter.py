"""Strict adapter for cumulative water-meter CSV exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import Measurement, Quality, Quantity


@dataclass(frozen=True, slots=True)
class MeterMapping:
    asset_id: str
    role: str


@dataclass(frozen=True, slots=True)
class VendorCsvConfig:
    delimiter: str
    timestamp_format: str
    timezone: str
    decimal_separator: str
    source_unit: str
    source_name: str
    fields: dict[str, str]
    quality_mapping: dict[str, Quality]
    meters: dict[str, MeterMapping]

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "VendorCsvConfig":
        return cls(
            delimiter=str(payload["delimiter"]),
            timestamp_format=str(payload["timestamp_format"]),
            timezone=str(payload["timezone"]),
            decimal_separator=str(payload["decimal_separator"]),
            source_unit=str(payload["source_unit"]),
            source_name=str(payload["source_name"]),
            fields={str(k): str(v) for k, v in dict(payload["fields"]).items()},
            quality_mapping={
                str(k): Quality(str(v))
                for k, v in dict(payload["quality_mapping"]).items()
            },
            meters={
                str(k): MeterMapping(
                    asset_id=str(dict(v)["asset_id"]),
                    role=str(dict(v)["role"]),
                )
                for k, v in dict(payload["meters"]).items()
            },
        )


@dataclass(frozen=True, slots=True)
class ImportAuditRow:
    row_number: int
    source_meter_id: str
    action: str
    reason: str
    measurement_id: str


@dataclass(frozen=True, slots=True)
class ImportResult:
    measurements: tuple[Measurement, ...]
    audit: tuple[ImportAuditRow, ...]
    roles_by_asset: dict[str, str]


def import_vendor_csv(path: str | Path, config: VendorCsvConfig) -> ImportResult:
    """Convert a vendor export into validated canonical measurements.

    Exact duplicate source rows are accepted once and recorded as duplicates.
    Conflicting readings for the same meter and timestamp are rejected.
    """

    if config.source_unit != "L":
        raise ValueError(f"unsupported source unit: {config.source_unit!r}")
    required = {"meter_id", "timestamp", "value", "status"}
    if set(config.fields) != required:
        raise ValueError(f"field mapping must define exactly {sorted(required)}")
    timezone = ZoneInfo(config.timezone)
    measurements: list[Measurement] = []
    audit: list[ImportAuditRow] = []
    seen: dict[tuple[str, datetime], tuple[float, Quality, str]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=config.delimiter)
        expected_headers = set(config.fields.values())
        if reader.fieldnames is None or not expected_headers.issubset(reader.fieldnames):
            raise ValueError("vendor CSV is missing one or more configured columns")
        for row_number, row in enumerate(reader, start=2):
            source_meter = row[config.fields["meter_id"]].strip()
            if source_meter not in config.meters:
                raise ValueError(f"row {row_number}: unknown meter {source_meter!r}")
            status = row[config.fields["status"]].strip()
            if status not in config.quality_mapping:
                raise ValueError(f"row {row_number}: unknown status {status!r}")
            timestamp_text = row[config.fields["timestamp"]].strip()
            try:
                local = datetime.strptime(timestamp_text, config.timestamp_format)
            except ValueError as error:
                raise ValueError(
                    f"row {row_number}: invalid timestamp {timestamp_text!r}"
                ) from error
            timestamp = local.replace(tzinfo=timezone)
            value_text = row[config.fields["value"]].strip()
            if config.decimal_separator != ".":
                value_text = value_text.replace(config.decimal_separator, ".")
            try:
                value_l = float(value_text)
            except ValueError as error:
                raise ValueError(f"row {row_number}: invalid register value") from error
            if not math.isfinite(value_l) or value_l < 0.0:
                raise ValueError(
                    f"row {row_number}: register value must be finite and non-negative"
                )
            quality = config.quality_mapping[status]
            key = (source_meter, timestamp)
            signature = (value_l, quality, status)
            if key in seen:
                if seen[key] != signature:
                    raise ValueError(
                        f"row {row_number}: conflicting reading for meter and timestamp"
                    )
                audit.append(
                    ImportAuditRow(row_number, source_meter, "duplicate", "exact", "")
                )
                continue
            seen[key] = signature
            mapping = config.meters[source_meter]
            measurement = Measurement.create(
                asset_id=mapping.asset_id,
                channel="total_water_register_reconciled",
                quantity=Quantity.CUMULATIVE_VOLUME,
                timestamp=timestamp,
                value=value_l / 1000.0,
                quality=quality,
                source=config.source_name,
            )
            measurements.append(measurement)
            audit.append(
                ImportAuditRow(
                    row_number,
                    source_meter,
                    "accepted",
                    "mapped_to_canonical_si",
                    measurement.measurement_id,
                )
            )
    ordered = tuple(sorted(measurements, key=lambda item: (item.timestamp, item.asset_id)))
    for source_meter, mapping in config.meters.items():
        values = [item.value for item in ordered if item.asset_id == mapping.asset_id]
        if len(values) < 2:
            raise ValueError(f"meter {source_meter!r} has fewer than two readings")
        if any(right < left for left, right in zip(values, values[1:])):
            raise ValueError(f"meter {source_meter!r} register is not monotonic")
    return ImportResult(
        measurements=ordered,
        audit=tuple(audit),
        roles_by_asset={mapping.asset_id: mapping.role for mapping in config.meters.values()},
    )
