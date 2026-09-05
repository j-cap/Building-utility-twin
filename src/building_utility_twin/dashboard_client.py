"""Replaceable API client and compact view model for the operator dashboard."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, Self

import httpx


class DashboardApiError(RuntimeError):
    """Raised when the operator API cannot satisfy a dashboard request."""


class DashboardDataSource(Protocol):
    def health(self) -> dict[str, Any]: ...

    def portfolio_overview(self) -> dict[str, Any]: ...

    def buildings(self) -> list[dict[str, Any]]: ...

    def building_meters(self, building_id: str) -> list[dict[str, Any]]: ...

    def building_balance(self, building_id: str) -> dict[str, Any]: ...

    def meter_profile(self, meter_id: str) -> dict[str, Any]: ...

    def imports(self, *, include_runtime_fields: bool = True) -> list[dict[str, Any]]: ...

    def issues(
        self,
        *,
        status: str | None = None,
        building_id: str | None = None,
        include_runtime_fields: bool = True,
    ) -> list[dict[str, Any]]: ...

    def analytics_summary(self) -> dict[str, Any]: ...

    def analytics_evidence(
        self,
        *,
        evidence_level: str | None = None,
        outcome: str | None = None,
        campaign_id: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def update_issue(
        self,
        issue_id: str,
        *,
        status: str | None = None,
        operator_note: str | None = None,
    ) -> dict[str, Any]: ...


class HttpDashboardClient:
    """HTTP implementation used by the Streamlit application."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            transport=transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method, path, params=params, json=json_body
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise DashboardApiError(
                f"operator API request failed: {method} {path}"
            ) from error

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def portfolio_overview(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/portfolio/overview")

    def buildings(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/v1/buildings")

    def building_meters(self, building_id: str) -> list[dict[str, Any]]:
        return self._request(
            "GET", f"/api/v1/buildings/{building_id}/meters"
        )

    def building_balance(self, building_id: str) -> dict[str, Any]:
        return self._request(
            "GET", f"/api/v1/buildings/{building_id}/water-balance"
        )

    def meter_profile(self, meter_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/meters/{meter_id}/profile")

    def imports(
        self, *, include_runtime_fields: bool = True
    ) -> list[dict[str, Any]]:
        return self._request(
            "GET",
            "/api/v1/imports",
            params={"include_runtime_fields": include_runtime_fields},
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
        return self._request("GET", "/api/v1/issues", params=params)

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
        return self._request(
            "PATCH", f"/api/v1/issues/{issue_id}", json_body=payload
        )

    def analytics_summary(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/analytics/summary")

    def analytics_evidence(
        self,
        *,
        evidence_level: str | None = None,
        outcome: str | None = None,
        campaign_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, object] = {}
        if evidence_level is not None:
            params["evidence_level"] = evidence_level
        if outcome is not None:
            params["outcome"] = outcome
        if campaign_id is not None:
            params["campaign_id"] = campaign_id
        return self._request(
            "GET", "/api/v1/analytics/evidence", params=params
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build_dashboard_snapshot(client: DashboardDataSource) -> dict[str, Any]:
    """Build a stable, compact representation of every P2 dashboard surface."""

    health = client.health()
    overview = client.portfolio_overview()
    buildings = client.buildings()
    first_building_id = str(buildings[0]["building_id"])
    meters = client.building_meters(first_building_id)
    first_meter_id = str(meters[0]["meter_id"])

    balance = client.building_balance(first_building_id)
    balance_series = balance.pop("series")
    balance["series_evidence"] = {
        "point_count": len(balance_series),
        "first": balance_series[0],
        "last": balance_series[-1],
        "maximum_absolute_residual_l": round(
            max(abs(item["balance_residual_l"]) for item in balance_series),
            6,
        ),
    }

    profile = client.meter_profile(first_meter_id)
    measurements = profile.pop("measurements")
    profile["series_evidence"] = {
        "point_count": len(measurements),
        "first": measurements[0],
        "last": measurements[-1],
    }
    return {
        "dashboard_contract_version": "1.0",
        "pages": [
            "Portfolio health",
            "Building balance",
            "Meter detail",
            "Imports",
            "Review queue",
        ],
        "health": health,
        "overview": overview,
        "example_building_balance": balance,
        "example_meter_profile": profile,
        "imports": client.imports(include_runtime_fields=False),
        "issues": client.issues(include_runtime_fields=False),
    }
