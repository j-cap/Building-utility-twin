"""Versioned relational backend for canonical utility measurements."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
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
        with Session(self.engine) as session:
            existing = session.get(ImportJobRow, import_id)
            if existing is not None:
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
