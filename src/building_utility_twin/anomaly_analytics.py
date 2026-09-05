"""Building reconciliation and anomaly analytics for Experiment 4."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
from typing import Any

from .building_system import (
    BuildingSimulationResult,
    Experiment3Config,
    simulate_building,
)
from .contracts import Quality
from .meters import IdealCumulativeMeter
from .telemetry import (
    MeterObservation,
    ReconciledReading,
    TelemetryFaultConfig,
    reconcile_observations,
)


@dataclass(frozen=True, slots=True)
class OffsetWindow:
    start_offset_seconds: int
    end_offset_seconds: int

    def validate_for(self, duration_seconds: int, step_seconds: int) -> None:
        if not 0 <= self.start_offset_seconds < self.end_offset_seconds:
            raise ValueError("anomaly window offsets are not ordered")
        if self.end_offset_seconds > duration_seconds:
            raise ValueError("anomaly window exceeds the experiment duration")
        if (
            self.start_offset_seconds % step_seconds
            or self.end_offset_seconds % step_seconds
        ):
            raise ValueError("anomaly windows must align with simulation steps")

    def contains_interval(self, offset_seconds: int) -> bool:
        return self.start_offset_seconds <= offset_seconds < self.end_offset_seconds

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OffsetWindow":
        return cls(
            start_offset_seconds=int(payload["start_offset_seconds"]),
            end_offset_seconds=int(payload["end_offset_seconds"]),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "start_offset_seconds": self.start_offset_seconds,
            "end_offset_seconds": self.end_offset_seconds,
        }


@dataclass(frozen=True, slots=True)
class WaterLeakConfig:
    window: OffsetWindow
    flow_l_min: float

    def __post_init__(self) -> None:
        if self.flow_l_min <= 0.0:
            raise ValueError("leak flow must be positive")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WaterLeakConfig":
        return cls(
            window=OffsetWindow.from_dict(payload),
            flow_l_min=float(payload["flow_l_min"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {**self.window.to_dict(), "flow_l_min": self.flow_l_min}


@dataclass(frozen=True, slots=True)
class MeterUnderregistrationConfig:
    meter_id: str
    window: OffsetWindow
    registration_fraction: float

    def __post_init__(self) -> None:
        if not self.meter_id.strip():
            raise ValueError("under-registering meter identifier must not be empty")
        if not 0.0 < self.registration_fraction < 1.0:
            raise ValueError("registration fraction must lie in (0, 1)")

    @classmethod
    def from_dict(
        cls, payload: dict[str, Any]
    ) -> "MeterUnderregistrationConfig":
        return cls(
            meter_id=str(payload["meter_id"]),
            window=OffsetWindow.from_dict(payload),
            registration_fraction=float(payload["registration_fraction"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "meter_id": self.meter_id,
            **self.window.to_dict(),
            "registration_fraction": self.registration_fraction,
        }


@dataclass(frozen=True, slots=True)
class ExcessStorageLossConfig:
    window: OffsetWindow
    loss_multiplier: float

    def __post_init__(self) -> None:
        if self.loss_multiplier <= 1.0:
            raise ValueError("excess-loss multiplier must exceed one")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExcessStorageLossConfig":
        return cls(
            window=OffsetWindow.from_dict(payload),
            loss_multiplier=float(payload["loss_multiplier"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {**self.window.to_dict(), "loss_multiplier": self.loss_multiplier}


@dataclass(frozen=True, slots=True)
class AnalyticsThresholds:
    water_balance_alarm_l_min: float
    thermal_loss_alarm_w: float
    minimum_water_precision: float
    minimum_water_recall: float
    minimum_thermal_precision: float
    minimum_thermal_recall: float
    maximum_telemetry_reconstruction_error_l: float

    def __post_init__(self) -> None:
        if self.water_balance_alarm_l_min <= 0.0:
            raise ValueError("water-balance threshold must be positive")
        if self.thermal_loss_alarm_w <= 0.0:
            raise ValueError("thermal-loss threshold must be positive")
        for name in (
            "minimum_water_precision",
            "minimum_water_recall",
            "minimum_thermal_precision",
            "minimum_thermal_recall",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must lie in [0, 1]")
        if self.maximum_telemetry_reconstruction_error_l <= 0.0:
            raise ValueError("telemetry reconstruction limit must be positive")

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AnalyticsThresholds":
        return cls(
            **{
                field: float(payload[field])
                for field in cls.__dataclass_fields__
            }
        )

    def to_dict(self) -> dict[str, float]:
        return {
            field: float(getattr(self, field)) for field in self.__dataclass_fields__
        }


@dataclass(frozen=True, slots=True)
class Experiment4Config:
    experiment_id: str
    building: Experiment3Config
    telemetry: TelemetryFaultConfig
    water_leak: WaterLeakConfig
    meter_underregistration: MeterUnderregistrationConfig
    excess_storage_loss: ExcessStorageLossConfig
    analytics: AnalyticsThresholds

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        physical = self.building.physical_template
        self.telemetry.validate_for(physical)
        for window in (
            self.water_leak.window,
            self.meter_underregistration.window,
            self.excess_storage_loss.window,
        ):
            window.validate_for(physical.duration_seconds, physical.step_seconds)
        water_windows = (
            self.water_leak.window,
            self.meter_underregistration.window,
        )
        if water_windows[0].end_offset_seconds > water_windows[1].start_offset_seconds:
            raise ValueError("water anomaly windows must not overlap")
        apartment_ids = {item.apartment_id for item in self.building.apartments}
        if self.meter_underregistration.meter_id not in apartment_ids:
            raise ValueError("under-registering meter must identify an apartment")

    @classmethod
    def from_json_file(cls, path: str | Path) -> "Experiment4Config":
        path = Path(path)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls(
            experiment_id=str(payload["experiment_id"]),
            building=Experiment3Config.from_json_file(
                path.parent / str(payload["building_config"])
            ),
            telemetry=TelemetryFaultConfig.from_dict(payload["telemetry"]),
            water_leak=WaterLeakConfig.from_dict(payload["water_leak"]),
            meter_underregistration=MeterUnderregistrationConfig.from_dict(
                payload["meter_underregistration"]
            ),
            excess_storage_loss=ExcessStorageLossConfig.from_dict(
                payload["excess_storage_loss"]
            ),
            analytics=AnalyticsThresholds.from_dict(payload["analytics"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id,
            "building_configuration": self.building.to_dict(),
            "telemetry": self.telemetry.to_dict(),
            "water_leak": self.water_leak.to_dict(),
            "meter_underregistration": self.meter_underregistration.to_dict(),
            "excess_storage_loss": self.excess_storage_loss.to_dict(),
            "analytics": self.analytics.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MeterChannelResult:
    meter_id: str
    physical_cumulative_m3: tuple[float, ...]
    registered_cumulative_m3: tuple[float, ...]
    device_register_m3: tuple[float, ...]
    observations: tuple[MeterObservation, ...]
    packets_by_arrival: tuple[MeterObservation, ...]
    reconciled: tuple[ReconciledReading, ...]
    simulated_rollover_count: int
    telemetry: TelemetryFaultConfig


@dataclass(frozen=True, slots=True)
class WaterBalanceWindow:
    start: datetime
    end: datetime
    available_at: datetime
    duration_seconds: int
    residual_l_min: float
    expected_anomaly_l_min: float
    leak_component_l_min: float
    meter_bias_component_l_min: float
    expected_alarm: bool
    alarm: bool
    quality: Quality


@dataclass(frozen=True, slots=True)
class ThermalBalanceWindow:
    start: datetime
    end: datetime
    duration_seconds: int
    unaccounted_loss_power_w: float
    expected_excess_loss_power_w: float
    expected_alarm: bool
    alarm: bool


@dataclass(frozen=True, slots=True)
class Experiment4SimulationResult:
    building: BuildingSimulationResult
    building_total_with_leak_m3: tuple[float, ...]
    leak_cumulative_m3: tuple[float, ...]
    meter_channels: tuple[MeterChannelResult, ...]
    water_windows: tuple[WaterBalanceWindow, ...]
    thermal_windows: tuple[ThermalBalanceWindow, ...]


def _quantize(value_m3: float, telemetry: TelemetryFaultConfig) -> float:
    resolution_m3 = telemetry.register_resolution_l / 1000.0
    modulus_m3 = telemetry.register_modulus_l / 1000.0
    quantized = round(value_m3 / resolution_m3) * resolution_m3
    return min(max(quantized, 0.0), modulus_m3 - resolution_m3)


def simulate_meter_channel(
    *,
    meter_id: str,
    timestamps: tuple[datetime, ...],
    physical_cumulative_m3: tuple[float, ...],
    physical_step_seconds: int,
    telemetry: TelemetryFaultConfig,
    registration_fractions: tuple[float, ...] | None = None,
) -> MeterChannelResult:
    """Simulate one finite cumulative register and reconcile delivered packets."""

    if not meter_id.strip():
        raise ValueError("meter_id must not be empty")
    if len(timestamps) != len(physical_cumulative_m3):
        raise ValueError("timestamps and cumulative series must have equal length")
    if len(timestamps) < 2:
        raise ValueError("meter channel requires at least one interval")
    interval_count = len(timestamps) - 1
    if registration_fractions is None:
        registration_fractions = (1.0,) * interval_count
    if len(registration_fractions) != interval_count:
        raise ValueError("registration fractions must match the interval count")
    if any(not 0.0 <= value <= 1.0 for value in registration_fractions):
        raise ValueError("registration fractions must lie in [0, 1]")

    increments = tuple(
        right - left
        for left, right in zip(
            physical_cumulative_m3, physical_cumulative_m3[1:]
        )
    )
    if any(value < -1e-15 for value in increments):
        raise ValueError("physical cumulative meter must be monotonic")
    registered = [0.0]
    for increment, fraction in zip(increments, registration_fractions):
        registered.append(registered[-1] + max(increment, 0.0) * fraction)

    rng = random.Random(telemetry.seed)
    modulus_m3 = telemetry.register_modulus_l / 1000.0
    reset_indices = {
        offset // physical_step_seconds for offset in telemetry.reset_offsets_seconds
    }
    segment_m3 = 0.0
    raw_values = [_quantize(0.0, telemetry)]
    pre_reset: dict[int, float] = {}
    rollover_count = 0
    previous_raw = raw_values[0]
    for interval_index, target_increment in enumerate(
        right - left for left, right in zip(registered, registered[1:])
    ):
        measured_increment = target_increment
        if measured_increment > 0.0 and telemetry.increment_noise_std_fraction:
            measured_increment *= max(
                0.0,
                1.0
                + rng.gauss(0.0, telemetry.increment_noise_std_fraction),
            )
        segment_m3 += measured_increment
        boundary_index = interval_index + 1
        raw_before_reset = _quantize(segment_m3 % modulus_m3, telemetry)
        if boundary_index in reset_indices:
            pre_reset[boundary_index] = raw_before_reset
            segment_m3 = 0.0
            raw = _quantize(0.0, telemetry)
        else:
            raw = raw_before_reset
            if raw < previous_raw:
                rollover_count += 1
        raw_values.append(raw)
        previous_raw = raw

    stride = telemetry.readout_interval_seconds // physical_step_seconds
    sample_indices = tuple(range(0, len(timestamps), stride))
    transport_rng = random.Random(telemetry.seed + 1)
    observations = []
    for sequence_number, boundary_index in enumerate(sample_indices):
        event = "reset" if boundary_index in reset_indices else "none"
        forced = boundary_index in (0, len(timestamps) - 1) or event == "reset"
        dropped = (
            False
            if forced
            else transport_rng.random() < telemetry.packet_loss_probability
        )
        delay_seconds = (
            None
            if dropped
            else transport_rng.randint(0, telemetry.max_packet_delay_seconds)
        )
        observed_at = timestamps[boundary_index]
        observations.append(
            MeterObservation(
                sequence_number=sequence_number,
                observed_at=observed_at,
                true_cumulative_m3=physical_cumulative_m3[boundary_index],
                raw_register_m3=raw_values[boundary_index],
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
    observation_tuple = tuple(observations)
    reconciled = reconcile_observations(observation_tuple, telemetry)
    return MeterChannelResult(
        meter_id=meter_id,
        physical_cumulative_m3=physical_cumulative_m3,
        registered_cumulative_m3=tuple(registered),
        device_register_m3=tuple(raw_values),
        observations=observation_tuple,
        packets_by_arrival=tuple(
            sorted(
                (item for item in observation_tuple if item.received_at is not None),
                key=lambda item: (item.received_at, item.sequence_number),
            )
        ),
        reconciled=reconciled,
        simulated_rollover_count=rollover_count,
        telemetry=telemetry,
    )


def _interval_multipliers(
    config: Experiment4Config,
) -> tuple[float, ...]:
    physical = config.building.physical_template
    return tuple(
        (
            config.excess_storage_loss.loss_multiplier
            if config.excess_storage_loss.window.contains_interval(offset)
            else 1.0
        )
        for offset in range(0, physical.duration_seconds, physical.step_seconds)
    )


def _leak_flow(config: Experiment4Config) -> tuple[float, ...]:
    physical = config.building.physical_template
    leak_m3_s = config.water_leak.flow_l_min / 60_000.0
    return tuple(
        leak_m3_s if config.water_leak.window.contains_interval(offset) else 0.0
        for offset in range(0, physical.duration_seconds, physical.step_seconds)
    )


def _registration_fractions(
    config: Experiment4Config, meter_id: str
) -> tuple[float, ...]:
    physical = config.building.physical_template
    return tuple(
        (
            config.meter_underregistration.registration_fraction
            if meter_id == config.meter_underregistration.meter_id
            and config.meter_underregistration.window.contains_interval(offset)
            else 1.0
        )
        for offset in range(0, physical.duration_seconds, physical.step_seconds)
    )


def _water_windows(
    config: Experiment4Config,
    timestamps: tuple[datetime, ...],
    leak_cumulative_m3: tuple[float, ...],
    channels: tuple[MeterChannelResult, ...],
) -> tuple[WaterBalanceWindow, ...]:
    readings = {
        channel.meter_id: {
            item.observation.observed_at: item for item in channel.reconciled
        }
        for channel in channels
    }
    common_times = sorted(
        set.intersection(*(set(values) for values in readings.values()))
    )
    timestamp_index = {timestamp: index for index, timestamp in enumerate(timestamps)}
    channel_by_id = {channel.meter_id: channel for channel in channels}
    building_id = "building"
    apartment_ids = [
        item.apartment_id for item in config.building.apartments
    ]
    threshold = config.analytics.water_balance_alarm_l_min
    windows = []
    for start, end in zip(common_times, common_times[1:]):
        start_index = timestamp_index[start]
        end_index = timestamp_index[end]
        duration_seconds = round((end - start).total_seconds())
        start_readings = {key: values[start] for key, values in readings.items()}
        end_readings = {key: values[end] for key, values in readings.items()}
        building_delta = (
            end_readings[building_id].cumulative_m3
            - start_readings[building_id].cumulative_m3
        )
        apartment_delta = sum(
            end_readings[meter_id].cumulative_m3
            - start_readings[meter_id].cumulative_m3
            for meter_id in apartment_ids
        )
        duration_minutes = duration_seconds / 60.0
        residual_l_min = (building_delta - apartment_delta) * 1000.0 / duration_minutes
        leak_delta = leak_cumulative_m3[end_index] - leak_cumulative_m3[start_index]
        biased_channel = channel_by_id[config.meter_underregistration.meter_id]
        bias_delta = (
            biased_channel.physical_cumulative_m3[end_index]
            - biased_channel.physical_cumulative_m3[start_index]
            - biased_channel.registered_cumulative_m3[end_index]
            + biased_channel.registered_cumulative_m3[start_index]
        )
        leak_component = leak_delta * 1000.0 / duration_minutes
        bias_component = bias_delta * 1000.0 / duration_minutes
        expected = leak_component + bias_component
        all_readings = tuple(start_readings.values()) + tuple(end_readings.values())
        quality = (
            Quality.SUSPECT
            if any(item.quality is Quality.SUSPECT for item in all_readings)
            else Quality.GOOD
        )
        windows.append(
            WaterBalanceWindow(
                start=start,
                end=end,
                available_at=max(
                    item.observation.received_at or item.observation.observed_at
                    for item in end_readings.values()
                ),
                duration_seconds=duration_seconds,
                residual_l_min=residual_l_min,
                expected_anomaly_l_min=expected,
                leak_component_l_min=leak_component,
                meter_bias_component_l_min=bias_component,
                expected_alarm=expected >= threshold,
                alarm=abs(residual_l_min) >= threshold,
                quality=quality,
            )
        )
    return tuple(windows)


def _thermal_windows(
    config: Experiment4Config, result: BuildingSimulationResult
) -> tuple[ThermalBalanceWindow, ...]:
    physical = config.building.physical_template
    tank_config = config.building.shared_tank
    stride = config.telemetry.readout_interval_seconds // physical.step_seconds
    tank = result.shared_tank
    windows = []
    for start_index in range(0, len(result.total_flow_m3_s), stride):
        end_index = min(start_index + stride, len(result.total_flow_m3_s))
        if end_index <= start_index:
            continue
        duration_seconds = (end_index - start_index) * physical.step_seconds
        stored_change_j = (
            tank.stored_energy_j[end_index] - tank.stored_energy_j[start_index]
        )
        boiler_j = (
            tank.boiler_output_energy_j[end_index]
            - tank.boiler_output_energy_j[start_index]
        )
        dhw_j = (
            tank.dhw_output_energy_j[end_index]
            - tank.dhw_output_energy_j[start_index]
        )
        nominal_loss_j = sum(
            tank_config.standing_loss_coefficient_w_k
            * max(
                tank.temperature_k[index]
                - 273.15
                - tank_config.ambient_temperature_c,
                0.0,
            )
            * physical.step_seconds
            for index in range(start_index, end_index)
        )
        actual_loss_j = (
            tank.standing_loss_energy_j[end_index]
            - tank.standing_loss_energy_j[start_index]
        )
        residual_j = stored_change_j - (boiler_j - dhw_j - nominal_loss_j)
        unaccounted_w = -residual_j / duration_seconds
        expected_w = (actual_loss_j - nominal_loss_j) / duration_seconds
        threshold = config.analytics.thermal_loss_alarm_w
        windows.append(
            ThermalBalanceWindow(
                start=result.timestamps[start_index],
                end=result.timestamps[end_index],
                duration_seconds=duration_seconds,
                unaccounted_loss_power_w=unaccounted_w,
                expected_excess_loss_power_w=expected_w,
                expected_alarm=expected_w >= threshold,
                alarm=unaccounted_w >= threshold,
            )
        )
    return tuple(windows)


def simulate_experiment_4(config: Experiment4Config) -> Experiment4SimulationResult:
    physical = config.building.physical_template
    building = simulate_building(
        config.building,
        standing_loss_multipliers=_interval_multipliers(config),
    )
    leak_flow = _leak_flow(config)
    leak_cumulative = IdealCumulativeMeter().readings(
        leak_flow, physical.step_seconds
    )
    building_with_leak = tuple(
        value + leak
        for value, leak in zip(building.total_volume_m3, leak_cumulative)
    )
    meter_inputs = [("building", building_with_leak)] + [
        (item.spec.apartment_id, item.demand.total_volume_m3)
        for item in building.apartments
    ]
    channels = []
    for index, (meter_id, cumulative) in enumerate(meter_inputs):
        telemetry = replace(
            config.telemetry,
            seed=config.telemetry.seed + 10 * index,
            reset_offsets_seconds=(
                config.telemetry.reset_offsets_seconds
                if meter_id == "building"
                else ()
            ),
        )
        channels.append(
            simulate_meter_channel(
                meter_id=meter_id,
                timestamps=building.timestamps,
                physical_cumulative_m3=cumulative,
                physical_step_seconds=physical.step_seconds,
                telemetry=telemetry,
                registration_fractions=_registration_fractions(config, meter_id),
            )
        )
    channel_tuple = tuple(channels)
    return Experiment4SimulationResult(
        building=building,
        building_total_with_leak_m3=building_with_leak,
        leak_cumulative_m3=leak_cumulative,
        meter_channels=channel_tuple,
        water_windows=_water_windows(
            config, building.timestamps, leak_cumulative, channel_tuple
        ),
        thermal_windows=_thermal_windows(config, building),
    )
