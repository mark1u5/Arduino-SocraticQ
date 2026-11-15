"""Frozen data contracts shared by /hardware, /engine, /agent_rawtree, and /analytics_tukey.

Uses Python standard library only so every environment (QRB2210, laptop, CI) can import it with zero setup.
"""

from typing import Any, Literal, Optional, Protocol, TypedDict

UartEventType = Literal[
    "PROBLEM_SELECT",
    "HINT_REQUEST",
    "ANSWER_SUBMIT",
    "CONFIDENCE_UPDATE",
    "SESSION_RESET",
]

EngineEventType = Literal["PROBLEM_SELECT", "HINT_REQUEST", "ANSWER_SUBMIT"]

LedState = Literal[
    "IDLE",
    "THINKING",
    "SOCRATIC_NUDGE",
    "SOLVED",
    "BOTTLENECK_ALERT",
]

MathCategory = Literal[
    "Geometry",
    "Combinatorics",
    "NumberTheory",
    "Algebra",
    "Logic",
]

PedagogicalMove = Literal[
    "DECOMPOSE_PROBLEM",
    "COUNTEREXAMPLE_CHECK",
    "INVARIANT_NUDGE",
    "PARITY_CHECK",
    "BOUNDARY_TEST",
    "AFFIRM_CORRECT",
]

InsightType = Literal["BOTTLENECK", "CALIBRATION_GAP", "INTERVENTION"]
InsightStatus = Literal["ACTIVE", "APPLIED", "RESOLVED"]


class UartEventFrame(TypedDict):
    """MCU -> Linux Daemon newline-delimited JSON payload."""

    schema_v: int
    device_id: str
    seq: int
    ts_ms: int
    event_type: UartEventType
    problem_id: str
    dial_category_idx: int
    confidence_dial: int
    selected_option: Optional[str]
    attempt_num: int


class UartFeedbackFrame(TypedDict):
    """Linux Daemon -> MCU newline-delimited JSON payload."""

    schema_v: int
    ack_seq: int
    led_state: LedState
    rgb: list[int]
    buzz_pattern: str
    lcd_line1: str
    lcd_line2: str


class SocraticResponse(TypedDict):
    """Output contract for /engine/socratic_engine.py::generate_socratic_hint."""

    problem_id: str
    category: MathCategory
    difficulty_level: int
    hint_text: str
    lcd_summary: str
    pedagogical_move: PedagogicalMove
    is_correct: Optional[bool]
    misconception_tag: str
    latency_ms: int
    model_id: str
    offline_mode: bool


class TelemetryEvent(TypedDict):
    """Row contract for RawTree `telemetry_events` (and FastAPI /ingest)."""

    event_id: str
    session_id: str
    device_id: str
    ts_utc: str
    problem_id: str
    cangur_year: int
    category: str
    difficulty_level: int
    event_type: str
    attempt_num: int
    confidence_pct: int
    is_correct: Optional[bool]
    misconception_tag: Optional[str]
    pedagogical_move: Optional[str]
    solve_elapsed_ms: int
    slm_latency_ms: int
    offline_cached: bool


class AgentInsight(TypedDict):
    """Row contract for RawTree `agent_insights` scratchpad table."""

    insight_id: str
    created_at_utc: str
    cycle_num: int
    insight_type: InsightType
    target_category: str
    target_problem_id: Optional[str]
    severity_score: float
    sql_executed: str
    evidence_summary: str
    recommended_nudge: str
    status: InsightStatus


class SocraticEngineFn(Protocol):
    """Callable signature contract for Dev 2's generate_socratic_hint."""

    def __call__(
        self,
        problem_id: str,
        event_type: EngineEventType,
        attempt_num: int,
        confidence_pct: int,
        selected_option: Optional[str] = None,
        student_note: Optional[str] = None,
        agent_override_nudge: Optional[str] = None,
    ) -> SocraticResponse: ...


