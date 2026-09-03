"""Reproducible runner for Iteration B / Experiment 1."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Sequence

from .contracts import Measurement, Quantity
from .domestic_hot_water import (
    Experiment1Config,
    ThermalSimulationResult,
    simulate_domestic_hot_water,
)
from .storage import JsonLinesMeasurementStore


JOULES_PER_KWH = 3_600_000.0


@dataclass(frozen=True, slots=True)
class Experiment1Artifacts:
    output_directory: Path
    measurements_path: Path
    timeseries_path: Path
    events_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _measurement_records(
    config: Experiment1Config, result: ThermalSimulationResult
) -> list[Measurement]:
    assets = config.asset_ids
    records: list[Measurement] = []
    flow_channels = (
        (assets["water_inlet"], "total_water_flow", result.demand.total_flow_m3_s),
        (assets["cold_branch"], "cold_water_flow", result.demand.cold_flow_m3_s),
        (assets["hot_branch"], "hot_water_flow", result.demand.hot_flow_m3_s),
    )
    power_channels = (
        ("useful_dhw_power", result.useful_power_w),
        ("boiler_input_power", result.boiler_input_power_w),
    )
    cold_temperature_k = config.cold_supply_temperature_c + 273.15
    hot_temperature_k = config.hot_supply_temperature_c + 273.15

    for index, timestamp in enumerate(result.demand.timestamps[:-1]):
        for asset_id, channel, values in flow_channels:
            records.append(
                Measurement.create(
                    asset_id=asset_id,
                    channel=channel,
                    quantity=Quantity.VOLUMETRIC_FLOW_RATE,
                    timestamp=timestamp,
                    value=values[index],
                    source="fixture-demand-simulator",
                    duration_seconds=config.step_seconds,
                )
            )
        records.extend(
            (
                Measurement.create(
                    asset_id=assets["boiler"],
                    channel="cold_supply_temperature",
                    quantity=Quantity.TEMPERATURE,
                    timestamp=timestamp,
                    value=cold_temperature_k,
                    source="central-boiler-simulator",
                ),
                Measurement.create(
                    asset_id=assets["boiler"],
                    channel="hot_supply_temperature",
                    quantity=Quantity.TEMPERATURE,
                    timestamp=timestamp,
                    value=hot_temperature_k,
                    source="central-boiler-simulator",
                ),
            )
        )
        for channel, values in power_channels:
            records.append(
                Measurement.create(
                    asset_id=assets["boiler"],
                    channel=channel,
                    quantity=Quantity.THERMAL_POWER,
                    timestamp=timestamp,
                    value=values[index],
                    source="central-boiler-simulator",
                    duration_seconds=config.step_seconds,
                )
            )

    register_channels = (
        (
            assets["water_meter"],
            "total_water_register",
            Quantity.CUMULATIVE_VOLUME,
            result.demand.total_volume_m3,
            "virtual-water-meter",
        ),
        (
            assets["cold_meter"],
            "cold_water_register",
            Quantity.CUMULATIVE_VOLUME,
            result.demand.cold_volume_m3,
            "virtual-water-meter",
        ),
        (
            assets["hot_meter"],
            "hot_water_register",
            Quantity.CUMULATIVE_VOLUME,
            result.demand.hot_volume_m3,
            "virtual-water-meter",
        ),
        (
            assets["heat_meter"],
            "useful_dhw_energy_register",
            Quantity.CUMULATIVE_ENERGY,
            result.useful_energy_j,
            "virtual-heat-meter",
        ),
        (
            assets["boiler_energy_meter"],
            "boiler_input_energy_register",
            Quantity.CUMULATIVE_ENERGY,
            result.boiler_input_energy_j,
            "virtual-heat-meter",
        ),
    )
    for index, timestamp in enumerate(result.demand.timestamps):
        for asset_id, channel, quantity, values, source in register_channels:
            records.append(
                Measurement.create(
                    asset_id=asset_id,
                    channel=channel,
                    quantity=quantity,
                    timestamp=timestamp,
                    value=values[index],
                    source=source,
                )
            )
    return records


def _write_timeseries(
    path: Path, config: Experiment1Config, result: ThermalSimulationResult
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "interval_start_utc",
                "interval_end_utc",
                "total_flow_m3_s",
                "cold_flow_m3_s",
                "hot_flow_m3_s",
                "total_volume_end_m3",
                "cold_volume_end_m3",
                "hot_volume_end_m3",
                "cold_temperature_k",
                "hot_temperature_k",
                "useful_power_w",
                "boiler_input_power_w",
                "useful_energy_end_j",
                "boiler_input_energy_end_j",
            )
        )
        for index in range(len(result.demand.total_flow_m3_s)):
            writer.writerow(
                (
                    _timestamp(result.demand.timestamps[index]),
                    _timestamp(result.demand.timestamps[index + 1]),
                    format(result.demand.total_flow_m3_s[index], ".17g"),
                    format(result.demand.cold_flow_m3_s[index], ".17g"),
                    format(result.demand.hot_flow_m3_s[index], ".17g"),
                    format(result.demand.total_volume_m3[index + 1], ".17g"),
                    format(result.demand.cold_volume_m3[index + 1], ".17g"),
                    format(result.demand.hot_volume_m3[index + 1], ".17g"),
                    format(config.cold_supply_temperature_c + 273.15, ".17g"),
                    format(config.hot_supply_temperature_c + 273.15, ".17g"),
                    format(result.useful_power_w[index], ".17g"),
                    format(result.boiler_input_power_w[index], ".17g"),
                    format(result.useful_energy_j[index + 1], ".17g"),
                    format(result.boiler_input_energy_j[index + 1], ".17g"),
                )
            )


def _write_events(
    path: Path, config: Experiment1Config, result: ThermalSimulationResult
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "fixture",
                "start_utc",
                "end_utc",
                "duration_steps",
                "total_volume_l",
                "cold_volume_l",
                "hot_volume_l",
                "hot_fraction",
            )
        )
        for event in result.demand.events:
            writer.writerow(
                (
                    event.fixture,
                    _timestamp(result.demand.timestamps[event.start_index]),
                    _timestamp(
                        result.demand.timestamps[
                            event.start_index + event.duration_steps
                        ]
                    ),
                    event.duration_steps,
                    format(event.total_volume_l, ".17g"),
                    format(event.cold_volume_l, ".17g"),
                    format(event.hot_volume_l, ".17g"),
                    format(event.hot_fraction, ".17g"),
                )
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _fixture_summary(result: ThermalSimulationResult) -> dict[str, object]:
    fixtures: dict[str, dict[str, float | int]] = {}
    for event in result.demand.events:
        item = fixtures.setdefault(
            event.fixture,
            {"event_count": 0, "total_volume_l": 0.0, "hot_volume_l": 0.0},
        )
        item["event_count"] = int(item["event_count"]) + 1
        item["total_volume_l"] = float(item["total_volume_l"]) + event.total_volume_l
        item["hot_volume_l"] = float(item["hot_volume_l"]) + event.hot_volume_l
    return fixtures


def _build_summary(
    config: Experiment1Config,
    result: ThermalSimulationResult,
    measurements_path: Path,
    timeseries_path: Path,
    events_path: Path,
    stored_measurement_count: int,
) -> dict[str, object]:
    step = config.step_seconds
    total_integral = sum(result.demand.total_flow_m3_s) * step
    cold_integral = sum(result.demand.cold_flow_m3_s) * step
    hot_integral = sum(result.demand.hot_flow_m3_s) * step
    total_meter = result.demand.total_volume_m3[-1]
    cold_meter = result.demand.cold_volume_m3[-1]
    hot_meter = result.demand.hot_volume_m3[-1]
    useful_integral = sum(result.useful_power_w) * step
    input_integral = sum(result.boiler_input_power_w) * step
    useful_meter = result.useful_energy_j[-1]
    input_meter = result.boiler_input_energy_j[-1]
    expected_useful = (
        config.water_density_kg_m3
        * config.water_heat_capacity_j_kg_k
        * hot_meter
        * (config.hot_supply_temperature_c - config.cold_supply_temperature_c)
    )
    instantaneous_water_residual = max(
        abs(total - cold - hot)
        for total, cold, hot in zip(
            result.demand.total_flow_m3_s,
            result.demand.cold_flow_m3_s,
            result.demand.hot_flow_m3_s,
        )
    )
    water_residuals = {
        "max_interval_split_residual_m3_s": instantaneous_water_residual,
        "cumulative_split_residual_m3": total_meter - cold_meter - hot_meter,
        "total_meter_residual_m3": total_integral - total_meter,
        "cold_meter_residual_m3": cold_integral - cold_meter,
        "hot_meter_residual_m3": hot_integral - hot_meter,
    }
    energy_residuals = {
        "useful_meter_residual_j": useful_integral - useful_meter,
        "boiler_input_meter_residual_j": input_integral - input_meter,
        "thermodynamic_residual_j": useful_meter - expected_useful,
        "efficiency_residual_j": input_meter * config.boiler_efficiency - useful_meter,
    }
    water_passed = all(
        abs(value) <= config.conservation_tolerance_m3
        for key, value in water_residuals.items()
        if key.endswith("_m3")
    ) and instantaneous_water_residual <= config.conservation_tolerance_m3 / step
    energy_passed = all(
        abs(value) <= config.energy_tolerance_j for value in energy_residuals.values()
    )

    return {
        "schema_version": "1.0",
        "experiment_id": config.experiment_id,
        "configuration": config.to_dict(),
        "results": {
            "interval_count": len(result.demand.total_flow_m3_s),
            "event_count": len(result.demand.events),
            "stored_measurement_count": stored_measurement_count,
            "total_volume_l": total_meter * 1000.0,
            "cold_volume_l": cold_meter * 1000.0,
            "hot_volume_l": hot_meter * 1000.0,
            "hot_water_share": hot_meter / total_meter,
            "peak_total_flow_l_min": max(result.demand.total_flow_m3_s) * 60_000.0,
            "peak_hot_flow_l_min": max(result.demand.hot_flow_m3_s) * 60_000.0,
            "peak_boiler_input_power_kw": max(result.boiler_input_power_w) / 1000.0,
            "useful_dhw_energy_kwh": useful_meter / JOULES_PER_KWH,
            "boiler_input_energy_kwh": input_meter / JOULES_PER_KWH,
            "modeled_boiler_loss_kwh": (input_meter - useful_meter) / JOULES_PER_KWH,
            "fixture_breakdown": _fixture_summary(result),
        },
        "conservation": {
            "water": {
                **water_residuals,
                "tolerance_m3": config.conservation_tolerance_m3,
                "passed": water_passed,
            },
            "energy": {
                **energy_residuals,
                "tolerance_j": config.energy_tolerance_j,
                "passed": energy_passed,
            },
            "passed": water_passed and energy_passed,
        },
        "reproducibility": {
            "seed": config.seed,
            "measurements_sha256": _sha256(measurements_path),
            "timeseries_sha256": _sha256(timeseries_path),
            "events_sha256": _sha256(events_path),
        },
    }


def _plot_result(path: Path, result: ThermalSimulationResult) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    times = result.demand.timestamps[:-1]
    total_l_min = [item * 60_000.0 for item in result.demand.total_flow_m3_s]
    cold_l_min = [item * 60_000.0 for item in result.demand.cold_flow_m3_s]
    hot_l_min = [item * 60_000.0 for item in result.demand.hot_flow_m3_s]
    total_l = [item * 1000.0 for item in result.demand.total_volume_m3]
    cold_l = [item * 1000.0 for item in result.demand.cold_volume_m3]
    hot_l = [item * 1000.0 for item in result.demand.hot_volume_m3]
    useful_kw = [item / 1000.0 for item in result.useful_power_w]
    input_kw = [item / 1000.0 for item in result.boiler_input_power_w]
    input_kwh = [item / JOULES_PER_KWH for item in result.boiler_input_energy_j]

    plt.rcParams.update(
        {"font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
    )
    figure, axes = plt.subplots(3, 1, figsize=(7.2, 7.0), sharex=True)
    axes[0].step(times, total_l_min, where="post", label="total", color="#333333")
    axes[0].step(times, cold_l_min, where="post", label="cold", color="#146c94")
    axes[0].step(times, hot_l_min, where="post", label="hot", color="#c7511f")
    axes[0].set_ylabel("Flow [L/min]")
    axes[0].set_title("Experiment 1: fixture demand and central-boiler coupling")
    axes[0].legend(ncol=3, frameon=False)
    axes[0].grid(alpha=0.22)

    axes[1].plot(result.demand.timestamps, total_l, label="total", color="#333333")
    axes[1].plot(result.demand.timestamps, cold_l, label="cold", color="#146c94")
    axes[1].plot(result.demand.timestamps, hot_l, label="hot", color="#c7511f")
    axes[1].set_ylabel("Cumulative volume [L]")
    axes[1].legend(ncol=3, frameon=False)
    axes[1].grid(alpha=0.22)

    axes[2].step(times, input_kw, where="post", label="boiler input", color="#7b2cbf")
    axes[2].step(times, useful_kw, where="post", label="useful DHW", color="#2a9d8f")
    axes[2].set_ylabel("Thermal power [kW]")
    axes[2].set_xlabel("Time (UTC)")
    axes[2].grid(alpha=0.22)
    axes[2].legend(loc="upper left", frameon=False)
    energy_axis = axes[2].twinx()
    energy_axis.plot(
        result.demand.timestamps,
        input_kwh,
        label="input energy",
        color="#e9c46a",
        linewidth=1.5,
    )
    energy_axis.set_ylabel("Input energy [kWh]")
    energy_axis.spines["top"].set_visible(False)
    energy_axis.legend(loc="upper right", frameon=False)

    axes[2].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[2].set_xlim(result.demand.timestamps[0], result.demand.timestamps[-1])
    figure.tight_layout()
    figure.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
        metadata={"Software": "Building Utility Twin", "Creation Time": None},
    )
    plt.close(figure)


def run_experiment_1(
    config: Experiment1Config, output_directory: str | Path
) -> Experiment1Artifacts:
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    measurements_path = output_directory / "measurements.jsonl"
    timeseries_path = output_directory / "timeseries.csv"
    events_path = output_directory / "events.csv"
    summary_path = output_directory / "summary.json"
    figure_path = output_directory / "experiment_1_hot_water_boiler.png"

    result = simulate_domestic_hot_water(config)
    records = _measurement_records(config, result)
    store = JsonLinesMeasurementStore(measurements_path)
    stored_measurement_count = store.replace(records)
    if store.read_all() != sorted(
        records, key=lambda item: (item.timestamp, item.asset_id, item.channel)
    ):
        raise RuntimeError("file-backed measurement round-trip changed the data")
    _write_timeseries(timeseries_path, config, result)
    _write_events(events_path, config, result)
    summary = _build_summary(
        config,
        result,
        measurements_path,
        timeseries_path,
        events_path,
        stored_measurement_count,
    )
    if not summary["conservation"]["passed"]:  # type: ignore[index]
        raise RuntimeError("Experiment 1 failed a conservation check")
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _plot_result(figure_path, result)
    return Experiment1Artifacts(
        output_directory=output_directory,
        measurements_path=measurements_path,
        timeseries_path=timeseries_path,
        events_path=events_path,
        summary_path=summary_path,
        figure_path=figure_path,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/experiment_1.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("results/experiment_1")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = Experiment1Config.from_json_file(arguments.config)
    artifacts = run_experiment_1(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
