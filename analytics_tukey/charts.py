"""Tukey-Grade Exploratory Data Analysis (EDA) Charts for SocraticQ.

Implements John Tukey's principles of visual clarity:
  1. Never hide variance or multimodal bottlenecks behind simple means.
  2. Five-Number Summaries (Min, Q1, Median, Q3, Max) with explicit 1.5 * IQR whiskers.
  3. Individual outlier strip overlays to preserve data integrity.
  4. Diagnostic calibration curves exposing false certainty (Dunning-Kruger).
  5. Empirical evidence of agent intervention ROI (Pre vs. Post).
"""

from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Visual Design System (Tukey Coherence & Aesthetic)
# ---------------------------------------------------------------------------

THEME_BG = "#0B0F19"         # Deep Slate Dark
CARD_BG = "#151E2E"          # Elevated Panel
GRID_COLOR = "#223048"       # Subtle structural grid
TEXT_MAIN = "#E2E8F0"        # Crisp readable white/silver
TEXT_MUTED = "#94A3B8"       # Secondary labels

COLOR_INDIGO = "#6366F1"     # Baseline student attempts
COLOR_AMBER = "#F59E0B"      # Socratic hints / warning
COLOR_EMERALD = "#10B981"    # Solved / accurate
COLOR_CORAL = "#F43F5E"      # Outlier / high error
COLOR_PURPLE = "#A855F7"     # Autonomous RawTree Agent

BASE_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor=CARD_BG,
    font=dict(family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif", color=TEXT_MAIN, size=12),
    margin=dict(l=48, r=32, t=48, b=48),
    xaxis=dict(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR, tickcolor=GRID_COLOR),
    yaxis=dict(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR, tickcolor=GRID_COLOR),
    legend=dict(bgcolor="rgba(21, 30, 46, 0.8)", bordercolor=GRID_COLOR, borderwidth=1),
)


def _empty_figure(title: str) -> go.Figure:
    """Returns a styled placeholder figure when a query or filter yields 0 rows."""
    fig = go.Figure()
    fig.update_layout(**BASE_LAYOUT, title=dict(text=title, x=0.01))
    return fig


# ---------------------------------------------------------------------------
# 1. Category Solve-Time Boxplot with Jittered Outliers
# ---------------------------------------------------------------------------

