"""Deterministic analytics campaigns with explicit evidence levels.

The test bench verifies software mechanisms against synthetic interventions. It
does not estimate operational accuracy, false-alarm rates, or diagnostic
validity. Those claims require held-out field data.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any
from uuid import UUID, uuid5

from .synthetic_portfolio import SyntheticPortfolio


ANALYTICS_CONTRACT_VERSION = "1.0"
CAMPAIGN_NAMESPACE = UUID("c3e756ed-70d0-4bd8-a622-91b5f30e5d30")
EVIDENCE_NAMESPACE = UUID("9a8f4c97-f137-4fb1-9651-05b42964dfb4")


class EvidenceLevel(str, Enum):
    """Strength and intended use of an analytic result."""

    DATA_QUALITY = "data_quality"
    ACCOUNTING_PLAUSIBILITY = "accounting_plausibility"
    DIAGNOSTIC_RESEARCH = "diagnostic_research"


class EvidenceOutcome(str, Enum):
    """Disposition exposed to the operator interface."""

    CLEAR = "clear"
    REVIEW = "review"
    RESEARCH_ONLY = "research_only"


@dataclass(frozen=True, slots=True)
class AnalyticsEvidence:
    evidence_id: str
    campaign_id: str
    analytic_id: str
    evidence_level: EvidenceLevel
    outcome: EvidenceOutcome
    expected_outcome: EvidenceOutcome
    building_id: str | None
    meter_id: str | None
    title: str
    interpretation: str
    observed: dict[str, Any]
    thresholds: dict[str, Any]
    provenance: dict[str, Any]
    operational_claim_allowed: bool = False
    contract_version: str = ANALYTICS_CONTRACT_VERSION

    def __post_init__(self) -> None:
        UUID(self.evidence_id)
        UUID(self.campaign_id)
        if not self.analytic_id.strip() or not self.title.strip():
            raise ValueError("analytic identity and title must not be empty")
        if self.contract_version != ANALYTICS_CONTRACT_VERSION:
            raise ValueError("unsupported analytics contract version")
        if self.evidence_level is EvidenceLevel.DIAGNOSTIC_RESEARCH:
            if self.outcome is not EvidenceOutcome.RESEARCH_ONLY:
                raise ValueError("diagnostic research must remain research_only")
            if self.operational_claim_allowed:
                raise ValueError("synthetic diagnostic evidence cannot support a claim")

    @property
    def mechanism_agrees(self) -> bool:
        return self.outcome is self.expected_outcome

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "evidence_id": self.evidence_id,
            "campaign_id": self.campaign_id,
            "analytic_id": self.analytic_id,
            "evidence_level": self.evidence_level.value,
            "outcome": self.outcome.value,
            "expected_outcome": self.expected_outcome.value,
            "mechanism_agrees": self.mechanism_agrees,
            "building_id": self.building_id,
            "meter_id": self.meter_id,
            "title": self.title,
            "interpretation": self.interpretation,
            "observed": self.observed,
            "thresholds": self.thresholds,
            "provenance": self.provenance,
            "operational_claim_allowed": self.operational_claim_allowed,
        }


@dataclass(frozen=True, slots=True)
class AnalyticsCampaign:
    campaign_id: str
    portfolio_id: str
    portfolio_digest: str
    evaluated_at_utc: datetime
    evidence: tuple[AnalyticsEvidence, ...]
    contract_version: str = ANALYTICS_CONTRACT_VERSION

    @property
    def digest(self) -> str:
        payload = self.to_dict(include_digest=False)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result = {
            "contract_version": self.contract_version,
            "campaign_id": self.campaign_id,
            "portfolio_id": self.portfolio_id,
            "portfolio_digest": self.portfolio_digest,
            "evaluated_at_utc": _timestamp(self.evaluated_at_utc),
            "synthetic_only": True,
            "evidence": [item.to_dict() for item in self.evidence],
        }
        if include_digest:
            result["campaign_digest"] = self.digest
        return result


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _register_diagnostics(
    readings: list[tuple[datetime, float]],
    *,
    expected_interval: timedelta,
    expected_end: datetime,
    stale_after: timedelta,
) -> dict[str, Any]:
    values_by_time: dict[datetime, list[float]] = defaultdict(list)
    for timestamp, value in readings:
        values_by_time[timestamp].append(value)
    ordered_times = sorted(values_by_time)
    duplicate_count = sum(
        max(0, len(values) - len(set(values))) for values in values_by_time.values()
    )
    conflicting_count = sum(len(set(values)) > 1 for values in values_by_time.values())
    representative = [(time, values_by_time[time][0]) for time in ordered_times]
    non_monotonic_count = sum(
        right[1] < left[1] for left, right in zip(representative, representative[1:])
    )
    expected_seconds = expected_interval.total_seconds()
    intervals = [
        (right - left).total_seconds()
        for left, right in zip(ordered_times, ordered_times[1:])
    ]
    missing_count = sum(
        max(0, round(seconds / expected_seconds) - 1)
        for seconds in intervals
        if math.isclose(seconds % expected_seconds, 0.0, abs_tol=1e-6)
    )
    unexpected_interval_count = sum(
        not math.isclose(seconds, expected_seconds, abs_tol=1e-6)
        for seconds in intervals
    )
    staleness_seconds = (
        max(0.0, (expected_end - ordered_times[-1]).total_seconds())
        if ordered_times
        else math.inf
    )
    return {
        "reading_count": len(readings),
        "missing_interval_count": missing_count,
        "duplicate_reading_count": duplicate_count,
        "conflicting_timestamp_count": conflicting_count,
        "non_monotonic_step_count": non_monotonic_count,
        "unexpected_interval_count": unexpected_interval_count,
        "staleness_seconds": staleness_seconds,
        "stale": staleness_seconds > stale_after.total_seconds(),
    }


def _hourly_increments(
    readings: list[tuple[datetime, float]], expected_interval: timedelta
) -> list[tuple[datetime, float]]:
    result: list[tuple[datetime, float]] = []
    for left, right in zip(readings, readings[1:]):
        if right[0] - left[0] == expected_interval:
            result.append((left[0], (right[1] - left[1]) * 1000.0))
    return result


def _round_metrics(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: round(value, 6) if isinstance(value, float) and math.isfinite(value) else value
        for key, value in values.items()
    }


def run_analytics_campaign(portfolio: SyntheticPortfolio) -> AnalyticsCampaign:
    """Run one deterministic synthetic campaign over every P3 analytic family."""

    config = portfolio.config
    evaluated_at = config.start + timedelta(days=config.days)
    campaign_id = str(
        uuid5(
            CAMPAIGN_NAMESPACE,
            f"{ANALYTICS_CONTRACT_VERSION}:{portfolio.digest}:p3-reference",
        )
    )
    first_building = portfolio.buildings[0]
    building_meters = [
        meter for meter in portfolio.meters if meter.building_id == first_building.building_id
    ]
    building_meter = next(meter for meter in building_meters if meter.role == "building")
    apartment_meters = [meter for meter in building_meters if meter.role == "apartment"]
    first_meter = apartment_meters[0]
    readings_by_asset: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for measurement in portfolio.measurements:
        readings_by_asset[measurement.asset_id].append(
            (measurement.timestamp.astimezone(UTC), measurement.value)
        )
    for readings in readings_by_asset.values():
        readings.sort(key=lambda item: item[0])

    observed_base = readings_by_asset[first_meter.asset_id]
    building_readings = readings_by_asset[building_meter.asset_id]
    step = timedelta(minutes=config.interval_minutes)
    stale_after = step * 1.5
    expected_end = evaluated_at
    expected_boundary_count = (
        config.days * 1440 // config.interval_minutes + 1
    )
    reference_increment = (
        observed_base[-1][1] - observed_base[0][1]
    ) / (expected_boundary_count - 1)
    base = [
        (
            config.start.astimezone(UTC) + index * step,
            observed_base[0][1] + index * reference_increment,
        )
        for index in range(expected_boundary_count)
    ]
    common_provenance = {
        "source": "deterministic_synthetic_campaign",
        "portfolio_digest": portfolio.digest,
        "reference_register": "complete_linearized_register_with_observed_endpoints",
        "synthetic_intervention": True,
        "field_validation_required": True,
    }
    evidence: list[AnalyticsEvidence] = []

    def add(
        analytic_id: str,
        level: EvidenceLevel,
        outcome: EvidenceOutcome,
        *,
        title: str,
        interpretation: str,
        observed: dict[str, Any],
        thresholds: dict[str, Any],
        meter_id: str | None = None,
        expected: EvidenceOutcome | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        evidence_id = str(uuid5(EVIDENCE_NAMESPACE, f"{campaign_id}:{analytic_id}"))
        evidence.append(
            AnalyticsEvidence(
                evidence_id=evidence_id,
                campaign_id=campaign_id,
                analytic_id=analytic_id,
                evidence_level=level,
                outcome=outcome,
                expected_outcome=expected or outcome,
                building_id=first_building.building_id,
                meter_id=meter_id,
                title=title,
                interpretation=interpretation,
                observed=_round_metrics(observed),
                thresholds=thresholds,
                provenance={**common_provenance, **(provenance or {})},
            )
        )

    quality_cases: list[tuple[str, str, list[tuple[datetime, float]], str]] = []
    missing = list(base)
    missing.pop(min(10, len(missing) - 2))
    quality_cases.append(("missing_readings", "missing_interval_count", missing, "Missing boundary"))
    stale = [item for item in base if item[0] <= expected_end - 2 * step]
    quality_cases.append(("stale_readings", "stale", stale, "Stale tail"))
    duplicate = list(base) + [base[min(20, len(base) - 2)]]
    quality_cases.append(("duplicate_readings", "duplicate_reading_count", duplicate, "Duplicate row"))
    conflict_index = min(30, len(base) - 2)
    conflict = list(base) + [(base[conflict_index][0], base[conflict_index][1] + 0.1)]
    quality_cases.append(("conflicting_readings", "conflicting_timestamp_count", conflict, "Conflicting value"))
    non_monotonic = list(base)
    non_index = min(40, len(base) - 2)
    non_monotonic[non_index] = (
        non_monotonic[non_index][0],
        max(0.0, non_monotonic[non_index - 1][1] - 0.05),
    )
    quality_cases.append(("non_monotonic_register", "non_monotonic_step_count", non_monotonic, "Register reversal"))
    irregular = list(base)
    cadence_index = min(50, len(base) - 2)
    irregular[cadence_index] = (
        irregular[cadence_index][0] + timedelta(minutes=15),
        irregular[cadence_index][1],
    )
    quality_cases.append(("unexpected_sampling", "unexpected_interval_count", irregular, "Cadence shift"))

    for analytic_id, target_metric, readings, title in quality_cases:
        diagnostics = _register_diagnostics(
            readings,
            expected_interval=step,
            expected_end=expected_end,
            stale_after=stale_after,
        )
        target_value = diagnostics[target_metric]
        detected = bool(target_value)
        add(
            analytic_id,
            EvidenceLevel.DATA_QUALITY,
            EvidenceOutcome.REVIEW if detected else EvidenceOutcome.CLEAR,
            title=title,
            interpretation=(
                "The injected data defect is exposed as review evidence. The test "
                "verifies detector mechanics, not a field-calibrated alarm rate."
            ),
            observed={"target_metric": target_metric, "target_value": target_value, **diagnostics},
            thresholds={
                "expected_interval_seconds": int(step.total_seconds()),
                "stale_after_seconds": int(stale_after.total_seconds()),
                "review_when_target_nonzero": True,
            },
            meter_id=first_meter.meter_id,
            expected=EvidenceOutcome.REVIEW,
            provenance={"intervention": analytic_id},
        )

    period_consumption_l = (base[-1][1] - base[0][1]) * 1000.0
    add(
        "period_consumption",
        EvidenceLevel.ACCOUNTING_PLAUSIBILITY,
        EvidenceOutcome.CLEAR if period_consumption_l >= 0.0 else EvidenceOutcome.REVIEW,
        title="Period consumption",
        interpretation="End-minus-start register consumption is finite and non-negative.",
        observed={"period_consumption_l": period_consumption_l},
        thresholds={"minimum_consumption_l": 0.0},
        meter_id=first_meter.meter_id,
        expected=EvidenceOutcome.CLEAR,
        provenance={"intervention": "none"},
    )

    expected_apartments = len(apartment_meters)
    observed_apartments = expected_apartments - 1
    add(
        "topology_completeness",
        EvidenceLevel.ACCOUNTING_PLAUSIBILITY,
        EvidenceOutcome.REVIEW,
        title="Incomplete accounting topology",
        interpretation="One synthetic apartment meter is absent from the accounting scope.",
        observed={
            "expected_apartment_meter_count": expected_apartments,
            "observed_apartment_meter_count": observed_apartments,
            "missing_apartment_meter_count": 1,
        },
        thresholds={"required_completeness_percent": 100.0},
        expected=EvidenceOutcome.REVIEW,
        provenance={"intervention": "remove_one_apartment_meter"},
    )

    apartment_period_l = sum(
        (readings_by_asset[meter.asset_id][-1][1] - readings_by_asset[meter.asset_id][0][1])
        * 1000.0
        for meter in apartment_meters
    )
    building_period_l = (building_readings[-1][1] - building_readings[0][1]) * 1000.0
    baseline_residual_l = building_period_l - apartment_period_l
    injected_residual_l = baseline_residual_l + 500.0
    add(
        "building_apartment_balance",
        EvidenceLevel.ACCOUNTING_PLAUSIBILITY,
        EvidenceOutcome.REVIEW,
        title="Building/apartment balance residual",
        interpretation=(
            "The injected residual exceeds the test threshold. It is balance evidence "
            "and does not identify a leak or a faulty meter."
        ),
        observed={
            "baseline_residual_l": baseline_residual_l,
            "injected_residual_l": injected_residual_l,
            "injected_component_l": 500.0,
        },
        thresholds={"absolute_residual_review_l": 250.0},
        expected=EvidenceOutcome.REVIEW,
        provenance={"intervention": "add_500_l_unexplained_building_register"},
    )

    injected_night_flows = [120.0, 120.0, 120.0, 120.0]
    night_threshold = 60.0
    required_windows = 3
    consecutive_above = sum(value > night_threshold for value in injected_night_flows)
    add(
        "persistent_night_flow",
        EvidenceLevel.ACCOUNTING_PLAUSIBILITY,
        EvidenceOutcome.REVIEW,
        title="Persistent night flow",
        interpretation=(
            "Four synthetic night windows exceed the test threshold. Persistence is "
            "plausibility evidence, not proof of leakage."
        ),
        observed={
            "night_window_count": len(injected_night_flows),
            "consecutive_windows_above_threshold": consecutive_above,
            "minimum_injected_night_flow_l_per_hour": min(injected_night_flows),
        },
        thresholds={
            "flow_review_l_per_hour": night_threshold,
            "required_consecutive_windows": required_windows,
        },
        expected=EvidenceOutcome.REVIEW,
        provenance={"intervention": "four_constant_night_flow_windows"},
    )

    thermal_input_kwh = 1000.0
    thermal_draw_kwh = 820.0
    storage_change_kwh = 80.0
    thermal_residual_kwh = thermal_input_kwh - thermal_draw_kwh - storage_change_kwh
    add(
        "thermal_balance",
        EvidenceLevel.ACCOUNTING_PLAUSIBILITY,
        EvidenceOutcome.REVIEW,
        title="Thermal balance residual",
        interpretation=(
            "The synthetic energy residual exceeds the test threshold. Calibration, "
            "boundary definitions, and field uncertainty remain unvalidated."
        ),
        observed={
            "thermal_input_kwh": thermal_input_kwh,
            "thermal_draw_kwh": thermal_draw_kwh,
            "storage_change_kwh": storage_change_kwh,
            "unaccounted_thermal_energy_kwh": thermal_residual_kwh,
        },
        thresholds={"absolute_residual_review_kwh": 25.0},
        expected=EvidenceOutcome.REVIEW,
        provenance={"intervention": "add_100_kwh_unaccounted_energy"},
    )

    add(
        "leak_or_meter_fault_attribution",
        EvidenceLevel.DIAGNOSTIC_RESEARCH,
        EvidenceOutcome.RESEARCH_ONLY,
        title="Leak versus meter-fault attribution",
        interpretation=(
            "A 500 L balance residual is observationally compatible with both an "
            "unmetered leak and building-meter over-registration. The cause remains "
            "unidentified without independent evidence."
        ),
        observed={
            "balance_residual_l": 500.0,
            "observationally_equivalent_hypothesis_count": 2,
            "confirmed_cause_count": 0,
        },
        thresholds={"promotion_gate": "independent_field_evidence"},
        expected=EvidenceOutcome.RESEARCH_ONLY,
        provenance={"intervention": "equivalent_leak_and_meter_bias_hypotheses"},
    )

    increments = _hourly_increments(building_readings, step)
    split = config.start + timedelta(days=max(1, config.days * 2 // 3))
    train = [(time, value) for time, value in increments if time < split]
    holdout = [(time, value) for time, value in increments if time >= split]
    by_hour: dict[int, list[float]] = defaultdict(list)
    for timestamp, value in train:
        by_hour[timestamp.hour].append(value)
    absolute_errors = [
        abs(value - statistics.mean(by_hour[timestamp.hour]))
        for timestamp, value in holdout
        if by_hour[timestamp.hour]
    ]
    forecast_mae = statistics.mean(absolute_errors)
    add(
        "forecasting",
        EvidenceLevel.DIAGNOSTIC_RESEARCH,
        EvidenceOutcome.RESEARCH_ONLY,
        title="Hourly-profile forecast",
        interpretation=(
            "A deterministic hourly-profile model produces a finite synthetic "
            "holdout error. It is a software benchmark, not a deployable forecast claim."
        ),
        observed={
            "training_interval_count": len(train),
            "holdout_interval_count": len(absolute_errors),
            "holdout_mae_l_per_hour": forecast_mae,
        },
        thresholds={"promotion_gate": "held_out_field_baseline_comparison"},
        expected=EvidenceOutcome.RESEARCH_ONLY,
        provenance={"intervention": "none", "model": "mean_consumption_by_hour"},
    )

    hourly_mean = {hour: statistics.mean(values) for hour, values in by_hour.items()}
    hourly_scale = {
        hour: max(statistics.pstdev(values), 1.0) for hour, values in by_hour.items()
    }
    scored_holdout = [
        abs(value - hourly_mean[timestamp.hour]) / hourly_scale[timestamp.hour]
        for timestamp, value in holdout
        if timestamp.hour in hourly_mean
    ]
    injected_time, injected_value = holdout[len(holdout) // 2]
    injected_score = abs(
        injected_value + 1000.0 - hourly_mean[injected_time.hour]
    ) / hourly_scale[injected_time.hour]
    add(
        "learned_anomaly_score",
        EvidenceLevel.DIAGNOSTIC_RESEARCH,
        EvidenceOutcome.RESEARCH_ONLY,
        title="Learned hourly anomaly score",
        interpretation=(
            "The fitted hourly baseline ranks an injected spike above the ordinary "
            "synthetic holdout. Field prevalence and score calibration are unknown."
        ),
        observed={
            "ordinary_holdout_max_score": max(scored_holdout),
            "injected_spike_l": 1000.0,
            "injected_spike_score": injected_score,
            "injected_ranked_above_holdout": injected_score > max(scored_holdout),
        },
        thresholds={"promotion_gate": "held_out_field_precision_recall"},
        expected=EvidenceOutcome.RESEARCH_ONLY,
        provenance={"intervention": "add_1000_l_hourly_spike", "model": "hourly_z_score"},
    )

    if len(evidence) != 14:
        raise RuntimeError(f"expected 14 analytics cases, got {len(evidence)}")
    return AnalyticsCampaign(
        campaign_id=campaign_id,
        portfolio_id=config.portfolio_id,
        portfolio_digest=portfolio.digest,
        evaluated_at_utc=evaluated_at,
        evidence=tuple(evidence),
    )
