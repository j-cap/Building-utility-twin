"""Deterministic synthetic portfolios for pilot and interface development."""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .contracts import Measurement, Quality, Quantity


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    portfolio_id: str
    portfolio_name: str
    start: datetime
    days: int
    interval_minutes: int
    building_count: int
    apartments_per_building: int
    minimum_occupants: int
    maximum_occupants: int
    daily_liters_per_person: float
    apartment_missing_probability: float
    building_missing_probability: float
    suspect_probability: float
    seed: int

    def __post_init__(self) -> None:
        if not self.portfolio_id.strip() or not self.portfolio_name.strip():
            raise ValueError("portfolio identity must not be empty")
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("portfolio start must be timezone-aware")
        if self.days <= 0 or self.interval_minutes <= 0:
            raise ValueError("duration and interval must be positive")
        if 1440 % self.interval_minutes:
            raise ValueError("interval_minutes must divide a day")
        if self.building_count <= 0 or self.apartments_per_building <= 0:
            raise ValueError("portfolio topology counts must be positive")
        if not 1 <= self.minimum_occupants <= self.maximum_occupants:
            raise ValueError("occupant bounds are invalid")
        if self.daily_liters_per_person <= 0.0:
            raise ValueError("daily consumption must be positive")
        for name in (
            "apartment_missing_probability",
            "building_missing_probability",
            "suspect_probability",
        ):
            value = getattr(self, name)
            if not 0.0 <= value < 1.0:
                raise ValueError(f"{name} must lie in [0, 1)")

    @classmethod
    def from_json_file(cls, path: str | Path) -> PortfolioConfig:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        payload["start"] = datetime.fromisoformat(payload["start"])
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "portfolio_name": self.portfolio_name,
            "start": self.start.astimezone(UTC).isoformat(),
            "days": self.days,
            "interval_minutes": self.interval_minutes,
            "building_count": self.building_count,
            "apartments_per_building": self.apartments_per_building,
            "minimum_occupants": self.minimum_occupants,
            "maximum_occupants": self.maximum_occupants,
            "daily_liters_per_person": self.daily_liters_per_person,
            "apartment_missing_probability": self.apartment_missing_probability,
            "building_missing_probability": self.building_missing_probability,
            "suspect_probability": self.suspect_probability,
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class BuildingRecord:
    building_id: str
    portfolio_id: str
    name: str
    timezone: str


@dataclass(frozen=True, slots=True)
class ApartmentRecord:
    apartment_id: str
    building_id: str
    name: str
    occupants: int


@dataclass(frozen=True, slots=True)
class MeterRecord:
    meter_id: str
    asset_id: str
    building_id: str
    apartment_id: str | None
    role: str
    utility: str
    serial_number: str