def build_solve_time_boxplot(df: pd.DataFrame, category_filter: Optional[str] = None) -> go.Figure:
    """Tukey Boxplot with all observations jittered to expose bimodal stall points."""
    if df.empty or "category" not in df.columns or "solve_sec" not in df.columns:
        return _empty_figure("No solve-time observations available.")

    plot_df = df.copy()
    if category_filter and category_filter != "All":
        plot_df = plot_df[plot_df["category"] == category_filter]

    if plot_df.empty:
        return _empty_figure("No solve-time observations in current filter.")

    fig = go.Figure()

    categories = sorted(plot_df["category"].unique())
    palette = [COLOR_INDIGO, COLOR_AMBER, COLOR_EMERALD, COLOR_PURPLE, "#38BDF8"]

    for idx, cat in enumerate(categories):
        sub = plot_df[plot_df["category"] == cat]
        color = palette[idx % len(palette)]

        fig.add_trace(
            go.Box(
                y=sub["solve_sec"],
                x=sub["category"],
                name=cat,
                boxpoints="all",
                jitter=0.45,
                pointpos=-0.2,
                marker=dict(size=5, opacity=0.7, color=color),
                line=dict(color=color, width=2),
                fillcolor="rgba(30, 41, 59, 0.7)",
                customdata=np.stack((sub["problem_id"], sub["attempt_num"], sub["confidence_pct"]), axis=-1),
                hovertemplate=(
                    "<b>Problem:</b> %{customdata[0]}<br>"
                    "<b>Solve Time:</b> %{y:.1f}s<br>"
                    "<b>Attempt #:</b> %{customdata[1]}<br>"
                    "<b>Confidence:</b> %{customdata[2]}%<extra></extra>"
                ),
            )
        )

    # Highlight Tukey outlier threshold reference band (~120s)
    fig.add_hline(
        y=120,
        line_dash="dot",
        line_color=COLOR_CORAL,
        annotation_text="Tukey Outlier Stall Threshold (120s)",
        annotation_position="top left",
        annotation_font_color=COLOR_CORAL,
    )

    fig.update_layout(
        **BASE_LAYOUT,
        title=dict(text="<b>Solve-Time Distribution by Cangur Category</b> (Boxplot + Jittered Outliers)", x=0.01),
        xaxis_title="Cangur Mathematics Category",
        yaxis_title="Solve Time (seconds)",
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# 2. Confidence Calibration & Dunning-Kruger Diagnostic
# ---------------------------------------------------------------------------

def build_confidence_calibration_chart(df: pd.DataFrame) -> go.Figure:
    """Cross-references hardware analog confidence dial vs. actual accuracy."""
    if df.empty or "event_type" not in df.columns or "confidence_pct" not in df.columns:
        return _empty_figure("No confidence calibration data available.")

    ans_df = df[df["event_type"].isin(["ANSWER_SUBMIT", "SOLVED"]) & df["is_correct"].notna()].copy()
    if ans_df.empty:
        ans_df = df[df["is_correct"].notna()].copy()
    if ans_df.empty:
        return _empty_figure("No confidence calibration data available.")

    # Bin confidence into 10% deciles (include_lowest=True ensures 0% is not dropped as NaN)
    ans_df["conf_bin"] = (pd.cut(
        ans_df["confidence_pct"],
        bins=np.arange(0, 110, 10),
        labels=[f"{i}-{i+9}%" for i in range(0, 100, 10)],
        include_lowest=True,
    ))

    calib = ans_df.groupby("conf_bin", observed=False).agg(
        total=("is_correct", "count"),
        accuracy=("is_correct", lambda x: (x == 1).mean() if len(x) > 0 else 0),
        mean_conf=("confidence_pct", "mean"),
    ).reset_index()

    # Drop unobserved empty bins before plotting
    calib = calib[calib["total"] > 0].copy()
    if calib.empty:
        return _empty_figure("No confidence calibration data available.")

    calib["accuracy_pct"] = calib["accuracy"] * 100.0

    fig = go.Figure()

    # Ideal calibration diagonal y = x
    fig.add_trace(
        go.Scatter(
            x=[0, 100],
            y=[0, 100],
            mode="lines",
            line=dict(color=TEXT_MUTED, dash="dash", width=1.5),
            name="Ideal Calibration (y = x)",
            hoverinfo="skip",
        )
    )

    # Empirical calibration curve
    fig.add_trace(
        go.Scatter(
            x=calib["mean_conf"],
            y=calib["accuracy_pct"],
            mode="lines+markers",
            name="Empirical Accuracy",
            line=dict(color=COLOR_AMBER, width=3),
            marker=dict(size=9, color=COLOR_AMBER, symbol="diamond"),
            hovertemplate="<b>Confidence:</b> %{x:.1f}%<br><b>Accuracy:</b> %{y:.1f}%<extra></extra>",
        )
    )

    # Dunning-Kruger danger zone annotation (High confidence >= 70%, accuracy <= 40%)
    fig.add_shape(
        type="rect",
        x0=70,
        x1=100,
        y0=0,
        y1=45,
        fillcolor="rgba(244, 63, 94, 0.15)",
        line=dict(color=COLOR_CORAL, width=1, dash="dot"),
    )
    fig.add_annotation(
        x=85,
        y=22,
        text="<b>Dunning-Kruger Zone</b><br>(False High Certainty)",
        showarrow=False,
        font=dict(color=COLOR_CORAL, size=11),
    )

    layout_dict = dict(BASE_LAYOUT)
    layout_dict.update(
        title=dict(text="<b>Hardware Confidence Dial vs. Empirical Correctness</b> (Calibration Curve)", x=0.01),
        xaxis=dict(title="Reported Confidence Dial (%)", range=[0, 105], **BASE_LAYOUT["xaxis"]),
        yaxis=dict(title="Empirical Accuracy (%)", range=[0, 105], **BASE_LAYOUT["yaxis"]),
    )
    fig.update_layout(layout_dict)
    return fig


# ---------------------------------------------------------------------------
# 3. Misconception Cluster Heatmap
# ---------------------------------------------------------------------------

def build_misconception_heatmap(df: pd.DataFrame) -> go.Figure:
    """Tukey matrix exposing clusters of student errors across problem domains."""
    if df.empty or "misconception_tag" not in df.columns or "category" not in df.columns:
        return _empty_figure("No misconception clusters logged.")
    sub_df = df[df["misconception_tag"].notna() & (df["misconception_tag"] != "none")].copy()
    if sub_df.empty:
        return _empty_figure("No misconception clusters logged.")

    pivot = pd.crosstab(
        index=sub_df["category"],
        columns=sub_df["misconception_tag"],
    )

    # Clean display labels
    clean_cols = [c.replace("_", " ").title() for c in pivot.columns]

    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=clean_cols,
            y=pivot.index,
            colorscale=[
                [0.0, "#0F172A"],
                [0.2, "#1E293B"],
                [0.5, "#4F46E5"],
                [0.8, "#F59E0B"],
                [1.0, "#F43F5E"],
            ],
            text=pivot.values,
            texttemplate="%{text}",
            textfont=dict(color=TEXT_MAIN, size=13),
            hoverongaps=False,
            colorbar=dict(title="Instances", thickness=14, tickcolor=GRID_COLOR),
        )
    )

    fig.update_layout(
        **BASE_LAYOUT,
        title=dict(text="<b>Dominant Misconception Clusters by Category</b> (Error Heatmap)", x=0.01),
        xaxis_title="Misconception Diagnosis Tag",
        yaxis_title="Mathematics Domain",
    )
    return fig


