"""Reproducible adapter-substitution runner for Iteration F / Experiment 5."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Sequence

from .contracts import Measurement, Quality, Quantity
from .file_adapter import ImportResult, VendorCsvConfig, import_vendor_csv
from .storage import JsonLinesMeasurementStore


@dataclass(frozen=True, slots=True)
class BalanceWindow:
    start: datetime
    end: datetime
    duration_seconds: int
    building_delta_l: float
    apartment_delta_l: float
    residual_l_min: float
    alarm: bool
    quality: Quality


@dataclass(frozen=True, slots=True)
class Experiment5Artifacts:
    output_directory: Path
    measurements_path: Path
    import_audit_path: Path
    water_balance_path: Path
    alarms_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _balance_windows(
    measurements: tuple[Measurement, ...],
    roles_by_asset: dict[str, str],
    threshold_l_min: float,
) -> tuple[BalanceWindow, ...]:
    registers: dict[str, dict[datetime, Measurement]] = {}
    for item in measurements:
        registers.setdefault(item.asset_id, {})[item.timestamp] = item
    building_assets = [asset for asset, role in roles_by_asset.items() if role == "building"]
    apartment_assets = [asset for asset, role in roles_by_asset.items() if role == "apartment"]
    if len(building_assets) != 1 or not apartment_assets:
        raise ValueError("configuration requires one building and at least one apartment meter")
    assets = building_assets + sorted(apartment_assets)
    common_times = sorted(set.intersection(*(set(registers[asset]) for asset in assets)))
    windows: list[BalanceWindow] = []
    for start, end in zip(common_times, common_times[1:]):
        duration_seconds = round((end - start).total_seconds())
        if duration_seconds <= 0:
            raise ValueError("common reading timestamps must increase")
        building_delta_l = (
            registers[building_assets[0]][end].value
            - registers[building_assets[0]][start].value
        ) * 1000.0
        apartment_delta_l = sum(
            (registers[asset][end].value - registers[asset][start].value) * 1000.0
            for asset in apartment_assets
        )
        residual_l_min = (building_delta_l - apartment_delta_l) / (duration_seconds / 60.0)
        endpoints = [registers[asset][time] for asset in assets for time in (start, end)]
        quality = Quality.SUSPECT if any(item.quality is not Quality.GOOD for item in endpoints) else Quality.GOOD
        windows.append(
            BalanceWindow(
                start, end, duration_seconds, building_delta_l, apartment_delta_l,
                residual_l_min, abs(residual_l_min) >= threshold_l_min, quality,
            )
        )
    return tuple(windows)


def _overlaps(window: BalanceWindow, reference: dict[str, str]) -> bool:
    start = datetime.fromisoformat(reference["start_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(reference["end_utc"].replace("Z", "+00:00"))
    return window.start < end and window.end > start


def _reference_result(
    windows: tuple[BalanceWindow, ...], reference: dict[str, str]
) -> dict[str, object]:
    start = datetime.fromisoformat(reference["start_utc"].replace("Z", "+00:00"))
    matching = [item for item in windows if item.alarm and _overlaps(item, reference)]
    first = min((item.end for item in matching), default=None)
    return {
        "detected": bool(matching),
        "alarm_window_count": len(matching),
        "detection_delay_seconds": (
            None if first is None else max(0, round((first - start).total_seconds()))
        ),
    }


def _write_outputs(
    output: Path,
    imported: ImportResult,
    windows: tuple[BalanceWindow, ...],
    building_asset: str,
) -> tuple[Path, Path, Path]:
    audit_path = output / "import_audit.csv"
    with audit_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("row_number", "source_meter_id", "action", "reason", "measurement_id"))
        for item in imported.audit:
            writer.writerow((item.row_number, item.source_meter_id, item.action, item.reason, item.measurement_id))
    balance_path = output / "water_balance.csv"
    with balance_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("window_start_utc", "window_end_utc", "duration_seconds", "building_delta_l", "apartment_delta_l", "residual_l_min", "alarm", "quality"))
        for item in windows:
            writer.writerow((_timestamp(item.start), _timestamp(item.end), item.duration_seconds, format(item.building_delta_l, ".17g"), format(item.apartment_delta_l, ".17g"), format(item.residual_l_min, ".17g"), item.alarm, item.quality.value))
    alarms_path = output / "alarms.csv"
    with alarms_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("anomaly_type", "asset_id", "window_start_utc", "window_end_utc", "residual_l_min", "evidence"))
        for item in windows:
            if item.alarm:
                writer.writerow(("water_balance_anomaly", building_asset, _timestamp(item.start), _timestamp(item.end), format(item.residual_l_min, ".17g"), "building register minus sum of apartment registers"))
    return audit_path, balance_path, alarms_path


def _plot(path: Path, imported: ImportResult, windows: tuple[BalanceWindow, ...], threshold: float, references: list[dict[str, str]]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    colors = ("#333333", "#146c94", "#2a9d8f", "#e9c46a", "#c7511f")
    figure, axes = plt.subplots(3, 1, figsize=(7.2, 6.8), sharex=True)
    for color, asset in zip(colors, sorted(imported.roles_by_asset)):
        series = [item for item in imported.measurements if item.asset_id == asset]
        axes[0].plot([item.timestamp for item in series], [item.value * 1000 for item in series], color=color, linewidth=.9, label=imported.roles_by_asset[asset] + ": " + asset)
    axes[0].set_title("Experiment 5: vendor export through the canonical data path")
    axes[0].set_ylabel("Register [L]")
    axes[0].legend(ncol=2, frameon=False, fontsize=7)
    times = [item.end for item in windows]
    axes[1].plot(times, [item.building_delta_l for item in windows], color="#333333", marker=".", markersize=2.5, linewidth=.8, label="building")
    axes[1].plot(times, [item.apartment_delta_l for item in windows], color="#146c94", marker=".", markersize=2.5, linewidth=.8, label="apartments")
    axes[1].set_ylabel("Common-window\nvolume [L]")
    axes[1].legend(frameon=False, fontsize=8)
    axes[2].plot(times, [item.residual_l_min for item in windows], color="#146c94", marker=".", markersize=3, linewidth=.9, label="imported-data residual")
    axes[2].axhline(threshold, color="#d62828", linewidth=.9, label="alarm threshold")
    axes[2].axhline(-threshold, color="#d62828", linewidth=.9)
    axes[2].scatter([item.end for item in windows if item.alarm], [item.residual_l_min for item in windows if item.alarm], color="#d62828", s=10, label="alarm")
    axes[2].set_ylabel("Residual [L/min]")
    axes[2].set_xlabel("Time (UTC)")
    axes[2].legend(ncol=3, frameon=False, fontsize=7.5)
    shade_colors = ("#2a9d8f", "#e9c46a")
    for axis in axes:
        axis.grid(alpha=.22)
        for color, reference in zip(shade_colors, references):
            axis.axvspan(datetime.fromisoformat(reference["start_utc"].replace("Z", "+00:00")), datetime.fromisoformat(reference["end_utc"].replace("Z", "+00:00")), color=color, alpha=.12)
    axes[2].xaxis.set_major_locator(mdates.HourLocator(interval=3))
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    figure.tight_layout()
    figure.savefig(path, dpi=150, bbox_inches="tight", metadata={"Software": "Building Utility Twin", "Creation Time": None})
    plt.close(figure)


def run_experiment_5(config_path: str | Path, output_directory: str | Path) -> Experiment5Artifacts:
    config_path = Path(config_path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    source_path = (config_path.parent / config["input_path"]).resolve()
    adapter = VendorCsvConfig.from_dict(config["adapter"])
    threshold = float(config["analytics"]["water_balance_alarm_l_min"])
    imported = import_vendor_csv(source_path, adapter)
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    measurements_path = output / "measurements.jsonl"
    store = JsonLinesMeasurementStore(measurements_path)
    imported_count = store.replace(imported.measurements)
    canonical_import = tuple(store.read_all())
    if canonical_import != imported.measurements or imported_count != len(canonical_import):
        raise RuntimeError("canonical import round trip changed the data")
    windows = _balance_windows(canonical_import, imported.roles_by_asset, threshold)
    derived = [Measurement.create(asset_id=next(asset for asset, role in imported.roles_by_asset.items() if role == "building"), channel="building_water_balance_residual", quantity=Quantity.VOLUMETRIC_FLOW_RATE, timestamp=item.end, value=item.residual_l_min / 60_000, quality=item.quality, source="imported-water-balance-analytics", duration_seconds=item.duration_seconds) for item in windows]
    stored_count = store.replace((*canonical_import, *derived))
    if len(store.read_all()) != stored_count:
        raise RuntimeError("canonical file-backed round trip changed the data")
    building_asset = next(asset for asset, role in imported.roles_by_asset.items() if role == "building")
    audit_path, balance_path, alarms_path = _write_outputs(output, imported, windows, building_asset)
    references = list(config["reference_windows"])
    event_results = {
        reference["name"]: _reference_result(windows, reference)
        for reference in references
    }
    false_alarms = sum(item.alarm and not any(_overlaps(item, reference) for reference in references) for item in windows)
    accepted = sum(item.action == "accepted" for item in imported.audit)
    checks = {
        "all_source_rows_accepted": accepted == len(imported.audit),
        "input_matches_frozen_hash": _sha256(source_path) == config["expected_input_sha256"],
        "canonical_round_trip": stored_count == accepted + len(windows),
        "all_registers_monotonic": True,
        "both_reference_events_detected": all(
            bool(result["detected"]) for result in event_results.values()
        ),
        "no_alarms_outside_reference_windows": false_alarms == 0,
    }
    summary = {
        "schema_version": "1.0",
        "experiment_id": config["experiment_id"],
        "source": {"kind": "frozen_representative_vendor_export", "path": config["input_path"], "sha256": _sha256(source_path), "field_data": False, "limitation": "The export preserves external file conventions but is derived from a controlled simulation reference; it validates substitution, not field performance."},
        "import": {"source_row_count": len(imported.audit), "accepted_measurement_count": accepted, "duplicate_row_count": sum(item.action == "duplicate" for item in imported.audit), "canonical_unit": "m3", "utc_normalized": True, "meter_count": len(imported.roles_by_asset)},
        "water_balance": {
            "common_window_count": len(windows),
            "alarm_count": sum(item.alarm for item in windows),
            "alarm_threshold_l_min": threshold,
            "false_alarm_count_outside_reference_windows": false_alarms,
            "minimum_window_duration_seconds": min(item.duration_seconds for item in windows),
            "maximum_window_duration_seconds": max(item.duration_seconds for item in windows),
            "good_quality_window_count": sum(item.quality is Quality.GOOD for item in windows),
            "suspect_quality_window_count": sum(item.quality is Quality.SUSPECT for item in windows),
            "maximum_residual_l_min": max(item.residual_l_min for item in windows),
            "reference_event_detection": event_results,
            "source_identifiable_from_balance_alone": False,
        },
        "results": {"stored_measurement_count": stored_count},
        "acceptance": {"checks": checks, "passed": all(checks.values())},
        "reproducibility": {"input_sha256": _sha256(source_path), "measurements_sha256": _sha256(measurements_path), "import_audit_sha256": _sha256(audit_path), "water_balance_sha256": _sha256(balance_path), "alarms_sha256": _sha256(alarms_path)},
    }
    if not summary["acceptance"]["passed"]:
        raise RuntimeError("Experiment 5 failed an acceptance check")
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    figure_path = output / "experiment_5_file_adapter.png"
    _plot(figure_path, imported, windows, threshold, references)
    return Experiment5Artifacts(output, measurements_path, audit_path, balance_path, alarms_path, summary_path, figure_path, summary)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/experiment_5.json"))
    parser.add_argument("--output", type=Path, default=Path("results/experiment_5"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    artifacts = run_experiment_5(arguments.config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
