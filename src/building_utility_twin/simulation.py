"""One-pipe physical simulation and virtual cumulative meter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
from typing import Any


SECONDS_PER_DAY = 86_400


def parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("start_utc must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    experiment_id: str
    asset_id: str
    meter_id: str
    start_utc: datetime
    duration_seconds: int
    step_seconds: int
    seed: int
    occupants: int
    base_flow_l_min: float
    event_probability_per_minute: float
    conservation_tolerance_m3: float

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.asset_id or not self.meter_id:
            raise ValueError("experiment and asset identifiers must not be empty")
        if self.duration_seconds != SECONDS_PER_DAY:
            raise ValueError("Experiment 0 must cover exactly one day")
        if self.start_utc.tzinfo is None or self.start_utc.utcoffset() is None:
            raise ValueError("start_utc must be timezone-aware")
        if self.start_utc.utcoffset() != timedelta(0):
            raise ValueError("start_utc must be expressed in UTC")
        if self.step_seconds <= 0 or self.duration_seconds % self.step_seconds:
            raise ValueError("step_seconds must divide the one-day duration")
        if self.occupants <= 0:
            raise ValueError("occupants must be positive")
        if self.base_flow_l_min < 0.0:
            raise ValueError("base flow must be non-negative")
        if not 0.0 <= self.event_probability_per_minute <= 1.0:
            raise ValueError("event probability must lie in [0, 1]")
        if self.conservation_tolerance_m3 <= 0.0:
            raise ValueError("conservation tolerance must be positive")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExperimentConfig":
        return cls(
            experiment_id=str(payload["experiment_id"]),
            asset_id=str(payload["asset_id"]),
            meter_id=str(payload["meter_id"]),
            start_utc=parse_utc(str(payload["start_utc"])),
            duration_seconds=int(payload["duration_seconds"]),
            step_seconds=int(payload["step_seconds"]),
            seed=int(payload["seed"]),
            occupants=int(payload["occupants"]),
            base_flow_l_min=float(payload["base_flow_l_min"]),
            event_probability_per_minute=float(
                payload["event_probability_per_minute"]
            ),
            conservation_tolerance_m3=float(
                payload["conservation_tolerance_m3"]
            ),
        )

    @classmethod
    def from_json_file(cls, path: str | Path) -> "ExperimentConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "asset_id": self.asset_id,
            "meter_id": self.meter_id,
            "start_utc": self.start_utc.isoformat(timespec="seconds").replace(
                "+00:00", "Z"
            ),
            "duration_seconds": self.duration_seconds,
            "step_seconds": self.step_seconds,
            "seed": self.seed,
            "occupants": self.occupants,
            "base_flow_l_min": self.base_flow_l_min,
            "event_probability_per_minute": self.event_probability_per_minute,
            "conservation_tolerance_m3": self.conservation_tolerance_m3,
        }


@dataclass(frozen=True, slots=True)
class SimulationResult:
    timestamps: tuple[datetime, ...]
    flow_m3_s: tuple[float, ...]
    cumulative_volume_m3: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.timestamps) != len(self.flow_m3_s) + 1:
            raise ValueError("timestamps must contain both interval boundaries")
        if len(self.cumulative_volume_m3) != len(self.timestamps):
            raise ValueError("cumulative volume must be defined at every boundary")


def _demand_multiplier(hour: float) -> float:
    """Smooth residential morning/evening usage profile."""

    morning = math.exp(-0.5 * ((hour - 7.25) / 1.25) ** 2)
    evening = math.exp(-0.5 * ((hour - 19.0) / 1.8) ** 2)
    midday = math.exp(-0.5 * ((hour - 12.5) / 2.5) ** 2)
    return 0.35 + 1.9 * morning + 1.6 * evening + 0.55 * midday


def _draw_event(rng: random.Random) -> tuple[int, float]:
    """Return event duration in minutes and rate in litres per minute."""

    draw = rng.random()
    if draw < 0.55:  # short tap draw
        return rng.randint(1, 3), rng.uniform(1.5, 5.0)
    if draw < 0.82:  # sink or appliance draw
        return rng.randint(3, 8), rng.uniform(3.0, 7.0)
    if draw < 0.96:  # shower-like draw
        return rng.randint(5, 11), rng.uniform(6.0, 10.0)
    return rng.randint(2, 4), rng.uniform(9.0, 13.0)


def simulate_one_pipe_day(config: ExperimentConfig) -> SimulationResult:
    """Simulate a lossless pipe and ideal cumulative meter for one day."""

    rng = random.Random(config.seed)
    count = config.duration_seconds // config.step_seconds
    step_minutes = config.step_seconds / 60.0
    flow_l_min = [config.base_flow_l_min for _ in range(count)]

    for index in range(count):
        hour = index * config.step_seconds / 3600.0
        probability = (
            config.event_probability_per_minute
            * step_minutes
            * config.occupants
            * _demand_multiplier(hour)
        )
        if rng.random() < min(probability, 1.0):
            duration_minutes, rate_l_min = _draw_event(rng)
            duration_steps = max(1, math.ceil(duration_minutes / step_minutes))
            for active_index in range(index, min(index + duration_steps, count)):
                flow_l_min[active_index] += rate_l_min

    flow_m3_s = tuple(rate / 60_000.0 for rate in flow_l_min)
    timestamps = tuple(
        config.start_utc + timedelta(seconds=index * config.step_seconds)
        for index in range(count + 1)
    )

    cumulative = [0.0]
    for rate in flow_m3_s:
        cumulative.append(cumulative[-1] + rate * config.step_seconds)

    return SimulationResult(
        timestamps=timestamps,
        flow_m3_s=flow_m3_s,
        cumulative_volume_m3=tuple(cumulative),
    )
