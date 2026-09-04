"""Reproducible runner for Iteration D / Experiment 3."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

from .building_system import (
    BuildingSimulationResult,
    Experiment3Config,
    simulate_building,
)
from .contracts import Measurement, Quantity
from .storage import JsonLinesMeasurementStore


JOULES_PER_KWH = 3_600_000.0


@dataclass(frozen=True, slots=True)
class Experiment3Artifacts:
    output_directory: Path
    measurements_path: Path
    timeseries_path: Path
    apartments_path: Path
    events_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _measurement_records(
    config: Experiment3Config, result: BuildingSimulationResult
) -> list[Measurement]:
    records: list[Measurement] = []
    physical = config.physical_template
    building_assets = physical.asset_ids
    interval_count = len(result.total_flow_m3_s)

    for index in range(interval_count):
        timestamp = result.timestamps[index]
        for apartment in result.apartments:
            asset_id = f"{apartment.spec.apartment_id}-water"
            for channel, values in (
                ("total_water_flow", apartment.demand.total_flow_m3_s),
                ("cold_water_flow", apartment.demand.cold_flow_m3_s),
                ("hot_water_flow", apartment.demand.hot_flow_m3_s),
            ):
                records.append(
                    Measurement.create(
                        asset_id=asset_id,
                        channel=channel,
                        quantity=Quantity.VOLUMETRIC_FLOW_RATE,
                        timestamp=timestamp,
                        value=values[index],
                        source="apartment-fixture-simulator",
                        duration_seconds=physical.step_seconds,
                    )
                )

        for asset_id, channel, values in (
            (
                building_assets["water_inlet"],
                "building_total_water_flow",
                result.total_flow_m3_s,
            ),
            (
                building_assets["cold_branch"],
                "building_cold_water_flow",
                result.cold_flow_m3_s,
            ),
            (
                building_assets["hot_branch"],
                "building_hot_water_flow",
                result.hot_flow_m3_s,
            ),
        ):
            records.append(
                Measurement.create(
                    asset_id=asset_id,
                    channel=channel,
                    quantity=Quantity.VOLUMETRIC_FLOW_RATE,
                    timestamp=timestamp,
                    value=values[index],
                    source="building-branch-aggregator",
                    duration_seconds=physical.step_seconds,
                )
            )

        tank = result.shared_tank
        for asset_id, channel, values in (
            (
                config.asset_ids["shared_boiler"],
                "boiler_thermal_output_power",
                tank.boiler_output_power_w,
            ),
            (
                config.asset_ids["shared_boiler"],
                "boiler_input_power",
                tank.boiler_input_power_w,
            ),
            (
                config.asset_ids["shared_tank"],
                "dhw_delivered_power",
                tank.dhw_output_power_w,
            ),
            (
                config.asset_ids["shared_tank"],
                "tank_standing_loss_power",
                tank.standing_loss_power_w,
            ),
        ):
            records.append(
                Measurement.create(
                    asset_id=asset_id,
                    channel=channel,
                    quantity=Quantity.THERMAL_POWER,
                    timestamp=timestamp,
                    value=values[index],
                    source="shared-dhw-storage-simulator",
                    duration_seconds=physical.step_seconds,
                )
            )

    for index, timestamp in enumerate(result.timestamps):
        for apartment in result.apartments:
            asset_id = f"{apartment.spec.apartment_id}-water-meter"
            for channel, values in (
                ("total_water_register", apartment.demand.total_volume_m3),
                ("cold_water_register", apartment.demand.cold_volume_m3),
                ("hot_water_register", apartment.demand.hot_volume_m3),
            ):
                records.append(
                    Measurement.create(
                        asset_id=asset_id,
                        channel=channel,
                        quantity=Quantity.CUMULATIVE_VOLUME,
                        timestamp=timestamp,
                        value=values[index],
                        source="virtual-apartment-water-meter",
                    )
                )

        for asset_id, channel, values in (
            (
                building_assets["water_meter"],
                "building_total_water_register",
                result.total_volume_m3,
            ),
            (
                building_assets["cold_meter"],
                "building_cold_water_register",
                result.cold_volume_m3,
            ),
            (
                building_assets["hot_meter"],
                "building_hot_water_register",
                result.hot_volume_m3,
            ),
        ):
            records.append(
                Measurement.create(
                    asset_id=asset_id,
                    channel=channel,
                    quantity=Quantity.CUMULATIVE_VOLUME,
                    timestamp=timestamp,
                    value=values[index],
                    source="virtual-building-water-meter",
                )
            )

        tank = result.shared_tank
        records.append(
            Measurement.create(
                asset_id=config.asset_ids["shared_tank"],
                channel="tank_temperature",
                quantity=Quantity.TEMPERATURE,
                timestamp=timestamp,
                value=tank.temperature_k[index],
                source="shared-dhw-storage-simulator",
            )
        )
        for asset_id, channel, values in (
            (
                config.asset_ids["shared_boiler"],
                "boiler_thermal_output_energy_register",
                tank.boiler_output_energy_j,
            ),
            (
                config.asset_ids["shared_boiler"],
                "boiler_input_energy_register",
                tank.boiler_input_energy_j,
            ),
            (
                config.asset_ids["shared_tank"],
                "dhw_delivered_energy_register",
                tank.dhw_output_energy_j,
            ),
            (
                config.asset_ids["shared_tank"],
                "tank_standing_loss_energy_register",
                tank.standing_loss_energy_j,
            ),
        ):
            records.append(
                Measurement.create(
                    asset_id=asset_id,
                    channel=channel,
                    quantity=Quantity.CUMULATIVE_ENERGY,
                    timestamp=timestamp,
                    value=values[index],
                    source="shared-dhw-storage-simulator",
                )
            )
    return records


def _write_timeseries(
    path: Path, config: Experiment3Config, result: BuildingSimulationResult
) -> None:
    tank = result.shared_tank
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "interval_start_utc",
                "interval_end_utc",
                "building_total_flow_m3_s",
                "building_cold_flow_m3_s",
                "building_hot_flow_m3_s",
                "building_total_volume_end_m3",
                "building_cold_volume_end_m3",
                "building_hot_volume_end_m3",
                "tank_temperature_start_k",
                "tank_temperature_end_k",
                "boiler_enabled",
                "boiler_thermal_output_power_w",
                "boiler_input_power_w",
                "dhw_delivered_power_w",
                "tank_standing_loss_power_w",
                "stored_energy_end_j",
                "boiler_thermal_output_energy_end_j",
                "boiler_input_energy_end_j",
                "dhw_delivered_energy_end_j",
                "tank_standing_loss_energy_end_j",
            )
        )
        for index in range(len(result.total_flow_m3_s)):
            writer.writerow(
                (
                    _timestamp(result.timestamps[index]),
                    _timestamp(result.timestamps[index + 1]),
                    format(result.total_flow_m3_s[index], ".17g"),
                    format(result.cold_flow_m3_s[index], ".17g"),
                    format(result.hot_flow_m3_s[index], ".17g"),
                    format(result.total_volume_m3[index + 1], ".17g"),
                    format(result.cold_volume_m3[index + 1], ".17g"),
                    format(result.hot_volume_m3[index + 1], ".17g"),
                    format(tank.temperature_k[index], ".17g"),
                    format(tank.temperature_k[index + 1], ".17g"),
                    tank.heater_enabled[index],
                    format(tank.boiler_output_power_w[index], ".17g"),
                    format(tank.boiler_input_power_w[index], ".17g"),
                    format(tank.dhw_output_power_w[index], ".17g"),
                    format(tank.standing_loss_power_w[index], ".17g"),
                    format(tank.stored_energy_j[index + 1], ".17g"),
                    format(tank.boiler_output_energy_j[index + 1], ".17g"),
                    format(tank.boiler_input_energy_j[index + 1], ".17g"),
                    format(tank.dhw_output_energy_j[index + 1], ".17g"),
                    format(tank.standing_loss_energy_j[index + 1], ".17g"),
                )
            )


def _write_apartments(
    path: Path, config: Experiment3Config, result: BuildingSimulationResult
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "apartment_id",
                "occupants",
                "demand_scale",
                "seed",
                "interval_start_utc",
                "total_flow_m3_s",
                "cold_flow_m3_s",
                "hot_flow_m3_s",
                "total_volume_end_m3",
                "cold_volume_end_m3",
                "hot_volume_end_m3",
            )
        )
        for apartment in result.apartments:
            demand = apartment.demand
            for index in range(len(demand.total_flow_m3_s)):
                writer.writerow(
                    (
                        apartment.spec.apartment_id,
                        apartment.spec.occupants,
                        format(apartment.spec.demand_scale, ".17g"),
                        apartment.spec.seed,
                        _timestamp(demand.timestamps[index]),
                        format(demand.total_flow_m3_s[index], ".17g"),
                        format(demand.cold_flow_m3_s[index], ".17g"),
                        format(demand.hot_flow_m3_s[index], ".17g"),
                        format(demand.total_volume_m3[index + 1], ".17g"),
                        format(demand.cold_volume_m3[index + 1], ".17g"),
                        format(demand.hot_volume_m3[index + 1], ".17g"),
                    )
                )


def _write_events(path: Path, result: BuildingSimulationResult) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "apartment_id",
                "fixture",
                "start_utc",
                "end_utc",
                "duration_steps",
                "total_volume_l",
                "cold_volume_l",
                "hot_volume_l",
            )
        )
        for apartment in result.apartments:
            for event in apartment.demand.events:
                writer.writerow(
                    (
                        apartment.spec.apartment_id,
                        event.fixture,
                        _timestamp(apartment.demand.timestamps[event.start_index]),
                        _timestamp(
                            apartment.demand.timestamps[
                                event.start_index + event.duration_steps
                            ]
                        ),
                        event.duration_steps,
                        format(event.total_volume_l, ".17g"),
                        format(event.cold_volume_l, ".17g"),
                        format(event.hot_volume_l, ".17g"),
                    )
                )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _cycles(enabled: tuple[bool, ...]) -> int:
    return sum(
        value and (index == 0 or not enabled[index - 1])
        for index, value in enumerate(enabled)
    )


def _apartment_summary(result: BuildingSimulationResult) -> dict[str, object]:
    return {
        apartment.spec.apartment_id: {
            "occupants": apartment.spec.occupants,
            "demand_scale": apartment.spec.demand_scale,
            "seed": apartment.spec.seed,
            "event_count": len(apartment.demand.events),
            "total_volume_l": apartment.demand.total_volume_m3[-1] * 1000.0,
            "cold_volume_l": apartment.demand.cold_volume_m3[-1] * 1000.0,
            "hot_volume_l": apartment.demand.hot_volume_m3[-1] * 1000.0,
            "peak_total_flow_l_min": (
                max(apartment.demand.total_flow_m3_s) * 60_000.0
            ),
        }
        for apartment in result.apartments
    }


def _build_summary(
    config: Experiment3Config,
    result: BuildingSimulationResult,
    measurements_path: Path,
    timeseries_path: Path,
    apartments_path: Path,
    events_path: Path,
    stored_measurement_count: int,
) -> dict[str, object]:
    step = config.physical_template.step_seconds
    apartments = result.apartments
    tank = result.shared_tank
    interval_count = len(result.total_flow_m3_s)

    aggregation_residuals = []
    branch_residuals = []
    for index in range(interval_count):
        for building_value, apartment_values in (
            (
                result.total_flow_m3_s[index],
                [item.demand.total_flow_m3_s[index] for item in apartments],
            ),
            (
                result.cold_flow_m3_s[index],
                [item.demand.cold_flow_m3_s[index] for item in apartments],
            ),
            (
                result.hot_flow_m3_s[index],
                [item.demand.hot_flow_m3_s[index] for item in apartments],
            ),
        ):
            aggregation_residuals.append(building_value - sum(apartment_values))
        branch_residuals.append(
            result.total_flow_m3_s[index]
            - result.cold_flow_m3_s[index]
            - result.hot_flow_m3_s[index]
        )
    cumulative_residuals = []
    for building_values, apartment_attribute in (
        (result.total_volume_m3, "total_volume_m3"),
        (result.cold_volume_m3, "cold_volume_m3"),
        (result.hot_volume_m3, "hot_volume_m3"),
    ):
        cumulative_residuals.append(
            building_values[-1]
            - sum(
                getattr(item.demand, apartment_attribute)[-1]
                for item in apartments
            )
        )
    integration_residuals = [
        sum(flow) * step - meter[-1]
        for flow, meter in (
            (result.total_flow_m3_s, result.total_volume_m3),
            (result.cold_flow_m3_s, result.cold_volume_m3),
            (result.hot_flow_m3_s, result.hot_volume_m3),
        )
    ]
    maximum_water_residual_m3 = max(
        [abs(value) * step for value in aggregation_residuals]
        + [abs(value) * step for value in branch_residuals]
        + [abs(value) for value in cumulative_residuals]
        + [abs(value) for value in integration_residuals]
    )

    boiler_output_j = tank.boiler_output_energy_j[-1]
    boiler_input_j = tank.boiler_input_energy_j[-1]
    dhw_output_j = tank.dhw_output_energy_j[-1]
    standing_loss_j = tank.standing_loss_energy_j[-1]
    stored_energy_change_j = tank.stored_energy_j[-1] - tank.stored_energy_j[0]
    storage_balance_residual_j = stored_energy_change_j - (
        boiler_output_j - dhw_output_j - standing_loss_j
    )
    efficiency_residual_j = (
        boiler_input_j * config.shared_tank.boiler_efficiency - boiler_output_j
    )
    energy_residuals = {
        "storage_balance_residual_j": storage_balance_residual_j,
        "boiler_efficiency_residual_j": efficiency_residual_j,
    }

    apartment_peaks = [
        max(item.demand.total_flow_m3_s) * 60_000.0 for item in apartments
    ]
    building_peak_l_min = max(result.total_flow_m3_s) * 60_000.0
    peak_index = max(
        range(interval_count), key=lambda index: result.total_flow_m3_s[index]
    )
    simultaneous_active = [
        sum(item.demand.total_flow_m3_s[index] > 0.0 for item in apartments)
        for index in range(interval_count)
    ]
    minimum_temperature_c = min(tank.temperature_k) - 273.15
    maximum_temperature_c = max(tank.temperature_k) - 273.15
    end_temperature_c = tank.temperature_k[-1] - 273.15
    hot_volume_m3 = result.hot_volume_m3[-1]
    volume_weighted_supply_temperature_c = (
        config.physical_template.cold_supply_temperature_c
        + dhw_output_j
        / (
            config.physical_template.water_density_kg_m3
            * config.physical_template.water_heat_capacity_j_kg_k
            * hot_volume_m3
        )
    )
    water_passed = (
        maximum_water_residual_m3 <= config.aggregation_tolerance_m3
    )
    energy_passed = all(
        abs(value) <= config.energy_tolerance_j
        for value in energy_residuals.values()
    )
    tank_bounds_passed = (
        minimum_temperature_c
        >= config.shared_tank.minimum_service_temperature_c
        and maximum_temperature_c
        <= config.shared_tank.thermostat_upper_c + 1e-12
        and max(tank.boiler_output_power_w)
        <= config.shared_tank.boiler_max_thermal_power_kw * 1000.0 + 1e-12
    )
    expected_measurement_count = (
        interval_count * (3 * len(apartments) + 3 + 4)
        + (interval_count + 1) * (3 * len(apartments) + 3 + 1 + 4)
    )
    checks = {
        "apartment_aggregation": water_passed,
        "water_branch_conservation": max(map(abs, branch_residuals))
        <= config.aggregation_tolerance_m3 / step,
        "shared_tank_energy_balance": energy_passed,
        "tank_temperature_and_power_bounds": tank_bounds_passed,
        "canonical_round_trip": stored_measurement_count
        == expected_measurement_count,
        "multiple_apartments_active": max(simultaneous_active) >= 2,
        "storage_dynamics_exercised": _cycles(tank.heater_enabled) > 0
        and standing_loss_j > 0.0,
    }

    return {
        "schema_version": "1.0",
        "experiment_id": config.experiment_id,
        "configuration": config.to_dict(),
        "apartments": _apartment_summary(result),
        "building": {
            "apartment_count": len(apartments),
            "occupant_count": sum(item.spec.occupants for item in apartments),
            "event_count": sum(len(item.demand.events) for item in apartments),
            "total_volume_l": result.total_volume_m3[-1] * 1000.0,
            "cold_volume_l": result.cold_volume_m3[-1] * 1000.0,
            "hot_volume_l": result.hot_volume_m3[-1] * 1000.0,
            "peak_total_flow_l_min": building_peak_l_min,
            "peak_time_utc": _timestamp(result.timestamps[peak_index]),
            "sum_apartment_peak_flows_l_min": sum(apartment_peaks),
            "diversity_factor": sum(apartment_peaks) / building_peak_l_min,
            "maximum_simultaneously_active_apartments": max(simultaneous_active),
        },
        "shared_tank": {
            "minimum_temperature_c": minimum_temperature_c,
            "maximum_temperature_c": maximum_temperature_c,
            "end_temperature_c": end_temperature_c,
            "volume_weighted_dhw_supply_temperature_c": (
                volume_weighted_supply_temperature_c
            ),
            "heater_runtime_minutes": (
                sum(tank.heater_enabled) * step / 60.0
            ),
            "heater_cycle_count": _cycles(tank.heater_enabled),
            "peak_dhw_output_power_kw": max(tank.dhw_output_power_w) / 1000.0,
            "peak_boiler_thermal_output_power_kw": (
                max(tank.boiler_output_power_w) / 1000.0
            ),
            "boiler_thermal_output_energy_kwh": (
                boiler_output_j / JOULES_PER_KWH
            ),
            "boiler_input_energy_kwh": boiler_input_j / JOULES_PER_KWH,
            "dhw_delivered_energy_kwh": dhw_output_j / JOULES_PER_KWH,
            "standing_loss_energy_kwh": standing_loss_j / JOULES_PER_KWH,
            "boiler_conversion_loss_kwh": (
                (boiler_input_j - boiler_output_j) / JOULES_PER_KWH
            ),
            "stored_energy_change_kwh": (
                stored_energy_change_j / JOULES_PER_KWH
            ),
        },
        "conservation": {
            "water": {
                "maximum_residual_m3": maximum_water_residual_m3,
                "tolerance_m3": config.aggregation_tolerance_m3,
                "passed": water_passed,
            },
            "energy": {
                **energy_residuals,
                "tolerance_j": config.energy_tolerance_j,
                "passed": energy_passed,
            },
        },
        "acceptance": {
            "checks": checks,
            "passed": all(checks.values()),
        },
        "results": {
            "stored_measurement_count": stored_measurement_count,
        },
        "reproducibility": {
            "apartment_seeds": [item.spec.seed for item in apartments],
            "measurements_sha256": _sha256(measurements_path),
            "timeseries_sha256": _sha256(timeseries_path),
            "apartments_sha256": _sha256(apartments_path),
            "events_sha256": _sha256(events_path),
        },
    }


def _plot_result(
    path: Path, config: Experiment3Config, result: BuildingSimulationResult
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    times = result.timestamps[:-1]
    colors = ("#146c94", "#2a9d8f", "#e9c46a", "#c7511f", "#7b2cbf")
    plt.rcParams.update(
        {"font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
    )
    figure, axes = plt.subplots(4, 1, figsize=(7.2, 8.4), sharex=True)

    for color, apartment in zip(colors, result.apartments):
        axes[0].step(
            times,
            [value * 60_000.0 for value in apartment.demand.total_flow_m3_s],
            where="post",
            linewidth=0.8,
            color=color,
            alpha=0.8,
            label=apartment.spec.apartment_id,
        )
    axes[0].step(
        times,
        [value * 60_000.0 for value in result.total_flow_m3_s],
        where="post",
        linewidth=1.2,
        color="#333333",
        label="building",
    )
    axes[0].set_ylabel("Flow [L/min]")
    axes[0].set_title("Experiment 3: apartment aggregation and shared DHW storage")
    axes[0].legend(ncol=5, frameon=False, fontsize=7.5)
    axes[0].grid(alpha=0.22)

    for color, apartment in zip(colors, result.apartments):
        axes[1].plot(
            result.timestamps,
            [value * 1000.0 for value in apartment.demand.total_volume_m3],
            linewidth=0.9,
            color=color,
            label=apartment.spec.apartment_id,
        )
    axes[1].plot(
        result.timestamps,
        [value * 1000.0 for value in result.total_volume_m3],
        linewidth=1.4,
        color="#333333",
        label="building",
    )
    axes[1].set_ylabel("Cumulative [L]")
    axes[1].grid(alpha=0.22)

    tank = result.shared_tank
    temperature_c = [value - 273.15 for value in tank.temperature_k]
    axes[2].plot(
        result.timestamps,
        temperature_c,
        color="#c7511f",
        linewidth=1.2,
        label="tank temperature",
    )
    axes[2].axhline(
        config.shared_tank.thermostat_lower_c,
        color="#999999",
        linestyle="--",
        linewidth=0.8,
        label="thermostat band",
    )
    axes[2].axhline(
        config.shared_tank.thermostat_upper_c,
        color="#999999",
        linestyle="--",
        linewidth=0.8,
    )
    axes[2].set_ylabel("Tank [°C]")
    axes[2].grid(alpha=0.22)
    axes[2].legend(loc="lower left", frameon=False, fontsize=8)
    power_axis = axes[2].twinx()
    power_axis.step(
        times,
        [value / 1000.0 for value in tank.boiler_output_power_w],
        where="post",
        color="#7b2cbf",
        linewidth=0.9,
        alpha=0.8,
        label="boiler output",
    )
    power_axis.set_ylabel("Boiler [kW]")
    power_axis.spines["top"].set_visible(False)
    power_axis.legend(loc="lower right", frameon=False, fontsize=8)

    for values, label, color in (
        (tank.boiler_input_energy_j, "plant input", "#7b2cbf"),
        (tank.boiler_output_energy_j, "boiler output", "#2a9d8f"),
        (tank.dhw_output_energy_j, "delivered DHW", "#146c94"),
        (tank.standing_loss_energy_j, "standing loss", "#c7511f"),
    ):
        axes[3].plot(
            result.timestamps,
            [value / JOULES_PER_KWH for value in values],
            linewidth=1.0,
            label=label,
            color=color,
        )
    axes[3].set_ylabel("Cumulative [kWh]")
    axes[3].set_xlabel("Time (UTC)")
    axes[3].legend(ncol=4, frameon=False, fontsize=7.5)
    axes[3].grid(alpha=0.22)
    axes[3].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[3].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[3].set_xlim(result.timestamps[0], result.timestamps[-1])
    figure.tight_layout()
    figure.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
        metadata={"Software": "Building Utility Twin", "Creation Time": None},
    )
    plt.close(figure)


def run_experiment_3(
    config: Experiment3Config, output_directory: str | Path
) -> Experiment3Artifacts:
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    measurements_path = output_directory / "measurements.jsonl"
    timeseries_path = output_directory / "timeseries.csv"
    apartments_path = output_directory / "apartments.csv"
    events_path = output_directory / "events.csv"
    summary_path = output_directory / "summary.json"
    figure_path = output_directory / "experiment_3_building_storage.png"

    result = simulate_building(config)
    records = _measurement_records(config, result)
    store = JsonLinesMeasurementStore(measurements_path)
    stored_measurement_count = store.replace(records)
    if store.read_all() != sorted(
        records, key=lambda item: (item.timestamp, item.asset_id, item.channel)
    ):
        raise RuntimeError("file-backed measurement round-trip changed the data")
    _write_timeseries(timeseries_path, config, result)
    _write_apartments(apartments_path, config, result)
    _write_events(events_path, result)
    summary = _build_summary(
        config,
        result,
        measurements_path,
        timeseries_path,
        apartments_path,
        events_path,
        stored_measurement_count,
    )
    if not summary["acceptance"]["passed"]:  # type: ignore[index]
        raise RuntimeError("Experiment 3 failed an acceptance check")
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _plot_result(figure_path, config, result)
    return Experiment3Artifacts(
        output_directory=output_directory,
        measurements_path=measurements_path,
        timeseries_path=timeseries_path,
        apartments_path=apartments_path,
        events_path=events_path,
        summary_path=summary_path,
        figure_path=figure_path,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/experiment_3.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("results/experiment_3")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = Experiment3Config.from_json_file(arguments.config)
    artifacts = run_experiment_3(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
