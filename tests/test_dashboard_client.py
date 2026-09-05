import unittest

import httpx

from building_utility_twin.dashboard_client import (
    DashboardApiError,
    HttpDashboardClient,
)


class DashboardClientTests(unittest.TestCase):
    def test_http_client_uses_versioned_operator_routes(self) -> None:
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.path == "/health":
                return httpx.Response(200, json={"status": "ok"})
            if request.url.path == "/api/v1/issues/issue-1":
                return httpx.Response(
                    200,
                    json={"issue_id": "issue-1", "status": "investigating"},
                )
            if request.url.path == "/api/v1/analytics/summary":
                return httpx.Response(200, json={"evidence_count": 14})
            if request.url.path == "/api/v1/analytics/evidence":
                return httpx.Response(
                    200,
                    json=[{"evidence_level": "diagnostic_research"}],
                )
            return httpx.Response(404, json={"detail": "not found"})

        with HttpDashboardClient(
            "http://operator.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            self.assertEqual(client.health()["status"], "ok")
            updated = client.update_issue("issue-1", status="investigating")
            self.assertEqual(updated["status"], "investigating")
            self.assertEqual(client.analytics_summary()["evidence_count"], 14)
            self.assertEqual(
                client.analytics_evidence(evidence_level="diagnostic_research")[0][
                    "evidence_level"
                ],
                "diagnostic_research",
            )
            with self.assertRaises(DashboardApiError):
                client.portfolio_overview()
        self.assertEqual(
            requests,
            [
                ("GET", "/health"),
                ("PATCH", "/api/v1/issues/issue-1"),
                ("GET", "/api/v1/analytics/summary"),
                ("GET", "/api/v1/analytics/evidence"),
                ("GET", "/api/v1/portfolio/overview"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
