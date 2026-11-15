"""Dual-Mode Query & Data Engine for Comissio Tukey Dashboard.

Mode A (Offline / Local Mock):
    Generates 200+ realistic Cangur math telemetry events and agent insights
    strictly validating against contracts/schemas.py (TelemetryEvent and AgentInsight).
    Runs via in-memory SQLite with zero external dependencies and zero network lag.

Mode B (Live RawTree):
    Sends analytical SQL queries over HTTP to venue RawTree workspace (SQL-out).
    Falls back gracefully to Mode A if offline or if credentials are not configured.
"""

from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

import pandas as pd

from contracts.schemas import (
    AgentInsight,
    InsightStatus,
    InsightType,
    MathCategory,
    PedagogicalMove,
    TelemetryEvent,
    validate_agent_insight,
    validate_telemetry_event,
)

# ---------------------------------------------------------------------------
# Core DDL (mirrors agent_rawtree/schema.sql and contracts/schemas.py)
# ---------------------------------------------------------------------------

DDL_TELEMETRY_EVENTS = """
CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id          VARCHAR(64) PRIMARY KEY,
    session_id        VARCHAR(64) NOT NULL,
    device_id         VARCHAR(32) NOT NULL,
    ts_utc            TEXT NOT NULL,
    problem_id        VARCHAR(32) NOT NULL,
    cangur_year       INTEGER NOT NULL,
    category          VARCHAR(32) NOT NULL,
    difficulty_level  INTEGER NOT NULL,
    event_type        VARCHAR(24) NOT NULL,
    attempt_num       INTEGER NOT NULL,
    confidence_pct    INTEGER NOT NULL,
    is_correct        INTEGER,
    misconception_tag VARCHAR(64),
    pedagogical_move  VARCHAR(48),
    solve_elapsed_ms  INTEGER NOT NULL,
    slm_latency_ms    INTEGER NOT NULL,
    offline_cached    INTEGER NOT NULL DEFAULT 0
);
"""

DDL_AGENT_INSIGHTS = """
CREATE TABLE IF NOT EXISTS agent_insights (
    insight_id        VARCHAR(64) PRIMARY KEY,
    created_at_utc    TEXT NOT NULL,
    cycle_num         INTEGER NOT NULL,
    insight_type      VARCHAR(32) NOT NULL,
    target_category   VARCHAR(32) NOT NULL,
    target_problem_id VARCHAR(32),
    severity_score    REAL NOT NULL,
    sql_executed      TEXT NOT NULL,
    evidence_summary  TEXT NOT NULL,
    recommended_nudge TEXT NOT NULL,
    status            VARCHAR(16) NOT NULL
);
"""

DDL_AGENT_TRACES = """
CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id           VARCHAR(64) PRIMARY KEY,
    ts_utc             TEXT NOT NULL,
    cycle_num          INTEGER NOT NULL,
    agent_role         VARCHAR(32) NOT NULL,
    model_id           VARCHAR(48) NOT NULL,
    sql_executed       TEXT NOT NULL,
    sql_latency_ms     INTEGER NOT NULL,
    findings_count     INTEGER NOT NULL,
    action_taken       VARCHAR(64) NOT NULL,
    prompt_tokens      INTEGER NOT NULL,
    completion_tokens  INTEGER NOT NULL,
    estimated_cost_usd REAL NOT NULL
);
"""

# Global in-memory SQLite connection for Mode A
_LOCAL_DB: Optional[sqlite3.Connection] = None


# ---------------------------------------------------------------------------
# Mode A: High-Fidelity Synthetic Cangur Telemetry Generator
# ---------------------------------------------------------------------------

