"""Reproducible runner for Iteration C / Experiment 2."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

from .contracts import Measurement, Quality, Quantity
from .storage import JsonLinesMeasurementStore
from .telemetry import (
    Experiment2Config,
    MeterObservation,
    ReconciledReading,
    TelemetrySimulationResult,
    simulate_imperfect_telemetry,
)


@dataclass(frozen=True, slots=True)
class Experiment2Artifacts:
    output_directory: Path
    measurements_path: Path
    timeseries_path: Path
    telemetry_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _measurement_records(
    config: Experiment2Config, result: TelemetrySimulationResult
) -> list[Measurement]:
    asset_id = config.physical.asset_ids["water_meter"]
    reconciled_by_sequence = {
        item.observation.sequence_number: item for item in result.reconciled
    }
    records: list[Measurement] = []
    for observation in result.observations:
        if observation.dropped:
            continue
        raw_quality = Quality.SUSPECT if observation.event == "reset" else Quality.GOOD
        records.append(
            Measurement.create(
                asset_id=asset_id,
                channel="total_water_register_raw",
                quantity=Quantity.CUMULATIVE_VOLUME,
                timestamp=observation.observed_at,
                value=observation.raw_register_m3,
                quality=raw_quality,
                source="imperfect-water-meter",
            )
        )
        reconciled = reconciled_by_sequence[observation.sequence_number]
        records.append(
            Measurement.create(
                asset_id=asset_id,
                channel="total_water_register_reconciled",
                quantity=Quantity.CUMULATIVE_VOLUME,
                timestamp=observation.observed_at,
                value=reconciled.cumulative_m3,
                quality=reconciled.quality,
                source="meter-register-reconciler",
            )
        )
    return records


def _write_timeseries(
    path: Path, config: Experiment2Config, result: TelemetrySimulationResult
) -> None:
    observation_by_timestamp = {
        item.observed_at: item for item in result.observations
    }
    reconciled_by_timestamp = {
        item.observation.observed_at: item for item in result.reconciled
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "timestamp_utc",
                "true_cumulative_l",
                "device_register_l",
                "readout_scheduled",
                "packet_status",
                "received_at_utc",
                "delay_seconds",
                "event",
                "reconciled_cumulative_l",
                "reconstruction_error_l",
                "quality",
                "adjustment",
            )
        )
        for index, timestamp in enumerate(result.physical.demand.timestamps):
            observation = observation_by_timestamp.get(timestamp)
            reconciled = reconciled_by_timestamp.get(timestamp)
            true_l = result.physical.demand.total_volume_m3[index] * 1000.0
            writer.writerow(
                (
                    _timestamp(timestamp),
                    format(true_l, ".17g"),
                    format(result.device_register_m3[index] * 1000.0, ".17g"),
                    observation is not None,
                    (
                        ""
                        if observation is None
                        else "dropped" if observation.dropped else "delivered"
                    ),
                    (
                        ""
                        if observation is None or observation.received_at is None
                        else _timestamp(observation.received_at)
                    ),
                    (
                        ""
                        if observation is None or observation.delay_seconds is None
                        else observation.delay_seconds
                    ),
                    "" if observation is None else observation.event,
                    (
                        ""
                        if reconciled is None
                        else format(reconciled.cumulative_m3 * 1000.0, ".17g")
                    ),
                    (
                        ""
                        if reconciled is None
                        else format(
                            reconciled.cumulative_m3 * 1000.0 - true_l, ".17g"
                        )
                    ),
                    "" if reconciled is None else reconciled.quality.value,
                    "" if reconciled is None else reconciled.adjustment,
                )
            )


def _write_telemetry(
    path: Path, result: TelemetrySimulationResult
) -> None:
    arrival_rank = {
        item.sequence_number: index
        for index, item in enumerate(result.packets_by_arrival)
    }
    reconciled_by_sequence = {
        item.observation.sequence_number: item for item in result.reconciled
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "sequence_number",
                "observed_at_utc",
                "true_cumulative_l",
                "raw_register_l",
                "event",
                "pre_reset_register_l",
                "status",
                "received_at_utc",
                "delay_seconds",
                "arrival_rank",
                "reconciled_cumulative_l",
                "quality",
                "adjustment",
            )
        )
        for observation in result.observations:
            reconciled = reconciled_by_sequence.get(observation.sequence_number)
            writer.writerow(
                (
                    observation.sequence_number,
                    _timestamp(observation.observed_at),
                    format(observation.true_cumulative_m3 * 1000.0, ".17g"),
                    format(observation.raw_register_m3 * 1000.0, ".17g"),
                    observation.event,
                    (
                        ""
                        if observation.pre_reset_register_m3 is None
                        else format(
                            observation.pre_reset_register_m3 * 1000.0, ".17g"
                        )
                    ),
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
                    "" if reconciled is None else reconciled.quality.value,
                    "" if reconciled is None else reconciled.adjustment,
                )
            )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _out_of_order_count(packets: tuple[MeterObservation, ...]) -> int:
    maximum_sequence = -1
    count = 0
    for packet in packets:
        if packet.sequence_number < maximum_sequence:
            count += 1
        maximum_sequence = max(maximum_sequence, packet.sequence_number)
    return count


def _detected_rollovers(readings: tuple[ReconciledReading, ...]) -> int:
    return sum("rollover" in item.adjustment for item in readings)


def _build_summary(
    config: Experiment2Config,
    result: TelemetrySimulationResult,
    measurements_path: Path,
    timeseries_path: Path,
    telemetry_path: Path,
    stored_measurement_count: int,
) -> dict[str, object]:
    observations = result.observations
    delivered = tuple(item for item in observations if not item.dropped)
    dropped = len(observations) - len(delivered)
    delays = [item.delay_seconds or 0 for item in delivered]
    gaps = [
        round((right.observed_at - left.observed_at).total_seconds())
        for left, right in zip(delivered, delivered[1:])
    ]
    errors_l = [
        (item.cumulative_m3 - item.observation.true_cumulative_m3) * 1000.0
        for item in result.reconciled
    ]
    end_error_l = errors_l[-1]
    maximum_error_l = max(abs(value) for value in errors_l)
    rmse_l = math.sqrt(sum(value * value for value in errors_l) / len(errors_l))
    monotonic = all(
        right.cumulative_m3 + 1e-15 >= left.cumulative_m3
        for left, right in zip(result.reconciled, result.reconciled[1:])
    )
    detected_rollovers = _detected_rollovers(result.reconciled)
    detected_resets = sum("reset" in item.adjustment for item in result.reconciled)
    configured_resets = len(config.telemetry.reset_offsets_seconds)
    physical_meter_m3 = result.physical.demand.total_volume_m3[-1]
    physical_integral_m3 = (
        sum(result.physical.demand.total_flow_m3_s) * config.physical.step_seconds
    )
    physical_residual_m3 = physical_integral_m3 - physical_meter_m3
    fault_classes_exercised = {
        "finite_resolution": config.telemetry.register_resolution_l > 0.0,
        "measurement_noise": config.telemetry.increment_noise_std_fraction > 0.0,
        "sparse_readout": (
            config.telemetry.readout_interval_seconds
            > config.physical.step_seconds
        ),
        "packet_loss": dropped > 0,
        "packet_delay": any(value > 0 for value in delays),
        "out_of_order_arrival": _out_of_order_count(result.packets_by_arrival) > 0,
        "register_rollover": result.simulated_rollover_count > 0,
        "register_reset": configured_resets > 0,
    }
    checks = {
        "physical_conservation": (
            abs(physical_residual_m3)
            <= config.physical.conservation_tolerance_m3
        ),
        "all_fault_classes_exercised": all(fault_classes_exercised.values()),
        "rollovers_recovered": (
            detected_rollovers == result.simulated_rollover_count
        ),
        "resets_recovered": detected_resets == configured_resets,
        "reconciled_register_monotonic": monotonic,
        "maximum_error_within_limit": (
            maximum_error_l <= config.maximum_reconstruction_error_l
        ),
        "end_error_within_limit": abs(end_error_l) <= config.maximum_end_error_l,
        "canonical_round_trip": stored_measurement_count == 2 * len(delivered),
    }
    return {
        "schema_version": "1.0",
        "experiment_id": config.experiment_id,
        "configuration": config.to_dict(),
        "physical_reference": {
            "total_volume_l": physical_meter_m3 * 1000.0,
            "conservation_residual_m3": physical_residual_m3,
            "conservation_tolerance_m3": (
                config.physical.conservation_tolerance_m3
            ),
        },
        "meter": {
            "simulated_rollover_count": result.simulated_rollover_count,
            "configured_reset_count": configured_resets,
            "resolution_l": config.telemetry.register_resolution_l,
            "increment_noise_std_fraction": (
                config.telemetry.increment_noise_std_fraction
            ),
            "register_modulus_l": config.telemetry.register_modulus_l,
        },
        "communications": {
            "scheduled_readout_count": len(observations),
            "delivered_packet_count": len(delivered),
            "dropped_packet_count": dropped,
            "delivery_ratio": len(delivered) / len(observations),
            "delayed_packet_count": sum(value > 0 for value in delays),
            "out_of_order_packet_count": _out_of_order_count(
                result.packets_by_arrival
            ),
            "maximum_delay_seconds": max(delays),
            "maximum_observation_gap_seconds": max(gaps),
        },
        "reconciliation": {
            "reconciled_reading_count": len(result.reconciled),
            "detected_rollover_count": detected_rollovers,
            "detected_reset_count": detected_resets,
            "suspect_reading_count": sum(
                item.quality is Quality.SUSPECT for item in result.reconciled
            ),
            "maximum_absolute_error_l": maximum_error_l,
            "rmse_l": rmse_l,
            "end_error_l": end_error_l,
            "reconciled_end_volume_l": (
                result.reconciled[-1].cumulative_m3 * 1000.0
            ),
            "monotonic": monotonic,
        },
        "acceptance": {
            "fault_classes_exercised": fault_classes_exercised,
            "checks": checks,
            "passed": all(checks.values()),
        },
        "results": {
            "stored_measurement_count": stored_measurement_count,
        },
        "reproducibility": {
            "physical_seed": config.physical.seed,
            "telemetry_seed": config.telemetry.seed,
            "measurements_sha256": _sha256(measurements_path),
            "timeseries_sha256": _sha256(timeseries_path),
            "telemetry_sha256": _sha256(telemetry_path),
        },
    }


def _plot_result(
    path: Path, config: Experiment2Config, result: TelemetrySimulationResult
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    timestamps = result.physical.demand.timestamps
    true_l = [
        value * 1000.0 for value in result.physical.demand.total_volume_m3
    ]
    raw_l = [value * 1000.0 for value in result.device_register_m3]
    reconciled_times = [item.observation.observed_at for item in result.reconciled]
    reconciled_l = [item.cumulative_m3 * 1000.0 for item in result.reconciled]
    errors_l = [
        (item.cumulative_m3 - item.observation.true_cumulative_m3) * 1000.0
        for item in result.reconciled
    ]
    delivered = [item for item in result.observations if not item.dropped]
    dropped = [item for item in result.observations if item.dropped]

    plt.rcParams.update(
        {"font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
    )
    figure, axes = plt.subplots(3, 1, figsize=(7.2, 7.0), sharex=True)
    axes[0].plot(timestamps, true_l, color="#333333", label="physical truth")
    axes[0].step(
        timestamps,
        raw_l,
        where="post",
        color="#c7511f",
        linewidth=1.0,
        label="finite device register",
    )
    axes[0].set_ylabel("Volume [L]")
    axes[0].set_title("Experiment 2: imperfect meter and telemetry recovery")
    axes[0].legend(ncol=2, frameon=False)
    axes[0].grid(alpha=0.22)

    axes[1].plot(timestamps, true_l, color="#333333", label="physical truth")
    axes[1].plot(
        reconciled_times,
        reconciled_l,
        marker=".",
        markersize=2.5,
        linewidth=1.0,
        color="#146c94",
        label="reconciled register",
    )
    axes[1].set_ylabel("Cumulative volume [L]")
    axes[1].grid(alpha=0.22)
    axes[1].legend(loc="upper left", frameon=False)
    error_axis = axes[1].twinx()
    error_axis.plot(
        reconciled_times,
        errors_l,
        color="#7b2cbf",
        linewidth=0.8,
        alpha=0.8,
        label="reconstruction error",
    )
    error_axis.set_ylabel("Error [L]")
    error_axis.spines["top"].set_visible(False)
    error_axis.legend(loc="lower right", frameon=False)

    axes[2].scatter(
        [item.observed_at for item in delivered],
        [item.delay_seconds for item in delivered],
        s=8,
        color="#2a9d8f",
        label="delivered",
    )
    axes[2].scatter(
        [item.observed_at for item in dropped],
        [config.telemetry.max_packet_delay_seconds] * len(dropped),
        s=13,
        marker="x",
        color="#d62828",
        label="dropped",
    )
    axes[2].set_ylabel("Delay [s]")
    axes[2].set_xlabel("Observation time (UTC)")
    axes[2].grid(alpha=0.22)
    axes[2].legend(ncol=2, frameon=False)
    for offset in config.telemetry.reset_offsets_seconds:
        reset_time = timestamps[0] + timedelta(seconds=offset)
        for axis in axes:
            axis.axvline(reset_time, color="#e9c46a", linestyle="--", linewidth=1.0)
    axes[2].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[2].set_xlim(timestamps[0], timestamps[-1])
    figure.tight_layout()
    figure.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
        metadata={"Software": "Building Utility Twin", "Creation Time": None},
    )
    plt.close(figure)


def run_experiment_2(
    config: Experiment2Config, output_directory: str | Path
) -> Experiment2Artifacts:
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    measurements_path = output_directory / "measurements.jsonl"
    timeseries_path = output_directory / "timeseries.csv"
    telemetry_path = output_directory / "telemetry.csv"
    summary_path = output_directory / "summary.json"
    figure_path = output_directory / "experiment_2_telemetry_recovery.png"

    result = simulate_imperfect_telemetry(config)
    records = _measurement_records(config, result)
    store = JsonLinesMeasurementStore(measurements_path)
    stored_measurement_count = store.replace(records)
    if store.read_all() != sorted(
        records, key=lambda item: (item.timestamp, item.asset_id, item.channel)
    ):
        raise RuntimeError("file-backed measurement round-trip changed the data")
    _write_timeseries(timeseries_path, config, result)
    _write_telemetry(telemetry_path, result)
    summary = _build_summary(
        config,
        result,
        measurements_path,
        timeseries_path,
        telemetry_path,
        stored_measurement_count,
    )
    if not summary["acceptance"]["passed"]:  # type: ignore[index]
        raise RuntimeError("Experiment 2 failed an acceptance check")
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _plot_result(figure_path, config, result)
    return Experiment2Artifacts(
        output_directory=output_directory,
        measurements_path=measurements_path,
        timeseries_path=timeseries_path,
        telemetry_path=telemetry_path,
        summary_path=summary_path,
        figure_path=figure_path,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("config/experiment_2.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("results/experiment_2")
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = Experiment2Config.from_json_file(arguments.config)
    artifacts = run_experiment_2(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
