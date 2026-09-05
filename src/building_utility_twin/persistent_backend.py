"""Versioned relational backend for canonical utility measurements."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .synthetic_portfolio import SyntheticPortfolio

BACKEND_SCHEMA_VERSION = 1
IMPORT_NAMESPACE = UUID("3df89c3e-38dc-4fd4-bbf9-b825843b6379")
ISSUE_NAMESPACE = UUID("ef07a700-5c35-4b8a-aad5-9000d38781bc")
ISSUE_STATUSES = frozenset({"open", "investigating", "resolved"})
COMPLETENESS_REVIEW_THRESHOLD_PERCENT = 97.5
SUSPECT_REVIEW_THRESHOLD_PERCENT = 2.0


class Base(DeclarativeBase):
    pass


class SchemaMetadata(Base):
    __tablename__ = "schema_metadata"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class PortfolioRow(Base):
    __tablename__ = "portfolios"
    portfolio_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)


class BuildingRow(Base):
    __tablename__ = "buildings"
    building_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (Index("ix_buildings_portfolio", "portfolio_id"),)


class ApartmentRow(Base):
    __tablename__ = "apartments"
    apartment_id: Mapped[str] = mapped_column(String, primary_key=True)
    building_id: Mapped[str] = mapped_column(
        ForeignKey("buildings.building_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    occupants: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (Index("ix_apartments_building", "building_id"),)


class MeterRow(Base):
    __tablename__ = "meters"
    meter_id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    building_id: Mapped[str] = mapped_column(
        ForeignKey("buildings.building_id"), nullable=False
    )
    apartment_id: Mapped[str | None] = mapped_column(
        ForeignKey("apartments.apartment_id"), nullable=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    utility: Mapped[str] = mapped_column(String, nullable=False)
    serial_number: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    __table_args__ = (
        Index("ix_meters_building", "building_id"),
        Index("ix_meters_apartment", "apartment_id"),
    )


class ImportJobRow(Base):
    __tablename__ = "import_jobs"
    import_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id"), nullable=False
    )
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    source_digest: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at_utc: Mapped[str] = mapped_column(String, nullable=False)


class MeasurementRow(Base):
    __tablename__ = "measurements"
    measurement_id: Mapped[str] = mapped_column(String, primary_key=True)
    meter_id: Mapped[str] = mapped_column(
        ForeignKey("meters.meter_id"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    asset_id: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[str] = mapped_column(String, nullable=False)
    timestamp_utc: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    quality: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        UniqueConstraint(
            "asset_id", "channel", "timestamp_utc",
            name="uq_measurement_asset_channel_time",
        ),
        Index("ix_measurements_meter_time", "meter_id", "timestamp_utc"),
        Index("ix_measurements_asset_time", "asset_id", "timestamp_utc"),
    )


class OperatorIssueRow(Base):
    __tablename__ = "operator_issues"
    issue_id: Mapped[str] = mapped_column(String, primary_key=True)
    portfolio_id: Mapped[str] = mapped_column(
        ForeignKey("portfolios.portfolio_id"), nullable=False
    )
    building_id: Mapped[str] = mapped_column(
        ForeignKey("buildings.building_id"), nullable=False
    )
    meter_id: Mapped[str | None] = mapped_column(
        ForeignKey("meters.meter_id"), nullable=True
    )
    category: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    evidence_json: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    operator_note: Mapped[str] = mapped_column(String, nullable=False, default="")
    detected_at_utc: Mapped[str] = mapped_column(String, nullable=False)
    updated_at_utc: Mapped[str] = mapped_column(String, nullable=False)
    __table_args__ = (
        Index("ix_operator_issues_status", "status"),
        Index("ix_operator_issues_building", "building_id"),
    )


@dataclass(frozen=True, slots=True)
class BackendLoadResult:
    import_id: str
    source_row_count: int
    accepted_count: int
    duplicate_count: int
    replayed: bool


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _batched(values: list[dict[str, Any]], size: int = 1000) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _portfolio_issue_rows(portfolio: SyntheticPortfolio) -> list[dict[str, Any]]:
    """Create deterministic review items from explicitly configured data quality."""

    expected_count = (
        portfolio.config.days * 1440 // portfolio.config.interval_minutes + 1
    )
    readings_by_asset: dict[str, list[Any]] = defaultdict(list)
    for measurement in portfolio.measurements:
        readings_by_asset[measurement.asset_id].append(measurement)

    detected_at = _utc_text(
        portfolio.config.start + timedelta(days=portfolio.config.days)
    )
    rows: list[dict[str, Any]] = []
    for meter in portfolio.meters:
        readings = readings_by_asset[meter.asset_id]
        quality_counts = Counter(item.quality.value for item in readings)
        observed_count = len(readings)
        missing_count = expected_count - observed_count
        completeness = 100.0 * observed_count / expected_count
        suspect_count = quality_counts.get("suspect", 0)
        suspect_percent = 100.0 * suspect_count / observed_count
        if (
            completeness >= COMPLETENESS_REVIEW_THRESHOLD_PERCENT
            and suspect_percent <= SUSPECT_REVIEW_THRESHOLD_PERCENT
        ):
            continue

        reasons = []
        if completeness < COMPLETENESS_REVIEW_THRESHOLD_PERCENT:
            reasons.append(
                f"completeness {completeness:.2f}% is below the "
                f"{COMPLETENESS_REVIEW_THRESHOLD_PERCENT:.1f}% review threshold"
            )
        if suspect_percent > SUSPECT_REVIEW_THRESHOLD_PERCENT:
            reasons.append(
                f"suspect share {suspect_percent:.2f}% exceeds the "
                f"{SUSPECT_REVIEW_THRESHOLD_PERCENT:.1f}% review threshold"
            )
        severity = (
            "warning"
            if completeness < 96.5 or suspect_percent > 2.5
            else "information"
        )
        evidence = {
            "classification": "data_quality",
            "expected_reading_count": expected_count,
            "observed_reading_count": observed_count,
            "missing_reading_count": missing_count,
            "completeness_percent": round(completeness, 4),
            "suspect_reading_count": suspect_count,
            "suspect_percent": round(suspect_percent, 4),
            "completeness_review_threshold_percent": (
                COMPLETENESS_REVIEW_THRESHOLD_PERCENT
            ),
            "suspect_review_threshold_percent": SUSPECT_REVIEW_THRESHOLD_PERCENT,
        }
        issue_id = str(
            uuid5(
                ISSUE_NAMESPACE,
                f"{portfolio.digest}:{meter.meter_id}:data-quality",
            )
        )
        rows.append(
            {
                "issue_id": issue_id,
                "portfolio_id": portfolio.config.portfolio_id,
                "building_id": meter.building_id,
                "meter_id": meter.meter_id,
                "category": "data_quality",
                "severity": severity,
                "title": "Meter data quality requires review",
                "description": "; ".join(reasons) + ".",
                "evidence_json": json.dumps(
                    evidence, sort_keys=True, separators=(",", ":")
                ),
                "status": "open",
                "operator_note": "",
                "detected_at_utc": detected_at,
                "updated_at_utc": detected_at,
            }
        )
    return rows


class PersistentBackend:
    """SQLite-backed repository with explicit schema and idempotent loading."""

    def __init__(self, database_url: str) -> None:
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        self.engine = create_engine(database_url, future=True, connect_args=connect_args)
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._configure_sqlite)
        self.initialize_schema()

    @staticmethod
    def _configure_sqlite(connection: Any, _: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    @classmethod
    def for_path(cls, path: str | Path) -> PersistentBackend:
        return cls(f"sqlite:///{Path(path).resolve()}")

    def initialize_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session, session.begin():
            current = session.get(SchemaMetadata, "schema_version")
            if current is None:
                session.add(
                    SchemaMetadata(
                        key="schema_version", value=str(BACKEND_SCHEMA_VERSION)
                    )
                )
            elif int(current.value) != BACKEND_SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current.value} is incompatible with "
                    f"application schema {BACKEND_SCHEMA_VERSION}"
                )

    def load_portfolio(
        self,
        portfolio: SyntheticPortfolio,
        *,
        source_name: str = "synthetic-portfolio-generator",
    ) -> BackendLoadResult:
        import_id = str(uuid5(IMPORT_NAMESPACE, portfolio.digest))
        with Session(self.engine) as session, session.begin():
            existing = session.get(ImportJobRow, import_id)
            if existing is not None:
                issue_rows = _portfolio_issue_rows(portfolio)
                if issue_rows:
                    session.execute(
                        sqlite_insert(OperatorIssueRow)
                        .values(issue_rows)
                        .on_conflict_do_nothing(index_elements=["issue_id"])
                    )
                return BackendLoadResult(
                    import_id,
                    existing.source_row_count,
                    0,
                    existing.source_row_count,
                    True,
                )

        with Session(self.engine) as session, session.begin():
            session.merge(
                PortfolioRow(
                    portfolio_id=portfolio.config.portfolio_id,
                    name=portfolio.config.portfolio_name,
                )
            )
            for item in portfolio.buildings:
                session.merge(
                    BuildingRow(
                        building_id=item.building_id,
                        portfolio_id=item.portfolio_id,
                        name=item.name,
                        timezone=item.timezone,
                    )
                )
            for item in portfolio.apartments:
                session.merge(
                    ApartmentRow(
                        apartment_id=item.apartment_id,
                        building_id=item.building_id,
                        name=item.name,
                        occupants=item.occupants,
                    )
                )
            for item in portfolio.meters:
                session.merge(
                    MeterRow(
                        meter_id=item.meter_id,
                        asset_id=item.asset_id,
                        building_id=item.building_id,
                        apartment_id=item.apartment_id,
                        role=item.role,
                        utility=item.utility,
                        serial_number=item.serial_number,
                    )
                )
            session.flush()
            meter_by_asset = {item.asset_id: item.meter_id for item in portfolio.meters}
            rows = [
                {
                    "measurement_id": item.measurement_id,
                    "meter_id": meter_by_asset[item.asset_id],
                    "schema_version": item.schema_version,
                    "asset_id": item.asset_id,
                    "channel": item.channel,
                    "quantity": item.quantity.value,
                    "timestamp_utc": _utc_text(item.timestamp),
                    "value": item.value,
                    "unit": item.unit,
                    "quality": item.quality.value,
                    "source": item.source,
                    "duration_seconds": item.duration_seconds,
                }
                for item in portfolio.measurements
            ]
            accepted_count = 0
            for batch in _batched(rows):
                result = session.execute(
                    sqlite_insert(MeasurementRow)
                    .values(batch)
                    .on_conflict_do_nothing(index_elements=["measurement_id"])
                )
                accepted_count += int(result.rowcount or 0)
            duplicate_count = len(rows) - accepted_count
            issue_rows = _portfolio_issue_rows(portfolio)
            if issue_rows:
                session.execute(
                    sqlite_insert(OperatorIssueRow)
                    .values(issue_rows)
                    .on_conflict_do_nothing(index_elements=["issue_id"])
                )
            session.add(
                ImportJobRow(
                    import_id=import_id,
                    portfolio_id=portfolio.config.portfolio_id,
                    source_name=source_name,
                    source_digest=portfolio.digest,
                    status="completed",
                    source_row_count=len(rows),
                    accepted_count=accepted_count,
                    duplicate_count=duplicate_count,
                    completed_at_utc=_utc_text(datetime.now(UTC)),
                )
            )
        return BackendLoadResult(
            import_id, len(rows), accepted_count, duplicate_count, False
        )

    def counts(self) -> dict[str, int]:
        with Session(self.engine) as session:
            return {
                "portfolio_count": session.scalar(select(func.count()).select_from(PortfolioRow)) or 0,
                "building_count": session.scalar(select(func.count()).select_from(BuildingRow)) or 0,
                "apartment_count": session.scalar(select(func.count()).select_from(ApartmentRow)) or 0,
                "meter_count": session.scalar(select(func.count()).select_from(MeterRow)) or 0,
                "measurement_count": session.scalar(select(func.count()).select_from(MeasurementRow)) or 0,
                "import_count": session.scalar(select(func.count()).select_from(ImportJobRow)) or 0,
            }

    def portfolio_summary(self) -> dict[str, Any]:
        counts = self.counts()
        with Session(self.engine) as session:
            bounds = session.execute(
                select(
                    func.min(MeasurementRow.timestamp_utc),
                    func.max(MeasurementRow.timestamp_utc),
                )
            ).one()
            quality = dict(
                session.execute(
                    select(MeasurementRow.quality, func.count())
                    .group_by(MeasurementRow.quality)
                    .order_by(MeasurementRow.quality)
                ).all()
            )
        return {
            **counts,
            "measurement_start_utc": bounds[0],
            "measurement_end_utc": bounds[1],
            "quality_counts": quality,
            "schema_version": BACKEND_SCHEMA_VERSION,
        }

    def list_buildings(self) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            apartment_counts = dict(
                session.execute(
                    select(ApartmentRow.building_id, func.count())
                    .group_by(ApartmentRow.building_id)
                ).all()
            )
            meter_counts = dict(
                session.execute(
                    select(MeterRow.building_id, func.count())
                    .group_by(MeterRow.building_id)
                ).all()
            )
            rows = session.scalars(select(BuildingRow).order_by(BuildingRow.building_id)).all()
            return [
                {
                    "building_id": row.building_id,
                    "portfolio_id": row.portfolio_id,
                    "name": row.name,
                    "timezone": row.timezone,
                    "apartment_count": apartment_counts.get(row.building_id, 0),
                    "meter_count": meter_counts.get(row.building_id, 0),
                }
                for row in rows
            ]

    def building_meters(self, building_id: str) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            if session.get(BuildingRow, building_id) is None:
                raise KeyError(building_id)
            rows = session.scalars(
                select(MeterRow)
                .where(MeterRow.building_id == building_id)
                .order_by(MeterRow.meter_id)
            ).all()
            return [
                {
                    "meter_id": row.meter_id,
                    "asset_id": row.asset_id,
                    "building_id": row.building_id,
                    "apartment_id": row.apartment_id,
                    "role": row.role,
                    "utility": row.utility,
                    "serial_number": row.serial_number,
                }
                for row in rows
            ]

    def meter_measurements(
        self,
        meter_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 10_000:
            raise ValueError("limit must lie in [1, 10000]")
        with Session(self.engine) as session:
            if session.get(MeterRow, meter_id) is None:
                raise KeyError(meter_id)
            statement = select(MeasurementRow).where(MeasurementRow.meter_id == meter_id)
            if start is not None:
                statement = statement.where(MeasurementRow.timestamp_utc >= start)
            if end is not None:
                statement = statement.where(MeasurementRow.timestamp_utc < end)
            rows = session.scalars(
                statement.order_by(MeasurementRow.timestamp_utc).limit(limit)
            ).all()
            return [
                {
                    "measurement_id": row.measurement_id,
                    "asset_id": row.asset_id,
                    "channel": row.channel,
                    "quantity": row.quantity,
                    "timestamp": row.timestamp_utc,
                    "value": row.value,
                    "unit": row.unit,
                    "quality": row.quality,
                    "source": row.source,
                    "duration_seconds": row.duration_seconds,
                    "schema_version": row.schema_version,
                }
                for row in rows
            ]

    def _period_metadata(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            first_timestamps = session.scalars(
                select(MeasurementRow.timestamp_utc)
                .distinct()
                .order_by(MeasurementRow.timestamp_utc)
                .limit(2)
            ).all()
            end = session.scalar(select(func.max(MeasurementRow.timestamp_utc)))
        if not first_timestamps or end is None:
            return {
                "start_utc": None,
                "end_utc": None,
                "interval_minutes": None,
                "expected_boundary_count": 0,
            }
        start = first_timestamps[0]
        if len(first_timestamps) == 1:
            return {
                "start_utc": start,
                "end_utc": end,
                "interval_minutes": None,
                "expected_boundary_count": 1,
            }
        start_dt = datetime.fromisoformat(start)
        second_dt = datetime.fromisoformat(first_timestamps[1])
        end_dt = datetime.fromisoformat(end)
        interval_seconds = int((second_dt - start_dt).total_seconds())
        expected_count = int((end_dt - start_dt).total_seconds()) // interval_seconds + 1
        return {
            "start_utc": start,
            "end_utc": end,
            "interval_minutes": interval_seconds // 60,
            "expected_boundary_count": expected_count,
        }

    def building_balance(self, building_id: str) -> dict[str, Any]:
        """Return exact-period and common-boundary water-balance evidence."""

        with Session(self.engine) as session:
            building = session.get(BuildingRow, building_id)
            if building is None:
                raise KeyError(building_id)
            meters = session.scalars(
                select(MeterRow)
                .where(MeterRow.building_id == building_id)
                .order_by(MeterRow.meter_id)
            ).all()
            meter_ids = [item.meter_id for item in meters]
            rows = session.scalars(
                select(MeasurementRow)
                .where(MeasurementRow.meter_id.in_(meter_ids))
                .order_by(MeasurementRow.timestamp_utc, MeasurementRow.meter_id)
            ).all()

        building_meter = next(item for item in meters if item.role == "building")
        apartment_meters = [item for item in meters if item.role == "apartment"]
        readings: dict[str, dict[str, float]] = defaultdict(dict)
        quality_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            readings[row.meter_id][row.timestamp_utc] = row.value
            quality_counts[row.meter_id][row.quality] += 1

        period = self._period_metadata()
        expected_count = period["expected_boundary_count"]
        building_values = readings[building_meter.meter_id]
        apartment_values = [readings[item.meter_id] for item in apartment_meters]
        common_timestamps = set(building_values)
        for values in apartment_values:
            common_timestamps.intersection_update(values)

        series = []
        for timestamp in sorted(common_timestamps):
            building_register_l = building_values[timestamp] * 1000.0
            apartment_register_l = sum(
                values[timestamp] * 1000.0 for values in apartment_values
            )
            series.append(
                {
                    "timestamp": timestamp,
                    "building_register_l": round(building_register_l, 4),
                    "apartment_register_sum_l": round(apartment_register_l, 4),
                    "balance_residual_l": round(
                        building_register_l - apartment_register_l, 6
                    ),
                }
            )

        def consumption(values: dict[str, float]) -> float:
            ordered = sorted(values)
            return (values[ordered[-1]] - values[ordered[0]]) * 1000.0

        building_consumption = consumption(building_values)
        apartment_consumption = sum(consumption(values) for values in apartment_values)
        residual = building_consumption - apartment_consumption
        relative = 100.0 * residual / building_consumption if building_consumption else 0.0
        tolerance_l = 0.01
        classification = (
            "within_rounding_tolerance"
            if abs(residual) <= tolerance_l
            else "balance_anomaly"
        )
        return {
            "building": {
                "building_id": building.building_id,
                "portfolio_id": building.portfolio_id,
                "name": building.name,
                "timezone": building.timezone,
            },
            "period": period,
            "building_meter": {
                "meter_id": building_meter.meter_id,
                "observed_boundary_count": len(building_values),
                "completeness_percent": round(
                    100.0 * len(building_values) / expected_count, 4
                ),
                "suspect_reading_count": quality_counts[
                    building_meter.meter_id
                ].get("suspect", 0),
                "consumption_l": round(building_consumption, 4),
            },
            "apartments": {
                "meter_count": len(apartment_meters),
                "consumption_l": round(apartment_consumption, 4),
            },
            "water_balance": {
                "residual_l": round(residual, 6),
                "relative_residual_percent": round(relative, 8),
                "rounding_tolerance_l": tolerance_l,
                "classification": classification,
                "interpretation": (
                    "This is balance evidence, not attribution to a leak or meter fault."
                ),
            },
            "matched_boundary_count": len(series),
            "series": series,
        }

    def meter_profile(self, meter_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            meter = session.get(MeterRow, meter_id)
            if meter is None:
                raise KeyError(meter_id)
        measurements = self.meter_measurements(meter_id, limit=10_000)
        period = self._period_metadata()
        expected_count = period["expected_boundary_count"]
        suspect_count = sum(item["quality"] == "suspect" for item in measurements)
        consumption_l = (
            (measurements[-1]["value"] - measurements[0]["value"]) * 1000.0
            if measurements
            else 0.0
        )
        return {
            "meter": {
                "meter_id": meter.meter_id,
                "asset_id": meter.asset_id,
                "building_id": meter.building_id,
                "apartment_id": meter.apartment_id,
                "role": meter.role,
                "utility": meter.utility,
                "serial_number": meter.serial_number,
            },
            "period": period,
            "summary": {
                "observed_reading_count": len(measurements),
                "expected_reading_count": expected_count,
                "completeness_percent": round(
                    100.0 * len(measurements) / expected_count, 4
                ),
                "suspect_reading_count": suspect_count,
                "suspect_percent": round(
                    100.0 * suspect_count / len(measurements), 4
                )
                if measurements
                else 0.0,
                "consumption_l": round(consumption_l, 4),
                "latest_reading_utc": measurements[-1]["timestamp"]
                if measurements
                else None,
            },
            "measurements": measurements,
        }

    @staticmethod
    def _issue_dict(
        row: OperatorIssueRow, *, include_runtime_fields: bool
    ) -> dict[str, Any]:
        result = {
            "issue_id": row.issue_id,
            "portfolio_id": row.portfolio_id,
            "building_id": row.building_id,
            "meter_id": row.meter_id,
            "category": row.category,
            "severity": row.severity,
            "title": row.title,
            "description": row.description,
            "evidence": json.loads(row.evidence_json),
            "status": row.status,
            "operator_note": row.operator_note,
            "detected_at_utc": row.detected_at_utc,
        }
        if include_runtime_fields:
            result["updated_at_utc"] = row.updated_at_utc
        return result

    def list_issues(
        self,
        *,
        status: str | None = None,
        building_id: str | None = None,
        include_runtime_fields: bool = True,
    ) -> list[dict[str, Any]]:
        if status is not None and status not in ISSUE_STATUSES:
            raise ValueError(f"unsupported issue status: {status}")
        with Session(self.engine) as session:
            statement = select(OperatorIssueRow)
            if status is not None:
                statement = statement.where(OperatorIssueRow.status == status)
            if building_id is not None:
                statement = statement.where(
                    OperatorIssueRow.building_id == building_id
                )
            rows = session.scalars(
                statement.order_by(
                    OperatorIssueRow.status,
                    OperatorIssueRow.severity.desc(),
                    OperatorIssueRow.building_id,
                    OperatorIssueRow.meter_id,
                )
            ).all()
            return [
                self._issue_dict(
                    row, include_runtime_fields=include_runtime_fields
                )
                for row in rows
            ]

    def update_issue(
        self,
        issue_id: str,
        *,
        status: str | None = None,
        operator_note: str | None = None,
    ) -> dict[str, Any]:
        if status is not None and status not in ISSUE_STATUSES:
            raise ValueError(f"unsupported issue status: {status}")
        if status is None and operator_note is None:
            raise ValueError("status or operator_note must be supplied")
        if operator_note is not None and len(operator_note) > 2000:
            raise ValueError("operator note must not exceed 2000 characters")
        with Session(self.engine) as session, session.begin():
            row = session.get(OperatorIssueRow, issue_id)
            if row is None:
                raise KeyError(issue_id)
            if status is not None:
                row.status = status
            if operator_note is not None:
                row.operator_note = operator_note.strip()
            row.updated_at_utc = _utc_text(datetime.now(UTC))
            session.flush()
            return self._issue_dict(row, include_runtime_fields=True)

    def portfolio_overview(self) -> dict[str, Any]:
        buildings = self.list_buildings()
        issues = self.list_issues(include_runtime_fields=False)
        active_issues = [item for item in issues if item["status"] != "resolved"]
        issues_by_building = Counter(item["building_id"] for item in active_issues)
        cards = []
        for building in buildings:
            balance = self.building_balance(str(building["building_id"]))
            cards.append(
                {
                    **building,
                    "consumption_l": balance["building_meter"]["consumption_l"],
                    "building_meter_completeness_percent": balance[
                        "building_meter"
                    ]["completeness_percent"],
                    "balance_residual_l": balance["water_balance"]["residual_l"],
                    "balance_classification": balance["water_balance"][
                        "classification"
                    ],
                    "active_issue_count": issues_by_building.get(
                        building["building_id"], 0
                    ),
                }
            )
        status_counts = Counter(item["status"] for item in issues)
        severity_counts = Counter(item["severity"] for item in active_issues)
        return {
            "portfolio": self.portfolio_summary(),
            "building_cards": cards,
            "review_queue": {
                "active_issue_count": len(active_issues),
                "status_counts": dict(sorted(status_counts.items())),
                "active_severity_counts": dict(sorted(severity_counts.items())),
            },
            "scope_note": (
                "Synthetic data-quality review signals are interface evidence, "
                "not field-performance claims."
            ),
        }

    def list_imports(self, *, include_runtime_fields: bool = True) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(ImportJobRow).order_by(ImportJobRow.import_id)
            ).all()
            result = [
                {
                    "import_id": row.import_id,
                    "portfolio_id": row.portfolio_id,
                    "source_name": row.source_name,
                    "source_digest": row.source_digest,
                    "status": row.status,
                    "source_row_count": row.source_row_count,
                    "accepted_count": row.accepted_count,
                    "duplicate_count": row.duplicate_count,
                }
                for row in rows
            ]
            if include_runtime_fields:
                for item, row in zip(result, rows):
                    item["completed_at_utc"] = row.completed_at_utc
            return result

    def close(self) -> None:
        self.engine.dispose()
