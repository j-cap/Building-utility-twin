"""Reproducible runner for Iteration E / Experiment 4."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Sequence

from .anomaly_analytics import (
    Experiment4Config,
    Experiment4SimulationResult,
    MeterChannelResult,
    ThermalBalanceWindow,
    WaterBalanceWindow,
    simulate_experiment_4,
)
from .contracts import Measurement, Quality, Quantity
from .storage import JsonLinesMeasurementStore


@dataclass(frozen=True, slots=True)
class Experiment4Artifacts:
    output_directory: Path
    measurements_path: Path
    telemetry_path: Path
    water_balance_path: Path
    thermal_balance_path: Path
    alarms_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _asset_id(config: Experiment4Config, meter_id: str) -> str:
    if meter_id == "building":
        return config.building.physical_template.asset_ids["water_meter"]
    return f"{meter_id}-water-meter"


def _measurement_records(
    config: Experiment4Config, result: Experiment4SimulationResult
) -> list[Measurement]:
    records: list[Measurement] = []
    for channel in result.meter_channels:
        reconciled_by_sequence = {
            item.observation.sequence_number: item for item in channel.reconciled
        }
        for observation in channel.observations:
            if observation.dropped:
                continue
            raw_quality = (
                Quality.SUSPECT if observation.event == "reset" else Quality.GOOD
            )
            records.append(
                Measurement.create(
                    asset_id=_asset_id(config, channel.meter_id),
                    channel="total_water_register_raw",
                    quantity=Quantity.CUMULATIVE_VOLUME,
                    timestamp=observation.observed_at,
                    value=observation.raw_register_m3,
                    quality=raw_quality,
                    source="imperfect-building-water-meter",
                )
            )
            reconciled = reconciled_by_sequence[observation.sequence_number]
            records.append(
                Measurement.create(
                    asset_id=_asset_id(config, channel.meter_id),
                    channel="total_water_register_reconciled",
                    quantity=Quantity.CUMULATIVE_VOLUME,
                    timestamp=observation.observed_at,
                    value=reconciled.cumulative_m3,
                    quality=reconciled.quality,
                    source="building-meter-register-reconciler",
                )
            )
    for window in result.water_windows:
        records.append(
            Measurement.create(
                asset_id=config.building.physical_template.asset_ids["water_meter"],
                channel="building_water_balance_residual",
                quantity=Quantity.VOLUMETRIC_FLOW_RATE,
                timestamp=window.end,
                value=window.residual_l_min / 60_000.0,
                quality=window.quality,
                source="building-water-balance-analytics",
                duration_seconds=window.duration_seconds,
            )
        )
    for window in result.thermal_windows:
        records.append(
            Measurement.create(
                asset_id=config.building.asset_ids["shared_tank"],
                channel="unaccounted_storage_loss_power",
                quantity=Quantity.THERMAL_POWER,
                timestamp=window.end,
                value=window.unaccounted_loss_power_w,
                source="shared-storage-balance-analytics",
                duration_seconds=window.duration_seconds,
            )
        )
    return records


def _write_telemetry(
    path: Path, result: Experiment4SimulationResult
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "meter_id",
                "sequence_number",
                "observed_at_utc",
                "physical_cumulative_l",
                "registered_target_l",
                "raw_register_l",
                "event",
                "status",
                "received_at_utc",
                "delay_seconds",
                "arrival_rank",
                "reconciled_cumulative_l",
                "physical_error_l",
                "telemetry_reconstruction_error_l",
                "quality",
                "adjustment",
            )
        )
        for channel in result.meter_channels:
            stride = (
                channel.telemetry.readout_interval_seconds
                // round(
                    (
                        result.building.timestamps[1]
                        - result.building.timestamps[0]
                    ).total_seconds()
                )
            )
            arrival_rank = {
                item.sequence_number: index
                for index, item in enumerate(channel.packets_by_arrival)
            }
            reconciled_by_sequence = {
                item.observation.sequence_number: item
                for item in channel.reconciled
            }
            for observation in channel.observations:
                reconciled = reconciled_by_sequence.get(observation.sequence_number)
                boundary_index = observation.sequence_number * stride
                registered_target = channel.registered_cumulative_m3[boundary_index]
                writer.writerow(
                    (
                        channel.meter_id,
                        observation.sequence_number,
                        _timestamp(observation.observed_at),
                        format(observation.true_cumulative_m3 * 1000.0, ".17g"),
                        format(registered_target * 1000.0, ".17g"),
                        format(observation.raw_register_m3 * 1000.0, ".17g"),
                        observation.event,
                        "dropped" if observation.dropped else "delivered",
                        (
                            ""
                            if observation.received_at is None
                            else _timestamp(observation.received_at)
                        ),
                        (
                            ""
                            if observation.delay_seconds is None
                            else observation.delay_seconds
                        ),
                        arrival_rank.get(observation.sequence_number, ""),
                        (
                            ""
                            if reconciled is None
                            else format(reconciled.cumulative_m3 * 1000.0, ".17g")
                        ),
                        (
                            ""
                            if reconciled is None
                            else format(
                                (
                                    reconciled.cumulative_m3
                                    - observation.true_cumulative_m3
                                )
                                * 1000.0,
                                ".17g",
                            )
                        ),
                        (
                            ""
                            if reconciled is None
                            else format(
                                (reconciled.cumulative_m3 - registered_target)
                                * 1000.0,
                                ".17g",
                            )
                        ),
                        "" if reconciled is None else reconciled.quality.value,
                        "" if reconciled is None else reconciled.adjustment,
                    )
                )


def _write_water_balance(path: Path, windows: tuple[WaterBalanceWindow, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "window_start_utc",
                "window_end_utc",
                "available_at_utc",
                "duration_seconds",
                "residual_l_min",
                "expected_anomaly_l_min",
                "leak_component_l_min",
                "meter_bias_component_l_min",
                "expected_alarm",
                "alarm",
                "quality",
            )
        )
        for item in windows:
            writer.writerow(
                (
                    _timestamp(item.start),
                    _timestamp(item.end),
                    _timestamp(item.available_at),
                    item.duration_seconds,
                    format(item.residual_l_min, ".17g"),
                    format(item.expected_anomaly_l_min, ".17g"),
                    format(item.leak_component_l_min, ".17g"),
                    format(item.meter_bias_component_l_min, ".17g"),
                    item.expected_alarm,
                    item.alarm,
                    item.quality.value,
                )
            )


def _write_thermal_balance(
    path: Path, windows: tuple[ThermalBalanceWindow, ...]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "window_start_utc",
                "window_end_utc",
                "duration_seconds",
                "unaccounted_loss_power_w",
                "expected_excess_loss_power_w",
                "expected_alarm",
                "alarm",
            )
        )
        for item in windows:
            writer.writerow(
                (
                    _timestamp(item.start),
                    _timestamp(item.end),
                    item.duration_seconds,
                    format(item.unaccounted_loss_power_w, ".17g"),
                    format(item.expected_excess_loss_power_w, ".17g"),
                    item.expected_alarm,
                    item.alarm,
                )
            )


def _write_alarms(
    path: Path,
    config: Experiment4Config,
    result: Experiment4SimulationResult,
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "anomaly_type",
                "asset_id",
                "window_start_utc",
                "window_end_utc",
                "available_at_utc",
                "observed_value",
                "threshold",
                "unit",
                "severity_ratio",
                "evidence",
            )
        )
        for item in result.water_windows:
            if not item.alarm:
                continue
            threshold = config.analytics.water_balance_alarm_l_min
            writer.writerow(
                (
                    "water_balance_anomaly",
                    config.building.physical_template.asset_ids["water_meter"],
                    _timestamp(item.start),
                    _timestamp(item.end),
                    _timestamp(item.available_at),
                    format(item.residual_l_min, ".17g"),
                    format(threshold, ".17g"),
                    "L/min",
                    format(abs(item.residual_l_min) / threshold, ".17g"),
                    "building register minus sum of apartment registers",
                )
            )
        for item in result.thermal_windows:
            if not item.alarm:
                continue
            threshold = config.analytics.thermal_loss_alarm_w
            writer.writerow(
                (
                    "unaccounted_storage_loss",
                    config.building.asset_ids["shared_tank"],
                    _timestamp(item.start),
                    _timestamp(item.end),
                    _timestamp(item.end),
                    format(item.unaccounted_loss_power_w, ".17g"),
                    format(threshold, ".17g"),
                    "W",
                    format(item.unaccounted_loss_power_w / threshold, ".17g"),
                    "tank-state balance using nominal standing-loss coefficient",
                )
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _out_of_order_count(channel: MeterChannelResult) -> int:
    maximum_sequence = -1
    count = 0
    for packet in channel.packets_by_arrival:
        if packet.sequence_number < maximum_sequence:
            count += 1
        maximum_sequence = max(maximum_sequence, packet.sequence_number)
    return count


def _classification_metrics(windows: Sequence[object]) -> dict[str, object]:
    true_positive = sum(
        bool(getattr(item, "expected_alarm")) and bool(getattr(item, "alarm"))
        for item in windows
    )
    false_positive = sum(
        not bool(getattr(item, "expected_alarm")) and bool(getattr(item, "alarm"))
        for item in windows
    )
    false_negative = sum(
        bool(getattr(item, "expected_alarm")) and not bool(getattr(item, "alarm"))
        for item in windows
    )
    true_negative = len(windows) - true_positive - false_positive - false_negative
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 1.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 1.0
    )
    return {
        "window_count": len(windows),
        "true_positive_count": true_positive,
        "false_positive_count": false_positive,
        "false_negative_count": false_negative,
        "true_negative_count": true_negative,
        "precision": precision,
        "recall": recall,
    }


def _event_detection(
    windows: tuple[WaterBalanceWindow, ...],
    start: datetime,
    end: datetime,
) -> dict[str, object]:
    matching = [
        item
        for item in windows
        if item.alarm and item.start < end and item.end > start
    ]
    if not matching:
        return {"detected": False, "detection_delay_seconds": None}
    first = min(matching, key=lambda item: item.available_at)
    return {
        "detected": True,
        "detection_delay_seconds": max(
            0, round((first.available_at - start).total_seconds())
        ),
    }


def _meter_summary(
    result: Experiment4SimulationResult,
) -> tuple[dict[str, object], float, bool]:
    summary: dict[str, object] = {}
    maximum_reconstruction_error_l = 0.0
    all_discontinuities_recovered = True
    for channel in result.meter_channels:
        stride = channel.telemetry.readout_interval_seconds // round(
            (
                result.building.timestamps[1] - result.building.timestamps[0]
            ).total_seconds()
        )
        errors = []
        for item in channel.reconciled:
            boundary_index = item.observation.sequence_number * stride
            errors.append(
                (
                    item.cumulative_m3
                    - channel.registered_cumulative_m3[boundary_index]
                )
                * 1000.0
            )
        detected_rollovers = sum(
            "rollover" in item.adjustment for item in channel.reconciled
        )
        detected_resets = sum("reset" in item.adjustment for item in channel.reconciled)
        expected_resets = len(channel.telemetry.reset_offsets_seconds)
        maximum_error = max(abs(value) for value in errors)
        maximum_reconstruction_error_l = max(
            maximum_reconstruction_error_l, maximum_error
        )
        recovered = (
            detected_rollovers == channel.simulated_rollover_count
            and detected_resets == expected_resets
        )
        all_discontinuities_recovered = all_discontinuities_recovered and recovered
        delivered = [item for item in channel.observations if not item.dropped]
        summary[channel.meter_id] = {
            "scheduled_readout_count": len(channel.observations),
            "delivered_packet_count": len(delivered),
            "dropped_packet_count": len(channel.observations) - len(delivered),
            "delivery_ratio": len(delivered) / len(channel.observations),
            "out_of_order_packet_count": _out_of_order_count(channel),
            "simulated_rollover_count": channel.simulated_rollover_count,
            "detected_rollover_count": detected_rollovers,
            "configured_reset_count": expected_resets,
            "detected_reset_count": detected_resets,
            "maximum_telemetry_reconstruction_error_l": maximum_error,
            "physical_end_volume_l": channel.physical_cumulative_m3[-1] * 1000.0,
            "registered_end_volume_l": (
                channel.registered_cumulative_m3[-1] * 1000.0
            ),
        }
    return summary, maximum_reconstruction_error_l, all_discontinuities_recovered


def _build_summary(
    config: Experiment4Config,
    result: Experiment4SimulationResult,
    measurements_path: Path,
    telemetry_path: Path,
    water_balance_path: Path,
    thermal_balance_path: Path,
    alarms_path: Path,
    stored_measurement_count: int,
) -> dict[str, object]:
    water_metrics = _classification_metrics(result.water_windows)
    thermal_metrics = _classification_metrics(result.thermal_windows)
    meter_summary, maximum_telemetry_error_l, discontinuities_recovered = (
        _meter_summary(result)
    )
    start = result.building.timestamps[0]
    leak_start = start + timedelta(
        seconds=config.water_leak.window.start_offset_seconds
    )
    leak_end = start + timedelta(seconds=config.water_leak.window.end_offset_seconds)
    bias_start = start + timedelta(
        seconds=config.meter_underregistration.window.start_offset_seconds
    )
    bias_end = start + timedelta(
        seconds=config.meter_underregistration.window.end_offset_seconds
    )
    leak_detection = _event_detection(result.water_windows, leak_start, leak_end)
    bias_detection = _event_detection(result.water_windows, bias_start, bias_end)
    telemetry_faults = {
        "finite_resolution": config.telemetry.register_resolution_l > 0.0,
        "measurement_noise": config.telemetry.increment_noise_std_fraction > 0.0,
        "sparse_readout": (
            config.telemetry.readout_interval_seconds
            > config.building.physical_template.step_seconds
        ),
        "packet_loss": any(
            item.dropped
            for channel in result.meter_channels
            for item in channel.observations
        ),
        "packet_delay": any(
            (item.delay_seconds or 0) > 0
            for channel in result.meter_channels
            for item in channel.observations
        ),
        "out_of_order_arrival": any(
            _out_of_order_count(channel) > 0 for channel in result.meter_channels
        ),
        "register_rollover": any(
            channel.simulated_rollover_count > 0
            for channel in result.meter_channels
        ),
        "register_reset": bool(config.telemetry.reset_offsets_seconds),
    }
    expected_measurement_count = (
        2
        * sum(
            sum(not item.dropped for item in channel.observations)
            for channel in result.meter_channels
        )
        + len(result.water_windows)
        + len(result.thermal_windows)
    )
    checks = {
        "all_telemetry_faults_exercised": all(telemetry_faults.values()),
        "rollovers_and_resets_recovered": discontinuities_recovered,
        "telemetry_reconstruction_within_limit": (
            maximum_telemetry_error_l
            <= config.analytics.maximum_telemetry_reconstruction_error_l
        ),
        "water_precision": (
            float(water_metrics["precision"])
            >= config.analytics.minimum_water_precision
        ),
        "water_recall": (
            float(water_metrics["recall"])
            >= config.analytics.minimum_water_recall
        ),
        "leak_event_detected": bool(leak_detection["detected"]),
        "meter_fault_event_detected": bool(bias_detection["detected"]),
        "thermal_precision": (
            float(thermal_metrics["precision"])
            >= config.analytics.minimum_thermal_precision
        ),
        "thermal_recall": (
            float(thermal_metrics["recall"])
            >= config.analytics.minimum_thermal_recall
        ),
        "canonical_round_trip": stored_measurement_count
        == expected_measurement_count,
    }
    biased_meter = next(
        item
        for item in result.meter_channels
        if item.meter_id == config.meter_underregistration.meter_id
    )
    actual_standing_loss_kwh = (
        result.building.shared_tank.standing_loss_energy_j[-1] / 3_600_000.0
    )
    baseline = simulate_experiment_4_baseline_loss(config)
    actual_tank = result.building.shared_tank
    baseline_tank = baseline.shared_tank
    baseline_standing_loss_kwh = (
        baseline_tank.standing_loss_energy_j[-1] / 3_600_000.0
    )
    actual_input_kwh = actual_tank.boiler_input_energy_j[-1] / 3_600_000.0
    baseline_input_kwh = baseline_tank.boiler_input_energy_j[-1] / 3_600_000.0
    return {
        "schema_version": "1.0",
        "experiment_id": config.experiment_id,
        "configuration": config.to_dict(),
        "injections": {
            "unmetered_leak_volume_l": result.leak_cumulative_m3[-1] * 1000.0,
            "meter_underregistration_volume_l": (
                biased_meter.physical_cumulative_m3[-1]
                - biased_meter.registered_cumulative_m3[-1]
            )
            * 1000.0,
            "standing_loss_energy_kwh": actual_standing_loss_kwh,
            "baseline_standing_loss_energy_kwh": baseline_standing_loss_kwh,
            "excess_standing_loss_energy_kwh": (
                actual_standing_loss_kwh - baseline_standing_loss_kwh
            ),
        },
        "telemetry": {
            "fault_classes_exercised": telemetry_faults,
            "meter_channels": meter_summary,
            "maximum_reconstruction_error_l": maximum_telemetry_error_l,
            "all_discontinuities_recovered": discontinuities_recovered,
        },
        "water_balance": {
            **water_metrics,
            "alarm_threshold_l_min": config.analytics.water_balance_alarm_l_min,
            "common_window_count": len(result.water_windows),
            "leak_event": leak_detection,
            "meter_underregistration_event": bias_detection,
            "source_identifiable_from_balance_alone": False,
            "identifiability_note": (
                "A positive building-minus-apartment residual detects missing "
                "volume but cannot by itself distinguish an unmetered leak from "
                "an under-registering apartment meter."
            ),
        },
        "thermal_balance": {
            **thermal_metrics,
            "alarm_threshold_w": config.analytics.thermal_loss_alarm_w,
            "maximum_unaccounted_loss_power_w": max(
                item.unaccounted_loss_power_w for item in result.thermal_windows
            ),
            "plant_impact": {
                "baseline_boiler_input_energy_kwh": baseline_input_kwh,
                "anomalous_boiler_input_energy_kwh": actual_input_kwh,
                "additional_boiler_input_energy_kwh": (
                    actual_input_kwh - baseline_input_kwh
                ),
                "baseline_heater_runtime_minutes": sum(
                    baseline_tank.heater_enabled
                ),
                "anomalous_heater_runtime_minutes": sum(
                    actual_tank.heater_enabled
                ),
                "baseline_heater_cycle_count": _cycles(
                    baseline_tank.heater_enabled
                ),
                "anomalous_heater_cycle_count": _cycles(
                    actual_tank.heater_enabled
                ),
                "baseline_final_temperature_c": (
                    baseline_tank.temperature_k[-1] - 273.15
                ),
                "anomalous_final_temperature_c": (
                    actual_tank.temperature_k[-1] - 273.15
                ),
            },
        },
        "results": {
            "stored_measurement_count": stored_measurement_count,
            "alarm_count": sum(item.alarm for item in result.water_windows)
            + sum(item.alarm for item in result.thermal_windows),
        },
        "acceptance": {"checks": checks, "passed": all(checks.values())},
        "reproducibility": {
            "building_apartment_seeds": [
                item.seed for item in config.building.apartments
            ],
            "telemetry_seed": config.telemetry.seed,
            "measurements_sha256": _sha256(measurements_path),
            "telemetry_sha256": _sha256(telemetry_path),
            "water_balance_sha256": _sha256(water_balance_path),
            "thermal_balance_sha256": _sha256(thermal_balance_path),
            "alarms_sha256": _sha256(alarms_path),
        },
    }


def simulate_experiment_4_baseline_loss(config: Experiment4Config):
    """Return the same building day without the excess standing-loss injection."""

    from .building_system import simulate_building

    return simulate_building(config.building)


def _cycles(enabled: tuple[bool, ...]) -> int:
    return sum(
        value and (index == 0 or not enabled[index - 1])
        for index, value in enumerate(enabled)
    )


def _plot_result(
    path: Path,
    config: Experiment4Config,
    result: Experiment4SimulationResult,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    start = result.building.timestamps[0]
    leak_start = start + timedelta(
        seconds=config.water_leak.window.start_offset_seconds
    )
    leak_end = start + timedelta(seconds=config.water_leak.window.end_offset_seconds)
    bias_start = start + timedelta(
        seconds=config.meter_underregistration.window.start_offset_seconds
    )
    bias_end = start + timedelta(
        seconds=config.meter_underregistration.window.end_offset_seconds
    )
    loss_start = start + timedelta(
        seconds=config.excess_storage_loss.window.start_offset_seconds
    )
    loss_end = start + timedelta(
        seconds=config.excess_storage_loss.window.end_offset_seconds
    )

    plt.rcParams.update(
        {"font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
    )
    figure, axes = plt.subplots(4, 1, figsize=(7.2, 8.4), sharex=True)

    water_times = [item.end for item in result.water_windows]
    axes[0].plot(
        water_times,
        [item.residual_l_min for item in result.water_windows],
        color="#146c94",
        linewidth=1.0,
        marker=".",
        markersize=2.5,
        label="estimated balance residual",
    )
    axes[0].plot(
        water_times,
        [item.expected_anomaly_l_min for item in result.water_windows],
        color="#333333",
        linewidth=1.0,
        linestyle="--",
        label="injected missing volume",
    )
    threshold = config.analytics.water_balance_alarm_l_min
    axes[0].axhline(threshold, color="#d62828", linewidth=0.9, label="alarm threshold")
    axes[0].axhline(-threshold, color="#d62828", linewidth=0.9)
    axes[0].set_ylabel("Water residual\n[L/min]")
    axes[0].set_title("Experiment 4: reconciliation and anomaly analytics")
    axes[0].legend(ncol=3, frameon=False, fontsize=7.5)
    axes[0].grid(alpha=0.22)

    colors = ("#333333", "#146c94", "#2a9d8f", "#e9c46a", "#c7511f")
    for color, channel in zip(colors, result.meter_channels):
        stride = channel.telemetry.readout_interval_seconds // round(
            (
                result.building.timestamps[1] - result.building.timestamps[0]
            ).total_seconds()
        )
        axes[1].plot(
            [item.observation.observed_at for item in channel.reconciled],
            [
                (
                    item.cumulative_m3
                    - channel.physical_cumulative_m3[
                        item.observation.sequence_number * stride
                    ]
                )
                * 1000.0
                for item in channel.reconciled
            ],
            linewidth=0.9,
            color=color,
            label=channel.meter_id,
        )
    axes[1].set_ylabel("Register error [L]")
    axes[1].legend(ncol=5, frameon=False, fontsize=7.2)
    axes[1].grid(alpha=0.22)

    thermal_times = [item.end for item in result.thermal_windows]
    axes[2].plot(
        thermal_times,
        [item.unaccounted_loss_power_w for item in result.thermal_windows],
        color="#7b2cbf",
        linewidth=1.1,
        label="estimated unaccounted loss",
    )
    axes[2].plot(
        thermal_times,
        [item.expected_excess_loss_power_w for item in result.thermal_windows],
        color="#333333",
        linestyle="--",
        linewidth=0.9,
        label="injected excess loss",
    )
    axes[2].axhline(
        config.analytics.thermal_loss_alarm_w,
        color="#d62828",
        linewidth=0.9,
        label="alarm threshold",
    )
    axes[2].set_ylabel("Thermal residual [W]")
    axes[2].legend(ncol=3, frameon=False, fontsize=7.5)
    axes[2].grid(alpha=0.22)

    tank = result.building.shared_tank
    axes[3].plot(
        result.building.timestamps,
        [value - 273.15 for value in tank.temperature_k],
        color="#c7511f",
        linewidth=1.1,
        label="tank temperature",
    )
    axes[3].set_ylabel("Tank [°C]")
    axes[3].set_xlabel("Time (UTC)")
    axes[3].grid(alpha=0.22)
    power_axis = axes[3].twinx()
    power_axis.step(
        result.building.timestamps[:-1],
        [value / 1000.0 for value in tank.boiler_output_power_w],
        where="post",
        color="#7b2cbf",
        linewidth=0.8,
        alpha=0.75,
        label="boiler output",
    )
    power_axis.set_ylabel("Boiler [kW]")
    power_axis.spines["top"].set_visible(False)
    axes[3].legend(loc="lower left", frameon=False, fontsize=8)
    power_axis.legend(loc="lower right", frameon=False, fontsize=8)

    for axis in axes[:2]:
        axis.axvspan(leak_start, leak_end, color="#2a9d8f", alpha=0.10)
        axis.axvspan(bias_start, bias_end, color="#e9c46a", alpha=0.16)
    for axis in axes[2:]:
        axis.axvspan(loss_start, loss_end, color="#7b2cbf", alpha=0.10)
    axes[3].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[3].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[3].set_xlim(result.building.timestamps[0], result.building.timestamps[-1])
    figure.tight_layout()
    figure.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
        metadata={"Software": "Building Utility Twin", "Creation Time": None},
    )
    plt.close(figure)


def run_experiment_4(
    config: Experiment4Config, output_directory: str | Path
) -> Experiment4Artifacts:
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    measurements_path = output_directory / "measurements.jsonl"
    telemetry_path = output_directory / "telemetry.csv"
    water_balance_path = output_directory / "water_balance.csv"
    thermal_balance_path = output_directory / "thermal_balance.csv"
    alarms_path = output_directory / "alarms.csv"
    summary_path = output_directory / "summary.json"
    figure_path = output_directory / "experiment_4_anomaly_analytics.png"

    result = simulate_experiment_4(config)
    records = _measurement_records(config, result)
    store = JsonLinesMeasurementStore(measurements_path)
    stored_measurement_count = store.replace(records)
    if store.read_all() != sorted(
        records, key=lambda item: (item.timestamp, item.asset_id, item.channel)
    ):
        raise RuntimeError("file-backed measurement round-trip changed the data")
    _write_telemetry(telemetry_path, result)
    _write_water_balance(water_balance_path, result.water_windows)
    _write_thermal_balance(thermal_balance_path, result.thermal_windows)
    _write_alarms(alarms_path, config, result)
    summary = _build_summary(
        config,
        result,
        measurements_path,
        telemetry_path,
        water_balance_path,
        thermal_balance_path,
        alarms_path,
        stored_measurement_count,
    )
    if not summary["acceptance"]["passed"]:  # type: ignore[index]
        raise RuntimeError("Experiment 4 failed an acceptance check")
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _plot_result(figure_path, config, result)
    return Experiment4Artifacts(
        output_directory=output_directory,
        measurements_path=measurements_path,
        telemetry_path=telemetry_path,
        water_balance_path=water_balance_path,
        thermal_balance_path=thermal_balance_path,
        alarms_path=alarms_path,
        summary_path=summary_path,
        figure_path=figure_path,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/experiment_4.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("results/experiment_4")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = Experiment4Config.from_json_file(arguments.config)
    artifacts = run_experiment_4(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
