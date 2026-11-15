"""Pre-tested analytical SQL queries and data extractors for Dev 4 (Tukey Dashboard).

Provides standard John Tukey Exploratory Data Analysis queries matching Section 3.3
of TEAM_EXECUTION_PLAN.md, plus agent meta-cognition metrics.

Dev 4 can import this module directly:
    from agent_rawtree.queries import fetch_tukey_datasets
    data = fetch_tukey_datasets()
"""

from __future__ import annotations

from typing import Any, Optional

from agent_rawtree.rawtree_client import RawTreeClient


# 1. Tukey Spread Query: Raw solve times for boxplots, IQR, and outlier detection
SQL_SOLVE_TIME_DISTRIBUTION = """
SELECT
    session_id,
    problem_id,
    category,
    difficulty_level,
    attempt_num,
    confidence_pct,
    solve_elapsed_ms / 1000.0 AS solve_sec,
    slm_latency_ms,
    is_correct,
    misconception_tag,
    ts_utc
FROM telemetry_events
ORDER BY ts_utc DESC;
"""

# 2. Dunning-Kruger / Confidence Calibration: Hardware dial vs. empirical accuracy
SQL_CONFIDENCE_CALIBRATION = """
SELECT
    category,
    difficulty_level,
    CASE
        WHEN confidence_pct >= 70 THEN 'High (>=70%)'
        WHEN confidence_pct >= 40 THEN 'Medium (40-69%)'
        ELSE 'Low (<40%)'
    END AS confidence_band,
    COUNT(*) AS submissions,
    AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS empirical_accuracy,
    AVG(confidence_pct) / 100.0 AS mean_confidence
FROM telemetry_events
WHERE is_correct IS NOT NULL
GROUP BY category, difficulty_level, confidence_band
ORDER BY category, difficulty_level;
"""

# 3. Classroom Bottlenecks: Detect high attempts + low solve rate + misconception clustering
SQL_CLASSROOM_BOTTLENECKS = """
SELECT
    category,
    problem_id,
    COUNT(*) AS total_events,
    COUNT(DISTINCT session_id) AS active_students,
    AVG(attempt_num) AS avg_attempts,
    AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS accuracy_rate,
    AVG(solve_elapsed_ms) / 1000.0 AS mean_solve_sec,
    MAX(misconception_tag) AS dominant_misconception
FROM telemetry_events
GROUP BY category, problem_id
HAVING COUNT(*) >= 3 AND AVG(attempt_num) >= 2.0
ORDER BY avg_attempts DESC;
"""

# 4. Misconception Clustering & Drift over time
SQL_MISCONCEPTION_CLUSTERS = """
SELECT
    category,
    misconception_tag,
    COUNT(*) AS occurrences,
    AVG(confidence_pct) AS avg_confidence,
    AVG(solve_elapsed_ms) / 1000.0 AS mean_solve_sec
FROM telemetry_events
WHERE misconception_tag IS NOT NULL
GROUP BY category, misconception_tag
ORDER BY occurrences DESC;
"""

# 5. Agent Insights Timeline: Cognitive scratchpad & active interventions
SQL_AGENT_INSIGHTS = """
SELECT
    insight_id,
    created_at_utc,
    cycle_num,
    insight_type,
    target_category,
    target_problem_id,
    severity_score,
    sql_executed,
    evidence_summary,
    recommended_nudge,
    status
FROM agent_insights
ORDER BY created_at_utc DESC;
"""

# 6. Agent Meta-Cognition & Trace Telemetry (RawTree Self-Monitoring Challenge)
SQL_AGENT_TRACES = """
SELECT
    trace_id,
    ts_utc,
    cycle_num,
    agent_role,
    model_id,
    sql_executed,
    sql_latency_ms,
    findings_count,
    action_taken,
    prompt_tokens,
    completion_tokens,
    estimated_cost_usd
FROM agent_traces
ORDER BY ts_utc DESC;
"""


def fetch_tukey_datasets(client: Optional[RawTreeClient] = None) -> dict[str, list[dict[str, Any]]]:
    """Execute all core analytical queries and return a dictionary of result rows."""
    c = client or RawTreeClient()
    return {
        "solve_times": c.query_sql(SQL_SOLVE_TIME_DISTRIBUTION),
        "calibration": c.query_sql(SQL_CONFIDENCE_CALIBRATION),
        "bottlenecks": c.query_sql(SQL_CLASSROOM_BOTTLENECKS),
        "misconceptions": c.query_sql(SQL_MISCONCEPTION_CLUSTERS),
        "insights": c.query_sql(SQL_AGENT_INSIGHTS),
        "traces": c.query_sql(SQL_AGENT_TRACES),
    }
