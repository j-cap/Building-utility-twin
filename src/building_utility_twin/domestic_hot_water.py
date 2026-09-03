"""Fixture-based water demand and central-boiler simulation for Experiment 1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import random
from typing import Any

from .meters import IdealCumulativeMeter
from .simulation import SECONDS_PER_DAY, parse_utc


SUPPORTED_TIME_PROFILES = {
    "awake",
    "morning_evening",
    "meals",
    "daytime",
    "evening",
}

REQUIRED_ASSETS = {
    "water_inlet",
    "cold_branch",
    "hot_branch",
    "boiler",
    "water_meter",
    "cold_meter",
    "hot_meter",
    "heat_meter",
    "boiler_energy_meter",
}


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    name: str
    daily_volume_share: float
    hot_fraction: float
    mean_event_volume_l: float
    nominal_flow_l_min: float
    time_profile: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("fixture name must not be empty")
        if not 0.0 < self.daily_volume_share <= 1.0:
            raise ValueError("daily_volume_share must lie in (0, 1]")
        if not 0.0 <= self.hot_fraction <= 1.0:
            raise ValueError("hot_fraction must lie in [0, 1]")
        if self.mean_event_volume_l <= 0.0 or self.nominal_flow_l_min <= 0.0:
            raise ValueError("fixture volumes and flow rates must be positive")
        if self.time_profile not in SUPPORTED_TIME_PROFILES:
            raise ValueError(f"unsupported time profile: {self.time_profile}")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FixtureSpec":
        return cls(
            name=str(payload["name"]),
            daily_volume_share=float(payload["daily_volume_share"]),
            hot_fraction=float(payload["hot_fraction"]),
            mean_event_volume_l=float(payload["mean_event_volume_l"]),
            nominal_flow_l_min=float(payload["nominal_flow_l_min"]),
            time_profile=str(payload["time_profile"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "daily_volume_share": self.daily_volume_share,
            "hot_fraction": self.hot_fraction,
            "mean_event_volume_l": self.mean_event_volume_l,
            "nominal_flow_l_min": self.nominal_flow_l_min,
            "time_profile": self.time_profile,
        }


@dataclass(frozen=True, slots=True)
class Experiment1Config:
    experiment_id: str
    start_utc: datetime
    duration_seconds: int
    step_seconds: int
    seed: int
    occupants: int
    target_total_l_per_person_day: float
    target_hot_l_per_person_day: float
    cold_supply_temperature_c: float
    hot_supply_temperature_c: float
    water_density_kg_m3: float
    water_heat_capacity_j_kg_k: float
    boiler_efficiency: float
    conservation_tolerance_m3: float
    energy_tolerance_j: float
    asset_ids: dict[str, str]
    fixtures: tuple[FixtureSpec, ...]

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        if self.duration_seconds != SECONDS_PER_DAY:
            raise ValueError("Experiment 1 must cover exactly one day")
        if self.start_utc.tzinfo is None or self.start_utc.utcoffset() != timedelta(0):
            raise ValueError("start_utc must be timezone-aware UTC")
        if self.step_seconds <= 0 or self.duration_seconds % self.step_seconds:
            raise ValueError("step_seconds must divide the one-day duration")
        if self.occupants <= 0:
            raise ValueError("occupants must be positive")
        if not 0.0 < self.target_hot_l_per_person_day < self.target_total_l_per_person_day:
            raise ValueError("hot-water target must lie between zero and total water")
        if self.hot_supply_temperature_c <= self.cold_supply_temperature_c:
            raise ValueError("hot supply temperature must exceed cold supply temperature")
        if self.water_density_kg_m3 <= 0.0 or self.water_heat_capacity_j_kg_k <= 0.0:
            raise ValueError("water properties must be positive")
        if not 0.0 < self.boiler_efficiency <= 1.0:
            raise ValueError("boiler_efficiency must lie in (0, 1]")
        if self.conservation_tolerance_m3 <= 0.0 or self.energy_tolerance_j <= 0.0:
            raise ValueError("conservation tolerances must be positive")
        if set(self.asset_ids) != REQUIRED_ASSETS:
            missing = sorted(REQUIRED_ASSETS - set(self.asset_ids))
            extra = sorted(set(self.asset_ids) - REQUIRED_ASSETS)
            raise ValueError(f"asset_ids mismatch; missing={missing}, extra={extra}")
        if any(not value.strip() for value in self.asset_ids.values()):
            raise ValueError("asset identifiers must not be empty")
        if len(set(self.asset_ids.values())) != len(self.asset_ids):
            raise ValueError("asset identifiers must be unique")
        if not self.fixtures:
            raise ValueError("at least one fixture is required")

        volume_share = sum(item.daily_volume_share for item in self.fixtures)
        if not math.isclose(volume_share, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("fixture daily-volume shares must sum to one")
        modeled_hot_fraction = sum(
            item.daily_volume_share * item.hot_fraction for item in self.fixtures
        )
        target_hot_fraction = (
            self.target_hot_l_per_person_day / self.target_total_l_per_person_day
        )
        if not math.isclose(
            modeled_hot_fraction, target_hot_fraction, rel_tol=0.0, abs_tol=1e-12
        ):
            raise ValueError(
                "fixture hot fractions must reproduce the configured hot-water target"
            )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Experiment1Config":
        return cls(
            experiment_id=str(payload["experiment_id"]),
            start_utc=parse_utc(str(payload["start_utc"])),
            duration_seconds=int(payload["duration_seconds"]),
            step_seconds=int(payload["step_seconds"]),
            seed=int(payload["seed"]),
            occupants=int(payload["occupants"]),
            target_total_l_per_person_day=float(
                payload["target_total_l_per_person_day"]
            ),
            target_hot_l_per_person_day=float(
                payload["target_hot_l_per_person_day"]
            ),
            cold_supply_temperature_c=float(payload["cold_supply_temperature_c"]),
            hot_supply_temperature_c=float(payload["hot_supply_temperature_c"]),
            water_density_kg_m3=float(payload["water_density_kg_m3"]),
            water_heat_capacity_j_kg_k=float(
                payload["water_heat_capacity_j_kg_k"]
            ),
            boiler_efficiency=float(payload["boiler_efficiency"]),
            conservation_tolerance_m3=float(
                payload["conservation_tolerance_m3"]
            ),
            energy_tolerance_j=float(payload["energy_tolerance_j"]),
            asset_ids={str(key): str(value) for key, value in payload["asset_ids"].items()},
            fixtures=tuple(FixtureSpec.from_dict(item) for item in payload["fixtures"]),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Experiment1Config":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "start_utc": self.start_utc.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "duration_seconds": self.duration_seconds,
            "step_seconds": self.step_seconds,
            "seed": self.seed,
            "occupants": self.occupants,
            "target_total_l_per_person_day": self.target_total_l_per_person_day,
            "target_hot_l_per_person_day": self.target_hot_l_per_person_day,
            "cold_supply_temperature_c": self.cold_supply_temperature_c,
            "hot_supply_temperature_c": self.hot_supply_temperature_c,
            "water_density_kg_m3": self.water_density_kg_m3,
            "water_heat_capacity_j_kg_k": self.water_heat_capacity_j_kg_k,
            "boiler_efficiency": self.boiler_efficiency,
            "conservation_tolerance_m3": self.conservation_tolerance_m3,
            "energy_tolerance_j": self.energy_tolerance_j,
            "asset_ids": dict(self.asset_ids),
            "fixtures": [item.to_dict() for item in self.fixtures],
        }


@dataclass(frozen=True, slots=True)
class FixtureEvent:
    fixture: str
    start_index: int
    duration_steps: int
    total_volume_l: float
    hot_fraction: float

    @property
    def cold_volume_l(self) -> float:
        return self.total_volume_l * (1.0 - self.hot_fraction)

    @property
    def hot_volume_l(self) -> float:
        return self.total_volume_l * self.hot_fraction


@dataclass(frozen=True, slots=True)
class FixtureDemandResult:
    timestamps: tuple[datetime, ...]
    total_flow_m3_s: tuple[float, ...]
    cold_flow_m3_s: tuple[float, ...]
    hot_flow_m3_s: tuple[float, ...]
    total_volume_m3: tuple[float, ...]
    cold_volume_m3: tuple[float, ...]
    hot_volume_m3: tuple[float, ...]
    events: tuple[FixtureEvent, ...]


@dataclass(frozen=True, slots=True)
class ThermalSimulationResult:
    demand: FixtureDemandResult
    useful_power_w: tuple[float, ...]
    boiler_input_power_w: tuple[float, ...]
    useful_energy_j: tuple[float, ...]
    boiler_input_energy_j: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CentralBoiler:
    cold_temperature_k: float
    hot_temperature_k: float
    efficiency: float
    water_density_kg_m3: float
    water_heat_capacity_j_kg_k: float

    def __post_init__(self) -> None:
        if self.hot_temperature_k <= self.cold_temperature_k:
            raise ValueError("hot temperature must exceed cold temperature")
        if not 0.0 < self.efficiency <= 1.0:
            raise ValueError("efficiency must lie in (0, 1]")

    def power_from_hot_flow(self, hot_flow_m3_s: float) -> tuple[float, float]:
        if hot_flow_m3_s < 0.0:
            raise ValueError("hot-water flow must be non-negative")
        useful = (
            self.water_density_kg_m3
            * self.water_heat_capacity_j_kg_k
            * hot_flow_m3_s
            * (self.hot_temperature_k - self.cold_temperature_k)
        )
        return useful, useful / self.efficiency


def _gaussian(hour: float, center: float, width: float) -> float:
    return math.exp(-0.5 * ((hour - center) / width) ** 2)


def _time_weight(profile: str, hour: float) -> float:
    if profile == "awake":
        return 1.0 if 6.0 <= hour < 23.0 else 0.08
    if profile == "morning_evening":
        return 0.03 + _gaussian(hour, 7.25, 1.2) + 0.8 * _gaussian(hour, 20.0, 1.5)
    if profile == "meals":
        return (
            0.05
            + 0.8 * _gaussian(hour, 7.5, 0.9)
            + 0.6 * _gaussian(hour, 12.5, 1.0)
            + _gaussian(hour, 19.0, 1.2)
        )
    if profile == "daytime":
        return 0.03 + _gaussian(hour, 13.0, 3.5)
    if profile == "evening":
        return 0.03 + _gaussian(hour, 19.5, 1.8)
    raise ValueError(f"unsupported time profile: {profile}")


def _events_for_fixture(
    config: Experiment1Config, fixture: FixtureSpec, rng: random.Random
) -> list[FixtureEvent]:
    fixture_volume_l = (
        config.occupants
        * config.target_total_l_per_person_day
        * fixture.daily_volume_share
    )
    event_count = max(1, round(fixture_volume_l / fixture.mean_event_volume_l))
    raw_weights = [rng.lognormvariate(0.0, 0.25) for _ in range(event_count)]
    volumes = [fixture_volume_l * weight / sum(raw_weights) for weight in raw_weights]
    step_minutes = config.step_seconds / 60.0
    interval_count = config.duration_seconds // config.step_seconds

    events = []
    for volume_l in volumes:
        duration_steps = max(
            1, math.ceil(volume_l / fixture.nominal_flow_l_min / step_minutes)
        )
        latest_start = interval_count - duration_steps
        candidates = range(latest_start + 1)
        time_weights = [
            _time_weight(
                fixture.time_profile, index * config.step_seconds / 3600.0
            )
            for index in candidates
        ]
        start_index = rng.choices(candidates, weights=time_weights, k=1)[0]
        events.append(
            FixtureEvent(
                fixture=fixture.name,
                start_index=start_index,
                duration_steps=duration_steps,
                total_volume_l=volume_l,
                hot_fraction=fixture.hot_fraction,
            )
        )
    return events


def generate_fixture_demand(config: Experiment1Config) -> FixtureDemandResult:
    """Generate a calibrated reference day with stochastic event timing."""

    rng = random.Random(config.seed)
    events = []
    for fixture in config.fixtures:
        events.extend(_events_for_fixture(config, fixture, rng))
    events.sort(key=lambda item: (item.start_index, item.fixture))

    interval_count = config.duration_seconds // config.step_seconds
    step_minutes = config.step_seconds / 60.0
    total_l_min = [0.0] * interval_count
    cold_l_min = [0.0] * interval_count
    hot_l_min = [0.0] * interval_count

    for event in events:
        rate_l_min = event.total_volume_l / (event.duration_steps * step_minutes)
        for index in range(event.start_index, event.start_index + event.duration_steps):
            total_l_min[index] += rate_l_min
            hot_l_min[index] += rate_l_min * event.hot_fraction
            cold_l_min[index] += rate_l_min * (1.0 - event.hot_fraction)

    total_flow = tuple(value / 60_000.0 for value in total_l_min)
    cold_flow = tuple(value / 60_000.0 for value in cold_l_min)
    hot_flow = tuple(value / 60_000.0 for value in hot_l_min)
    timestamps = tuple(
        config.start_utc + timedelta(seconds=index * config.step_seconds)
        for index in range(interval_count + 1)
    )
    meter = IdealCumulativeMeter()
    return FixtureDemandResult(
        timestamps=timestamps,
        total_flow_m3_s=total_flow,
        cold_flow_m3_s=cold_flow,
        hot_flow_m3_s=hot_flow,
        total_volume_m3=meter.readings(total_flow, config.step_seconds),
        cold_volume_m3=meter.readings(cold_flow, config.step_seconds),
        hot_volume_m3=meter.readings(hot_flow, config.step_seconds),
        events=tuple(events),
    )


def simulate_domestic_hot_water(
    config: Experiment1Config,
) -> ThermalSimulationResult:
    demand = generate_fixture_demand(config)
    boiler = CentralBoiler(
        cold_temperature_k=config.cold_supply_temperature_c + 273.15,
        hot_temperature_k=config.hot_supply_temperature_c + 273.15,
        efficiency=config.boiler_efficiency,
        water_density_kg_m3=config.water_density_kg_m3,
        water_heat_capacity_j_kg_k=config.water_heat_capacity_j_kg_k,
    )
    powers = tuple(boiler.power_from_hot_flow(rate) for rate in demand.hot_flow_m3_s)
    useful_power = tuple(item[0] for item in powers)
    input_power = tuple(item[1] for item in powers)
    meter = IdealCumulativeMeter()
    return ThermalSimulationResult(
        demand=demand,
        useful_power_w=useful_power,
        boiler_input_power_w=input_power,
        useful_energy_j=meter.readings(useful_power, config.step_seconds),
        boiler_input_energy_j=meter.readings(input_power, config.step_seconds),
    )
