"""Stdlib-only idempotent demo seeder for the local RawTree SQLite mirror.

Populates `telemetry_events`, `agent_insights`, and `agent_traces` using the
generators in `agent_rawtree/seed_events.py` ONLY when `telemetry_events` is
empty (`COUNT(*) == 0`), so live student chat turns are never overwritten.
Optionally runs one mock/heuristic `AutonomousRawTreeAgent` cycle afterward.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Any, Optional

from agent_rawtree.rawtree_client import RawTreeClient
from agent_rawtree.seed_events import generate_event, seed_insights, seed_traces


def ensure_demo_seeded(
    client: Optional[RawTreeClient] = None,
    count: int = 200,
    run_agent_cycle: bool = True,
) -> dict[str, Any]:
    """Seed the local RawTree mirror if `telemetry_events` is empty.

    Returns a summary dict with `seeded` (bool), row counts, and any
    `agent_insight_id` emitted by the optional autonomous agent cycle.
    """
    cli = client or RawTreeClient(force_offline=True)
    before_rows = cli.query_sql("SELECT COUNT(*) AS n FROM telemetry_events;")
    events_before = int(before_rows[0]["n"]) if before_rows else 0

    if events_before > 0:
        ins_rows = cli.query_sql("SELECT COUNT(*) AS n FROM agent_insights;")
        trc_rows = cli.query_sql("SELECT COUNT(*) AS n FROM agent_traces;")
        return {
            "seeded": False,
            "events_before": events_before,
            "events_after": events_before,
            "insights_after": int(ins_rows[0]["n"]) if ins_rows else 0,
            "traces_after": int(trc_rows[0]["n"]) if trc_rows else 0,
            "agent_insight_id": None,
        }

    base_time = datetime.now(timezone.utc)
    for idx in range(1, max(1, int(count)) + 1):
        event = generate_event(idx, base_time)
        cli.insert_telemetry_event(event)

    seed_insights(cli, base_time)
    seed_traces(cli, base_time)

    agent_insight_id: Optional[str] = None
    if run_agent_cycle:
        from agent_rawtree.autonomous_agent import AutonomousRawTreeAgent

        agent = AutonomousRawTreeAgent(client=cli, force_heuristic=True)
        insight = agent.run_cycle()
        if insight:
            agent_insight_id = str(insight.get("insight_id") or "") or None

    ev_rows = cli.query_sql("SELECT COUNT(*) AS n FROM telemetry_events;")
    ins_rows = cli.query_sql("SELECT COUNT(*) AS n FROM agent_insights;")
    trc_rows = cli.query_sql("SELECT COUNT(*) AS n FROM agent_traces;")

    return {
        "seeded": True,
        "events_before": 0,
        "events_after": int(ev_rows[0]["n"]) if ev_rows else 0,
        "insights_after": int(ins_rows[0]["n"]) if ins_rows else 0,
        "traces_after": int(trc_rows[0]["n"]) if trc_rows else 0,
        "agent_insight_id": agent_insight_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotently seed local RawTree mirror for demo.")
    parser.add_argument("--count", type=int, default=200, help="Number of telemetry events to generate if empty")
    parser.add_argument("--no-agent", action="store_true", help="Skip running the initial mock agent cycle")
    args = parser.parse_args()

    result = ensure_demo_seeded(count=args.count, run_agent_cycle=not args.no_agent)
    print(f"[seed_demo] {result}")


if __name__ == "__main__":
    main()
