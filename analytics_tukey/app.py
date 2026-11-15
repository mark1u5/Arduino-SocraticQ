"""SocraticQ — Comissió Tukey Analytics Dashboard.

Minimal, plain-English Streamlit dashboard built on top of `analytics_tukey.queries`
and `analytics_tukey.charts`.
"""

from __future__ import annotations

import os
import pandas as pd
import streamlit as st

from analytics_tukey.charts import (
    build_agent_traces_chart,
    build_confidence_calibration_chart,
    build_difficulty_drift_chart,
    build_ecdf_solve_time,
    build_guardrail_audit_chart,
    build_intervention_impact_chart,
    build_misconception_heatmap,
    build_socratic_funnel_chart,
    build_solve_time_boxplot,
    build_tukey_residual_plot,
)
from analytics_tukey.queries import (
    compute_calibration_metrics,
    compute_intervention_effect_size,
    compute_tukey_summary,
    get_agent_insights,
    get_agent_traces,
    get_difficulty_drift,
    get_pre_post_intervention,
    get_tukey_spread,
)

# ---------------------------------------------------------------------------
# Page Configuration & Calm Watery/Slate Styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="SocraticQ | Tukey Analytics",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp {
        background: linear-gradient(180deg, #0F172A 0%, #0B1324 100%);
        color: #E2E8F0;
    }
    section[data-testid="stSidebar"] {
        background-color: #111C33;
        border-right: 1px solid #1E293B;
    }
    .stMetric {
        background: rgba(30, 41, 59, 0.72);
        padding: 16px 20px;
        border-radius: 12px;
        border: 1px solid #334155;
    }
    .stMetric label {
        color: #94A3B8 !important;
        font-size: 0.85rem !important;
        font-weight: 500;
    }
    .stMetric [data-testid="stMetricValue"] {
        color: #F8FAFC !important;
        font-size: 1.75rem !important;
        font-weight: 700;
    }
    .stMetric [data-testid="stMetricDelta"] {
        color: #38BDF8 !important;
        font-size: 0.82rem !important;
    }
    button[data-baseweb="tab"] {
        color: #94A3B8;
        font-weight: 600;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        color: #38BDF8;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar: Data Source & Filters
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("SocraticQ")
    st.caption("Learning Analytics & Live Agent Loop")

    st.markdown("---")
    source_choice = st.radio(
        "Data Source",
        options=[
            "Mode A: Local Mock (Offline)",
            "Mode B: Live RawTree (SQL-out)",
            "Mode C: Board Mirror (RAWTREE_LOCAL_DB)",
        ],
        index=0,
    )

    if "Board Mirror" in source_choice:
        source_key = "mirror"
    elif "Live RawTree" in source_choice:
        source_key = "rawtree"
    else:
        source_key = "mock"
    is_rawtree = source_key == "rawtree"

    if is_rawtree:
        rawtree_url = st.text_input(
            "RawTree API URL",
            value=os.environ.get("RAWTREE_API_URL", "https://api.rawtree.com"),
        )
        rawtree_key = st.text_input(
            "API Key",
            value=os.environ.get("RAWTREE_API_KEY", ""),
            type="password",
        )
        if rawtree_url:
            os.environ["RAWTREE_API_URL"] = rawtree_url
        if rawtree_key:
            os.environ["RAWTREE_API_KEY"] = rawtree_key
        else:
            os.environ.pop("RAWTREE_API_KEY", None)
    else:
        st.caption("Offline mode active — using local Cangur telemetry.")

    st.markdown("---")
    category_select = st.selectbox(
        "Category",
        options=["All", "Combinatorics", "Geometry", "NumberTheory", "Algebra", "Logic"],
        index=0,
    )

    diff_select = st.multiselect(
        "Difficulty Tier",
        options=[1, 2, 3],
        default=[1, 2, 3],
        format_func=lambda x: f"Tier {x} ({x + 2} pts)",
    )

    if st.button("Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Data Loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=5)
def load_data(src: str):
    spread = get_tukey_spread(source=src)
    insights = get_agent_insights(source=src)
    pre_post = get_pre_post_intervention(source=src)
    drift = get_difficulty_drift(source=src)
    traces = get_agent_traces(source=src)
    return spread, insights, pre_post, drift, traces


raw_spread, insights_df, pre_post_df, drift_df, traces_df = load_data(source_key)

filtered_spread = raw_spread.copy()
filtered_drift = drift_df.copy()

if not filtered_spread.empty:
    if category_select != "All" and "category" in filtered_spread.columns:
        filtered_spread = filtered_spread[filtered_spread["category"] == category_select]
    if diff_select and "difficulty_level" in filtered_spread.columns:
        filtered_spread = filtered_spread[filtered_spread["difficulty_level"].isin(diff_select)]

if not filtered_drift.empty:
    if category_select != "All" and "category" in filtered_drift.columns:
        filtered_drift = filtered_drift[filtered_drift["category"] == category_select]
    if diff_select and "nominal_tier" in filtered_drift.columns:
        filtered_drift = filtered_drift[filtered_drift["nominal_tier"].isin(diff_select)]


# ---------------------------------------------------------------------------
# Top Row: 3 Crisp Summary Metrics
# ---------------------------------------------------------------------------

solve_series = (
    filtered_spread["solve_sec"]
    if (not filtered_spread.empty and "solve_sec" in filtered_spread.columns)
    else pd.Series(dtype=float)
)
tukey_stats = compute_tukey_summary(solve_series)

total_interactions = int(len(filtered_spread))

if not filtered_spread.empty and "is_correct" in filtered_spread.columns:
    answered = filtered_spread.dropna(subset=["is_correct"])
    if not answered.empty:
        if "problem_id" in answered.columns and answered["problem_id"].nunique() > 1:
            median_accuracy = float(answered.groupby("problem_id")["is_correct"].mean().median() * 100.0)
        else:
            median_accuracy = float(answered["is_correct"].mean() * 100.0)
    else:
        median_accuracy = 0.0
else:
    median_accuracy = 0.0

st.title("SocraticQ Learning Analytics")
st.caption("Where students stall, how hints help, and what the live RawTree agent improves.")

m1, m2, m3 = st.columns(3)
with m1:
    st.metric("Total Interactions", f"{total_interactions:,}")
with m2:
    st.metric(
        "Median Solve Time",
        f"{tukey_stats['median']:.1f}s",
        delta=f"IQR: {tukey_stats['iqr']:.1f}s",
    )
with m3:
    st.metric("Median Accuracy", f"{median_accuracy:.1f}%")


# ---------------------------------------------------------------------------
# 3-Tab Layout
# ---------------------------------------------------------------------------

tab_overview, tab_learning, tab_agent = st.tabs([
    "Overview (Where Students Struggle)",
    "Learning & Guardrails",
    "Live RawTree Agent",
])

with tab_overview:
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Solve Time by Category (Tukey Boxplots)")
        st.plotly_chart(
            build_solve_time_boxplot(filtered_spread, category_filter=category_select),
            use_container_width=True,
        )
    with col_right:
        st.subheader("Cumulative Solve Time Distribution (ECDF)")
        st.plotly_chart(
            build_ecdf_solve_time(filtered_spread),
            use_container_width=True,
        )

    st.markdown("---")
    st.subheader("Confidence Calibration & Dunning-Kruger Diagnostics")
    calib_metrics = compute_calibration_metrics(filtered_spread)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric(
            "Brier Score",
            f"{calib_metrics['brier_score']:.3f}",
            help="Mean squared error between confidence dial probability and outcome (lower is better, 0 = perfect).",
        )
    with c2:
        st.metric(
            "Expected Calibration Error (ECE)",
            f"{calib_metrics['ece']:.3f}",
            help="Weighted mean gap between confidence decile and empirical accuracy.",
        )
    with c3:
        st.metric(
            "Overconfidence Gap",
            f"{calib_metrics['overconfidence_gap']:+.2f}",
            help="Excess of confidence over empirical accuracy in the high-confidence band (>=70%).",
        )
    with c4:
        st.metric(
            "High-Conf Error Rate",
            f"{calib_metrics['high_conf_error_rate']:.1f}%",
            help="Percentage of attempts with confidence >= 70% that resulted in incorrect answers.",
        )

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.plotly_chart(
            build_confidence_calibration_chart(filtered_spread),
            use_container_width=True,
        )
    with col_c2:
        st.plotly_chart(
            build_tukey_residual_plot(filtered_spread),
            use_container_width=True,
        )

with tab_learning:
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Hint Survival Across Attempts")
        st.plotly_chart(
            build_socratic_funnel_chart(filtered_spread),
            use_container_width=True,
        )
    with col_right:
        st.subheader("AI-Safety & Scaffolding Audit")
        st.plotly_chart(
            build_guardrail_audit_chart(filtered_spread),
            use_container_width=True,
        )

    st.markdown("---")
    col_l1, col_l2 = st.columns(2)
    with col_l1:
        st.subheader("Misconception Cluster Heatmap")
        st.plotly_chart(
            build_misconception_heatmap(filtered_spread),
            use_container_width=True,
        )
    with col_l2:
        st.subheader("Problem Difficulty Drift (Nominal vs. Empirical)")
        st.plotly_chart(
            build_difficulty_drift_chart(filtered_drift),
            use_container_width=True,
        )

with tab_agent:
    st.subheader("Autonomous Closed-Loop Intervention Effect Size")
    effect_metrics = compute_intervention_effect_size(pre_post_df)
    e1, e2, e3, e4 = st.columns(4)
    with e1:
        st.metric(
            "Cohen's d",
            f"{effect_metrics['cohens_d']:.2f}",
            delta=f"{effect_metrics['effect_label']} effect",
            help="Standardized mean difference (solve time reduction).",
        )
    with e2:
        st.metric(
            "Mann-Whitney U",
            f"{effect_metrics['mann_whitney_u']:.1f}",
            delta=f"p = {effect_metrics['mann_whitney_p']:.4f}",
            help="Non-parametric rank-sum test between pre- and post-intervention solve times.",
        )
    with e3:
        st.metric(
            "Cliff's Delta",
            f"{effect_metrics['cliffs_delta']:+.2f}",
            delta=effect_metrics["effect_label"],
            help="Non-parametric dominance probability difference between pre and post.",
        )
    with e4:
        st.metric(
            "Solve Time Reduction",
            f"{effect_metrics['time_reduction_pct']:.1f}%",
            delta=f"{effect_metrics['pre_median']:.1f}s → {effect_metrics['post_median']:.1f}s",
            help="Median solve time reduction post-intervention.",
        )

    st.markdown("---")
    col_a1, col_a2 = st.columns(2)
    with col_a1:
        st.subheader("Before vs. After Agent Intervention")
        st.plotly_chart(
            build_intervention_impact_chart(pre_post_df),
            use_container_width=True,
        )
    with col_a2:
        st.subheader("RawTree Agent Latency & Bottleneck Yield")
        st.plotly_chart(
            build_agent_traces_chart(traces_df),
            use_container_width=True,
        )

    st.markdown("---")
    st.subheader("Active Agent Insights")
    if insights_df.empty:
        st.info("No agent insights recorded yet.")
    else:
        display_cols = [
            c for c in [
                "cycle_num",
                "insight_type",
                "target_category",
                "target_problem_id",
                "severity_score",
                "recommended_nudge",
                "status",
                "evidence_summary",
            ]
            if c in insights_df.columns
        ]
        renamed = insights_df[display_cols].rename(columns={
            "cycle_num": "Cycle",
            "insight_type": "Type",
            "target_category": "Category",
            "target_problem_id": "Problem",
            "severity_score": "Severity",
            "recommended_nudge": "Recommended Nudge",
            "status": "Status",
            "evidence_summary": "Evidence",
        })
        st.dataframe(renamed, use_container_width=True, hide_index=True)
