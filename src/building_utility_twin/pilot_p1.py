"""Reproducible runner for Pilot Preparation P1."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .persistent_backend import BACKEND_SCHEMA_VERSION, PersistentBackend
from .synthetic_portfolio import PortfolioConfig, SyntheticPortfolio, generate_portfolio


@dataclass(frozen=True, slots=True)
class PilotP1Artifacts:
    output_directory: Path
    database_path: Path
    api_snapshot_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _api_snapshot(backend: PersistentBackend) -> dict[str, object]:
    buildings = backend.list_buildings()
    first_building = buildings[0]
    meters = backend.building_meters(str(first_building["building_id"]))
    first_meter = meters[0]
    return {
        "portfolio": backend.portfolio_summary(),
        "buildings": buildings,
        "example_building_meters": meters,
        "example_meter_measurements": backend.meter_measurements(
            str(first_meter["meter_id"]), limit=5
        ),
        "imports": backend.list_imports(include_runtime_fields=False),
    }


def _plot(path: Path, portfolio: SyntheticPortfolio) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    building_assets = {
        item.building_id: item.asset_id
        for item in portfolio.meters
        if item.role == "building"
    }
    totals = []
    completeness = []
    expected = portfolio.config.days * 1440 // portfolio.config.interval_minutes + 1
    for building in portfolio.buildings:
        values = [
            item
            for item in portfolio.measurements
            if item.asset_id == building_assets[building.building_id]
        ]
        totals.append((values[-1].value - values[0].value) * 1000.0)
        completeness.append(100.0 * len(values) / expected)

    labels = [item.building_id.replace("building-", "B") for item in portfolio.buildings]
    figure, axes = plt.subplots(2, 1, figsize=(7.2, 5.6))
    axes[0].bar(labels, totals, color="#146c94")
    axes[0].set_title("Pilot P1: persistent synthetic portfolio")
    axes[0].set_ylabel("30-day building water [L]")
    axes[0].grid(axis="y", alpha=0.22)
    axes[1].bar(labels, completeness, color="#2a9d8f")
    axes[1].axhline(100.0, color="#333333", linewidth=0.8)
    axes[1].set_ylabel("Building-meter completeness [%]")
    axes[1].set_xlabel("Synthetic building")
    axes[1].set_ylim(95.0, 100.2)
    axes[1].grid(axis="y", alpha=0.22)
    figure.tight_layout()
    figure.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
        metadata={"Software": "Building Utility Twin", "Creation Time": None},
    )
    plt.close(figure)


def run_pilot_p1(
    config: PortfolioConfig, output_directory: str | Path
) -> PilotP1Artifacts:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    database_path = output / "backend.sqlite3"
    database_path.unlink(missing_ok=True)
    portfolio = generate_portfolio(config)
    backend = PersistentBackend.for_path(database_path)
    first = backend.load_portfolio(portfolio)
    replay = backend.load_portfolio(portfolio)
    counts = backend.counts()
    snapshot = _api_snapshot(backend)
    api_snapshot_path = output / "api_snapshot.json"
    api_snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    quality_counts = Counter(item.quality.value for item in portfolio.measurements)
    checks = {
        "topology_round_trip": counts["building_count"] == len(portfolio.buildings)
        and counts["apartment_count"] == len(portfolio.apartments)
        and counts["meter_count"] == len(portfolio.meters),
        "measurement_round_trip": counts["measurement_count"] == len(portfolio.measurements),
        "first_load_complete": first.accepted_count == len(portfolio.measurements)
        and first.duplicate_count == 0,
        "replay_idempotent": replay.replayed
        and replay.accepted_count == 0
        and replay.duplicate_count == len(portfolio.measurements),
        "single_auditable_import": counts["import_count"] == 1,
        "api_snapshot_has_all_buildings": len(snapshot["buildings"]) == len(portfolio.buildings),
        "schema_version_current": snapshot["portfolio"]["schema_version"] == BACKEND_SCHEMA_VERSION,
    }
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "work_package": "pilot_preparation_p1",
        "configuration": config.to_dict(),
        "generated": {
            "portfolio_digest": portfolio.digest,
            "building_count": len(portfolio.buildings),
            "apartment_count": len(portfolio.apartments),
            "meter_count": len(portfolio.meters),
            "measurement_count": len(portfolio.measurements),
            "quality_counts": dict(sorted(quality_counts.items())),
        },
        "persistence": {
            "backend": "SQLite",
            "schema_version": BACKEND_SCHEMA_VERSION,
            "database_counts": counts,
            "first_load": asdict(first),
            "replay": asdict(replay),
        },
        "api": {
            "endpoint_count": 6,
            "snapshot_sha256": _sha256(api_snapshot_path),
        },
        "acceptance": {"checks": checks, "passed": all(checks.values())},
        "scope_note": (
            "P1 verifies application plumbing with deterministic synthetic data; "
            "it does not validate vendor semantics, operational thresholds, or field performance."
        ),
    }
    if not summary["acceptance"]["passed"]:  # type: ignore[index]
        backend.close()
        raise RuntimeError("Pilot P1 failed an acceptance check")
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    figure_path = output / "pilot_p1_persistent_portfolio.png"
    _plot(figure_path, portfolio)
    backend.close()
    return PilotP1Artifacts(
        output, database_path, api_snapshot_path, summary_path, figure_path, summary
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/pilot_p1.json"))
    parser.add_argument("--output", type=Path, default=Path("results/pilot_p1"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = PortfolioConfig.from_json_file(arguments.config)
    artifacts = run_pilot_p1(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
