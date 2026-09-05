"""Reproducible runner for Pilot Preparation P3."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from .analytics_test_bench import run_analytics_campaign
from .api import create_app
from .persistent_backend import PersistentBackend
from .synthetic_portfolio import PortfolioConfig, generate_portfolio


@dataclass(frozen=True, slots=True)
class PilotP3Artifacts:
    output_directory: Path
    database_path: Path
    analytics_snapshot_path: Path
    summary_path: Path
    figure_path: Path
    summary: dict[str, object]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65_536), b""):
            digest.update(block)
    return digest.hexdigest()


def _plot_analytics_bench(path: Path, snapshot: Mapping[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    evidence = snapshot["evidence"]
    summary = snapshot["summary"]
    background = "#08111f"
    panel = "#101d31"
    text = "#f8fafc"
    muted = "#9fb1c7"
    colors = {
        "clear": "#22c55e",
        "review": "#f59e0b",
        "research_only": "#a78bfa",
    }
    level_titles = (
        ("data_quality", "DATA QUALITY"),
        ("accounting_plausibility", "ACCOUNTING / PLAUSIBILITY"),
        ("diagnostic_research", "DIAGNOSTIC RESEARCH"),
    )
    short_labels = {
        "building_apartment_balance": "balance residual",
        "conflicting_readings": "conflicting values",
        "duplicate_readings": "duplicate rows",
        "forecasting": "hourly forecast",
        "learned_anomaly_score": "learned anomaly score",
        "leak_or_meter_fault_attribution": "leak vs meter fault",
        "missing_readings": "missing interval",
        "non_monotonic_register": "register reversal",
        "period_consumption": "period consumption",
        "persistent_night_flow": "persistent night flow",
        "stale_readings": "stale tail",
        "thermal_balance": "thermal residual",
        "topology_completeness": "topology completeness",
        "unexpected_sampling": "cadence shift",
    }

    figure = plt.figure(figsize=(12.0, 7.2), facecolor=background)
    grid = figure.add_gridspec(2, 3, height_ratios=(0.62, 3.0), hspace=0.34, wspace=0.2)
    header = figure.add_subplot(grid[0, :])
    header.set_facecolor(background)
    header.axis("off")
    metrics = (
        ("CASES", summary["evidence_count"], "#38bdf8"),
        (
            "MECHANISMS AGREE",
            f"{summary['mechanism_agreement_count']}/{summary['evidence_count']}",
            "#22c55e",
        ),
        ("REVIEW SIGNALS", summary["outcome_counts"]["review"], "#f59e0b"),
        ("OPERATIONAL CLAIMS", summary["operational_claim_count"], "#a78bfa"),
    )
    for index, (label, value, color) in enumerate(metrics):
        x = 0.01 + index * 0.247
        header.text(
            x,
            0.42,
            f"{label}\n{value}",
            transform=header.transAxes,
            color=text,
            fontsize=12.5,
            fontweight="bold",
            va="center",
            bbox={
                "boxstyle": "round,pad=0.65",
                "facecolor": panel,
                "edgecolor": color,
                "linewidth": 1.2,
            },
        )

    for column, (level, title) in enumerate(level_titles):
        axis = figure.add_subplot(grid[1, column], facecolor=panel)
        items = [item for item in evidence if item["evidence_level"] == level]
        positions = list(range(len(items)))
        axis.barh(
            positions,
            [1] * len(items),
            color=[colors[item["outcome"]] for item in items],
            height=0.58,
        )
        axis.set_yticks(positions, labels=[])
        axis.invert_yaxis()
        axis.set_xlim(0, 1.06)
        axis.set_xticks([])
        axis.set_title(title, color=text, loc="left", fontsize=11.5, pad=14)
        axis.tick_params(axis="y", length=0)
        for index, item in enumerate(items):
            axis.text(
                0.03,
                index,
                short_labels[item["analytic_id"]],
                color=background,
                fontsize=8.1,
                fontweight="bold",
                ha="left",
                va="center",
            )
            axis.text(
                0.97,
                index,
                {
                    "clear": "CLEAR",
                    "review": "REVIEW",
                    "research_only": "RESEARCH",
                }[item["outcome"]],
                color=background,
                fontsize=7.7,
                fontweight="bold",
                ha="right",
                va="center",
            )
        for spine in axis.spines.values():
            spine.set_color("#22324b")

    figure.suptitle(
        "Building Utility Twin · P3 analytics test bench",
        color=text,
        fontsize=18,
        fontweight="bold",
        x=0.055,
        ha="left",
        y=0.99,
    )
    figure.text(
        0.055,
        0.945,
        "Synthetic mechanism agreement — no field-calibrated thresholds or diagnostic claims",
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


def run_pilot_p3(
    config: PortfolioConfig, output_directory: str | Path
) -> PilotP3Artifacts:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    database_path = output / "backend.sqlite3"
    database_path.unlink(missing_ok=True)

    portfolio = generate_portfolio(config)
    campaign = run_analytics_campaign(portfolio)
    backend = PersistentBackend.for_path(database_path)
    backend.load_portfolio(portfolio)
    first_load = backend.store_analytics_campaign(campaign)
    replay = backend.store_analytics_campaign(campaign)
    backend.close()

    app = create_app(f"sqlite:///{database_path.resolve()}")
    with TestClient(app) as client:
        analytics_summary = client.get("/api/v1/analytics/summary").json()
        evidence = client.get("/api/v1/analytics/evidence").json()
        operator_issues = [
            item
            for item in client.get(
                "/api/v1/issues", params={"include_runtime_fields": False}
            ).json()
            if item["category"] == "analytics"
        ]
        api_endpoint_count = sum(
            route.path == "/health" or route.path.startswith("/api/v1/")
            for route in app.routes
        )

    snapshot = {
        "dashboard_contract_version": "1.1",
        "page": "Analytics test bench",
        "summary": analytics_summary,
        "evidence": evidence,
        "operator_review_items": operator_issues,
    }
    analytics_snapshot_path = output / "analytics_snapshot.json"
    analytics_snapshot_path.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    level_counts = Counter(item["evidence_level"] for item in evidence)
    outcome_counts = Counter(item["outcome"] for item in evidence)
    diagnostic = [
        item for item in evidence if item["evidence_level"] == "diagnostic_research"
    ]
    review_evidence_ids = {
        item["evidence_id"] for item in evidence if item["outcome"] == "review"
    }
    issue_evidence_ids = {
        item["evidence"]["analytics_evidence_id"] for item in operator_issues
    }
    checks = {
        "all_fourteen_mechanisms_present": len(evidence) == 14,
        "evidence_levels_complete": level_counts
        == {
            "data_quality": 6,
            "accounting_plausibility": 5,
            "diagnostic_research": 3,
        },
        "expected_outcomes_complete": outcome_counts
        == {"clear": 1, "review": 10, "research_only": 3},
        "all_mechanisms_agree": all(item["mechanism_agrees"] for item in evidence),
        "synthetic_campaign_makes_no_operational_claims": analytics_summary[
            "operational_claim_count"
        ]
        == 0,
        "diagnostics_remain_research_only": all(
            item["outcome"] == "research_only" for item in diagnostic
        ),
        "review_evidence_reaches_operator_queue": review_evidence_ids
        == issue_evidence_ids,
        "campaign_replay_is_idempotent": (
            first_load.accepted_count == 14
            and replay.accepted_count == 0
            and replay.duplicate_count == 14
        ),
        "analytics_api_complete": api_endpoint_count >= 13,
    }
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "work_package": "pilot_preparation_p3",
        "configuration_digest": portfolio.digest,
        "campaign": {
            "campaign_id": campaign.campaign_id,
            "campaign_digest": campaign.digest,
            "evidence_count": len(evidence),
            "evidence_level_counts": dict(sorted(level_counts.items())),
            "outcome_counts": dict(sorted(outcome_counts.items())),
            "mechanism_agreement_count": analytics_summary[
                "mechanism_agreement_count"
            ],
            "operator_review_issue_count": len(operator_issues),
            "first_load_accepted": first_load.accepted_count,
            "replay_duplicate_count": replay.duplicate_count,
        },
        "interface": {
            "operator_page_count": 6,
            "api_endpoint_count": api_endpoint_count,
            "snapshot_sha256": _sha256(analytics_snapshot_path),
        },
        "acceptance": {"checks": checks, "passed": all(checks.values())},
        "scope_note": (
            "P3 verifies analytics mechanisms with deterministic synthetic "
            "interventions. Threshold validity, false-alarm rates, forecast skill, "
            "and diagnostic attribution remain field-data questions."
        ),
    }
    if not summary["acceptance"]["passed"]:  # type: ignore[index]
        raise RuntimeError("Pilot P3 failed an acceptance check")
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    figure_path = output / "pilot_p3_analytics_test_bench.png"
    _plot_analytics_bench(figure_path, snapshot)
    return PilotP3Artifacts(
        output,
        database_path,
        analytics_snapshot_path,
        summary_path,
        figure_path,
        summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/pilot_p1.json"))
    parser.add_argument("--output", type=Path, default=Path("results/pilot_p3"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    config = PortfolioConfig.from_json_file(arguments.config)
    artifacts = run_pilot_p3(config, arguments.output)
    print(json.dumps(artifacts.summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
