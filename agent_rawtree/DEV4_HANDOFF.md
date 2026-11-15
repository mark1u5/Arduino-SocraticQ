# DEV4_HANDOFF.md — Handoff Guide for Dev 4 (Comissió Tukey Analytics)

> **To Dev 4 and any AI agent working on `/analytics_tukey`:**  
> Dev 3 (`/agent_rawtree`) has prepared all data pipelines, SQL queries, and test datasets.  
> You do **not** need to write raw SQL or mock CSV files—everything is ready to import.

---

## 1. Quickstart: 2-Line Data Connector

Dev 3 provides a turnkey helper in `agent_rawtree.queries` that queries RawTree (or the local SQLite mirror fallback) and returns all 6 datasets needed for your John Tukey visualizations:

```python
import pandas as pd
from agent_rawtree.queries import fetch_tukey_datasets

# Fetches all 6 analytical datasets in one call
datasets = fetch_tukey_datasets()

df_solve_times    = pd.DataFrame(datasets["solve_times"])    # Category solve-time spread
df_calibration    = pd.DataFrame(datasets["calibration"])    # Hardware confidence vs. accuracy
df_bottlenecks    = pd.DataFrame(datasets["bottlenecks"])    # Classroom failure bottlenecks
df_misconceptions = pd.DataFrame(datasets["misconceptions"])# Misconception clusters
df_insights       = pd.DataFrame(datasets["insights"])      # Live agent interventions & SQL
df_traces         = pd.DataFrame(datasets["traces"])        # Agent meta-cognition & token ROI
```

Alternatively, if you want to execute custom SQL queries against RawTree:
```python
from agent_rawtree.rawtree_client import RawTreeClient

client = RawTreeClient()
# Returns list of dicts:
rows = client.query_sql("SELECT category, AVG(attempt_num) FROM telemetry_events GROUP BY category")
# Or returns a pandas DataFrame directly:
df = client.query_df("SELECT * FROM telemetry_events")
```

---

## 2. Dataset Reference & Columns

### Table 1: `solve_times` (For Tukey Boxplots, IQR & Outlier Analysis)
| Column | Type | Description |
| :--- | :--- | :--- |
| `session_id` | str | Student session identifier (`sess-unoq-01` .. `45`) |
| `problem_id` | str | Cangur problem ID (`CAM-2023-N4-17`, `CAM-2022-N3-12`, etc.) |
| `category` | str | `Combinatorics`, `Geometry`, `Algebra`, `NumberTheory`, `Logic` |
| `difficulty_level` | int | 1 (3-point), 2 (4-point), 3 (5-point) |
| `attempt_num` | int | Number of attempts taken by student (1..5) |
| `confidence_pct` | int | 0..100 from hardware dial |
| `solve_sec` | float | Elapsed solve time in seconds (log-normal spread) |
| `slm_latency_ms` | int | Local SLM inference latency in milliseconds |
| `is_correct` | bool / None | True (correct), False (wrong), None (hint request) |
| `misconception_tag`| str / None | e.g. `ignored_parity_invariant`, `angle_sum_confusion` |
| `ts_utc` | str | UTC timestamp |

### Table 2: `calibration` (For Dunning-Kruger Calibration Curves)
| Column | Type | Description |
| :--- | :--- | :--- |
| `category` | str | Math category |
| `difficulty_level` | int | 1..3 |
| `confidence_band` | str | `Low (<40%)`, `Medium (40-69%)`, `High (>=70%)` |
| `submissions` | int | Total answer submissions in this band |
| `empirical_accuracy`| float | Measured accuracy (0.0 .. 1.0) |
| `mean_confidence` | float | Normalized confidence from hardware dial (0.0 .. 1.0) |

> **Key Demo Story:** In `Geometry`, students average `0.82` confidence in the `High` band but achieve only `0.24` empirical accuracy (severe overconfidence!).

### Table 3: `bottlenecks` (For Classroom Leaderboard)
Shows problems with average attempts $\ge 2.0$.  
`CAM-2023-N4-17` (Combinatorics) is the primary stage demo bottleneck with dominant misconception `ignored_parity_invariant` and average attempts $\approx 2.9$.

