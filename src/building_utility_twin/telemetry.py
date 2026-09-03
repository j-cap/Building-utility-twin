"""Imperfect cumulative-meter and telemetry models for Experiment 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import random
from typing import Any

from .contracts import Quality
from .domestic_hot_water import (
    Experiment1Config,
    ThermalSimulationResult,
    simulate_domestic_hot_water,
)


@dataclass(frozen=True, slots=True)
class TelemetryFaultConfig:
    """Configuration of the device and transport imperfections."""

    readout_interval_seconds: int
    register_resolution_l: float
    increment_noise_std_fraction: float
    register_modulus_l: float
    packet_loss_probability: float
    max_packet_delay_seconds: int
    reset_offsets_seconds: tuple[int, ...]
    seed: int
    rollover_drop_threshold_fraction: float

    def validate_for(self, physical: Experiment1Config) -> None:
        if self.readout_interval_seconds <= 0:
            raise ValueError("readout_interval_seconds must be positive")
        if self.readout_interval_seconds % physical.step_seconds:
            raise ValueError("readout interval must be a multiple of the physical step")
        if physical.duration_seconds % self.readout_interval_seconds:
            raise ValueError("readout interval must divide the experiment duration")
        if self.register_resolution_l <= 0.0:
            raise ValueError("register resolution must be positive")
        if self.increment_noise_std_fraction < 0.0:
            raise ValueError("increment noise standard deviation must be non-negative")
        if self.register_modulus_l <= self.register_resolution_l:
            raise ValueError("register modulus must exceed its resolution")
        if not 0.0 <= self.packet_loss_probability < 1.0:
            raise ValueError("packet loss probability must lie in [0, 1)")
        if self.max_packet_delay_seconds < 0:
            raise ValueError("maximum packet delay must be non-negative")
        if not 0.0 < self.rollover_drop_threshold_fraction < 1.0:
            raise ValueError("rollover threshold fraction must lie in (0, 1)")
        previous = -1
        for offset in self.reset_offsets_seconds:
            if not 0 < offset < physical.duration_seconds:
                raise ValueError("reset offsets must lie strictly inside the experiment")
            if offset % self.readout_interval_seconds:
                raise ValueError("reset offsets must coincide with scheduled readouts")
            if offset <= previous:
                raise ValueError("reset offsets must be strictly increasing")
            previous = offset

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TelemetryFaultConfig":
        return cls(
            readout_interval_seconds=int(payload["readout_interval_seconds"]),
            register_resolution_l=float(payload["register_resolution_l"]),
            increment_noise_std_fraction=float(
                payload["increment_noise_std_fraction"]
            ),
            register_modulus_l=float(payload["register_modulus_l"]),
            packet_loss_probability=float(payload["packet_loss_probability"]),
            max_packet_delay_seconds=int(payload["max_packet_delay_seconds"]),
            reset_offsets_seconds=tuple(
                int(value) for value in payload["reset_offsets_seconds"]
            ),
            seed=int(payload["seed"]),
            rollover_drop_threshold_fraction=float(
                payload["rollover_drop_threshold_fraction"]
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "readout_interval_seconds": self.readout_interval_seconds,
            "register_resolution_l": self.register_resolution_l,
            "increment_noise_std_fraction": self.increment_noise_std_fraction,
            "register_modulus_l": self.register_modulus_l,
            "packet_loss_probability": self.packet_loss_probability,
            "max_packet_delay_seconds": self.max_packet_delay_seconds,
            "reset_offsets_seconds": list(self.reset_offsets_seconds),
            "seed": self.seed,
            "rollover_drop_threshold_fraction": (
                self.rollover_drop_threshold_fraction
            ),
        }


@dataclass(frozen=True, slots=True)
class Experiment2Config:
    experiment_id: str
    physical: Experiment1Config
    telemetry: TelemetryFaultConfig
    maximum_reconstruction_error_l: float
    maximum_end_error_l: float

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        self.telemetry.validate_for(self.physical)
        if self.maximum_reconstruction_error_l <= 0.0:
            raise ValueError("maximum reconstruction error must be positive")
        if self.maximum_end_error_l <= 0.0:
            raise ValueError("maximum end error must be positive")

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Experiment2Config":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        physical_path = path.parent / str(payload["physical_config"])
        return cls(
            experiment_id=str(payload["experiment_id"]),
            physical=Experiment1Config.from_json_file(physical_path),
            telemetry=TelemetryFaultConfig.from_dict(payload["telemetry"]),
            maximum_reconstruction_error_l=float(
                payload["maximum_reconstruction_error_l"]
            ),
            maximum_end_error_l=float(payload["maximum_end_error_l"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "physical_configuration": self.physical.to_dict(),
            "telemetry": self.telemetry.to_dict(),
            "maximum_reconstruction_error_l": self.maximum_reconstruction_error_l,
            "maximum_end_error_l": self.maximum_end_error_l,
        }


@dataclass(frozen=True, slots=True)
class MeterObservation:
    sequence_number: int
    observed_at: datetime
    true_cumulative_m3: float
    raw_register_m3: float
    event: str
    pre_reset_register_m3: float | None
    dropped: bool
    received_at: datetime | None

    @property
    def delay_seconds(self) -> int | None:
        if self.received_at is None:
            return None
        return round((self.received_at - self.observed_at).total_seconds())


@dataclass(frozen=True, slots=True)
class ReconciledReading:
    observation: MeterObservation
    cumulative_m3: float
    adjustment: str
    quality: Quality
    observation_gap_seconds: int


@dataclass(frozen=True, slots=True)
class TelemetrySimulationResult:
    physical: ThermalSimulationResult
    device_register_m3: tuple[float, ...]
    observations: tuple[MeterObservation, ...]
    packets_by_arrival: tuple[MeterObservation, ...]
    reconciled: tuple[ReconciledReading, ...]
    simulated_rollover_count: int


def _quantize_register(value_m3: float, config: TelemetryFaultConfig) -> float:
    resolution_m3 = config.register_resolution_l / 1000.0
    modulus_m3 = config.register_modulus_l / 1000.0
    quantized = round(value_m3 / resolution_m3) * resolution_m3
    return min(max(quantized, 0.0), modulus_m3 - resolution_m3)


def _device_trace(
    physical: ThermalSimulationResult,
    physical_config: Experiment1Config,
    telemetry: TelemetryFaultConfig,
) -> tuple[tuple[float, ...], dict[int, float], int]:
    """Create a noisy, quantized, rolling register at physical-step resolution."""

    rng = random.Random(telemetry.seed)
    modulus_m3 = telemetry.register_modulus_l / 1000.0
    reset_indices = {
        offset // physical_config.step_seconds
        for offset in telemetry.reset_offsets_seconds
    }
    segment_volume_m3 = 0.0
    values = [_quantize_register(0.0, telemetry)]
    pre_reset: dict[int, float] = {}
    rollover_count = 0
    previous_raw = values[0]

    for interval_index, rate_m3_s in enumerate(
        physical.demand.total_flow_m3_s
    ):
        increment_m3 = rate_m3_s * physical_config.step_seconds
        if increment_m3 > 0.0 and telemetry.increment_noise_std_fraction > 0.0:
            increment_m3 *= max(
                0.0, 1.0 + rng.gauss(0.0, telemetry.increment_noise_std_fraction)
            )
        segment_volume_m3 += increment_m3
        boundary_index = interval_index + 1
        raw_before_reset = _quantize_register(
            segment_volume_m3 % modulus_m3, telemetry
        )
        if boundary_index in reset_indices:
            pre_reset[boundary_index] = raw_before_reset
            segment_volume_m3 = 0.0
            raw = _quantize_register(0.0, telemetry)
        else:
            raw = raw_before_reset
            if raw < previous_raw and boundary_index - 1 not in reset_indices:
                rollover_count += 1
        values.append(raw)
        previous_raw = raw
    return tuple(values), pre_reset, rollover_count


def _observations(
    physical: ThermalSimulationResult,
    physical_config: Experiment1Config,
    telemetry: TelemetryFaultConfig,
    device_register_m3: tuple[float, ...],
    pre_reset: dict[int, float],
) -> tuple[MeterObservation, ...]:
    stride = telemetry.readout_interval_seconds // physical_config.step_seconds
    reset_indices = set(pre_reset)
    sample_indices = tuple(range(0, len(device_register_m3), stride))
    rng = random.Random(telemetry.seed + 1)
    observations = []
    for sequence_number, boundary_index in enumerate(sample_indices):
        event = "reset" if boundary_index in reset_indices else "none"
        forced_delivery = (
            boundary_index == 0
            or boundary_index == len(device_register_m3) - 1
            or event == "reset"
        )
        dropped = (
            False
            if forced_delivery
            else rng.random() < telemetry.packet_loss_probability
        )
        observed_at = physical.demand.timestamps[boundary_index]
        delay_seconds = (
            None
            if dropped
            else rng.randint(0, telemetry.max_packet_delay_seconds)
        )
        observations.append(
            MeterObservation(
                sequence_number=sequence_number,
                observed_at=observed_at,
                true_cumulative_m3=physical.demand.total_volume_m3[boundary_index],
                raw_register_m3=device_register_m3[boundary_index],
                event=event,
                pre_reset_register_m3=pre_reset.get(boundary_index),
                dropped=dropped,
                received_at=(
                    None
                    if delay_seconds is None
                    else observed_at + timedelta(seconds=delay_seconds)
                ),
            )
        )
    return tuple(observations)


def reconcile_observations(
    observations: tuple[MeterObservation, ...],
    telemetry: TelemetryFaultConfig,
) -> tuple[ReconciledReading, ...]:
    """Reorder received packets and unwrap rollovers and declared resets."""

    delivered = sorted(
        (item for item in observations if not item.dropped),
        key=lambda item: (item.observed_at, item.sequence_number),
    )
    if not delivered:
        return ()
    modulus_m3 = telemetry.register_modulus_l / 1000.0
    rollover_threshold_m3 = (
        telemetry.rollover_drop_threshold_fraction * modulus_m3
    )
    first = delivered[0]
    readings = [
        ReconciledReading(
            observation=first,
            cumulative_m3=first.raw_register_m3,
            adjustment="none",
            quality=Quality.GOOD,
            observation_gap_seconds=0,
        )
    ]
    previous_raw = first.raw_register_m3
    previous_corrected = first.raw_register_m3
    previous_timestamp = first.observed_at

    for observation in delivered[1:]:
        gap_seconds = round(
            (observation.observed_at - previous_timestamp).total_seconds()
        )
        adjustment = "none"
        if observation.event == "reset":
            if observation.pre_reset_register_m3 is None:
                raise ValueError("reset observation requires its pre-reset register")
            delta_to_reset = observation.pre_reset_register_m3 - previous_raw
            if delta_to_reset < -rollover_threshold_m3:
                delta_to_reset += modulus_m3
                adjustment = "rollover+reset"
            else:
                adjustment = "reset"
            delta_m3 = delta_to_reset + observation.raw_register_m3
        else:
            delta_m3 = observation.raw_register_m3 - previous_raw
            if delta_m3 < -rollover_threshold_m3:
                delta_m3 += modulus_m3
                adjustment = "rollover"
        if delta_m3 < -1e-12:
            raise ValueError("unexplained backward register step")
        corrected = previous_corrected + max(delta_m3, 0.0)
        quality = (
            Quality.GOOD
            if adjustment == "none"
            and gap_seconds == telemetry.readout_interval_seconds
            else Quality.SUSPECT
        )
        readings.append(
            ReconciledReading(
                observation=observation,
                cumulative_m3=corrected,
                adjustment=adjustment,
                quality=quality,
                observation_gap_seconds=gap_seconds,
            )
        )
        previous_raw = observation.raw_register_m3
        previous_corrected = corrected
        previous_timestamp = observation.observed_at
    return tuple(readings)


def simulate_imperfect_telemetry(config: Experiment2Config) -> TelemetrySimulationResult:
    physical = simulate_domestic_hot_water(config.physical)
    device_register, pre_reset, simulated_rollovers = _device_trace(
        physical, config.physical, config.telemetry
    )
    observations = _observations(
        physical, config.physical, config.telemetry, device_register, pre_reset
    )
    packets_by_arrival = tuple(
        sorted(
            (item for item in observations if item.received_at is not None),
            key=lambda item: (item.received_at, item.sequence_number),
        )
    )
    reconciled = reconcile_observations(observations, config.telemetry)
    return TelemetrySimulationResult(
        physical=physical,
        device_register_m3=device_register,
        observations=observations,
        packets_by_arrival=packets_by_arrival,
        reconciled=reconciled,
        simulated_rollover_count=simulated_rollovers,
    )