# ---------------------------------------------------------------------------
# 4. Pre vs. Post Agent Intervention Analysis (ROI Evidence)
# ---------------------------------------------------------------------------

def build_intervention_impact_chart(pre_post_df: pd.DataFrame) -> go.Figure:
    """Shows empirical impact of RawTree autonomous agent nudge on attempts & solve time."""
    if pre_post_df.empty or "intervention_phase" not in pre_post_df.columns:
        fig = go.Figure()
        fig.update_layout(**BASE_LAYOUT, title="No intervention comparative data available.")
        return fig

    fig = go.Figure()

    phases = ["Pre-Intervention", "Post-Intervention"]
    colors = [COLOR_CORAL, COLOR_EMERALD]

    for phase, color in zip(phases, colors):
        sub = pre_post_df[pre_post_df["intervention_phase"] == phase]
        if sub.empty:
            continue

        fig.add_trace(
            go.Box(
                y=sub["solve_sec"],
                x=sub["intervention_phase"],
                name=phase,
                boxpoints="all",
                jitter=0.5,
                marker=dict(size=7, color=color, opacity=0.8),
                line=dict(color=color, width=2),
                fillcolor="rgba(30, 41, 59, 0.6)",
                customdata=np.stack((sub["attempt_num"], sub["confidence_pct"]), axis=-1),
                hovertemplate=(
                    "<b>Phase:</b> " + phase + "<br>"
                    "<b>Solve Time:</b> %{y:.1f}s<br>"
                    "<b>Attempts:</b> %{customdata[0]}<br>"
                    "<b>Confidence:</b> %{customdata[1]}%<extra></extra>"
                ),
            )
        )

    # Calculate medians for visual annotation
    pre_med = pre_post_df[pre_post_df["intervention_phase"] == "Pre-Intervention"]["solve_sec"].median()
    post_med = pre_post_df[pre_post_df["intervention_phase"] == "Post-Intervention"]["solve_sec"].median()

    if not np.isnan(pre_med) and not np.isnan(post_med):
        delta_pct = ((pre_med - post_med) / pre_med) * 100.0
        fig.add_annotation(
            x=0.5,
            y=max(pre_med, post_med) + 30,
            text=f"<b>Autonomous Agent ROI:</b> {delta_pct:.0f}% Median Time Reduction",
            showarrow=False,
            font=dict(color=COLOR_EMERALD, size=13),
            bgcolor=CARD_BG,
            bordercolor=COLOR_EMERALD,
            borderwidth=1,
            borderpad=6,
        )

    fig.update_layout(
        **BASE_LAYOUT,
        title=dict(
            text="<b>Target Problem CAM-2023-N4-17: Before vs. After Autonomous Agent Intervention</b>",
            x=0.01,
        ),
        xaxis_title="Intervention Phase",
        yaxis_title="Solve Time (seconds)",
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# 5. ECDF (Empirical Cumulative Distribution Function) for Cognitive Latency
# ---------------------------------------------------------------------------

def build_ecdf_solve_time(df: pd.DataFrame) -> go.Figure:
    """Tukey-grade ECDF comparing solve probability over time across categories."""
    if df.empty or "category" not in df.columns or "solve_sec" not in df.columns:
        return _empty_figure("No ECDF solve-time data available.")
    fig = go.Figure()
    categories = sorted(df["category"].unique())
    palette = [COLOR_INDIGO, COLOR_AMBER, COLOR_EMERALD, COLOR_PURPLE, "#38BDF8"]

    for idx, cat in enumerate(categories):
        sub = df[df["category"] == cat]["solve_sec"].dropna().sort_values()
        if sub.empty:
            continue
        n = len(sub)
        y = np.arange(1, n + 1) / n

        fig.add_trace(
            go.Scatter(
                x=sub,
                y=y,
                mode="lines",
                name=cat,
                line=dict(color=palette[idx % len(palette)], width=2.5),
                hovertemplate="<b>" + cat + ":</b> %{x:.1f}s (P = %{y:.2f})<extra></extra>",
            )
        )

    fig.update_layout(
        **BASE_LAYOUT,
        title=dict(text="<b>Empirical Cumulative Distribution Function (ECDF)</b> — Solve Time by Domain", x=0.01),
        xaxis_title="Solve Time (seconds)",
        yaxis_title="Cumulative Probability P(T ≤ t)",
    )
    return fig


# ---------------------------------------------------------------------------
# 6. Tukey Residual Analysis (Observed vs. Domain Median Residuals)
# ---------------------------------------------------------------------------

def build_tukey_residual_plot(df: pd.DataFrame) -> go.Figure:
    """Residual plot: e_i = SolveSec_i - Median_c(SolveSec). Exposes heteroscedastic stall tails."""
    if df.empty or "category" not in df.columns or "solve_sec" not in df.columns:
        return _empty_figure("No residual data available.")
    plot_df = df.copy()
    # Compute domain-specific median
    medians = plot_df.groupby("category")["solve_sec"].transform("median")
    plot_df["residual_sec"] = plot_df["solve_sec"] - medians

    fig = go.Figure()

    # Zero residual baseline
    fig.add_hline(
        y=0,
        line_color=TEXT_MUTED,
        line_dash="dash",
        annotation_text="Median Baseline (Residual = 0)",
        annotation_position="bottom right",
    )

    # Anomaly threshold: Residual > +60s
    fig.add_hline(
        y=60,
        line_color=COLOR_CORAL,
        line_dash="dot",
        annotation_text="Severe Cognitive Stall (+60s above median)",
        annotation_position="top left",
        annotation_font_color=COLOR_CORAL,
    )

    categories = sorted(plot_df["category"].unique())
    palette = [COLOR_INDIGO, COLOR_AMBER, COLOR_EMERALD, COLOR_PURPLE, "#38BDF8"]

    for idx, cat in enumerate(categories):
        sub = plot_df[plot_df["category"] == cat]
        fig.add_trace(
            go.Scatter(
                x=sub["confidence_pct"],
                y=sub["residual_sec"],
                mode="markers",
                name=cat,
                marker=dict(size=7, color=palette[idx % len(palette)], opacity=0.75),
                customdata=np.stack((sub["problem_id"], sub["attempt_num"]), axis=-1),
                hovertemplate=(
                    "<b>Category:</b> " + cat + "<br>"
                    "<b>Problem:</b> %{customdata[0]}<br>"
                    "<b>Confidence:</b> %{x}%<br>"
                    "<b>Residual:</b> %{y:+.1f}s<br>"
                    "<b>Attempt #:</b> %{customdata[1]}<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        **BASE_LAYOUT,
        title=dict(text="<b>Tukey Residual Plot</b>: Residual Solve Time vs. Confidence Dial", x=0.01),
        xaxis_title="Reported Confidence Dial (%)",
        yaxis_title="Solve Time Residual: y - Median(y) (seconds)",
    )
    return fig


# ---------------------------------------------------------------------------
# 7. Cangur Problem Difficulty Drift (Nominal vs. Empirical Burden)
# ---------------------------------------------------------------------------

def build_difficulty_drift_chart(drift_df: pd.DataFrame) -> go.Figure:
    """Evaluates whether official Cangur difficulty tiers match student cognitive struggle."""
    if drift_df.empty or "difficulty_drift" not in drift_df.columns or "student_count" not in drift_df.columns:
        return _empty_figure("No difficulty drift data available.")

    fig = go.Figure()

    # Ideal tier alignment diagonal
    fig.add_trace(
        go.Scatter(
            x=[0.8, 3.2],
            y=[0.8, 3.2],
            mode="lines",
            line=dict(color=TEXT_MUTED, dash="dash", width=1.5),
            name="Ideal Tier Parity",
            hoverinfo="skip",
        )
    )

    # Color mapping based on positive drift (harder than nominal) vs negative
    colors = [COLOR_CORAL if d > 0.15 else (COLOR_EMERALD if d < -0.15 else COLOR_AMBER) for d in drift_df["difficulty_drift"]]

    fig.add_trace(
        go.Scatter(
            x=drift_df["nominal_tier"],
            y=drift_df["empirical_score"],
            mode="markers+text",
            text=drift_df["problem_id"],
            textposition="top center",
            textfont=dict(color=TEXT_MAIN, size=10),
            marker=dict(
                size=np.clip(drift_df["student_count"] * 1.8, 12, 48),
                color=colors,
                opacity=0.85,
                line=dict(color=CARD_BG, width=1.5),
            ),
            customdata=np.stack((
                drift_df["category"],
                drift_df["avg_attempts"],
                drift_df["accuracy_rate"] * 100.0,
                drift_df["difficulty_drift"],
            ), axis=-1),
            hovertemplate=(
                "<b>Problem:</b> %{text}<br>"
                "<b>Domain:</b> %{customdata[0]}<br>"
                "<b>Nominal Tier:</b> %{x}<br>"
                "<b>Empirical Burden:</b> %{y:.2f}<br>"
                "<b>Avg Attempts:</b> %{customdata[1]:.1f}<br>"
                "<b>Accuracy:</b> %{customdata[2]:.1f}%<br>"
                "<b>Drift:</b> %{customdata[3]:+.2f}<extra></extra>"
            ),
        )
    )

    fig.add_annotation(
        x=2.0,
        y=2.5,
        text="<b>Difficulty Inflation Zone</b><br>(Higher cognitive load than official tier)",
        showarrow=True,
        arrowhead=2,
        arrowcolor=COLOR_CORAL,
        font=dict(color=COLOR_CORAL, size=11),
        bgcolor=CARD_BG,
    )

    layout_dict = dict(BASE_LAYOUT)
    layout_dict.update(
        title=dict(text="<b>Cangur Difficulty Drift Analysis</b>: Nominal Tier vs. Empirical Burden", x=0.01),
        xaxis=dict(title="Nominal Cangur Tier (1=3pt, 2=4pt, 3=5pt)", tickvals=[1, 2, 3], **BASE_LAYOUT["xaxis"]),
        yaxis=dict(title="Empirical Cognitive Burden Score", **BASE_LAYOUT["yaxis"]),
        showlegend=False,
    )
    fig.update_layout(layout_dict)
    return fig


# ---------------------------------------------------------------------------
# 8. Agent Meta-Cognition & Trace Telemetry (RawTree Self-Monitoring)
# ---------------------------------------------------------------------------

def build_agent_traces_chart(df_traces: pd.DataFrame) -> go.Figure:
    """Plots autonomous agent SQL query latency, findings yield, and token cost."""
    if df_traces.empty:
        fig = go.Figure()
        fig.update_layout(**BASE_LAYOUT, title="No agent traces recorded yet.")
        return fig

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df_traces["cycle_num"],
            y=df_traces["sql_latency_ms"],
            mode="lines+markers",
            name="RawTree SQL Latency (ms)",
            line=dict(color="#38BDF8", width=2.5),
            marker=dict(size=8, color="#38BDF8"),
        )
    )
    fig.add_trace(
        go.Bar(
            x=df_traces["cycle_num"],
            y=df_traces["findings_count"],
            name="Bottlenecks Yield",
            marker_color=COLOR_PURPLE,
            opacity=0.6,
            yaxis="y2",
        )
    )

    layout_dict = dict(BASE_LAYOUT)
    layout_dict.update(
        title=dict(text="<b>Autonomous Agent Cognition</b>: RawTree SQL Latency & Bottleneck Yield", x=0.01),
        xaxis=dict(title="Agent Autonomous Cycle Number", **BASE_LAYOUT["xaxis"]),
        yaxis=dict(title="SQL Query Latency (ms)", **BASE_LAYOUT["yaxis"]),
        yaxis2=dict(
            title="Bottleneck Yield",
            overlaying="y",
            side="right",
            gridcolor="rgba(0,0,0,0)",
            tickcolor=GRID_COLOR,
            color=COLOR_PURPLE,
        ),
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(21, 30, 46, 0.8)"),
    )
    fig.update_layout(layout_dict)
    return fig


# ---------------------------------------------------------------------------
# 9. Socratic Hint-Depth Survival / Funnel Drop-Off Curve
# ---------------------------------------------------------------------------

GUARDRAIL_TAGS = (
    "JAILBREAK_ATTEMPT",
    "OFFLOADING_ATTEMPT",
    "BINARY_SEARCH_PROBE",
    "UNSAFE_OFFTOPIC",
)


def build_socratic_funnel_chart(df: pd.DataFrame) -> go.Figure:
    """Survival curve tracking % of problem-sessions still unresolved vs. attempt_num (1..5+).

    Exposes where students recover rapidly after a Socratic nudge vs. plateauing
    in an invariant stall tail at deeper hint depths (Attempts 3-5+).
    """
    if df.empty or "category" not in df.columns or "attempt_num" not in df.columns:
        return _empty_figure("No Socratic funnel data available.")

    work = df.copy()
    work["attempt_num"] = (
        pd.to_numeric(work["attempt_num"], errors="coerce")
        .fillna(1)
        .astype(int)
        .clip(lower=1)
    )

    if "session_id" in work.columns and "problem_id" in work.columns:
        work["traj_id"] = work["session_id"].astype(str) + "::" + work["problem_id"].astype(str)
    elif "session_id" in work.columns:
        work["traj_id"] = work["session_id"].astype(str)
    else:
        work["traj_id"] = work.index.astype(str)

    if "is_correct" in work.columns:
        work["is_solved"] = work["is_correct"].isin([True, 1, "1", "true", "True"])
    else:
        work["is_solved"] = False

    solved_rows = work[work["is_solved"]].groupby(["category", "traj_id"])["attempt_num"].min()
    trajectories = (
        work.groupby(["category", "traj_id"], as_index=False)["attempt_num"]
        .max()
        .rename(columns={"attempt_num": "max_attempt"})
    )
    trajectories["solved_at"] = [
        solved_rows.get((cat, tid), np.inf)
        for cat, tid in zip(trajectories["category"], trajectories["traj_id"])
    ]

    depths = [1, 2, 3, 4, 5]
    depth_labels = ["1", "2", "3", "4", "5+"]
    palette = [COLOR_INDIGO, COLOR_AMBER, COLOR_EMERALD, COLOR_PURPLE, "#38BDF8"]

    fig = go.Figure()

    # Shade Attempt 3..5+ as the persistent cognitive stall region
    fig.add_vrect(
        x0=3,
        x1=5.15,
        fillcolor="rgba(244, 63, 94, 0.10)",
        line_width=0,
        annotation_text="Cognitive Stall Zone (3+ Hints)",
        annotation_position="top right",
        annotation_font_color=COLOR_CORAL,
        annotation_font_size=11,
    )
    fig.add_hline(
        y=50,
        line_dash="dot",
        line_color=TEXT_MUTED,
        annotation_text="50% Median Resolution Threshold",
        annotation_position="bottom left",
        annotation_font_color=TEXT_MUTED,
    )

    categories = sorted(trajectories["category"].dropna().unique())
    for idx, cat in enumerate(categories):
        sub = trajectories[trajectories["category"] == cat]
        total_n = len(sub)
        if total_n == 0:
            continue
        unresolved_pcts = []
        unresolved_counts = []
        resolved_counts = []
        for k in depths:
            # Unresolved entering attempt depth k (not yet solved at any attempt < k)
            n_unres = int((sub["solved_at"] >= k).sum())
            unresolved_counts.append(n_unres)
            resolved_counts.append(total_n - n_unres)
            unresolved_pcts.append((n_unres / total_n) * 100.0)

        color = palette[idx % len(palette)]
        fig.add_trace(
            go.Scatter(
                x=depths,
                y=unresolved_pcts,
                mode="lines+markers",
                name=f"{cat} (n={total_n})",
                line=dict(color=color, width=2.8),
                marker=dict(size=8, color=color),
                customdata=np.stack((unresolved_counts, resolved_counts, [total_n] * len(depths)), axis=-1),
                hovertemplate=(
                    f"<b>{cat}</b><br>"
                    "<b>Attempt Depth:</b> %{x}<br>"
                    "<b>Still Unresolved:</b> %{y:.1f}% (%{customdata[0]}/%{customdata[2]})<br>"
                    "<b>Recovered So Far:</b> %{customdata[1]}<extra></extra>"
                ),
            )
        )

    if len(categories) > 1 and not trajectories.empty:
        total_all = len(trajectories)
        overall_pcts = [float((trajectories["solved_at"] >= k).sum()) / total_all * 100.0 for k in depths]
        fig.add_trace(
            go.Scatter(
                x=depths,
                y=overall_pcts,
                mode="lines+markers",
                name=f"Overall Baseline (n={total_all})",
                line=dict(color=TEXT_MAIN, width=2, dash="dash"),
                marker=dict(size=6, color=TEXT_MAIN, symbol="diamond"),
                hovertemplate="<b>Overall:</b> %{y:.1f}% unresolved at Attempt %{x}<extra></extra>",
            )
        )

    layout_dict = dict(BASE_LAYOUT)
    layout_dict.update(
        title=dict(text="<b>Socratic Hint-Depth Survival Curve</b> (% Sessions Still Unresolved vs. Attempt Depth)", x=0.01),
        xaxis=dict(
            title="Attempt Depth (attempt_num)",
            tickvals=depths,
            ticktext=depth_labels,
            range=[0.85, 5.2],
            **BASE_LAYOUT["xaxis"],
        ),
        yaxis=dict(
            title="% of Sessions Still Unresolved",
            range=[0, 108],
            **BASE_LAYOUT["yaxis"],
        ),
    )
    fig.update_layout(layout_dict)
    return fig


# ---------------------------------------------------------------------------
# 10. AI-Safety & Confidence Calibration Audit (Guardrail Breakdown + Spread)
# ---------------------------------------------------------------------------

def build_guardrail_audit_chart(df: pd.DataFrame) -> go.Figure:
    """Compares normal Socratic scaffolding vs. 3-layer guardrail interceptions and confidence spread."""
    if df.empty:
        return _empty_figure("No AI-safety or calibration data available.")

    work = df.copy()
    if "misconception_tag" in work.columns:
        work["_tag"] = work["misconception_tag"].fillna("none").astype(str)
    else:
        work["_tag"] = "none"

    if "confidence_pct" in work.columns:
        work["_conf"] = pd.to_numeric(work["confidence_pct"], errors="coerce").fillna(50.0).clip(0, 100)
    else:
        work["_conf"] = 50.0

    if "is_correct" in work.columns:
        work["_has_truth"] = work["is_correct"].notna()
        work["_solved"] = work["is_correct"].isin([True, 1, "1", "true", "True"])
    else:
        work["_has_truth"] = False
        work["_solved"] = False

    is_guardrail = work["_tag"].isin(GUARDRAIL_TAGS)
    total_events = len(work)

    fig = make_subplots(
        rows=1,
        cols=2,
        column_widths=[0.48, 0.52],
        horizontal_spacing=0.11,
        subplot_titles=(
            "Normal Scaffolding vs. Guardrail Interceptions",
            "Confidence Spread vs. Empirical Accuracy by Cohort",
        ),
    )

    # Panel 1: Event volume breakdown across Normal Scaffolding & the 4 Guardrail vectors
    labels = [
        "Normal Socratic",
        "Offloading Attempt",
        "Jailbreak Attempt",
        "Binary Search Probe",
        "Unsafe Offtopic",
    ]
    counts = [
        int((~is_guardrail).sum()),
        int((work["_tag"] == "OFFLOADING_ATTEMPT").sum()),
        int((work["_tag"] == "JAILBREAK_ATTEMPT").sum()),
        int((work["_tag"] == "BINARY_SEARCH_PROBE").sum()),
        int((work["_tag"] == "UNSAFE_OFFTOPIC").sum()),
    ]
    bar_colors = [COLOR_EMERALD, COLOR_AMBER, COLOR_CORAL, COLOR_PURPLE, "#38BDF8"]
    pct_texts = [f"{c} ({(c / total_events * 100.0):.1f}%)" for c in counts]

    fig.add_trace(
        go.Bar(
            x=labels,
            y=counts,
            marker=dict(color=bar_colors, line=dict(color=CARD_BG, width=1)),
            text=pct_texts,
            textposition="outside",
            textfont=dict(color=TEXT_MAIN, size=11),
            name="Telemetry Events",
            showlegend=False,
            hovertemplate="<b>%{x}:</b> %{y} events<extra></extra>",
        ),
        row=1,
        col=1,
    )

    # Panel 2: Tukey Confidence Spread (Boxplot) + Empirical Accuracy overlay per cohort
    cohorts = [
        ("Solved (Verified)", (~is_guardrail) & work["_solved"], COLOR_EMERALD),
        ("Normal Scaffolding", (~is_guardrail) & (~work["_solved"]), COLOR_INDIGO),
        ("Guardrail Intercepted", is_guardrail, COLOR_CORAL),
    ]

    acc_x = []
    acc_y = []
    for cohort_name, mask, color in cohorts:
        sub = work[mask]
        if sub.empty:
            continue
        fig.add_trace(
            go.Box(
                y=sub["_conf"],
                x=[cohort_name] * len(sub),
                name=f"{cohort_name} Confidence",
                boxpoints="all",
                jitter=0.35,
                marker=dict(size=5, opacity=0.7, color=color),
                line=dict(color=color, width=2),
                fillcolor="rgba(30, 41, 59, 0.65)",
                showlegend=False,
                hovertemplate=(
                    f"<b>{cohort_name}</b><br>"
                    "<b>Confidence Dial:</b> %{y:.0f}%<extra></extra>"
                ),
            ),
            row=1,
            col=2,
        )
        truth_sub = sub[sub["_has_truth"]]
        if cohort_name == "Guardrail Intercepted":
            emp_acc = 0.0
        elif not truth_sub.empty:
            emp_acc = float(truth_sub["_solved"].mean()) * 100.0
        else:
            emp_acc = 0.0
        acc_x.append(cohort_name)
        acc_y.append(emp_acc)

    if acc_x:
        fig.add_trace(
            go.Scatter(
                x=acc_x,
                y=acc_y,
                mode="markers+text",
                name="Empirical Accuracy (%)",
                text=[f"{v:.0f}% acc" for v in acc_y],
                textposition="top center",
                textfont=dict(color=COLOR_AMBER, size=11),
                marker=dict(size=12, color=COLOR_AMBER, symbol="diamond", line=dict(color=CARD_BG, width=1.5)),
                hovertemplate="<b>%{x}</b><br><b>Empirical Accuracy:</b> %{y:.1f}%<extra></extra>",
            ),
            row=1,
            col=2,
        )

    guardrail_total = int(is_guardrail.sum())
    if guardrail_total == 0:
        fig.add_annotation(
            x=2.5,
            y=max(counts) * 0.45 if max(counts) > 0 else 1,
            xref="x1",
            yref="y1",
            text="<b>0 Safety Breaches / Triggers</b><br>(Fail-closed Judo defense armed)",
            showarrow=False,
            font=dict(color=TEXT_MUTED, size=11),
            bgcolor="rgba(21, 30, 46, 0.85)",
            bordercolor=GRID_COLOR,
            borderwidth=1,
            borderpad=6,
        )

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=CARD_BG,
        font=BASE_LAYOUT["font"],
        margin=dict(l=48, r=32, t=76, b=56),
        title=dict(text="<b>AI-Safety & Calibration Audit</b>: Socratic Judo Guardrails vs. Confidence Spread", x=0.01),
        legend=dict(x=0.68, y=0.98, bgcolor="rgba(21, 30, 46, 0.85)", bordercolor=GRID_COLOR, borderwidth=1),
    )
    fig.update_xaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR, tickcolor=GRID_COLOR)
    fig.update_yaxes(gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR, tickcolor=GRID_COLOR)
    fig.update_yaxes(title_text="Telemetry Event Count", row=1, col=1)
    fig.update_yaxes(title_text="Confidence Dial / Accuracy (%)", range=[0, 112], row=1, col=2)
    return fig