### Table 4: `insights` (For Agent Intervention Feed)
Contains rows written by the GLM 5.3 agent:
- `insight_id`: `ins-0001`, `ins-0002`...
- `created_at_utc`: Timestamp
- `insight_type`: `BOTTLENECK`, `CALIBRATION_GAP`, `INTERVENTION`
- `target_category`: e.g. `Combinatorics`
- `target_problem_id`: e.g. `CAM-2023-N4-17`
- `severity_score`: float (0.0 .. 1.0)
- `sql_executed`: The exact SQL query the agent wrote and executed
- `evidence_summary`: Quantitative finding
- `recommended_nudge`: Socratic hint that triggers the Arduino Uno Q purple LED!
- `status`: `ACTIVE`, `APPLIED`, `RESOLVED`

### Table 5: `traces` (For Agent Meta-Cognition & Token ROI)
> **Directly addresses the official RawTree challenge prompt:**  
> *"An agent that tracks its own work: tool calls, traces and costs land in RawTree, and the agent queries its history to improve."*

- `trace_id`: Identifier
- `cycle_num`: Agent execution loop count
- `sql_executed`: The SQL query run by the agent
- `sql_latency_ms`: Execution speed (`110ms–170ms`)
- `findings_count`: Findings detected
- `action_taken`: e.g. `EMITTED_BOTTLENECK_INTERVENTION`
- `prompt_tokens`, `completion_tokens`, `estimated_cost_usd`

---

## 3. Plug-and-Play Plotly Chart Recipes for `analytics_tukey/charts.py`

Here are copy-pasteable chart functions tailored for John Tukey's exploratory principles (boxplots, IQR, whiskers, calibration, and residual distributions):

```python
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd

# 1. Tukey Solve-Time Boxplots (Category spread & outliers)
def plot_solve_time_boxplots(df: pd.DataFrame):
    fig = px.box(
        df,
        x="category",
        y="solve_sec",
        color="category",
        points="outliers",  # John Tukey's outlier fences (1.5 * IQR)
        title="<b>John Tukey Solve-Time Distributions by Cangur Category</b>",
        labels={"solve_sec": "Time to Solve (seconds)", "category": "Category"},
        template="plotly_dark",
    )
    fig.update_layout(showlegend=False, font=dict(family="sans-serif"))
    return fig

# 2. Dunning-Kruger Calibration Gap (Hardware Dial vs. Empirical Accuracy)
def plot_confidence_calibration(df: pd.DataFrame):
    fig = px.bar(
        df,
        x="confidence_band",
        y=["mean_confidence", "empirical_accuracy"],
        barmode="group",
        facet_col="category",
        title="<b>Hardware Confidence Dial Calibration vs. Empirical Accuracy</b>",
        labels={"value": "Score (0.0 - 1.0)", "confidence_band": "Confidence Band"},
        template="plotly_dark",
        color_discrete_map={"mean_confidence": "#38bdf8", "empirical_accuracy": "#f43f5e"},
    )
    return fig

# 3. Misconception Cluster Heatmap
def plot_misconception_heatmap(df_solve_times: pd.DataFrame):
    ct = pd.crosstab(
        df_solve_times["category"],
        df_solve_times["misconception_tag"].fillna("No Misconception")
    )
    fig = px.imshow(
        ct,
        labels=dict(x="Misconception Tag", y="Category", color="Student Count"),
        title="<b>Classroom Misconception Cluster Matrix</b>",
        template="plotly_dark",
        color_continuous_scale="Purples",
    )
    return fig

# 4. Agent Meta-Cognition & Token ROI (RawTree Challenge Feature)
def plot_agent_traces(df_traces: pd.DataFrame):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df_traces["cycle_num"],
        y=df_traces["sql_latency_ms"],
        mode="lines+markers",
        name="SQL Latency (ms)",
        line=dict(color="#38bdf8", width=2),
    ))
    fig.add_trace(go.Bar(
        x=df_traces["cycle_num"],
        y=df_traces["findings_count"],
        name="Bottlenecks Detected",
        marker_color="#a855f7",
        yaxis="y2",
    ))
    fig.update_layout(
        title="<b>Autonomous Agent Cognition: RawTree SQL Latency & Bottleneck Yield</b>",
        template="plotly_dark",
        yaxis=dict(title="SQL Query Latency (ms)"),
        yaxis2=dict(title="Findings Yield", overlaying="y", side="right"),
        legend=dict(x=0.01, y=0.99),
    )
    return fig
```

---

## 4. Re-Seeding or Resetting Data

If you ever want to re-seed or clear test data:
```powershell
python -m agent_rawtree.seed_events --clear --count 200
```

Good luck Dev 4! Let's win the Comissió Tukey prize!