@dataclass(frozen=True, slots=True)
class SyntheticPortfolio:
    config: PortfolioConfig
    buildings: tuple[BuildingRecord, ...]
    apartments: tuple[ApartmentRecord, ...]
    meters: tuple[MeterRecord, ...]
    measurements: tuple[Measurement, ...]

    @property
    def digest(self) -> str:
        digest = hashlib.sha256()
        digest.update(
            json.dumps(
                self.config.to_dict(), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        for collection in (self.buildings, self.apartments, self.meters):
            for item in collection:
                digest.update(repr(item).encode("utf-8"))
                digest.update(b"\n")
        for item in self.measurements:
            digest.update(item.to_json().encode("utf-8"))
            digest.update(b"\n")
        return digest.hexdigest()


_HOURLY_PROFILE = (
    0.010, 0.008, 0.007, 0.007, 0.010, 0.025,
    0.075, 0.090, 0.055, 0.035, 0.030, 0.032,
    0.038, 0.034, 0.030, 0.030, 0.040, 0.065,
    0.090, 0.095, 0.075, 0.055, 0.035, 0.024,
)


def _interval_weight(timestamp: datetime, interval_minutes: int) -> float:
    start_hour = timestamp.hour
    fraction = interval_minutes / 60.0
    return _HOURLY_PROFILE[start_hour] * fraction


def _quality(rng: random.Random, probability: float) -> Quality:
    return Quality.SUSPECT if rng.random() < probability else Quality.GOOD


def generate_portfolio(config: PortfolioConfig) -> SyntheticPortfolio:
    """Generate a portfolio with exact building/apartment physical aggregation."""

    step = timedelta(minutes=config.interval_minutes)
    interval_count = config.days * 1440 // config.interval_minutes
    timestamps = tuple(config.start + index * step for index in range(interval_count + 1))
    buildings: list[BuildingRecord] = []
    apartments: list[ApartmentRecord] = []
    meters: list[MeterRecord] = []
    measurements: list[Measurement] = []

    for building_index in range(1, config.building_count + 1):
        building_id = f"building-{building_index:03d}"
        buildings.append(
            BuildingRecord(
                building_id, config.portfolio_id,
                f"Synthetic Building {building_index:03d}", "Europe/Vienna"
            )
        )
        apartment_cumulative: list[list[float]] = []
        for apartment_index in range(1, config.apartments_per_building + 1):
            apartment_id = f"{building_id}-apartment-{apartment_index:03d}"
            seed = config.seed + building_index * 100_000 + apartment_index
            rng = random.Random(seed)
            occupants = rng.randint(config.minimum_occupants, config.maximum_occupants)
            apartments.append(
                ApartmentRecord(
                    apartment_id, building_id,
                    f"Apartment {building_index:03d}/{apartment_index:03d}",
                    occupants,
                )
            )
            meter_id = f"meter-{building_index:03d}-{apartment_index:03d}"
            asset_id = f"{apartment_id}-water-meter"
            meters.append(
                MeterRecord(
                    meter_id, asset_id, building_id, apartment_id,
                    "apartment", "water", f"APT{building_index:03d}{apartment_index:03d}"
                )
            )
            cumulative = [0.0]
            for interval_index in range(interval_count):
                timestamp = timestamps[interval_index]
                day_factor = 0.85 + 0.30 * rng.random()
                pulse = max(0.0, 1.0 + rng.gauss(0.0, 0.35))
                increment_l = (
                    occupants
                    * config.daily_liters_per_person
                    * _interval_weight(timestamp, config.interval_minutes)
                    * day_factor
                    * pulse
                )
                cumulative.append(cumulative[-1] + increment_l / 1000.0)
            apartment_cumulative.append(cumulative)
            for index, (timestamp, value) in enumerate(zip(timestamps, cumulative)):
                boundary = index in (0, interval_count)
                if not boundary and rng.random() < config.apartment_missing_probability:
                    continue
                measurements.append(
                    Measurement.create(
                        asset_id=asset_id,
                        channel="total_water_register_reconciled",
                        quantity=Quantity.CUMULATIVE_VOLUME,
                        timestamp=timestamp,
                        value=round(value, 7),
                        quality=_quality(rng, config.suspect_probability),
                        source="synthetic-portfolio-v1",
                    )
                )

        building_meter_id = f"meter-{building_index:03d}-building"
        building_asset_id = f"{building_id}-water-meter"
        meters.append(
            MeterRecord(
                building_meter_id, building_asset_id, building_id, None,
                "building", "water", f"BLDG{building_index:03d}"
            )
        )
        building_rng = random.Random(config.seed + building_index * 1_000_000)
        building_cumulative = [
            math.fsum(series[index] for series in apartment_cumulative)
            for index in range(interval_count + 1)
        ]
        for index, (timestamp, value) in enumerate(zip(timestamps, building_cumulative)):
            boundary = index in (0, interval_count)
            if not boundary and building_rng.random() < config.building_missing_probability:
                continue
            measurements.append(
                Measurement.create(
                    asset_id=building_asset_id,
                    channel="total_water_register_reconciled",
                    quantity=Quantity.CUMULATIVE_VOLUME,
                    timestamp=timestamp,
                    value=round(value, 7),
                    quality=_quality(building_rng, config.suspect_probability),
                    source="synthetic-portfolio-v1",
                )
            )

    return SyntheticPortfolio(
        config=config,
        buildings=tuple(buildings),
        apartments=tuple(apartments),
        meters=tuple(meters),
        measurements=tuple(
            sorted(measurements, key=lambda item: (item.timestamp, item.asset_id))
        ),
    )
