"""Streamlit operator dashboard for the Building Utility Twin API."""

from __future__ import annotations

import os
from typing import Any

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from building_utility_twin.dashboard_client import (
    DashboardApiError,
    HttpDashboardClient,
)

PAGE_NAMES = (
    "Portfolio health",
    "Building balance",
    "Meter detail",
    "Imports",
    "Analytics test bench",
    "Review queue",
)
PLOTLY_LAYOUT = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"color": "#d8e4f0"},
    "margin": {"l": 12, "r": 12, "t": 48, "b": 12},
    "hoverlabel": {"bgcolor": "#101a2d", "font_color": "#f8fafc"},
}


def _format_liters(value: float) -> str:
    if abs(value) >= 1000.0:
        return f"{value / 1000.0:,.1f} m³"
    return f"{value:,.2f} L"


def _style_figure(figure: go.Figure, *, height: int = 390) -> go.Figure:
    figure.update_layout(**PLOTLY_LAYOUT, height=height)
    figure.update_xaxes(gridcolor="rgba(148,163,184,0.12)", zeroline=False)
    figure.update_yaxes(gridcolor="rgba(148,163,184,0.12)", zeroline=False)
    return figure


def _building_picker(
    client: HttpDashboardClient, *, key: str
) -> tuple[str, list[dict[str, Any]]]:
    buildings = client.buildings()
    labels = {str(item["building_id"]): str(item["name"]) for item in buildings}
    building_id = st.selectbox(
        "Building",
        options=list(labels),
        format_func=lambda value: f"{labels[value]} · {value}",
        key=key,
    )
    return str(building_id), buildings


def _portfolio_health(client: HttpDashboardClient) -> None:
    overview = client.portfolio_overview()
    portfolio = overview["portfolio"]
    queue = overview["review_queue"]
    cards = overview["building_cards"]

    st.title("Portfolio health")
    st.caption("Synthetic demonstration portfolio · operational claims require field data")
    columns = st.columns(5)
    columns[0].metric("Buildings", portfolio["building_count"])
    columns[1].metric("Meters", portfolio["meter_count"])
    columns[2].metric("Readings", f"{portfolio['measurement_count']:,}")
    columns[3].metric("Active reviews", queue["active_issue_count"])
    suspect = portfolio["quality_counts"].get("suspect", 0)
    suspect_share = 100.0 * suspect / portfolio["measurement_count"]
    columns[4].metric("Suspect readings", f"{suspect_share:.2f}%")

    left, right = st.columns((1.35, 1.0))
    with left:
        figure = px.bar(
            cards,
            x="name",
            y="consumption_l",
            color="active_issue_count",
            color_continuous_scale=["#38bdf8", "#f59e0b"],
            labels={
                "name": "Building",
                "consumption_l": "30-day consumption [L]",
                "active_issue_count": "Reviews",
            },
            title="Consumption and review load",
        )
        figure.update_layout(coloraxis_colorbar_title="Reviews")
        st.plotly_chart(_style_figure(figure), use_container_width=True)
    with right:
        completeness = go.Figure(
            go.Bar(
                x=[item["building_meter_completeness_percent"] for item in cards],
                y=[item["name"] for item in cards],
                orientation="h",
                marker_color="#22c55e",
                hovertemplate="%{x:.2f}%<extra></extra>",
            )
        )
        completeness.add_vline(
            x=97.5, line_dash="dot", line_color="#f59e0b"
        )
        completeness.update_layout(title="Building-meter completeness")
        completeness.update_xaxes(range=[95, 100.2], title="Completeness [%]")
        st.plotly_chart(
            _style_figure(completeness), use_container_width=True
        )

    st.subheader("Building status")
    st.dataframe(
        [
            {
                "Building": item["name"],
                "Consumption [m³]": round(item["consumption_l"] / 1000.0, 2),
                "Completeness [%]": round(
                    item["building_meter_completeness_percent"], 2
                ),
                "Balance residual [L]": item["balance_residual_l"],
                "Open reviews": item["active_issue_count"],
            }
            for item in cards
        ],
        use_container_width=True,
        hide_index=True,
    )


