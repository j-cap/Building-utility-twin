"""Reproducible runner for Iteration A / Experiment 0."""

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
from .simulation import ExperimentConfig, SimulationResult, simulate_one_pipe_day
from .storage import JsonLinesMeasurementStore


@dataclass(frozen=True, slots=True)
class ExperimentArtifacts:
    output_directory: Path
    measurements_path: Path
    timeseries_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _measurement_records(
    config: ExperimentConfig, result: SimulationResult
) -> list[Measurement]:
    records: list[Measurement] = []
    for index, rate in enumerate(result.flow_m3_s):
        records.append(
            Measurement.create(
                asset_id=config.asset_id,
                channel="outlet_flow",
                quantity=Quantity.VOLUMETRIC_FLOW_RATE,
                timestamp=result.timestamps[index],
                value=rate,
                source="one-pipe-simulator",
                duration_seconds=config.step_seconds,
            )
        )
    for index, volume in enumerate(result.cumulative_volume_m3):
        records.append(
            Measurement.create(
                asset_id=config.meter_id,
                channel="cumulative_register",
                quantity=Quantity.CUMULATIVE_VOLUME,
                timestamp=result.timestamps[index],
                value=volume,
                source="virtual-cumulative-meter",
            )
        )
    return records


def _write_timeseries(
    path: Path, config: ExperimentConfig, result: SimulationResult
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            (
                "interval_start_utc",
                "interval_end_utc",
                "inlet_flow_m3_s",
                "outlet_flow_m3_s",
                "meter_volume_start_m3",
                "meter_volume_end_m3",
            )
        )
        for index, rate in enumerate(result.flow_m3_s):
            writer.writerow(
                (
                    _timestamp(result.timestamps[index]),
                    _timestamp(result.timestamps[index + 1]),
                    format(rate, ".17g"),
                    format(rate, ".17g"),
                    format(result.cumulative_volume_m3[index], ".17g"),
                    format(result.cumulative_volume_m3[index + 1], ".17g"),
                )
            )


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _plot_result(path: Path, result: SimulationResult) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    hours = result.timestamps[:-1]
    flow_l_min = [rate * 60_000.0 for rate in result.flow_m3_s]
    cumulative_l = [volume * 1000.0 for volume in result.cumulative_volume_m3]

    plt.rcParams.update(
        {
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    figure, axes = plt.subplots(2, 1, figsize=(7.0, 4.8), sharex=True)
    axes[0].step(hours, flow_l_min, where="post", color="#146c94", linewidth=0.9)
    axes[0].set_ylabel("Flow [L/min]")
    axes[0].set_title("Experiment 0: one-pipe demand and virtual meter")
    axes[0].grid(alpha=0.22)

    axes[1].plot(
        result.timestamps,
        cumulative_l,
        color="#c7511f",
        linewidth=1.6,
    )
    axes[1].set_ylabel("Cumulative volume [L]")
    axes[1].set_xlabel("Time (UTC)")
    axes[1].grid(alpha=0.22)
    axes[1].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    axes[1].set_xlim(result.timestamps[0], result.timestamps[-1])
    figure.tight_layout()
    figure.savefig(
        path,
        dpi=180,
        bbox_inches="tight",
        metadata={"Software": "Building Utility Twin", "Creation Time": None},
    )
    plt.close(figure)


def _build_summary(
    config: ExperimentConfig,
    result: SimulationResult,
    measurements_path: Path,
    timeseries_path: Path,
    stored_measurement_count: int,
) -> dict[str, object]:
    step_seconds = config.step_seconds
    inlet_volume = sum(rate * step_seconds for rate in result.flow_m3_s)
    outlet_volume = sum(rate * step_seconds for rate in result.flow_m3_s)
    meter_delta = (
        result.cumulative_volume_m3[-1] - result.cumulative_volume_m3[0]
    )
    residual = inlet_volume - meter_delta
    pipe_balance = inlet_volume - outlet_volume
    monotonic = all(
        right >= left
        for left, right in zip(
            result.cumulative_volume_m3, result.cumulative_volume_m3[1:]
        )
    )

    return {
        "schema_version": "1.0",
        "experiment_id": config.experiment_id,
        "configuration": config.to_dict(),
        "results": {
            "interval_count": len(result.flow_m3_s),
            "boundary_count": len(result.timestamps),
            "stored_measurement_count": stored_measurement_count,
            "total_volume_m3": meter_delta,
            "total_volume_l": meter_delta * 1000.0,
            "peak_flow_m3_s": max(result.flow_m3_s),
            "peak_flow_l_min": max(result.flow_m3_s) * 60_000.0,
            "active_intervals": sum(
                rate > config.base_flow_l_min / 60_000.0
                for rate in result.flow_m3_s
            ),
            "meter_monotonic": monotonic,
        },
        "conservation": {
            "inlet_volume_m3": inlet_volume,
            "outlet_volume_m3": outlet_volume,
            "meter_delta_m3": meter_delta,
            "pipe_balance_residual_m3": pipe_balance,
            "meter_balance_residual_m3": residual,
            "tolerance_m3": config.conservation_tolerance_m3,
            "passed": (
                abs(pipe_balance) <= config.conservation_tolerance_m3
                and abs(residual) <= config.conservation_tolerance_m3
            ),
        },
        "reproducibility": {
            "seed": config.seed,
            "timeseries_sha256": _sha256(timeseries_path),
            "measurements_sha256": _sha256(measurements_path),
        },
    }


def run_experiment(
    config: ExperimentConfig, output_directory: str | Path
) -> ExperimentArtifacts:
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    measurements_path = output_directory / "measurements.jsonl"
    timeseries_path = output_directory / "timeseries.csv"
    summary_path = output_directory / "summary.json"
    figure_path = output_directory / "experiment_0_one_pipe_day.png"

    result = simulate_one_pipe_day(config)
    records = _measurement_records(config, result)
    store = JsonLinesMeasurementStore(measurements_path)
    stored_measurement_count = store.replace(records)
    if store.read_all() != sorted(
        records, key=lambda item: (item.timestamp, item.asset_id, item.channel)
    ):
        raise RuntimeError("file-backed measurement round-trip changed the data")

    _write_timeseries(timeseries_path, config, result)
    summary = _build_summary(
        config,
        result,
        measurements_path,
        timeseries_path,
        stored_measurement_count,
    )
    if not summary["conservation"]["passed"]:  # type: ignore[index]
        raise RuntimeError("Experiment 0 failed its conservation check")
    with summary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _plot_result(figure_path, result)

    return ExperimentArtifacts(
        output_directory=output_directory,
        measurements_path=measurements_path,
        timeseries_path=timeseries_path,
        summary_path=summary_path,
        figure_path=figure_path,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/experiment_0.json"),
        help="Experiment configuration JSON file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/experiment_0"),
        help="Output directory",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = ExperimentConfig.from_json_file(arguments.config)
    artifacts = run_experiment(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