def _require_keys(payload: dict[str, Any], required: tuple[str, ...], name: str) -> None:
    missing = [k for k in required if k not in payload]
    if missing:
        raise ValueError(f"{name}: missing keys {missing}")
    extra = [k for k in payload if k not in required]
    if extra:
        raise ValueError(f"{name}: unexpected keys {extra}")


def validate_uart_event(payload: dict[str, Any]) -> None:
    required = tuple(UartEventFrame.__annotations__.keys())
    _require_keys(payload, required, "UartEventFrame")
    if payload["event_type"] not in {
        "PROBLEM_SELECT",
        "HINT_REQUEST",
        "ANSWER_SUBMIT",
        "CONFIDENCE_UPDATE",
        "SESSION_RESET",
    }:
        raise ValueError(f"UartEventFrame: invalid event_type {payload['event_type']!r}")
    if not (0 <= int(payload["dial_category_idx"]) <= 4):
        raise ValueError("UartEventFrame: dial_category_idx must be in 0..4")
    if not (0 <= int(payload["confidence_dial"]) <= 100):
        raise ValueError("UartEventFrame: confidence_dial must be in 0..100")


def validate_uart_feedback(payload: dict[str, Any]) -> None:
    required = tuple(UartFeedbackFrame.__annotations__.keys())
    _require_keys(payload, required, "UartFeedbackFrame")
    if payload["led_state"] not in {
        "IDLE",
        "THINKING",
        "SOCRATIC_NUDGE",
        "SOLVED",
        "BOTTLENECK_ALERT",
    }:
        raise ValueError(f"UartFeedbackFrame: invalid led_state {payload['led_state']!r}")
    if len(payload["rgb"]) != 3:
        raise ValueError("UartFeedbackFrame: rgb must have 3 elements")


def validate_socratic_response(payload: dict[str, Any]) -> None:
    required = tuple(SocraticResponse.__annotations__.keys())
    _require_keys(payload, required, "SocraticResponse")
    if len(payload["hint_text"]) > 240:
        raise ValueError("SocraticResponse: hint_text exceeds 240 chars")
    if len(payload["lcd_summary"]) > 32:
        raise ValueError("SocraticResponse: lcd_summary exceeds 32 chars")
    if payload["category"] not in {
        "Geometry",
        "Combinatorics",
        "NumberTheory",
        "Algebra",
        "Logic",
    }:
        raise ValueError(f"SocraticResponse: invalid category {payload['category']!r}")
    if payload["pedagogical_move"] not in {
        "DECOMPOSE_PROBLEM",
        "COUNTEREXAMPLE_CHECK",
        "INVARIANT_NUDGE",
        "PARITY_CHECK",
        "BOUNDARY_TEST",
        "AFFIRM_CORRECT",
    }:
        raise ValueError(
            f"SocraticResponse: invalid pedagogical_move {payload['pedagogical_move']!r}"
        )


def validate_telemetry_event(payload: dict[str, Any]) -> None:
    required = tuple(TelemetryEvent.__annotations__.keys())
    _require_keys(payload, required, "TelemetryEvent")
    if not (0 <= int(payload["confidence_pct"]) <= 100):
        raise ValueError("TelemetryEvent: confidence_pct must be in 0..100")


def validate_agent_insight(payload: dict[str, Any]) -> None:
    required = tuple(AgentInsight.__annotations__.keys())
    _require_keys(payload, required, "AgentInsight")
    if payload["insight_type"] not in {"BOTTLENECK", "CALIBRATION_GAP", "INTERVENTION"}:
        raise ValueError(f"AgentInsight: invalid insight_type {payload['insight_type']!r}")
    if payload["status"] not in {"ACTIVE", "APPLIED", "RESOLVED"}:
        raise ValueError(f"AgentInsight: invalid status {payload['status']!r}")
    if not (0.0 <= float(payload["severity_score"]) <= 1.0):
        raise ValueError("AgentInsight: severity_score must be in 0.0..1.0")
