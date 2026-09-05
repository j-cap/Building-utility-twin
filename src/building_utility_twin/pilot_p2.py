"""Reproducible runner for Pilot Preparation P2."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from .api import create_app
from .dashboard_client import build_dashboard_snapshot
from .persistent_backend import PersistentBackend
from .synthetic_portfolio import PortfolioConfig, generate_portfolio


@dataclass(frozen=True, slots=True)
class PilotP2Artifacts:
    output_directory: Path
    database_path: Path
    dashboard_snapshot_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


class _TestApiDashboardClient:
    """Dashboard data source that traverses the FastAPI contract in-process."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    @staticmethod
    def _json(response: Any) -> Any:
        response.raise_for_status()
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._json(self.client.get("/health"))

    def portfolio_overview(self) -> dict[str, Any]:
        return self._json(self.client.get("/api/v1/portfolio/overview"))

    def buildings(self) -> list[dict[str, Any]]:
        return self._json(self.client.get("/api/v1/buildings"))

    def building_meters(self, building_id: str) -> list[dict[str, Any]]:
        return self._json(
            self.client.get(f"/api/v1/buildings/{building_id}/meters")
        )

    def building_balance(self, building_id: str) -> dict[str, Any]:
        return self._json(
            self.client.get(
                f"/api/v1/buildings/{building_id}/water-balance"
            )
        )

    def meter_profile(self, meter_id: str) -> dict[str, Any]:
        return self._json(
            self.client.get(f"/api/v1/meters/{meter_id}/profile")
        )

    def imports(
        self, *, include_runtime_fields: bool = True
    ) -> list[dict[str, Any]]:
        return self._json(
            self.client.get(
                "/api/v1/imports",
                params={"include_runtime_fields": include_runtime_fields},
            )
        )

    def issues(
        self,
        *,
        status: str | None = None,
        building_id: str | None = None,
        include_runtime_fields: bool = True,
    ) -> list[dict[str, Any]]:
        params: dict[str, object] = {
            "include_runtime_fields": include_runtime_fields
        }
        if status is not None:
            params["status"] = status
        if building_id is not None:
            params["building_id"] = building_id
        return self._json(self.client.get("/api/v1/issues", params=params))

    def update_issue(
        self,
        issue_id: str,
        *,
        status: str | None = None,
        operator_note: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, object] = {}
        if status is not None:
            payload["status"] = status
        if operator_note is not None:
            payload["operator_note"] = operator_note
        return self._json(
            self.client.patch(f"/api/v1/issues/{issue_id}", json=payload)
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _plot_operator_overview(path: Path, snapshot: Mapping[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    overview = snapshot["overview"]
    portfolio = overview["portfolio"]
    cards = overview["building_cards"]
    queue = overview["review_queue"]

    background = "#08111f"
    panel = "#101d31"
    text = "#f8fafc"
    muted = "#9fb1c7"
    cyan = "#38bdf8"
    amber = "#f59e0b"
    green = "#22c55e"

    figure = plt.figure(figsize=(11.2, 6.3), facecolor=background)
    grid = figure.add_gridspec(2, 2, height_ratios=(0.58, 2.5), hspace=0.35, wspace=0.28)
    header = figure.add_subplot(grid[0, :])
    header.set_facecolor(background)
    header.axis("off")
    metrics = (
        ("BUILDINGS", f"{portfolio['building_count']}", cyan),
        ("METERS", f"{portfolio['meter_count']}", cyan),
        ("READINGS", f"{portfolio['measurement_count']:,}", green),
        ("ACTIVE REVIEWS", f"{queue['active_issue_count']}", amber),
    )
    for index, (label, value, color) in enumerate(metrics):
        x = 0.01 + index * 0.247
        header.text(
            x,
            0.42,
            f"{label}\n{value}",
            transform=header.transAxes,
            color=text,
            fontsize=13,
            fontweight="bold",
            va="center",
            bbox={
                "boxstyle": "round,pad=0.65",
                "facecolor": panel,
                "edgecolor": color,
                "linewidth": 1.2,
            },
        )

    consumption = figure.add_subplot(grid[1, 0], facecolor=panel)
    labels = [item["building_id"].replace("building-", "B") for item in cards]
    values = [item["consumption_l"] / 1000.0 for item in cards]
    colors = [amber if item["active_issue_count"] else cyan for item in cards]
    consumption.bar(labels, values, color=colors, width=0.68)
    consumption.set_title("30-day building consumption", color=text, loc="left", pad=14)
    consumption.set_ylabel("Volume [m³]", color=muted)

    completeness = figure.add_subplot(grid[1, 1], facecolor=panel)
    complete = [item["building_meter_completeness_percent"] for item in cards]
    completeness.barh(labels, complete, color=green, height=0.62)
    completeness.axvline(97.5, color=amber, linestyle="--", linewidth=1.2)
    completeness.set_xlim(95.0, 100.2)
    completeness.set_title("Building-meter completeness", color=text, loc="left", pad=14)
    completeness.set_xlabel("Completeness [%]", color=muted)

    for axis in (consumption, completeness):
        axis.tick_params(colors=muted)
        for spine in axis.spines.values():
            spine.set_color("#22324b")
        axis.grid(axis="x" if axis is completeness else "y", color="#334155", alpha=0.35)
        axis.set_axisbelow(True)

    figure.suptitle(
        "Building Utility Twin · P2 operator overview",
        color=text,
        fontsize=18,
        fontweight="bold",
        x=0.06,
        ha="left",
        y=0.99,
    )
    figure.text(
        0.06,
        0.935,
        "Synthetic interface evidence — review signals are not confirmed leaks",
        color=muted,
        fontsize=10.5,
    )
    figure.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
        facecolor=figure.get_facecolor(),
        metadata={"Software": "Building Utility Twin", "Creation Time": None},
    )
    plt.close(figure)


def run_pilot_p2(
    config: PortfolioConfig, output_directory: str | Path
) -> PilotP2Artifacts:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    database_path = output / "backend.sqlite3"
    database_path.unlink(missing_ok=True)

    portfolio = generate_portfolio(config)
    backend = PersistentBackend.for_path(database_path)
    backend.load_portfolio(portfolio)
    backend.close()

    app = create_app(f"sqlite:///{database_path.resolve()}")
    with TestClient(app) as test_client:
        client = _TestApiDashboardClient(test_client)
        snapshot = build_dashboard_snapshot(client)
        api_endpoint_count = sum(
            route.path == "/health" or route.path.startswith("/api/v1/")
            for route in app.routes
        )

    dashboard_snapshot_path = output / "dashboard_snapshot.json"
    dashboard_snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    cards = snapshot["overview"]["building_cards"]
    issues = snapshot["issues"]
    checks = {
        "five_operator_pages": len(snapshot["pages"]) == 5,
        "portfolio_visible": len(cards) == len(portfolio.buildings),
        "building_balance_exposed": snapshot["example_building_balance"][
            "matched_boundary_count"
        ]
        > 0,
        "balance_worded_as_evidence": "not attribution"
        in snapshot["example_building_balance"]["water_balance"][
            "interpretation"
        ],
        "meter_quality_visible": snapshot["example_meter_profile"]["summary"][
            "suspect_reading_count"
        ]
        >= 0,
        "import_audit_visible": len(snapshot["imports"]) == 1,
        "review_queue_populated": len(issues) > 0,
        "review_items_are_evidence_backed": all(
            item["evidence"]["classification"] == "data_quality"
            for item in issues
        ),
        "operator_api_complete": api_endpoint_count == 11,
    }
    severity_counts: dict[str, int] = {}
    for issue in issues:
        severity = str(issue["severity"])
        severity_counts[severity] = severity_counts.get(severity, 0) + 1
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "work_package": "pilot_preparation_p2",
        "configuration_digest": portfolio.digest,
        "dashboard": {
            "page_count": len(snapshot["pages"]),
            "pages": snapshot["pages"],
            "api_endpoint_count": api_endpoint_count,
            "snapshot_sha256": _sha256(dashboard_snapshot_path),
        },
        "portfolio_view": {
            "building_count": len(cards),
            "active_issue_count": snapshot["overview"]["review_queue"][
                "active_issue_count"
            ],
            "issue_severity_counts": dict(sorted(severity_counts.items())),
            "minimum_building_meter_completeness_percent": min(
                item["building_meter_completeness_percent"] for item in cards
            ),
            "maximum_absolute_period_balance_residual_l": max(
                abs(item["balance_residual_l"]) for item in cards
            ),
        },
        "acceptance": {"checks": checks, "passed": all(checks.values())},
        "scope_note": (
            "P2 verifies an API-backed operator workflow with deterministic synthetic "
            "data; review items are data-quality evidence, not leak diagnoses."
        ),
    }
    if not summary["acceptance"]["passed"]:  # type: ignore[index]
        raise RuntimeError("Pilot P2 failed an acceptance check")
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    figure_path = output / "pilot_p2_operator_overview.png"
    _plot_operator_overview(figure_path, snapshot)
    return PilotP2Artifacts(
        output,
        database_path,
        dashboard_snapshot_path,
        summary_path,
        figure_path,
        summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/pilot_p1.json"))
    parser.add_argument("--output", type=Path, default=Path("results/pilot_p2"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = PortfolioConfig.from_json_file(arguments.config)
    artifacts = run_pilot_p2(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
