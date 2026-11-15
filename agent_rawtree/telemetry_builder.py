"""Stdlib-only TelemetryEvent builder shared by web_app and chat_server.

Extracted from agent_rawtree/web_app.py::_record_socratic_telemetry so the
offline stdlib chat server can emit contract-valid telemetry without importing
FastAPI or pandas. Event IDs carry a per-caller prefix (evt-web- / evt-chat-)
so double-counting across servers is auditable in analytics.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

VALID_CATEGORIES = {"Geometry", "Combinatorics", "NumberTheory", "Algebra", "Logic"}


def _cangur_year(problem_id: str) -> int:
    """Resolve cangur_year from the engine bank; fall back to parsing the id."""
    try:
        from engine import cangur_bank

        prob = cangur_bank.get_problem(problem_id)
        if prob and prob.get("year"):
            return int(prob["year"])
    except Exception:
        pass
    match = re.search(r"(19|20)\d{2}", str(problem_id))
    return int(match.group(0)) if match else 0


def build_telemetry_event(
    *,
    socratic_resp: dict[str, Any],
    event_type: str,
    attempt_num: int,
    confidence_pct: int,
    session_id: Optional[str],
    hesitation_ms: Optional[int],
    device_id: str,
    event_id_prefix: str = "evt-chat",
    default_session_id: str = "sess-chat-01",
) -> dict[str, Any]:
    """Build a frozen-contract TelemetryEvent dict from a SocraticResponse.

    Raises ValueError via validate_telemetry_event if the result is invalid.
    """
    pid = str(socratic_resp.get("problem_id") or "GENERAL-MATH")[:32]
    tag = socratic_resp.get("misconception_tag")
    tag_clean = None if not tag or tag == "none" else str(tag)[:64]
    slm_ms = int(socratic_resp.get("latency_ms") or 0)
    elapsed_ms = int(hesitation_ms) if hesitation_ms and hesitation_ms > 0 else max(500, slm_ms)

    raw_cat = str(socratic_resp.get("category") or "Algebra")
    category = raw_cat if raw_cat in VALID_CATEGORIES else "Algebra"

    event: dict[str, Any] = {
        "event_id": f"{event_id_prefix}-{uuid.uuid4().hex[:8]}",
        "session_id": (session_id or default_session_id)[:64],
        "device_id": device_id[:32],
        "ts_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "problem_id": pid,
        "cangur_year": _cangur_year(pid),
        "category": category,
        "difficulty_level": int(socratic_resp.get("difficulty_level") or 2),
        "event_type": str(event_type)[:24],
        "attempt_num": int(attempt_num),
        "confidence_pct": max(0, min(100, int(confidence_pct))),
        "is_correct": socratic_resp.get("is_correct"),
        "misconception_tag": tag_clean,
        "pedagogical_move": socratic_resp.get("pedagogical_move"),
        "solve_elapsed_ms": elapsed_ms,
        "slm_latency_ms": slm_ms,
        "offline_cached": bool(socratic_resp.get("offline_mode", True)),
    }
    from contracts.schemas import validate_telemetry_event

    validate_telemetry_event(dict(event))
    return event