CANGUR_PROBLEMS: list[dict[str, Any]] = [
    # Demo centerpiece: Combinatorics parity bottleneck (D=11, C=12 parity trap)
    {
        "problem_id": "CAM-2023-N4-17",
        "cangur_year": 2023,
        "category": "Combinatorics",
        "difficulty_level": 2,
        "is_bottleneck": True,
        "dominant_misconception": "ignored_parity_invariant",
        "recommended_move": "PARITY_CHECK",
    },
    {
        "problem_id": "CAM-2022-N4-19",
        "cangur_year": 2022,
        "category": "Geometry",
        "difficulty_level": 2,
        "is_bottleneck": False,
        "dominant_misconception": "forgot_to_halve_base_sum",
        "recommended_move": "COUNTEREXAMPLE_CHECK",
    },
    {
        "problem_id": "CAM-2020-N5-22",
        "cangur_year": 2020,
        "category": "Geometry",
        "difficulty_level": 3,
        "is_bottleneck": True,
        "dominant_misconception": "interior_exterior_confusion",
        "recommended_move": "INVARIANT_NUDGE",
    },
    {
        "problem_id": "CAM-2023-N3-09",
        "cangur_year": 2023,
        "category": "NumberTheory",
        "difficulty_level": 1,
        "is_bottleneck": False,
        "dominant_misconception": "returned_mean_not_largest",
        "recommended_move": "DECOMPOSE_PROBLEM",
    },
    {
        "problem_id": "CAM-2021-N4-15",
        "cangur_year": 2021,
        "category": "NumberTheory",
        "difficulty_level": 2,
        "is_bottleneck": True,
        "dominant_misconception": "assumed_period_five",
        "recommended_move": "BOUNDARY_TEST",
    },
    {
        "problem_id": "CAM-2023-N3-06",
        "cangur_year": 2023,
        "category": "Algebra",
        "difficulty_level": 1,
        "is_bottleneck": False,
        "dominant_misconception": "subtracted_without_halving",
        "recommended_move": "COUNTEREXAMPLE_CHECK",
    },
    {
        "problem_id": "CAM-2022-N4-14",
        "cangur_year": 2022,
        "category": "Algebra",
        "difficulty_level": 2,
        "is_bottleneck": False,
        "dominant_misconception": "dropped_cross_term_2xy",
        "recommended_move": "DECOMPOSE_PROBLEM",
    },
    {
        "problem_id": "CAM-2024-N4-11",
        "cangur_year": 2024,
        "category": "Logic",
        "difficulty_level": 2,
        "is_bottleneck": False,
        "dominant_misconception": "skipped_contradiction_check",
        "recommended_move": "COUNTEREXAMPLE_CHECK",
    },
]