def _building_balance(client: HttpDashboardClient) -> None:
    st.title("Building balance")
    building_id, _ = _building_picker(client, key="balance_building")
    balance = client.building_balance(building_id)
    building = balance["building"]
    meter = balance["building_meter"]
    apartments = balance["apartments"]
    evidence = balance["water_balance"]

    st.caption(
        f"{building['name']} · {balance['matched_boundary_count']} common boundaries"
    )
    columns = st.columns(4)
    columns[0].metric("Building consumption", _format_liters(meter["consumption_l"]))
    columns[1].metric(
        "Apartment sum", _format_liters(apartments["consumption_l"])
    )
    columns[2].metric("Balance residual", _format_liters(evidence["residual_l"]))
    columns[3].metric("Building completeness", f"{meter['completeness_percent']:.2f}%")

    series = balance["series"]
    registers = go.Figure()
    registers.add_trace(
        go.Scatter(
            x=[item["timestamp"] for item in series],
            y=[item["building_register_l"] for item in series],
            name="Building register",
            line={"color": "#38bdf8", "width": 2.4},
        )
    )
    registers.add_trace(
        go.Scatter(
            x=[item["timestamp"] for item in series],
            y=[item["apartment_register_sum_l"] for item in series],
            name="Apartment-register sum",
            line={"color": "#f59e0b", "width": 1.6, "dash": "dot"},
        )
    )
    registers.update_layout(title="Registers at complete common boundaries")
    registers.update_yaxes(title="Cumulative volume [L]")
    st.plotly_chart(_style_figure(registers), use_container_width=True)

    residual = go.Figure(
        go.Scatter(
            x=[item["timestamp"] for item in series],
            y=[item["balance_residual_l"] for item in series],
            fill="tozeroy",
            line={"color": "#22c55e", "width": 1.5},
            name="Balance residual",
        )
    )
    residual.update_layout(title="Balance evidence")
    residual.update_yaxes(title="Building − apartments [L]")
    st.plotly_chart(_style_figure(residual, height=280), use_container_width=True)
    st.info(evidence["interpretation"], icon="ℹ️")


def _meter_detail(client: HttpDashboardClient) -> None:
    st.title("Meter detail")
    building_id, _ = _building_picker(client, key="meter_building")
    meters = client.building_meters(building_id)
    labels = {
        str(item["meter_id"]): (
            f"{item['serial_number']} · {item['role']}"
        )
        for item in meters
    }
    meter_id = st.selectbox(
        "Meter",
        options=list(labels),
        format_func=lambda value: labels[value],
    )
    profile = client.meter_profile(str(meter_id))
    meter = profile["meter"]
    summary = profile["summary"]
    measurements = profile["measurements"]

    st.caption(f"{meter['asset_id']} · {meter['serial_number']}")
    columns = st.columns(4)
    columns[0].metric("Consumption", _format_liters(summary["consumption_l"]))
    columns[1].metric("Completeness", f"{summary['completeness_percent']:.2f}%")
    columns[2].metric("Suspect readings", summary["suspect_reading_count"])
    columns[3].metric("Latest reading", summary["latest_reading_utc"][:10])

    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=[item["timestamp"] for item in measurements],
            y=[item["value"] * 1000.0 for item in measurements],
            name="Register",
            line={"color": "#38bdf8", "width": 2.1},
        )
    )
    suspect = [item for item in measurements if item["quality"] == "suspect"]
    figure.add_trace(
        go.Scatter(
            x=[item["timestamp"] for item in suspect],
            y=[item["value"] * 1000.0 for item in suspect],
            name="Suspect quality",
            mode="markers",
            marker={"color": "#f59e0b", "size": 7},
        )
    )
    figure.update_layout(title="Cumulative register and quality markers")
    figure.update_yaxes(title="Register [L]")
    st.plotly_chart(_style_figure(figure), use_container_width=True)

    with st.expander("Canonical measurement sample"):
        st.dataframe(measurements[-20:], use_container_width=True, hide_index=True)


def _imports(client: HttpDashboardClient) -> None:
    st.title("Imports")
    imports = client.imports()
    completed = sum(item["status"] == "completed" for item in imports)
    columns = st.columns(3)
    columns[0].metric("Import jobs", len(imports))
    columns[1].metric("Completed", completed)
    columns[2].metric(
        "Accepted rows", f"{sum(item['accepted_count'] for item in imports):,}"
    )


