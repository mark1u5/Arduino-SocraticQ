"""Synthetic Cangur Telemetry Event Generator.

Generates realistic telemetry events matching contracts/schemas.py to:
1. Seed RawTree and the local fallback mirror.
2. Unblock Dev 4's Tukey dashboard immediately with rich statistical distributions
   (boxplots, outliers, calibration gaps, and error clusters).
3. Provide the exact bottleneck dataset needed for the live stage pitch on CAM-2023-N4-17.
4. Seed `agent_insights` and `agent_traces` for the Agent Timeline and Meta-Cognition panel.
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_rawtree.rawtree_client import RawTreeClient
from contracts.schemas import AgentInsight, TelemetryEvent, validate_agent_insight, validate_telemetry_event

PROBLEMS = [
    {
        "id": "CAM-2023-N4-17",
        "category": "Combinatorics",
        "year": 2023,
        "difficulty": 2,
        "story": "bottleneck",  # High attempts, low accuracy, parity misconception
    },
    {
        "id": "CAM-2022-N4-19",
        "category": "Geometry",
        "year": 2022,
        "difficulty": 2,
        "story": "dunning_kruger",  # High confidence, high error rate
    },
    {
        "id": "CAM-2023-N3-09",
        "category": "NumberTheory",
        "year": 2023,
        "difficulty": 1,
        "story": "well_calibrated",  # Easy, high accuracy, well calibrated
    },
    {
        "id": "CAM-2022-N4-14",
        "category": "Algebra",
        "year": 2022,
        "difficulty": 2,
        "story": "normal",  # Balanced
    },
    {
        "id": "CAM-2024-N4-11",
        "category": "Logic",
        "year": 2024,
        "difficulty": 2,
        "story": "fast_solve",  # Quick, low attempts
    },
]

MISCONCEPTIONS = {
    "Combinatorics": ["ignored_parity_invariant", "overcounted_black_squares", "off_by_one"],
    "Geometry": ["repeated_obtuse_angle", "forgot_to_halve_base_sum", "interior_exterior_confusion"],
    "NumberTheory": ["returned_mean_not_largest", "assumed_period_five", "missed_smallest_lcm"],
    "Algebra": ["dropped_cross_term_2xy", "subtracted_without_halving"],
    "Logic": ["skipped_contradiction_check", "converse_fallacy"],
}

PEDAGOGICAL_MOVES = [
    "DECOMPOSE_PROBLEM",
    "COUNTEREXAMPLE_CHECK",
    "INVARIANT_NUDGE",
    "PARITY_CHECK",
    "BOUNDARY_TEST",
    "AFFIRM_CORRECT",
]


def generate_event(idx: int, base_time: datetime) -> TelemetryEvent:
    prob = random.choices(
        PROBLEMS,
        weights=[35, 25, 15, 15, 10],  # Heavy weight on stage demo problems
        k=1,
    )[0]

    story = prob["story"]
    session_id = f"sess-unoq-{random.randint(1, 45):02d}"
    device_id = f"unoq-{random.randint(1, 4):02d}"

    # Time spread across the last 3 hours; bottleneck splits pre/post ins-0001 (-15m)
    if story == "bottleneck":
        is_post = random.random() < 0.30
        mins_ago = random.randint(0, 14) if is_post else random.randint(16, 180)
    else:
        is_post = False
        mins_ago = random.randint(0, 180)
    time_offset = timedelta(minutes=mins_ago, seconds=random.randint(0, 59))
    event_time = (base_time - time_offset).isoformat().replace("+00:00", "Z")

    event_type = random.choices(
        ["ANSWER_SUBMIT", "HINT_REQUEST", "SOLVED"],
        weights=[55, 30, 15],
        k=1,
    )[0]

    if story == "bottleneck":
        # CAM-2023-N4-17 (Combinatorics bottleneck: pre-intervention vs post-intervention recovery)
        if is_post:
            attempt_num = random.choices([1, 2], weights=[75, 25])[0]
            confidence_pct = random.randint(55, 85)
            is_correct = random.random() < 0.80
            misconception_tag = "ignored_parity_invariant" if not is_correct else None
            pedagogical_move = "AFFIRM_CORRECT" if is_correct else "PARITY_CHECK"
            solve_elapsed_ms = int(random.gauss(32000, 8000))
        else:
            attempt_num = random.choices([2, 3, 4, 5], weights=[25, 40, 20, 15])[0]
            confidence_pct = random.randint(15, 45)
            is_correct = False if attempt_num < 4 else (random.random() < 0.25)
            misconception_tag = "ignored_parity_invariant" if not is_correct else None
            pedagogical_move = "PARITY_CHECK"
            solve_elapsed_ms = int(random.gauss(68000, 18000))
        if solve_elapsed_ms < 10000:
            solve_elapsed_ms = 12000
    elif story == "dunning_kruger":
        # Geometry calibration gap (high confidence, low accuracy)
        attempt_num = random.choices([1, 2, 3], weights=[50, 35, 15])[0]
        confidence_pct = random.randint(70, 95)  # Overconfident
        is_correct = random.random() < 0.25  # But mostly wrong!
        misconception_tag = random.choice(["repeated_obtuse_angle", "forgot_to_halve_base_sum"]) if not is_correct else None
        pedagogical_move = "COUNTEREXAMPLE_CHECK" if not is_correct else "AFFIRM_CORRECT"
        solve_elapsed_ms = int(random.gauss(85000, 30000))
    elif story == "well_calibrated":
        attempt_num = random.choices([1, 2], weights=[80, 20])[0]
        confidence_pct = random.randint(75, 95)
        is_correct = random.random() < 0.85
        misconception_tag = "returned_mean_not_largest" if not is_correct else None
        pedagogical_move = "AFFIRM_CORRECT" if is_correct else "DECOMPOSE_PROBLEM"
        solve_elapsed_ms = int(random.gauss(30000, 8000))
    elif story == "fast_solve":
        attempt_num = 1
        confidence_pct = random.randint(60, 90)
        is_correct = random.random() < 0.90
        misconception_tag = None if is_correct else "skipped_contradiction_check"
        pedagogical_move = "AFFIRM_CORRECT" if is_correct else "BOUNDARY_TEST"
        solve_elapsed_ms = int(random.gauss(18000, 4000))
    else:  # normal
        attempt_num = random.choices([1, 2, 3], weights=[60, 30, 10])[0]
        confidence_pct = random.randint(40, 80)
        is_correct = random.random() < 0.65
        misconception_tag = random.choice(MISCONCEPTIONS[prob["category"]]) if not is_correct else None
        pedagogical_move = random.choice(PEDAGOGICAL_MOVES)
        solve_elapsed_ms = int(random.gauss(45000, 15000))

    if solve_elapsed_ms < 5000:
        solve_elapsed_ms = 5000

    slm_latency_ms = int(random.gauss(620, 120))
    if slm_latency_ms < 300:
        slm_latency_ms = 350

    if event_type == "HINT_REQUEST":
        is_correct = None

    if event_type == "SOLVED":
        # A SOLVED event must be a correct submission with no lingering
        # misconception; otherwise accuracy/calibration aggregates lie.
        is_correct = True
        misconception_tag = None
        pedagogical_move = "AFFIRM_CORRECT"

    event: TelemetryEvent = {
        "event_id": f"evt-{idx:04d}",
        "session_id": session_id,
        "device_id": device_id,
        "ts_utc": event_time,
        "problem_id": prob["id"],
        "cangur_year": prob["year"],
        "category": prob["category"],
        "difficulty_level": prob["difficulty"],
        "event_type": event_type,
        "attempt_num": attempt_num,
        "confidence_pct": confidence_pct,
        "is_correct": is_correct,
        "misconception_tag": misconception_tag,
        "pedagogical_move": pedagogical_move,
        "solve_elapsed_ms": solve_elapsed_ms,
        "slm_latency_ms": slm_latency_ms,
        "offline_cached": random.random() < 0.25,
    }

    validate_telemetry_event(dict(event))
    return event


def seed_insights(client: RawTreeClient, base_time: datetime) -> None:
    """Seed golden agent insights for the live pitch and Dev 4 dashboard."""
    insights: list[AgentInsight] = [
        {
            "insight_id": "ins-0001",
            "created_at_utc": (base_time - timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
            "cycle_num": 1,
            "insight_type": "BOTTLENECK",
            "target_category": "Combinatorics",
            "target_problem_id": "CAM-2023-N4-17",
            "severity_score": 0.84,
            "sql_executed": "SELECT problem_id, AVG(attempt_num) AS avg_att, AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS acc FROM telemetry_events WHERE category = 'Combinatorics' GROUP BY problem_id HAVING AVG(attempt_num) >= 2.2;",
            "evidence_summary": "78% of active sessions exceed 2 attempts on CAM-2023-N4-17 with dominant tag ignored_parity_invariant.",
            "recommended_nudge": "Color adjacent squares black and white first—how does each valid move change the colors?",
            "status": "ACTIVE",
        },
        {
            "insight_id": "ins-0002",
            "created_at_utc": (base_time - timedelta(minutes=8)).isoformat().replace("+00:00", "Z"),
            "cycle_num": 2,
            "insight_type": "CALIBRATION_GAP",
            "target_category": "Geometry",
            "target_problem_id": "CAM-2022-N4-19",
            "severity_score": 0.72,
            "sql_executed": "SELECT category, AVG(confidence_pct) AS mean_conf, AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS empirical_acc FROM telemetry_events WHERE event_type = 'ANSWER_SUBMIT' GROUP BY category;",
            "evidence_summary": "Severe Dunning-Kruger mismatch in Geometry (CAM-2022-N4-19): students average 82% confidence dial but only 24% empirical accuracy.",
            "recommended_nudge": "Could two angles in the same triangle both measure 100 degrees, given the 180-degree sum?",
            "status": "ACTIVE",
        },
        {
            "insight_id": "ins-0003",
            "created_at_utc": (base_time - timedelta(minutes=45)).isoformat().replace("+00:00", "Z"),
            "cycle_num": 1,
            "insight_type": "INTERVENTION",
            "target_category": "NumberTheory",
            "target_problem_id": "CAM-2023-N3-09",
            "severity_score": 0.45,
            "sql_executed": "SELECT problem_id, COUNT(*) FROM telemetry_events WHERE misconception_tag = 'returned_mean_not_largest' GROUP BY problem_id;",
            "evidence_summary": "Mean-vs-largest confusion on CAM-2023-N3-09 resolved after decomposition nudge. Accuracy improved from 48% to 88%.",
            "recommended_nudge": "What does dividing 72 by 3 give you—is that the smallest, middle, or largest integer?",
            "status": "RESOLVED",
        },
    ]

    for ins in insights:
        validate_agent_insight(dict(ins))
        client.insert_agent_insight(ins)
    print(f"[Seeder] Seeded {len(insights)} golden agent insights.")


def seed_traces(client: RawTreeClient, base_time: datetime) -> None:
    """Seed agent meta-cognition traces to power the RawTree self-monitoring challenge."""
    traces: list[dict[str, Any]] = [
        {
            "trace_id": "trc-0001",
            "ts_utc": (base_time - timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
            "cycle_num": 1,
            "agent_role": "AutonomousCognitiveLoop",
            "model_id": "glm-5.3",
            "sql_executed": "SELECT category, COUNT(*) FROM telemetry_events GROUP BY category",
            "sql_latency_ms": 114,
            "findings_count": 5,
            "action_taken": "OBSERVED_NOMINAL",
            "prompt_tokens": 480,
            "completion_tokens": 85,
            "estimated_cost_usd": 0.00028,
        },
        {
            "trace_id": "trc-0002",
            "ts_utc": (base_time - timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
            "cycle_num": 2,
            "agent_role": "AutonomousCognitiveLoop",
            "model_id": "glm-5.3",
            "sql_executed": "SELECT problem_id, AVG(attempt_num) FROM telemetry_events GROUP BY problem_id HAVING AVG(attempt_num) >= 2.2",
            "sql_latency_ms": 142,
            "findings_count": 1,
            "action_taken": "EMITTED_BOTTLENECK_INTERVENTION",
            "prompt_tokens": 720,
            "completion_tokens": 160,
            "estimated_cost_usd": 0.00044,
        },
        {
            "trace_id": "trc-0003",
            "ts_utc": (base_time - timedelta(minutes=8)).isoformat().replace("+00:00", "Z"),
            "cycle_num": 3,
            "agent_role": "AutonomousCognitiveLoop",
            "model_id": "glm-5.3",
            "sql_executed": "SELECT category, AVG(confidence_pct), AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) FROM telemetry_events GROUP BY category",
            "sql_latency_ms": 168,
            "findings_count": 1,
            "action_taken": "FLAGGED_CALIBRATION_GAP",
            "prompt_tokens": 650,
            "completion_tokens": 140,
            "estimated_cost_usd": 0.00039,
        },
    ]

    for tr in traces:
        client.insert_agent_trace(tr)
    print(f"[Seeder] Seeded {len(traces)} agent self-monitoring traces.")


def seed_database(count: int = 200, clear: bool = False) -> None:
    client = RawTreeClient()
    print(f"[Seeder] Target database mode: {'LIVE RAWTREE' if client.is_live else 'OFFLINE SQLITE MIRROR'}")

    if clear:
        print("[Seeder] Clearing tables before seeding...")
        client.query_sql("DELETE FROM telemetry_events;")
        client.query_sql("DELETE FROM agent_insights;")
        client.query_sql("DELETE FROM agent_traces;")

    base_time = datetime.now(timezone.utc)
    print(f"[Seeder] Generating {count} synthetic telemetry events...")

    for i in range(1, count + 1):
        event = generate_event(i, base_time)
        client.insert_telemetry_event(event)

    seed_insights(client, base_time)
    seed_traces(client, base_time)

    print(f"[Seeder] Successfully seeded all datasets.")

    # Run check verification queries
    total = client.query_sql("SELECT COUNT(*) AS total FROM telemetry_events")[0]["total"]
    total_insights = client.query_sql("SELECT COUNT(*) AS total FROM agent_insights")[0]["total"]
    total_traces = client.query_sql("SELECT COUNT(*) AS total FROM agent_traces")[0]["total"]
    categories = client.query_sql(
        "SELECT category, COUNT(*) as cnt, AVG(attempt_num) as avg_att FROM telemetry_events GROUP BY category"
    )
    print(f"[Seeder] Verification:")
    print(f"  - Telemetry Events: {total}")
    print(f"  - Agent Insights:   {total_insights}")
    print(f"  - Agent Traces:     {total_traces}")
    for row in categories:
        avg_att = float(row["avg_att"] or 0.0)
        print(f"    * {row['category']}: {row['cnt']} events (avg attempts: {avg_att:.2f})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed RawTree with Cangur telemetry, insights and traces")
    parser.add_argument("--count", type=int, default=200, help="Number of events to generate")
    parser.add_argument("--clear", action="store_true", help="Clear tables before seeding")
    args = parser.parse_args()
    seed_database(count=args.count, clear=args.clear)


if __name__ == "__main__":
    main()