def generate_mock_dataset(
    num_sessions: int = 42,
    events_target: int = 220,
    seed: int = 2026,
) -> tuple[list[TelemetryEvent], list[AgentInsight]]:
    """Generates synthetic student telemetry and agent insights.
    
    Includes Tukey-friendly distributions:
      - Bimodal solve times for Combinatorics (quick solvers vs heavy-tail outliers)
      - Dunning-Kruger false-confidence calibration cluster
      - Explicit Pre/Post intervention shift for CAM-2023-N4-17
    """
    random.seed(seed)
    telemetry_events: list[TelemetryEvent] = []
    agent_insights: list[AgentInsight] = []

    now = datetime.now(timezone.utc)
    base_time = now - timedelta(hours=2)
    intervention_time = now - timedelta(minutes=45)

    event_counter = 1
    session_ids = [f"sess-unoq-{i:02d}" for i in range(1, num_sessions + 1)]
    device_ids = ["unoq-01", "unoq-02", "unoq-bench-01", "unoq-bench-02"]

    for session_id in session_ids:
        device_id = random.choice(device_ids)
        # Each student attempts 2-4 problems
        chosen_problems = random.sample(CANGUR_PROBLEMS, k=random.randint(2, 4))

        for prob in chosen_problems:
            is_bottleneck = prob["is_bottleneck"]
            is_showcase = prob["problem_id"] == "CAM-2023-N4-17"

            # Decide timestamp (some before intervention, some after for showcase)
            if is_showcase:
                is_post_intervention = random.random() > 0.45
                if is_post_intervention:
                    prob_time = intervention_time + timedelta(
                        minutes=random.randint(5, 35), seconds=random.randint(0, 59)
                    )
                else:
                    prob_time = base_time + timedelta(
                        minutes=random.randint(5, 60), seconds=random.randint(0, 59)
                    )
            else:
                prob_time = base_time + timedelta(
                    minutes=random.randint(5, 110), seconds=random.randint(0, 59)
                )
                is_post_intervention = False

            # Simulation parameters based on category & difficulty
            if is_showcase and not is_post_intervention:
                # Pre-intervention showcase: students struggle badly, high attempts, false confidence
                max_attempts = random.randint(3, 5)
                event_types = ["HINT_REQUEST"] * (max_attempts - 1) + ["ANSWER_SUBMIT"]
                final_correct = random.random() < 0.35
            elif is_showcase and is_post_intervention:
                # Post-intervention showcase: agent intervention applied, rapid resolution!
                max_attempts = random.randint(1, 2)
                event_types = ["HINT_REQUEST"] * (max_attempts - 1) + ["ANSWER_SUBMIT"]
                final_correct = True
            elif is_bottleneck:
                max_attempts = random.randint(2, 4)
                event_types = ["HINT_REQUEST"] * (max_attempts - 1) + ["ANSWER_SUBMIT"]
                final_correct = random.random() < 0.50
            else:
                max_attempts = random.randint(1, 2)
                event_types = ["HINT_REQUEST"] * (max_attempts - 1) + ["ANSWER_SUBMIT"]
                final_correct = random.random() < 0.82

            cumulative_solve_ms = 0
            for attempt_idx, event_type in enumerate(event_types, start=1):
                # Solve time calculation with Tukey outliers
                if is_bottleneck and attempt_idx >= 3:
                    # Outlier tail (Tukey boxplot whisker breakers: 180s - 260s)
                    step_solve_ms = random.randint(35_000, 75_000)
                else:
                    # Normal distribution solve time
                    step_solve_ms = random.randint(12_000, 32_000)
                cumulative_solve_ms += step_solve_ms

                # Confidence dial (analog potentiometer 0..100)
                if attempt_idx == 1:
                    # Frequently overconfident or uncalibrated on first attempt
                    confidence_pct = random.choice([25, 40, 75, 85, 90])
                elif not final_correct and attempt_idx > 1:
                    # Sinking confidence on repeated failure
                    confidence_pct = random.randint(15, 45)
                else:
                    # Rebounding confidence
                    confidence_pct = random.randint(65, 95)

                is_final_step = attempt_idx == len(event_types)
                if event_type == "HINT_REQUEST":
                    is_correct_val: Optional[bool] = None
                    misconception = prob["dominant_misconception"]
                    ped_move = prob["recommended_move"]
                else:  # ANSWER_SUBMIT
                    is_correct_val = final_correct if is_final_step else False
                    misconception = (
                        None if is_correct_val else prob["dominant_misconception"]
                    )
                    ped_move = "AFFIRM_CORRECT" if is_correct_val else prob["recommended_move"]

                # Quantized SLM on QRB2210 latency (usually 550ms - 980ms)
                slm_latency = random.randint(520, 940)

                event_dict: TelemetryEvent = {
                    "event_id": f"evt-{event_counter:04d}",
                    "session_id": session_id,
                    "device_id": device_id,
                    "ts_utc": prob_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "problem_id": prob["problem_id"],
                    "cangur_year": prob["cangur_year"],
                    "category": prob["category"],
                    "difficulty_level": prob["difficulty_level"],
                    "event_type": event_type,
                    "attempt_num": attempt_idx,
                    "confidence_pct": confidence_pct,
                    "is_correct": is_correct_val,
                    "misconception_tag": misconception,
                    "pedagogical_move": ped_move,
                    "solve_elapsed_ms": cumulative_solve_ms,
                    "slm_latency_ms": slm_latency,
                    "offline_cached": random.random() < 0.25,
                }

                # Contract schema validation
                validate_telemetry_event(event_dict)
                telemetry_events.append(event_dict)
                event_counter += 1

                # Advance timestamp by step duration
                prob_time += timedelta(milliseconds=step_solve_ms + slm_latency)

    # -----------------------------------------------------------------------
    # Generate Agent Insights (RawTree Autonomous Cognitive Scratchpad)
    # -----------------------------------------------------------------------
    agent_insights.append({
        "insight_id": "ins-0001",
        "created_at_utc": (now - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycle_num": 14,
        "insight_type": "BOTTLENECK",
        "target_category": "Combinatorics",
        "target_problem_id": "CAM-2023-N4-17",
        "severity_score": 0.88,
        "sql_executed": (
            "SELECT problem_id, AVG(attempt_num) AS avg_att, "
            "AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS acc "
            "FROM telemetry_events WHERE category='Combinatorics' "
            "GROUP BY problem_id HAVING AVG(attempt_num) >= 2.5;"
        ),
        "evidence_summary": (
            "78% of active sessions stalled on CAM-2023-N4-17. "
            "Average attempts: 3.42, empirical accuracy: 24.1%. "
            "Dominant misconception: ignored_parity_invariant."
        ),
        "recommended_nudge": (
            "Prompt student to color the coordinate grid like a chessboard. "
            "Can two consecutive moves land on cells with identical parity?"
        ),
        "status": "APPLIED",
    })

    agent_insights.append({
        "insight_id": "ins-0002",
        "created_at_utc": (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycle_num": 18,
        "insight_type": "CALIBRATION_GAP",
        "target_category": "Combinatorics",
        "target_problem_id": "CAM-2023-N4-17",
        "severity_score": 0.74,
        "sql_executed": (
            "SELECT confidence_pct, is_correct, COUNT(*) "
            "FROM telemetry_events WHERE confidence_pct >= 75 AND is_correct = 0 "
            "GROUP BY confidence_pct, is_correct;"
        ),
        "evidence_summary": (
            "Severe Dunning-Kruger gap detected: 62% of failed attempts reported "
            "confidence >= 75% on initial submission before hint request."
        ),
        "recommended_nudge": (
            "Trigger counterexample verification before accepting submission."
        ),
        "status": "APPLIED",
    })

    agent_insights.append({
        "insight_id": "ins-0003",
        "created_at_utc": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cycle_num": 22,
        "insight_type": "INTERVENTION",
        "target_category": "Geometry",
        "target_problem_id": "CAM-2020-N5-22",
        "severity_score": 0.65,
        "sql_executed": (
            "SELECT misconception_tag, COUNT(*) FROM telemetry_events "
            "WHERE problem_id = 'CAM-2020-N5-22' "
            "GROUP BY misconception_tag;"
        ),
        "evidence_summary": (
            "Misconception interior_exterior_confusion cluster identified in 5 distinct sessions."
        ),
        "recommended_nudge": (
            "If each interior angle is 150 degrees, what is each exterior turning angle, and what do all exterior turns add up to?"
        ),
        "status": "ACTIVE",
    })

    for insight in agent_insights:
        validate_agent_insight(insight)

    return telemetry_events, agent_insights


# ---------------------------------------------------------------------------
# Database Initialization & Seeding
# ---------------------------------------------------------------------------

def init_local_db() -> sqlite3.Connection:
    """Initializes in-memory SQLite database populated with validated mock data."""
    global _LOCAL_DB
    if _LOCAL_DB is not None:
        return _LOCAL_DB

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(DDL_TELEMETRY_EVENTS)
    cursor.execute(DDL_AGENT_INSIGHTS)
    cursor.execute(DDL_AGENT_TRACES)

    telemetry_events, agent_insights = generate_mock_dataset()

    for evt in telemetry_events:
        cursor.execute(
            """
            INSERT INTO telemetry_events (
                event_id, session_id, device_id, ts_utc, problem_id,
                cangur_year, category, difficulty_level, event_type,
                attempt_num, confidence_pct, is_correct, misconception_tag,
                pedagogical_move, solve_elapsed_ms, slm_latency_ms, offline_cached
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evt["event_id"],
                evt["session_id"],
                evt["device_id"],
                evt["ts_utc"],
                evt["problem_id"],
                evt["cangur_year"],
                evt["category"],
                evt["difficulty_level"],
                evt["event_type"],
                evt["attempt_num"],
                evt["confidence_pct"],
                1 if evt["is_correct"] is True else (0 if evt["is_correct"] is False else None),
                evt["misconception_tag"],
                evt["pedagogical_move"],
                evt["solve_elapsed_ms"],
                evt["slm_latency_ms"],
                1 if evt["offline_cached"] else 0,
            ),
        )

    for ins in agent_insights:
        cursor.execute(
            """
            INSERT INTO agent_insights (
                insight_id, created_at_utc, cycle_num, insight_type,
                target_category, target_problem_id, severity_score,
                sql_executed, evidence_summary, recommended_nudge, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ins["insight_id"],
                ins["created_at_utc"],
                ins["cycle_num"],
                ins["insight_type"],
                ins["target_category"],
                ins["target_problem_id"],
                ins["severity_score"],
                ins["sql_executed"],
                ins["evidence_summary"],
                ins["recommended_nudge"],
                ins["status"],
            ),
        )

    now = datetime.now(timezone.utc)
    seed_traces = [
        (
            "trc-0014",
            (now - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            14,
            "BOTTLENECK_SCANNER",
            "glm-5.3",
            "SELECT problem_id, AVG(attempt_num) AS avg_att FROM telemetry_events WHERE category='Combinatorics' GROUP BY problem_id HAVING AVG(attempt_num) >= 2.5;",
            42,
            3,
            "EMIT_INTERVENTION",
            612,
            148,
            0.0019,
        ),
        (
            "trc-0018",
            (now - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            18,
            "CALIBRATION_AUDITOR",
            "glm-5.3",
            "SELECT confidence_pct, is_correct, COUNT(*) FROM telemetry_events WHERE confidence_pct >= 75 AND is_correct = 0 GROUP BY confidence_pct, is_correct;",
            37,
            2,
            "LOG_CALIBRATION_GAP",
            584,
            132,
            0.0017,
        ),
        (
            "trc-0022",
            (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            22,
            "MISCONCEPTION_CLUSTERER",
            "glm-5.3",
            "SELECT misconception_tag, COUNT(*) FROM telemetry_events WHERE problem_id = 'CAM-2020-N5-22' GROUP BY misconception_tag;",
            51,
            1,
            "QUEUE_EDGE_NUDGE",
            640,
            165,
            0.0021,
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO agent_traces (
            trace_id, ts_utc, cycle_num, agent_role, model_id,
            sql_executed, sql_latency_ms, findings_count, action_taken,
            prompt_tokens, completion_tokens, estimated_cost_usd
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        seed_traces,
    )

    conn.commit()
    _LOCAL_DB = conn
    return _LOCAL_DB


# ---------------------------------------------------------------------------
# Dual-Mode Query Runners (Mode A: Local SQLite, Mode B: Live RawTree)
# ---------------------------------------------------------------------------

def run_query(sql: str, source: Optional[str] = None) -> pd.DataFrame:
    """Executes SQL against either the local SQLite mock or live RawTree API.
    
    Args:
        sql: Standard SQL query string.
        source: 'mock' (default) or 'rawtree'. Can also be set via DATA_SOURCE env var.
    """
    active_source = (source or os.environ.get("DATA_SOURCE", "mock")).lower()

    if active_source == "mirror":
        return _run_mirror_query(sql)

    if active_source == "rawtree":
        try:
            return _run_rawtree_query(sql)
        except Exception as exc:
            # Stage fallback protocol: never let UI crash if Wi-Fi / RawTree endpoint is unreachable
            print(f"[analytics_tukey.queries] RawTree query failed ({exc}), falling back to local mock.")
            return _run_local_query(sql)

    return _run_local_query(sql)


def _run_local_query(sql: str) -> pd.DataFrame:
    conn = init_local_db()
    return pd.read_sql_query(sql, conn)


def _run_mirror_query(sql: str) -> pd.DataFrame:
    """Mode C hook: read the board's local RawTree SQLite mirror (offline demo).

    Uses RAWTREE_LOCAL_DB (falls back to agent_rawtree/local_rawtree.db) so the
    dashboard shows exactly the telemetry the chat server / agent produced.
    """
    try:
        from agent_rawtree.rawtree_client import RawTreeClient

        client = RawTreeClient(force_offline=True)
        rows = client.query_sql(sql)
        return pd.DataFrame(rows)
    except Exception as exc:
        print(f"[analytics_tukey.queries] Mirror query failed ({exc}); falling back to local mock.")
        return _run_local_query(sql)


def _run_rawtree_query(sql: str) -> pd.DataFrame:
    """Mode B hook: Uses Dev 3's RawTreeClient (api.rawtree.com + local_rawtree.db)."""
    try:
        from agent_rawtree.rawtree_client import RawTreeClient
        client = RawTreeClient()
        return client.query_df(sql)
    except Exception as exc:
        print(f"[analytics_tukey.queries] RawTreeClient query error ({exc}), trying direct REST fallback.")

    import requests

    rawtree_url = os.environ.get("RAWTREE_API_URL")
    rawtree_key = os.environ.get("RAWTREE_API_KEY")

    if not rawtree_url or not rawtree_key:
        raise ValueError("RAWTREE_API_URL or RAWTREE_API_KEY environment variables not set.")

    headers = {
        "Authorization": f"Bearer {rawtree_key}",
        "Content-Type": "application/json",
    }
    payload = {"sql": sql}

    endpoint = f"{rawtree_url.rstrip('/')}/v1/query"
    resp = requests.post(endpoint, json=payload, headers=headers, timeout=8)
    resp.raise_for_status()
    data = resp.json()

    # RawTree standard response format contains 'rows' or 'data'
    rows = data.get("rows", data.get("data", []))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# High-Level Analytical Query API (used by Streamlit app and charts)
# ---------------------------------------------------------------------------

def get_tukey_spread(source: Optional[str] = None) -> pd.DataFrame:
    """Tukey Spread Query: Raw observations for Boxplots, IQR, and Outliers."""
    sql = """
    SELECT
        session_id,
        device_id,
        problem_id,
        category,
        difficulty_level,
        attempt_num,
        confidence_pct,
        solve_elapsed_ms / 1000.0 AS solve_sec,
        slm_latency_ms,
        is_correct,
        misconception_tag,
        pedagogical_move,
        event_type,
        ts_utc,
        offline_cached
    FROM telemetry_events
    ORDER BY ts_utc DESC;
    """
    df = run_query(sql, source=source)
    if not df.empty and "ts_utc" in df.columns:
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], format="mixed", utc=True)
    return df


def get_bottlenecks(source: Optional[str] = None) -> pd.DataFrame:
    """Core Query 1: Detect Classroom Bottlenecks (High attempts + low accuracy)."""
    sql = """
    SELECT
        category,
        problem_id,
        difficulty_level,
        COUNT(*) AS total_events,
        COUNT(DISTINCT session_id) AS active_students,
        AVG(attempt_num) AS avg_attempts,
        AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS accuracy_rate,
        AVG(solve_elapsed_ms) / 1000.0 AS mean_solve_sec,
        MAX(misconception_tag) AS dominant_misconception
    FROM telemetry_events
    GROUP BY category, problem_id, difficulty_level
    HAVING COUNT(*) >= 3
    ORDER BY avg_attempts DESC, accuracy_rate ASC;
    """
    return run_query(sql, source=source)


def get_calibration_gap(source: Optional[str] = None) -> pd.DataFrame:
    """Core Query 2: Confidence Dial vs. Actual Accuracy (Dunning-Kruger detection)."""
    sql = """
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
    WHERE event_type IN ('ANSWER_SUBMIT', 'SOLVED') AND is_correct IS NOT NULL
    GROUP BY category, difficulty_level, confidence_band
    ORDER BY category, difficulty_level, mean_confidence DESC;
    """
    return run_query(sql, source=source)


def get_agent_insights(source: Optional[str] = None) -> pd.DataFrame:
    """Core Table 2: Autonomous RawTree Agent Scratchpad Interventions."""
    sql = """
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
    df = run_query(sql, source=source)
    if not df.empty and "created_at_utc" in df.columns:
        df["created_at_utc"] = pd.to_datetime(df["created_at_utc"], format="mixed", utc=True)
    return df


def get_agent_traces(source: Optional[str] = None) -> pd.DataFrame:
    """Core Table 3: Autonomous RawTree Agent Self-Monitoring Traces & Meta-Memory."""
    sql = """
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
    try:
        df = run_query(sql, source=source)
        if not df.empty and "ts_utc" in df.columns:
            df["ts_utc"] = pd.to_datetime(df["ts_utc"], format="mixed", utc=True)
        return df
    except Exception:
        return pd.DataFrame()


def get_pre_post_intervention(
    target_problem_id: str = "CAM-2023-N4-17",
    source: Optional[str] = None,
) -> pd.DataFrame:
    """Compares student performance before vs. after autonomous agent intervention."""
    clean_problem_id = "".join(c for c in str(target_problem_id).strip() if c.isalnum() or c in "-_")
    if not clean_problem_id:
        clean_problem_id = "CAM-2023-N4-17"

    sql = f"""
    SELECT
        session_id,
        problem_id,
        attempt_num,
        confidence_pct,
        solve_elapsed_ms / 1000.0 AS solve_sec,
        is_correct,
        misconception_tag,
        ts_utc,
        CASE
            WHEN ts_utc >= COALESCE(
                (
                    SELECT created_at_utc FROM agent_insights
                    WHERE target_problem_id = '{clean_problem_id}'
                    ORDER BY created_at_utc ASC
                    LIMIT 1
                ),
                (
                    SELECT created_at_utc FROM agent_insights
                    ORDER BY created_at_utc ASC
                    LIMIT 1
                )
            ) THEN 'Post-Intervention'
            ELSE 'Pre-Intervention'
        END AS intervention_phase
    FROM telemetry_events
    WHERE problem_id = '{clean_problem_id}'
    ORDER BY ts_utc ASC;
    """
    df = run_query(sql, source=source)
    if not df.empty and "ts_utc" in df.columns:
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], format="mixed", utc=True)
    return df


# ---------------------------------------------------------------------------
# Advanced Tukey Statistical Calculations
# ---------------------------------------------------------------------------

def compute_tukey_summary(series: pd.Series) -> dict[str, float]:
    """Computes full John Tukey five-number summary and resistant location metrics."""
    s = series.dropna()
    if s.empty:
        return {
            "min": 0.0, "q1": 0.0, "median": 0.0, "q3": 0.0, "max": 0.0,
            "iqr": 0.0, "trimean": 0.0, "midhinge": 0.0, "quartile_skew": 0.0,
            "upper_fence": 0.0, "outlier_count": 0, "outlier_pct": 0.0,
        }

    q1 = float(s.quantile(0.25))
    median = float(s.median())
    q3 = float(s.quantile(0.75))
    iqr = q3 - q1
    min_val = float(s.min())
    max_val = float(s.max())

    # Tukey's Trimean: resistant weighted measure of central tendency
    trimean = (q1 + 2.0 * median + q3) / 4.0
    midhinge = (q1 + q3) / 2.0

    # Quartile Skewness (Bowley / Tukey index): in [-1, 1], >0 is right-skewed
    quartile_skew = ((q3 - median) - (median - q1)) / iqr if iqr > 0 else 0.0

    upper_fence = q3 + 1.5 * iqr
    outlier_count = int((s > upper_fence).sum())
    outlier_pct = (outlier_count / len(s)) * 100.0

    return {
        "min": round(min_val, 1),
        "q1": round(q1, 1),
        "median": round(median, 1),
        "q3": round(q3, 1),
        "max": round(max_val, 1),
        "iqr": round(iqr, 1),
        "trimean": round(trimean, 1),
        "midhinge": round(midhinge, 1),
        "quartile_skew": round(quartile_skew, 2),
        "upper_fence": round(upper_fence, 1),
        "outlier_count": outlier_count,
        "outlier_pct": round(outlier_pct, 1),
    }


def compute_calibration_metrics(df: pd.DataFrame) -> dict[str, float]:
    """Calculates Brier Score and Expected Calibration Error (ECE) for hardware dial."""
    if df.empty or "event_type" not in df.columns:
        return {"brier_score": 0.25, "overconfidence_gap": 0.0, "high_conf_error_rate": 0.0, "ece": 0.0}
    sub = df[df["event_type"].isin(["ANSWER_SUBMIT", "SOLVED"]) & df["is_correct"].notna()].copy()
    if sub.empty:
        sub = df[df["is_correct"].notna()].copy()

    s = sub.dropna(subset=["confidence_pct", "is_correct"]).copy()
    if s.empty:
        return {"brier_score": 0.25, "overconfidence_gap": 0.0, "high_conf_error_rate": 0.0, "ece": 0.0}

    conf = s["confidence_pct"].astype(float) / 100.0
    actual = s["is_correct"].astype(float)

    # Brier Score: Mean squared error between confidence probability and outcome (lower is better, 0 = perfect)
    brier = float(((conf - actual) ** 2).mean())

    # Dunning-Kruger high confidence error subset (confidence >= 70% but wrong)
    high_conf = s[s["confidence_pct"] >= 70]
    if not high_conf.empty:
        overconf_gap = float((high_conf["confidence_pct"] / 100.0).mean() - high_conf["is_correct"].mean())
        high_conf_error_rate = float((high_conf["is_correct"] == 0).mean() * 100.0)
    else:
        overconf_gap = 0.0
        high_conf_error_rate = 0.0

    # Expected Calibration Error (ECE) across 10% bins (include_lowest=True ensures 0% is retained)
    s["bin"] = pd.cut(s["confidence_pct"], bins=range(0, 110, 10), labels=range(10), include_lowest=True)
    ece = 0.0
    for _, group in s.groupby("bin", observed=False):
        if len(group) > 0:
            bin_conf = group["confidence_pct"].mean() / 100.0
            bin_acc = group["is_correct"].mean()
            ece += (len(group) / len(s)) * abs(bin_conf - bin_acc)

    return {
        "brier_score": round(float(brier), 3),
        "overconfidence_gap": round(float(overconf_gap), 2),
        "high_conf_error_rate": round(float(high_conf_error_rate), 1),
        "ece": round(float(ece), 3),
    }


def compute_intervention_effect_size(pre_post_df: pd.DataFrame) -> dict[str, Any]:
    """Computes non-parametric Cliff's Delta, Cohen's d, Mann-Whitney U, and percentage shift pre vs post agent intervention."""
    if pre_post_df.empty or "intervention_phase" not in pre_post_df.columns or "solve_sec" not in pre_post_df.columns:
        return {
            "pre_median": 0.0, "post_median": 0.0, "time_reduction_pct": 0.0,
            "cliffs_delta": 0.0, "cohens_d": 0.0, "mann_whitney_u": 0.0, "mann_whitney_p": 1.0,
            "effect_label": "None", "pre_attempts": 0.0, "post_attempts": 0.0
        }
    pre = pre_post_df[pre_post_df["intervention_phase"] == "Pre-Intervention"]["solve_sec"].dropna()
    post = pre_post_df[pre_post_df["intervention_phase"] == "Post-Intervention"]["solve_sec"].dropna()

    if pre.empty or post.empty:
        return {
            "pre_median": 0.0, "post_median": 0.0, "time_reduction_pct": 0.0,
            "cliffs_delta": 0.0, "cohens_d": 0.0, "mann_whitney_u": 0.0, "mann_whitney_p": 1.0,
            "effect_label": "None", "pre_attempts": 0.0, "post_attempts": 0.0
        }

    pre_med = float(pre.median())
    post_med = float(post.median())
    red_pct = ((pre_med - post_med) / pre_med) * 100.0 if pre_med > 0 else 0.0

    n1, n2 = len(pre), len(post)
    greater = sum(x > y for x in pre for y in post)
    less = sum(x < y for x in pre for y in post)
    equal = sum(x == y for x in pre for y in post)

    # Cliff's Delta: non-parametric effect size for non-normal distribution comparisons
    cliffs_d = (greater - less) / (n1 * n2)

    # Mann-Whitney U statistic: rank-sum comparison between pre and post intervention
    u1 = greater + 0.5 * equal
    u2 = n1 * n2 - u1
    mann_whitney_u_stat = min(u1, u2)

    # Asymptotic p-value for Mann-Whitney U
    mu_u = (n1 * n2) / 2.0
    sigma_u = (n1 * n2 * (n1 + n2 + 1) / 12.0) ** 0.5
    if sigma_u > 0:
        z_score = (mann_whitney_u_stat - mu_u) / sigma_u
        import math
        mann_whitney_p_val = math.erfc(abs(z_score) / (2.0 ** 0.5))
    else:
        mann_whitney_p_val = 1.0

    # Cohen's d: parametric standardized mean difference
    m1, m2 = float(pre.mean()), float(post.mean())
    if n1 > 1 and n2 > 1:
        v1 = float(pre.var(ddof=1))
        v2 = float(post.var(ddof=1))
        s_pooled = (((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)) ** 0.5
        cohens_d_stat = (m1 - m2) / s_pooled if s_pooled > 0 else 0.0
    else:
        cohens_d_stat = 0.0

    # Standard thresholds: |d| < 0.147 negligible, < 0.33 small, < 0.474 medium, >= 0.474 large
    if abs(cliffs_d) >= 0.474:
        label = "Large"
    elif abs(cliffs_d) >= 0.33:
        label = "Medium"
    elif abs(cliffs_d) >= 0.147:
        label = "Small"
    else:
        label = "Negligible"

    pre_att = float(pre_post_df[pre_post_df["intervention_phase"] == "Pre-Intervention"]["attempt_num"].mean()) if "attempt_num" in pre_post_df.columns else 0.0
    post_att = float(pre_post_df[pre_post_df["intervention_phase"] == "Post-Intervention"]["attempt_num"].mean()) if "attempt_num" in pre_post_df.columns else 0.0

    return {
        "pre_median": round(pre_med, 1),
        "post_median": round(post_med, 1),
        "time_reduction_pct": round(red_pct, 1),
        "cliffs_delta": round(cliffs_d, 2),
        "cohens_d": round(cohens_d_stat, 2),
        "mann_whitney_u": round(mann_whitney_u_stat, 1),
        "mann_whitney_p": round(mann_whitney_p_val, 4),
        "effect_label": label,
        "pre_attempts": round(pre_att, 2),
        "post_attempts": round(post_att, 2),
    }


def get_difficulty_drift(source: Optional[str] = None) -> pd.DataFrame:
    """Detects difficulty inflation: problems whose empirical cognitive burden diverges from nominal tier."""
    sql = """
    SELECT
        problem_id,
        category,
        difficulty_level AS nominal_tier,
        COUNT(*) AS total_events,
        COUNT(DISTINCT session_id) AS student_count,
        AVG(attempt_num) AS avg_attempts,
        AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS accuracy_rate,
        AVG(solve_elapsed_ms) / 1000.0 AS mean_solve_sec
    FROM telemetry_events
    GROUP BY problem_id, category, difficulty_level
    HAVING COUNT(*) >= 3
    ORDER BY avg_attempts DESC;
    """
    df = run_query(sql, source=source)
    if df.empty:
        return pd.DataFrame(
            columns=[
                "problem_id",
                "category",
                "nominal_tier",
                "total_events",
                "student_count",
                "avg_attempts",
                "accuracy_rate",
                "mean_solve_sec",
                "empirical_score",
                "difficulty_drift",
            ]
        )
    # Empirical difficulty score combining attempts and solve time
    df["empirical_score"] = (df["avg_attempts"] * 0.5) + (df["mean_solve_sec"] / 30.0 * 0.5)
    # Drift metric: deviation from expected nominal tier
    df["difficulty_drift"] = df["empirical_score"] - df["nominal_tier"]
    return df.sort_values(by="difficulty_drift", ascending=False)


if __name__ == "__main__":
    print("[analytics_tukey.queries] Initializing Mode A (Local Mock)...")
    db = init_local_db()
    count_evt = db.execute("SELECT COUNT(*) FROM telemetry_events").fetchone()[0]
    count_ins = db.execute("SELECT COUNT(*) FROM agent_insights").fetchone()[0]
    print(f"[OK] Mode A ready: {count_evt} telemetry events, {count_ins} agent insights.")

    print("\n--- Top Bottlenecks ---")
    print(get_bottlenecks().head())

    print("\n--- Agent Insights ---")
    print(get_agent_insights()[["insight_id", "insight_type", "target_problem_id", "severity_score"]])