def _analytics_test_bench(client: HttpDashboardClient) -> None:
    st.title("Analytics test bench")
    st.caption(
        "Synthetic mechanism checks · thresholds and diagnostic performance require field data"
    )
    summary = client.analytics_summary()
    columns = st.columns(4)
    columns[0].metric("Campaigns", summary["campaign_count"])
    columns[1].metric("Evidence records", summary["evidence_count"])
    columns[2].metric(
        "Mechanisms agreeing",
        f"{summary['mechanism_agreement_count']}/{summary['evidence_count']}",
    )
    columns[3].metric("Operational claims", summary["operational_claim_count"])
    st.info(summary["scope_note"], icon="ℹ️")

    filter_columns = st.columns(2)
    level_label = filter_columns[0].selectbox(
        "Evidence level",
        (
            "All",
            "Data quality",
            "Accounting / plausibility",
            "Diagnostic research",
        ),
    )
    outcome_label = filter_columns[1].selectbox(
        "Outcome", ("All", "Clear", "Review", "Research only")
    )
    level = {
        "Data quality": "data_quality",
        "Accounting / plausibility": "accounting_plausibility",
        "Diagnostic research": "diagnostic_research",
    }.get(level_label)
    outcome = {
        "Clear": "clear",
        "Review": "review",
        "Research only": "research_only",
    }.get(outcome_label)
    evidence = client.analytics_evidence(evidence_level=level, outcome=outcome)
    if not evidence:
        st.warning("No analytics evidence matches the selected filters.")
        return

    level_counts = summary["evidence_level_counts"]
    chart_data = [
        {
            "Evidence level": label,
            "Cases": level_counts.get(key, 0),
        }
        for key, label in (
            ("data_quality", "Data quality"),
            ("accounting_plausibility", "Accounting / plausibility"),
            ("diagnostic_research", "Diagnostic research"),
        )
    ]
    figure = px.bar(
        chart_data,
        x="Evidence level",
        y="Cases",
        color="Evidence level",
        color_discrete_sequence=("#38bdf8", "#f59e0b", "#a78bfa"),
        title="Reference campaign coverage",
    )
    figure.update_layout(showlegend=False)
    st.plotly_chart(_style_figure(figure, height=320), use_container_width=True)
    st.dataframe(
        [
            {
                "Level": item["evidence_level"],
                "Analytic": item["analytic_id"],
                "Outcome": item["outcome"],
                "Expected": item["expected_outcome"],
                "Agrees": item["mechanism_agrees"],
                "Operational claim": item["operational_claim_allowed"],
            }
            for item in evidence
        ],
        use_container_width=True,
        hide_index=True,
    )
    labels = {
        str(item["evidence_id"]): f"{item['title']} · {item['outcome']}"
        for item in evidence
    }
    evidence_id = st.selectbox(
        "Evidence detail",
        options=list(labels),
        format_func=lambda value: labels[value],
    )
    selected = next(item for item in evidence if item["evidence_id"] == evidence_id)
    st.write(selected["interpretation"])
    left, right = st.columns(2)
    with left:
        st.markdown("**Observed evidence**")
        st.json(selected["observed"])
    with right:
        st.markdown("**Test thresholds and gates**")
        st.json(selected["thresholds"])
    st.dataframe(
        [
            {
                "Import": item["import_id"],
                "Source": item["source_name"],
                "Status": item["status"],
                "Rows": item["source_row_count"],
                "Accepted": item["accepted_count"],
                "Duplicates": item["duplicate_count"],
                "Completed [UTC]": item.get("completed_at_utc", ""),
                "Digest": item["source_digest"][:12],
            }
            for item in imports
        ],
        use_container_width=True,
        hide_index=True,
    )


def _review_queue(client: HttpDashboardClient) -> None:
    st.title("Review queue")
    st.caption(
        "Review signals describe quality or plausibility evidence; they are not diagnoses."
    )
    filter_columns = st.columns(2)
    status_label = filter_columns[0].selectbox(
        "Status",
        ("Active", "Open", "Investigating", "Resolved", "All"),
    )
    building_label = filter_columns[1].selectbox(
        "Building", ("All", *[item["building_id"] for item in client.buildings()])
    )
    status = {
        "Open": "open",
        "Investigating": "investigating",
        "Resolved": "resolved",
    }.get(status_label)
    issues = client.issues(
        status=status,
        building_id=None if building_label == "All" else building_label,
    )
    if status_label == "Active":
        issues = [item for item in issues if item["status"] != "resolved"]
    if not issues:
        st.success("No review items match the current filters.")
        return

    columns = st.columns(3)
    columns[0].metric("Matching reviews", len(issues))
    columns[1].metric(
        "Warnings", sum(item["severity"] == "warning" for item in issues)
    )
    columns[2].metric(
        "Investigating", sum(item["status"] == "investigating" for item in issues)
    )

    labels = {
        str(item["issue_id"]): (
            f"{item['severity'].upper()} · {item['building_id']} · {item['meter_id']}"
        )
        for item in issues
    }
    issue_id = st.selectbox(
        "Review item", options=list(labels), format_func=lambda value: labels[value]
    )
    issue = next(item for item in issues if item["issue_id"] == issue_id)
    st.subheader(issue["title"])
    st.write(issue["description"])
    evidence = issue["evidence"]
    if issue["category"] == "data_quality":
        evidence_columns = st.columns(4)
        evidence_columns[0].metric(
            "Completeness", f"{evidence['completeness_percent']:.2f}%"
        )
        evidence_columns[1].metric("Missing", evidence["missing_reading_count"])
        evidence_columns[2].metric("Suspect", evidence["suspect_reading_count"])
        evidence_columns[3].metric("Classification", evidence["classification"])
    else:
        evidence_columns = st.columns(3)
        evidence_columns[0].metric("Evidence level", evidence["classification"])
        evidence_columns[1].metric("Analytic", evidence["analytic_id"])
        evidence_columns[2].metric(
            "Operational claim",
            "Allowed" if evidence["operational_claim_allowed"] else "Not allowed",
        )
        with st.expander("Observed values and test thresholds", expanded=True):
            st.json(
                {
                    "observed": evidence["observed"],
                    "thresholds": evidence["thresholds"],
                }
            )

    with st.form("review_form"):
        new_status = st.selectbox(
            "Review status",
            ("open", "investigating", "resolved"),
            index=("open", "investigating", "resolved").index(issue["status"]),
        )
        note = st.text_area(
            "Operator note",
            value=issue["operator_note"],
            max_chars=2000,
            placeholder="Record checks performed and the next action.",
        )
        submitted = st.form_submit_button("Save review")
    if submitted:
        client.update_issue(
            str(issue_id), status=new_status, operator_note=note
        )
        st.success("Review saved.")
        st.rerun()


def _render() -> None:
    st.set_page_config(
        page_title="Building Utility Twin",
        page_icon="◫",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .stApp { background: #08111f; color: #d8e4f0; }
        [data-testid="stSidebar"] { background: #0d1728; }
        [data-testid="stMetric"] {
          background: #101d31; border: 1px solid #22324b;
          border-radius: 10px; padding: 0.9rem 1rem;
        }
        [data-testid="stMetricLabel"] { color: #9fb1c7; font-size: 0.9rem; }
        [data-testid="stMetricValue"] { color: #f8fafc; }
        h1, h2, h3 { color: #f8fafc; letter-spacing: -0.02em; }
        p, label, [data-testid="stCaptionContainer"] { font-size: 1rem; }
        div[data-testid="stDataFrame"] { border: 1px solid #22324b; border-radius: 8px; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.sidebar.title("Utility Twin")
    st.sidebar.caption("Operator workspace · P3")
    default_url = os.environ.get("BUILDING_UTILITY_API_URL", "http://127.0.0.1:8000")
    api_url = st.sidebar.text_input("API URL", value=default_url)
    page = st.sidebar.radio("Workspace", PAGE_NAMES)

    with HttpDashboardClient(api_url) as client:
        try:
            health = client.health()
            st.sidebar.success(
                f"Backend connected · schema {health['schema_version']}"
            )
            {
                "Portfolio health": _portfolio_health,
                "Building balance": _building_balance,
                "Meter detail": _meter_detail,
                "Imports": _imports,
                "Analytics test bench": _analytics_test_bench,
                "Review queue": _review_queue,
            }[page](client)
        except DashboardApiError:
            st.error(
                "The operator API is unavailable. Check the API URL and start the backend."
            )
            st.stop()


if __name__ == "__main__":
    _render()
