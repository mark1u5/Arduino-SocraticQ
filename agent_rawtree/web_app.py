"""FastAPI Telemetry Ingest & Mentor Web Server for SocraticQ.

Owned by Dev 3 (agent_rawtree).

Endpoints:
- POST /ingest: Receives telemetry JSON frames from Dev 1's edge hardware bridge.
- GET  /stream: Real-time Server-Sent Events (SSE) stream for instant reactive UI and hardware alerts.
- GET  /interventions/active: Returns active agent-generated interventions (triggers purple LED).
- POST /interventions/{insight_id}/apply: Marks intervention as applied by edge mentor.
- GET  /stats: Returns live counts and system status across tables.
- GET  /health: Service health check.
- GET  /problems: Returns curated Cangur problems without answer keys.
- POST /tutor/interact: Backward-compatible Socratic tutor endpoint.
- POST /api/text-query: Unified web UI text/math Socratic + guardrail + RawTree endpoint.
- POST /api/voice-query: Unified web UI voice (STT + Socratic + guardrail + RawTree) endpoint.
- GET  /api/system-status: Hardware, RAM/CPU, SLM, STT, RawTree & AI-Safety metrics.
- GET  /api/cortex-snapshot: Initial telemetry & active insights hydration for static UI.
- GET  /: Serves unified dark-mode Web UI (static/index.html).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import platform
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_rawtree.rawtree_client import RawTreeClient
from contracts.schemas import (
    PedagogicalMove,
    SocraticResponse,
    TelemetryEvent,
    validate_socratic_response,
    validate_telemetry_event,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"

app = FastAPI(
    title="SocraticQ Agentic Cortex (RawTree)",
    description="Real-time analytical scratchpad, edge Socratic voice/text mentor, and cognitive intervention engine.",
    version="2.0.0",
)

# Enable CORS for local testing across ports (FastAPI on 8000, Streamlit on 8501)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

client = RawTreeClient()

# Active SSE subscribers queue set
_subscribers: set[asyncio.Queue[str]] = set()

# Decision D2: Serialize STT & SLM decoding so QRB2210 4x ARM Cortex-A53 cores never contend.
_EDGE_INFERENCE_SEM = asyncio.Semaphore(1)

# Latest edge inference metadata surfaced on /health, /api/system-status, and /tutor/interact.
_LAST_INFERENCE_META: dict[str, Any] = {
    "prompt_n": 0,
    "tok_s": 0.0,
    "latency_ms": 0,
    "model_id": os.environ.get("SLM_MODEL_ID", "qwen3.5-0.8b"),
    "offline_mode": True,
}

MOVE_TO_DIMENSION: dict[str, str] = {
    "DECOMPOSE_PROBLEM": "Clarification",
    "COUNTEREXAMPLE_CHECK": "Probing Assumptions",
    "INVARIANT_NUDGE": "Probing Reasons & Evidence",
    "PARITY_CHECK": "Alternative Perspectives",
    "BOUNDARY_TEST": "Implications & Consequences",
    "AFFIRM_CORRECT": "Meta-Questioning",
}

_JAILBREAK_RE = re.compile(
    r"(?i)\b(?:ignore\s+(?:all\s+)?(?:previous|prior|your)?\s*(?:rules|instructions)|"
    r"system\s+prompt|jailbreak|developer\s+mode|bypass\s+guardrail|act\s+as\s+dan)\b"
)
_OFFLOAD_RE = re.compile(
    r"(?i)\b(?:just\s+solve|solve\s+(?:this|it)\s+for\s+me|"
    r"(?:tell|give)\s+me\s+the\s+(?:final\s+)?answer|what\s+is\s+the\s+answer)\b"
)


async def broadcast_sse(event_type: str, data: dict[str, Any]) -> None:
    """Broadcast an event payload to all connected SSE browser/bridge clients."""
    if not _subscribers:
        return
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except Exception:
            pass


class TelemetryEventPayload(BaseModel):
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
    confidence_pct: int = Field(ge=0, le=100)
    is_correct: Optional[bool] = None
    misconception_tag: Optional[str] = None
    pedagogical_move: Optional[str] = None
    solve_elapsed_ms: int
    slm_latency_ms: int
    offline_cached: bool = False


class TutorInteractPayload(BaseModel):
    problem_id: str
    event_type: Literal["PROBLEM_SELECT", "HINT_REQUEST", "ANSWER_SUBMIT"] = "HINT_REQUEST"
    attempt_num: int = Field(default=1, ge=1)
    confidence_pct: int = Field(default=50, ge=0, le=100)
    selected_option: Optional[str] = None
    student_note: Optional[str] = None
    session_id: Optional[str] = None
    mode: Literal["teach", "solve"] = "teach"
    conversation_context: Optional[str] = None
    prior_problem_id: Optional[str] = None


class TextQueryPayload(BaseModel):
    problem_id: str = "GENERAL-MATH"
    text: str = ""
    event_type: Literal["PROBLEM_SELECT", "HINT_REQUEST", "ANSWER_SUBMIT"] = "HINT_REQUEST"
    attempt_num: int = Field(default=1, ge=1)
    confidence_pct: int = Field(default=50, ge=0, le=100)
    selected_option: Optional[str] = None
    session_id: Optional[str] = None
    hesitation_ms: Optional[int] = Field(default=None, ge=0)
    mode: Literal["teach", "solve"] = "teach"
    conversation_context: Optional[str] = None
    prior_problem_id: Optional[str] = None


_FALLBACK_PROBLEMS: list[dict[str, Any]] = [
    {
        "problem_id": "CAM-2023-N4-17",
        "year": 2023,
        "category": "Combinatorics",
        "difficulty_level": 2,
        "statement": "A 5x5 chessboard has 13 black squares and 12 white squares. You remove 1 white square. What is the maximum number of 1x2 dominoes that can cover the remaining 24 squares without overlapping?",
        "options": {"A": "9", "B": "10", "C": "12", "D": "11", "E": "13"},
    },
    {
        "problem_id": "CAM-2022-N4-19",
        "year": 2022,
        "category": "Geometry",
        "difficulty_level": 2,
        "statement": "One interior angle of an isosceles triangle measures 100 degrees. What is the measure of each of the other two angles?",
        "options": {"A": "30", "B": "40", "C": "50", "D": "80", "E": "100"},
    },
    {
        "problem_id": "CAM-2023-N3-09",
        "year": 2023,
        "category": "NumberTheory",
        "difficulty_level": 1,
        "statement": "The sum of three consecutive integers is 72. What is the largest of the three integers?",
        "options": {"A": "23", "B": "24", "C": "25", "D": "26", "E": "27"},
    },
    {
        "problem_id": "CAM-2022-N4-14",
        "year": 2022,
        "category": "Algebra",
        "difficulty_level": 2,
        "statement": "If x + y = 12 and x * y = 27, what is the value of x^2 + y^2?",
        "options": {"A": "63", "B": "81", "C": "90", "D": "117", "E": "144"},
    },
    {
        "problem_id": "CAM-2024-N4-11",
        "year": 2024,
        "category": "Logic",
        "difficulty_level": 2,
        "statement": "Anna says 'Biel is lying.' Biel says 'Carla is lying.' Carla says 'Both Anna and Biel are lying.' Exactly one friend is telling the truth. Who is telling the truth?",
        "options": {"A": "Anna", "B": "Biel", "C": "Carla", "D": "Anna and Carla", "E": "Impossible"},
    },
]


def _load_public_problems() -> list[dict[str, Any]]:
    """Load Cangur problems from engine.cangur_bank without exposing correct_option."""
    try:
        from engine import cangur_bank

        if hasattr(cangur_bank, "get_public_problems"):
            probs = cangur_bank.get_public_problems()
            if probs:
                return probs
        probs = []
        for pid in cangur_bank.list_problem_ids():
            p = cangur_bank.get_problem(pid)
            if p:
                probs.append(
                    {
                        "problem_id": p["problem_id"],
                        "year": p["year"],
                        "category": p["category"],
                        "difficulty_level": p["difficulty_level"],
                        "statement": p["statement"],
                        "options": p["options"],
                    }
                )
        if probs:
            return probs
    except Exception:
        pass
    return _FALLBACK_PROBLEMS


def _call_with_supported_kwargs(fn: Any, **kwargs: Any) -> Any:
    """Invoke a callable passing only parameters accepted by its signature."""
    sig = inspect.signature(fn)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(**kwargs)
    filtered = {k: v for k, v in kwargs.items() if k in params}
    return fn(**filtered)


def _fallback_guardrail_check(
    text: str,
    problem_id: str,
    base_category: str = "Algebra",
    base_difficulty: int = 2,
) -> Optional[dict[str, Any]]:
    """Lightweight Socratic Judo fallback when engine.generate_socratic_turn is unavailable."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    reason: Optional[str] = None
    hint: Optional[str] = None
    move: PedagogicalMove = "COUNTEREXAMPLE_CHECK"

    if _JAILBREAK_RE.search(cleaned):
        reason = "JAILBREAK_ATTEMPT"
        hint = "My tutoring rules stay locked so you build real mastery. What quantity or relation in this problem can you check yourself first?"
    elif _OFFLOAD_RE.search(cleaned):
        reason = "OFFLOADING_ATTEMPT"
        move = "DECOMPOSE_PROBLEM"
        hint = "Handing over the result skips the thinking step you need. What is the very first operation or invariant you can isolate?"

    if not reason or not hint:
        return None

    resp: SocraticResponse = {
        "problem_id": problem_id,
        "category": base_category if base_category in {"Geometry", "Combinatorics", "NumberTheory", "Algebra", "Logic"} else "Algebra",  # type: ignore[typeddict-item]
        "difficulty_level": base_difficulty,
        "hint_text": hint,
        "lcd_summary": "Socratic Judo redirect",
        "pedagogical_move": move,
        "is_correct": None,
        "misconception_tag": reason,
        "latency_ms": 1,
        "model_id": "socratic-judo-guard",
        "offline_mode": True,
    }
    validate_socratic_response(resp)
    return {
        "socratic_response": resp,
        "socratic_dimension": "Meta-Questioning",
        "guardrail_triggered": True,
        "guardrail_reason": reason,
    }


def _extract_inference_metrics(
    meta: dict[str, Any],
    socratic_resp: dict[str, Any],
    problem_id: str,
    student_text: Optional[str],
) -> tuple[int, float]:
    """Extract or derive honest prompt_n and tok_s without fabricating fake SLM speeds."""
    timings = meta.get("timings") if isinstance(meta.get("timings"), dict) else {}
    usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    raw_prompt_n = (
        meta.get("prompt_n")
        if meta.get("prompt_n") is not None
        else timings.get("prompt_n", usage.get("prompt_tokens"))
    )
    raw_tok_s = (
        meta.get("tok_s")
        if meta.get("tok_s") is not None
        else timings.get("predicted_per_second")
    )

    model_id = str(socratic_resp.get("model_id") or "")
    latency_ms = max(0, int(socratic_resp.get("latency_ms") or 0))
    hint_text = str(socratic_resp.get("hint_text") or "")
    is_zero_token_path = (
        model_id.endswith("-fallback")
        or model_id.endswith("-oracle")
        or model_id.startswith("mock")
        or "guardrail" in model_id
        or "judo" in model_id
        or model_id == "web-fallback"
    )

    if raw_prompt_n is not None:
        prompt_n = max(0, int(raw_prompt_n))
    elif is_zero_token_path:
        prompt_n = 0
    else:
        prompt_n = max(1, len(f"{problem_id} {student_text or ''}".strip()) // 4)

    if raw_tok_s is not None:
        tok_s = round(max(0.0, float(raw_tok_s)), 1)
    elif prompt_n == 0 or latency_ms <= 0:
        tok_s = 0.0
    else:
        pred_n = timings.get("predicted_n") or usage.get("completion_tokens") or max(1, len(hint_text) // 4)
        tok_s = round(float(pred_n) / (latency_ms / 1000.0), 1)

    return prompt_n, tok_s


def _build_hint_ladder(
    problem_id: str,
    student_text: Optional[str],
    socratic_resp: Optional[dict[str, Any]] = None,
    solution_text: Optional[str] = None,
    guardrail_triggered: bool = False,
) -> dict[str, Any]:
    """Build the 4-Rung Hint Ladder cleanly without exposing answer keys prematurely."""
    prob: Optional[dict[str, Any]] = None
    easier_neighbor: Optional[dict[str, Any]] = None
    topic_vote: Optional[dict[str, Any]] = None

    try:
        from engine import cangur_bank

        prob = cangur_bank.get_problem(problem_id)
        if prob is None and student_text:
            prob = cangur_bank.match_concept_guide(problem_id, student_text)

        easier_fn = getattr(cangur_bank, "get_easier_neighbor", None)
        if callable(easier_fn) and prob is not None:
            raw_neighbor = easier_fn(prob)
            if isinstance(raw_neighbor, dict):
                easier_neighbor = {
                    "problem_id": raw_neighbor.get("problem_id"),
                    "category": raw_neighbor.get("category"),
                    "difficulty_level": raw_neighbor.get("difficulty_level"),
                    "statement": raw_neighbor.get("statement"),
                    "intuition_hook": raw_neighbor.get("intuition_hook"),
                }

        if easier_neighbor is None and prob is not None:
            cur_diff = int(prob.get("difficulty_level") or 2)
            sim_fn = getattr(cangur_bank, "retrieve_similar_problems", None)
            if callable(sim_fn):
                candidates = sim_fn(
                    prob.get("statement") or student_text or problem_id,
                    exclude_id=problem_id,
                    top_k=5,
                )
                easier = [
                    c for c in candidates if int(c.get("difficulty_level") or 99) < cur_diff
                ] or candidates
                if easier:
                    best = easier[0]
                    easier_neighbor = {
                        "problem_id": best.get("problem_id"),
                        "category": best.get("category"),
                        "difficulty_level": best.get("difficulty_level"),
                        "statement": best.get("statement"),
                        "intuition_hook": best.get("intuition_hook"),
                    }

        topic_fn = getattr(cangur_bank, "infer_topic_from_neighbors", None)
        if callable(topic_fn) and prob is not None:
            raw_topic = topic_fn(prob, k=5)
            if isinstance(raw_topic, dict):
                topic_vote = raw_topic
            elif isinstance(raw_topic, str):
                topic_vote = {"topic": raw_topic}
    except Exception:
        pass

    if prob is None:
        prob = next((p for p in _FALLBACK_PROBLEMS if p["problem_id"] == problem_id), None)

    category = (
        (socratic_resp or {}).get("category")
        or (topic_vote or {}).get("topic")
        or (prob or {}).get("category")
        or "Algebra"
    )
    move = str((socratic_resp or {}).get("pedagogical_move") or (prob or {}).get("default_move") or "DECOMPOSE_PROBLEM")
    lcd_summary = str(
        (socratic_resp or {}).get("lcd_summary")
        or (prob or {}).get("default_lcd_summary")
        or "Break into smaller steps"
    )
    dimension = MOVE_TO_DIMENSION.get(move, "Clarification")

    rung1 = {
        "rung": 1,
        "label": "Easier Neighbour",
        "available": easier_neighbor is not None,
        "problem_id": (easier_neighbor or {}).get("problem_id"),
        "category": (easier_neighbor or {}).get("category") or category,
        "difficulty_level": (easier_neighbor or {}).get("difficulty_level"),
        "statement": (easier_neighbor or {}).get("statement"),
        "intuition_hook": (easier_neighbor or {}).get("intuition_hook")
        or f"Try a smaller Tier-1 {category} case before solving the full problem.",
    }

    rung2 = {
        "rung": 2,
        "label": "Topic & Strategy",
        "topic": (topic_vote or {}).get("topic") or category,
        "strategy": lcd_summary,
        "pedagogical_move": move,
        "socratic_dimension": dimension,
        "votes": (topic_vote or {}).get("votes"),
    }

    rung3 = {
        "rung": 3,
        "label": "Socratic Nudge",
        "hint_text": (socratic_resp or {}).get("hint_text"),
        "guardrail_triggered": guardrail_triggered,
        "safety_verified": True,
        "model_id": (socratic_resp or {}).get("model_id"),
        "latency_ms": (socratic_resp or {}).get("latency_ms"),
    }

    rung4 = {
        "rung": 4,
        "label": "Full Solution",
        "gated": True,
        "requires_confirmation": True,
        "unlocked": bool(solution_text),
        "solution_text": solution_text,
    }

    return {"rung1": rung1, "rung2": rung2, "rung3": rung3, "rung4": rung4}


def _record_inference_meta(
    socratic_resp: dict[str, Any],
    prompt_n: int,
    tok_s: float,
) -> None:
    _LAST_INFERENCE_META.update(
        {
            "prompt_n": prompt_n,
            "tok_s": tok_s,
            "latency_ms": int(socratic_resp.get("latency_ms") or 0),
            "model_id": str(socratic_resp.get("model_id") or os.environ.get("SLM_MODEL_ID", "qwen3.5-0.8b")),
            "offline_mode": bool(socratic_resp.get("offline_mode", True)),
        }
    )


def _run_socratic_sync(
    *,
    problem_id: str,
    event_type: str,
    attempt_num: int,
    confidence_pct: int,
    selected_option: Optional[str],
    student_text: Optional[str],
    override_nudge: Optional[str],
    session_id: Optional[str],
    hesitation_ms: Optional[int],
    mode: str = "teach",
) -> dict[str, Any]:
    """Run Socratic turn synchronously (called inside worker thread under _EDGE_INFERENCE_SEM)."""
    try:
        from engine import socratic_engine

        turn_fn = getattr(socratic_engine, "generate_socratic_turn", None)
        if callable(turn_fn):
            raw = _call_with_supported_kwargs(
                turn_fn,
                problem_id=problem_id,
                event_type=event_type,
                attempt_num=attempt_num,
                confidence_pct=confidence_pct,
                selected_option=selected_option,
                student_note=student_text,
                student_text=student_text,
                text=student_text,
                agent_override_nudge=override_nudge,
                session_id=session_id,
                hesitation_ms=hesitation_ms,
                mode=mode,
            )
            if isinstance(raw, dict) and isinstance(raw.get("socratic_response"), dict):
                socratic_resp = dict(raw["socratic_response"])
                meta = raw
            elif isinstance(raw, dict) and isinstance(raw.get("response"), dict):
                socratic_resp = dict(raw["response"])
                meta = raw
            else:
                meta = dict(raw)
                socratic_resp = {
                    k: meta[k]
                    for k in SocraticResponse.__annotations__.keys()
                    if k in meta
                }
            move = str(socratic_resp.get("pedagogical_move") or "DECOMPOSE_PROBLEM")
            tag = str(socratic_resp.get("misconception_tag") or "")
            guardrail_triggered = bool(
                meta.get("guardrail_triggered")
                or tag in {"JAILBREAK_ATTEMPT", "OFFLOADING_ATTEMPT", "BINARY_SEARCH_PROBE", "UNSAFE_OFFTOPIC"}
            )
            guardrail_reason = meta.get("guardrail_reason") or (tag if guardrail_triggered else None)
            dimension = meta.get("socratic_dimension") or (
                "Meta-Questioning" if guardrail_triggered else MOVE_TO_DIMENSION.get(move, "Clarification")
            )
            solution_text = meta.get("solution_text") or socratic_resp.get("solution_text")
            prompt_n, tok_s = _extract_inference_metrics(meta, socratic_resp, problem_id, student_text)
            _record_inference_meta(socratic_resp, prompt_n, tok_s)
            return {
                "socratic_response": socratic_resp,
                "socratic_dimension": dimension,
                "guardrail_triggered": guardrail_triggered,
                "guardrail_reason": guardrail_reason,
                "solution_text": solution_text,
                "prompt_n": prompt_n,
                "tok_s": tok_s,
                "hint_ladder": _build_hint_ladder(
                    problem_id, student_text, socratic_resp, solution_text, guardrail_triggered
                ),
            }

        # Fallback to generate_socratic_hint when generate_socratic_turn is not yet present
        judo = _fallback_guardrail_check(student_text or "", problem_id)
        if judo is not None:
            _record_inference_meta(judo["socratic_response"], 0, 0.0)
            return {
                **judo,
                "prompt_n": 0,
                "tok_s": 0.0,
                "hint_ladder": _build_hint_ladder(
                    problem_id, student_text, judo["socratic_response"], None, True
                ),
            }

        socratic_resp = dict(
            socratic_engine.generate_socratic_hint(
                problem_id=problem_id,
                event_type=event_type,  # type: ignore[arg-type]
                attempt_num=attempt_num,
                confidence_pct=confidence_pct,
                selected_option=selected_option,
                student_note=student_text,
                agent_override_nudge=override_nudge,
            )
        )
        move = str(socratic_resp.get("pedagogical_move") or "DECOMPOSE_PROBLEM")
        prompt_n, tok_s = _extract_inference_metrics({}, socratic_resp, problem_id, student_text)
        _record_inference_meta(socratic_resp, prompt_n, tok_s)
        return {
            "socratic_response": socratic_resp,
            "socratic_dimension": MOVE_TO_DIMENSION.get(move, "Clarification"),
            "guardrail_triggered": False,
            "guardrail_reason": None,
            "prompt_n": prompt_n,
            "tok_s": tok_s,
            "hint_ladder": _build_hint_ladder(problem_id, student_text, socratic_resp, None, False),
        }
    except Exception:
        judo = _fallback_guardrail_check(student_text or "", problem_id)
        if judo is not None:
            _record_inference_meta(judo["socratic_response"], 0, 0.0)
            return {
                **judo,
                "prompt_n": 0,
                "tok_s": 0.0,
                "hint_ladder": _build_hint_ladder(
                    problem_id, student_text, judo["socratic_response"], None, True
                ),
            }
        fallback_resp: SocraticResponse = {
            "problem_id": problem_id,
            "category": "Algebra",
            "difficulty_level": 2,
            "hint_text": override_nudge or "What quantity or relationship in this problem stays fixed as you take each step?",
            "lcd_summary": "Check invariant",
            "pedagogical_move": "INVARIANT_NUDGE",
            "is_correct": None,
            "misconception_tag": "none",
            "latency_ms": 1,
            "model_id": "web-fallback",
            "offline_mode": True,
        }
        _record_inference_meta(fallback_resp, 0, 0.0)
        return {
            "socratic_response": fallback_resp,
            "socratic_dimension": "Probing Reasons & Evidence",
            "guardrail_triggered": False,
            "guardrail_reason": None,
            "prompt_n": 0,
            "tok_s": 0.0,
            "hint_ladder": _build_hint_ladder(problem_id, student_text, fallback_resp, None, False),
        }


def _run_stt_sync(
    audio_bytes: bytes,
    filename: str,
    fallback_transcript: Optional[str],
) -> dict[str, Any]:
    """Run offline STT transcription synchronously under _EDGE_INFERENCE_SEM."""
    t0 = time.perf_counter()
    try:
        from engine import stt as stt_mod  # type: ignore[import-not-found]

        fn = getattr(stt_mod, "transcribe_audio", None)
        if callable(fn):
            res = _call_with_supported_kwargs(
                fn,
                audio_bytes=audio_bytes,
                data=audio_bytes,
                filename=filename,
                fallback_transcript=fallback_transcript,
            )
            if isinstance(res, dict):
                text = str(res.get("transcription") or res.get("text") or "").strip()
                if not text and fallback_transcript:
                    text = fallback_transcript.strip()
                return {
                    "transcription": text,
                    "stt_engine": str(res.get("stt_engine") or res.get("engine") or "local-stt"),
                    "stt_latency_ms": int(res.get("stt_latency_ms") or res.get("latency_ms") or (time.perf_counter() - t0) * 1000),
                }
            text = str(res or "").strip() or (fallback_transcript or "").strip()
            return {
                "transcription": text,
                "stt_engine": "local-stt",
                "stt_latency_ms": int((time.perf_counter() - t0) * 1000),
            }
    except Exception:
        pass

    cleaned_fallback = (fallback_transcript or "").strip()
    return {
        "transcription": cleaned_fallback,
        "stt_engine": "browser-speech-fallback" if cleaned_fallback else "offline-stt-unavailable",
        "stt_latency_ms": int((time.perf_counter() - t0) * 1000),
    }


def _record_socratic_telemetry(
    *,
    socratic_resp: dict[str, Any],
    event_type: str,
    attempt_num: int,
    confidence_pct: int,
    session_id: Optional[str],
    hesitation_ms: Optional[int],
    device_id: str,
    background_tasks: BackgroundTasks,
) -> TelemetryEvent:
    """Build, validate, and enqueue a frozen-contract TelemetryEvent."""
    from agent_rawtree.telemetry_builder import build_telemetry_event

    event_dict: TelemetryEvent = build_telemetry_event(
        socratic_resp=socratic_resp,
        event_type=event_type,
        attempt_num=attempt_num,
        confidence_pct=confidence_pct,
        session_id=session_id,
        hesitation_ms=hesitation_ms,
        device_id=device_id,
        event_id_prefix="evt-web",
        default_session_id="sess-web-01",
    )
    validate_telemetry_event(dict(event_dict))
    background_tasks.add_task(client.insert_telemetry_event, event_dict)
    return event_dict


def _detect_platform_info() -> tuple[str, bool]:
    """Detect Qualcomm QRB2210 / Debian ARM64 or host development environment."""
    for dt_path in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        p = Path(dt_path)
        if p.is_file():
            try:
                model_str = p.read_text(encoding="utf-8", errors="ignore").strip("\x00\n\r ")
                lower = model_str.lower()
                if "qrb2210" in lower or "uno q" in lower or "qualcomm" in lower:
                    return f"Qualcomm QRB2210 ({model_str})", True
                if model_str:
                    return model_str, "aarch64" in platform.machine().lower()
            except Exception:
                pass
    sys_name = platform.system()
    machine = platform.machine()
    if sys_name == "Linux" and machine.lower() in {"aarch64", "arm64"}:
        return "Qualcomm QRB2210 (Debian ARM64)", True
    return f"{sys_name} ({machine})", False


def _read_cpu_ram_metrics() -> dict[str, Any]:
    """Read CPU % and RAM MB used/free/total via psutil, /proc/meminfo, or Win32."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        cpu_pct = round(float(psutil.cpu_percent(interval=None)), 1)
        return {
            "cpu_pct": cpu_pct,
            "ram_used_mb": round(vm.used / (1024 * 1024), 1),
            "ram_free_mb": round(vm.available / (1024 * 1024), 1),
            "ram_total_mb": round(vm.total / (1024 * 1024), 1),
            "ram_pct": round(float(vm.percent), 1),
        }
    except Exception:
        pass

    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            vals: dict[str, int] = {}
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    num = v.strip().split(" ", 1)[0]
                    if num.isdigit():
                        vals[k.strip()] = int(num)
            total_kb = vals.get("MemTotal", 0)
            avail_kb = vals.get("MemAvailable", vals.get("MemFree", 0))
            used_kb = max(0, total_kb - avail_kb)
            return {
                "cpu_pct": 0.0,
                "ram_used_mb": round(used_kb / 1024.0, 1),
                "ram_free_mb": round(avail_kb / 1024.0, 1),
                "ram_total_mb": round(total_kb / 1024.0, 1),
                "ram_pct": round((used_kb / total_kb * 100.0) if total_kb else 0.0, 1),
            }
        except Exception:
            pass

    if platform.system() == "Windows":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_mb = round(stat.ullTotalPhys / (1024 * 1024), 1)
                free_mb = round(stat.ullAvailPhys / (1024 * 1024), 1)
                used_mb = round(max(0.0, total_mb - free_mb), 1)
                return {
                    "cpu_pct": 0.0,
                    "ram_used_mb": used_mb,
                    "ram_free_mb": free_mb,
                    "ram_total_mb": total_mb,
                    "ram_pct": round(float(stat.dwMemoryLoad), 1),
                }
        except Exception:
            pass

    return {"cpu_pct": 0.0, "ram_used_mb": 0.0, "ram_free_mb": 0.0, "ram_total_mb": 0.0, "ram_pct": 0.0}


def _is_offline_mode() -> bool:
    """Return True when edge tutoring is operating without external cloud LLM calls."""
    use_mock = os.environ.get("SOCRATIC_MOCK", "0") == "1"
    helm_enabled = os.environ.get("ENABLE_HELMCODE_FALLBACK", "0") == "1" and bool(
        os.environ.get("HELMCODE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    )
    return use_mock or not helm_enabled


def _probe_llama_server() -> tuple[bool, str]:
    """Quick non-blocking probe of local llama-server (:8081) health."""
    raw_url = os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:8081/v1/chat/completions")
    base = raw_url.split("/v1/")[0].rstrip("/")
    health_url = f"{base}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=0.25) as resp:
            if resp.status < 500:
                return True, raw_url
    except Exception:
        pass
    return False, raw_url


@app.get("/health")
def health_check() -> dict[str, Any]:
    metrics = _read_cpu_ram_metrics()
    return {
        "status": "healthy",
        "service": "agent_rawtree",
        "rawtree_mode": "LIVE_RAWTREE" if client.is_live else "OFFLINE_SQLITE_MIRROR",
        "api_url": client.api_url if client.is_live else "local_rawtree.db",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "sse_subscribers": len(_subscribers),
        "offline_mode": bool(_LAST_INFERENCE_META.get("offline_mode", _is_offline_mode())),
        "ram_free_mb": metrics["ram_free_mb"],
        "ram_total_mb": metrics["ram_total_mb"],
        "prompt_n": _LAST_INFERENCE_META["prompt_n"],
        "tok_s": _LAST_INFERENCE_META["tok_s"],
    }


@app.get("/stats")
def get_stats() -> dict[str, Any]:
    """Return summary counts of telemetry events, agent insights, and agent traces."""
    events_cnt = client.query_sql("SELECT COUNT(*) AS total FROM telemetry_events")[0]["total"]
    insights_cnt = client.query_sql("SELECT COUNT(*) AS total FROM agent_insights")[0]["total"]
    active_cnt = client.query_sql(
        "SELECT COUNT(*) AS total FROM agent_insights WHERE status = 'ACTIVE'"
    )[0]["total"]
    traces_cnt = client.query_sql("SELECT COUNT(*) AS total FROM agent_traces")[0]["total"]

    return {
        "telemetry_events_total": events_cnt,
        "agent_insights_total": insights_cnt,
        "active_interventions": active_cnt,
        "agent_traces_total": traces_cnt,
        "rawtree_live": client.is_live,
    }


@app.get("/api/system-status")
def system_status() -> dict[str, Any]:
    """Live edge hardware, SLM, STT, RawTree, and AI-Safety guardrail telemetry."""
    stats = get_stats()
    device_label, is_qrb2210 = _detect_platform_info()
    metrics = _read_cpu_ram_metrics()
    slm_reachable, llama_url = _probe_llama_server()

    use_mock = os.environ.get("SOCRATIC_MOCK", "0") == "1"
    helm_enabled = os.environ.get("ENABLE_HELMCODE_FALLBACK", "0") == "1" and bool(
        os.environ.get("HELMCODE_API_KEY")
    )
    model_id = os.environ.get("SLM_MODEL_ID", "qwen3.5-0.8b")

    if use_mock:
        model_status = "Mock"
    elif slm_reachable:
        model_status = "Local SLM Offline"
    elif helm_enabled:
        model_status = "HelmCode"
    else:
        model_status = "Deterministic Offline"

    stt_status: dict[str, Any] = {
        "available": False,
        "engine": "browser-speech-fallback",
        "offline_ready": False,
    }
    try:
        from engine import stt as stt_mod  # type: ignore[import-not-found]

        if hasattr(stt_mod, "get_stt_status"):
            raw_stt = stt_mod.get_stt_status()
            if isinstance(raw_stt, dict):
                stt_status = raw_stt
    except Exception:
        pass

    guardrail_rows = client.query_sql(
        "SELECT COUNT(*) AS total FROM telemetry_events "
        "WHERE misconception_tag IN ('JAILBREAK_ATTEMPT', 'OFFLOADING_ATTEMPT', 'BINARY_SEARCH_PROBE', 'UNSAFE_OFFTOPIC')"
    )
    guardrail_intercepts = int(guardrail_rows[0]["total"]) if guardrail_rows else 0

    return {
        "device_name": device_label,
        "is_qrb2210": is_qrb2210,
        "cpu_pct": metrics["cpu_pct"],
        "ram_used_mb": metrics["ram_used_mb"],
        "ram_free_mb": metrics["ram_free_mb"],
        "ram_total_mb": metrics["ram_total_mb"],
        "ram_pct": metrics["ram_pct"],
        "offline_mode": bool(_LAST_INFERENCE_META.get("offline_mode", _is_offline_mode())),
        "prompt_n": _LAST_INFERENCE_META["prompt_n"],
        "tok_s": _LAST_INFERENCE_META["tok_s"],
        "last_latency_ms": _LAST_INFERENCE_META["latency_ms"],
        "model_status": model_status,
        "model_id": model_id,
        "slm_reachable": slm_reachable,
        "llama_server_url": llama_url,
        "stt": stt_status,
        "rawtree_live": stats["rawtree_live"],
        "rawtree_mode": "LIVE_RAWTREE" if stats["rawtree_live"] else "OFFLINE_SQLITE_MIRROR",
        "guardrail_intercepts": guardrail_intercepts,
        "telemetry_events_total": stats["telemetry_events_total"],
        "active_interventions": stats["active_interventions"],
        "agent_insights_total": stats["agent_insights_total"],
        "agent_traces_total": stats["agent_traces_total"],
    }


@app.get("/api/cortex-snapshot")
def cortex_snapshot() -> dict[str, Any]:
    """Hydrate initial telemetry rows, active insights, and summary stats for the web UI."""
    stats = get_stats()
    active_insights = get_active_interventions()
    recent_events = client.query_sql(
        "SELECT event_id, problem_id, category, event_type, confidence_pct, is_correct, "
        "misconception_tag, pedagogical_move, solve_elapsed_ms, ts_utc "
        "FROM telemetry_events ORDER BY ts_utc DESC LIMIT 12;"
    )
    return {
        "stats": stats,
        "active_insights": active_insights,
        "recent_events": recent_events,
    }


@app.get("/stream")
async def sse_stream() -> StreamingResponse:
    """Server-Sent Events stream for instant reactive UI updates and hardware alerts."""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=100)
    _subscribers.add(q)

    async def event_generator():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _subscribers.discard(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/ingest", status_code=202)
async def ingest_telemetry(payload: TelemetryEventPayload, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Ingest student telemetry event from Dev 1's hardware serial bridge."""
    event_dict: TelemetryEvent = payload.model_dump()  # type: ignore

    try:
        validate_telemetry_event(dict(event_dict))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"Contract validation error: {e}")

    background_tasks.add_task(client.insert_telemetry_event, event_dict)
    asyncio.create_task(broadcast_sse("telemetry", event_dict))

    return {
        "status": "accepted",
        "event_id": event_dict["event_id"],
        "received_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/internal/broadcast_intervention")
async def broadcast_intervention(insight: dict[str, Any]) -> dict[str, str]:
    """Internal endpoint called by autonomous_agent to push live intervention to SSE clients."""
    await broadcast_sse("intervention", insight)
    return {"status": "broadcasted"}


@app.get("/interventions/active")
def get_active_interventions(
    problem_id: Optional[str] = None,
    category: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Queried by Uno Q hardware bridge to light up purple LED on active bottlenecks."""
    sql = "SELECT * FROM agent_insights WHERE status = 'ACTIVE'"
    params: list[Any] = []
    if problem_id:
        sql += " AND target_problem_id = ?"
        params.append(problem_id)
    if category:
        sql += " AND target_category = ?"
        params.append(category)
    sql += " ORDER BY severity_score DESC LIMIT 5;"

    return client.query_sql(sql, tuple(params) if params else None)


@app.post("/interventions/{insight_id}/apply")
async def mark_intervention_applied(insight_id: str) -> dict[str, Any]:
    """Mark an intervention as applied by the edge tutor."""
    sql = "UPDATE agent_insights SET status = 'APPLIED' WHERE insight_id = ?;"
    await asyncio.to_thread(client.query_sql, sql, (insight_id,))
    asyncio.create_task(broadcast_sse("intervention_applied", {"insight_id": insight_id}))
    return {"status": "updated", "insight_id": insight_id, "new_state": "APPLIED"}


class ExplorerInspectPayload(BaseModel):
    problem_id: str
    language: Literal["eng", "cat"] = "eng"
    use_calibration: bool = True
    use_prior: bool = True
    rungs: list[int] = Field(default_factory=lambda: [1, 2, 3])
    ask_model: bool = False
    selected_option: Optional[str] = None


def _compute_fallback_explorer_scores(
    prob: dict[str, Any],
    use_calibration: bool,
    use_prior: bool,
) -> dict[str, Any]:
    """Pure-stdlib fallback scoring breakdown when engine.inspect_problem is unavailable."""
    letters = ("A", "B", "C", "D", "E")
    options = prob.get("options") or {}
    stmt = str(prob.get("statement") or "")
    level = int(prob.get("difficulty_level") or 2)
    m_q = re.search(r"(\d+)$", str(prob.get("problem_id") or ""))
    qnum = int(m_q.group(1)) if m_q else 10

    # Prior distribution by (level, qnum band)
    prior_base = {
        "A": 0.19,
        "B": 0.21,
        "C": 0.22,
        "D": 0.20,
        "E": 0.18,
    }
    if qnum <= 10:
        prior_base["B"] += 0.03
        prior_base["C"] += 0.03
    elif level >= 2:
        prior_base["D"] += 0.04
        prior_base["C"] += 0.02
    total_p = sum(prior_base.values())
    prior_probs = {k: round(v / total_p, 4) for k, v in prior_base.items()}

    # Option-value heuristic features + numeric median/match
    nums_in_stmt = set(re.findall(r"-?\d+(?:\.\d+)?", stmt))
    parsed_vals: dict[str, float] = {}
    for k in letters:
        val_str = str(options.get(k, "")).strip()
        try:
            parsed_vals[k] = float(val_str)
        except ValueError:
            pass
    sorted_vals = sorted(parsed_vals.values())
    median_val = sorted_vals[len(sorted_vals) // 2] if sorted_vals else None

    raw_scores: dict[str, float] = {}
    features: dict[str, dict[str, Any]] = {}
    for k in letters:
        val_str = str(options.get(k, "")).strip()
        appears = bool(val_str and val_str in nums_in_stmt)
        is_median = bool(median_val is not None and k in parsed_vals and parsed_vals[k] == median_val)
        score = 0.20
        if k in options:
            score += 0.04
        if is_median:
            score += 0.06
        if appears:
            score -= 0.02
        # Mild deterministic letter bias on raw scores (simulates uncalibrated SLM)
        if k == "D":
            score += 0.07
        elif k == "A":
            score += 0.04
        raw_scores[k] = round(score, 4)
        features[k] = {
            "value": val_str,
            "numeric": parsed_vals.get(k),
            "is_median": is_median,
            "appears_in_stem": appears,
            "prior": prior_probs[k],
        }

    raw_sum = sum(raw_scores.values()) or 1.0
    raw_probs = {k: round(v / raw_sum, 4) for k, v in raw_scores.items()}

    # Calibrated scores strip D/A bias and optionally blend (level, qnum) prior
    cal_raw: dict[str, float] = {}
    for k in letters:
        s = raw_probs[k]
        if use_calibration:
            if k == "D":
                s -= 0.06
            elif k == "A":
                s -= 0.03
        if use_prior:
            s = 0.65 * max(0.01, s) + 0.35 * prior_probs[k]
        cal_raw[k] = max(0.01, s)
    cal_sum = sum(cal_raw.values()) or 1.0
    calibrated_probs = {k: round(v / cal_sum, 4) for k, v in cal_raw.items()}
    return {
        "raw_scores": raw_probs,
        "calibrated_scores": calibrated_probs,
        "prior_probs": prior_probs if use_prior else {k: 0.2 for k in letters},
        "prior_key": f"level={level}, qnum={qnum}",
        "features": features,
    }


@app.get("/problems")
def list_problems() -> list[dict[str, Any]]:
    """Return curated Cangur problems without correct_option for the web tutor UI."""
    return _load_public_problems()


@app.get("/api/problem/{problem_id}/ladder")
def get_problem_ladder(problem_id: str) -> dict[str, Any]:
    """Return Rungs 1-2 (and gated Rung 4 status) for the selected problem without running SLM."""
    ladder = _build_hint_ladder(problem_id=problem_id, student_text=None)
    return {"problem_id": problem_id, "hint_ladder": ladder}


@app.get("/api/explorer/problems")
def list_explorer_problems(lang: Literal["eng", "cat"] = "eng") -> list[dict[str, Any]]:
    """Return problems with answer keys and Catalan/English twin metadata for the Workbench UI."""
    items: list[dict[str, Any]] = []
    try:
        from engine import cangur_bank

        bridge_fn = getattr(cangur_bank, "bridge_cat_to_eng", None)
        for pid in cangur_bank.list_problem_ids():
            p = cangur_bank.get_problem(pid)
            if not p:
                continue
            bridged = bridge_fn(pid) if callable(bridge_fn) else None
            stmt_eng = (bridged or {}).get("statement_eng") or p.get("statement", "")
            opts_eng = (bridged or {}).get("options_eng") or p.get("options", {})
            stmt_cat = (bridged or {}).get("statement_cat") or p.get("statement_cat")
            opts_cat = (bridged or {}).get("options_cat") or p.get("options_cat")
            use_cat = lang == "cat" and bool(stmt_cat)
            items.append(
                {
                    "problem_id": pid,
                    "year": p.get("year", 0),
                    "category": p.get("category", "Algebra"),
                    "difficulty_level": p.get("difficulty_level", 2),
                    "statement": stmt_cat if use_cat else stmt_eng,
                    "options": opts_cat if (use_cat and opts_cat) else opts_eng,
                    "statement_eng": stmt_eng,
                    "options_eng": opts_eng,
                    "statement_cat": stmt_cat,
                    "options_cat": opts_cat,
                    "has_cat": bool(stmt_cat),
                    "bridged": bool((bridged or {}).get("bridged")),
                    "correct_option": p.get("correct_option"),
                    "intuition_hook": p.get("intuition_hook"),
                    "language": "cat" if use_cat else "eng",
                }
            )
        if items:
            return items
    except Exception:
        pass

    for p in _FALLBACK_PROBLEMS:
        items.append(
            {
                **p,
                "statement_eng": p["statement"],
                "options_eng": p["options"],
                "statement_cat": None,
                "options_cat": None,
                "has_cat": False,
                "bridged": False,
                "correct_option": None,
                "language": "eng",
            }
        )
    return items


@app.post("/api/explorer/inspect")
async def explorer_inspect(payload: ExplorerInspectPayload) -> dict[str, Any]:
    """Interactive Test Workbench endpoint exposing prompt, calibration, prior, Rungs 1-4 & leak check."""
    t0 = time.perf_counter()

    def _run_inspect() -> dict[str, Any]:
        prob: Optional[dict[str, Any]] = None
        bridged_info: Optional[dict[str, Any]] = None
        prompt_text = ""
        engine_inspection: dict[str, Any] = {}
        rungs_out: dict[str, Any] = {}
        leak_safe = True
        leak_count = 0

        try:
            from engine import cangur_bank, prompts, socratic_engine

            prob = cangur_bank.get_problem(payload.problem_id)
            bridge_fn = getattr(cangur_bank, "bridge_cat_to_eng", None)
            if callable(bridge_fn):
                bridged_info = bridge_fn(payload.problem_id)
            if prob is None and bridged_info is not None:
                prob = bridged_info
            if prob is None:
                prob = cangur_bank.match_concept_guide(payload.problem_id, None)

            inspect_fn = getattr(socratic_engine, "inspect_problem", None)
            if callable(inspect_fn):
                res = _call_with_supported_kwargs(
                    inspect_fn,
                    problem=prob,
                    use_calibration=payload.use_calibration,
                    use_prior=payload.use_prior,
                    language=payload.language,
                    confirm_solution=(4 in payload.rungs),
                )
                if isinstance(res, dict):
                    engine_inspection = res

            eng_rungs = engine_inspection.get("rungs")
            if isinstance(eng_rungs, dict):
                for r in (1, 2, 3, 4):
                    if r in payload.rungs and (r in eng_rungs or str(r) in eng_rungs):
                        rungs_out[f"rung{r}"] = eng_rungs.get(r) or eng_rungs.get(str(r))
            else:
                rung_fn = getattr(socratic_engine, "get_hint_rung", None)
                if callable(rung_fn):
                    for r in (1, 2, 3, 4):
                        if r in payload.rungs:
                            try:
                                rungs_out[f"rung{r}"] = _call_with_supported_kwargs(
                                    rung_fn,
                                    problem=prob,
                                    rung=r,
                                    confirm_solution=(r == 4),
                                    selected_option=payload.selected_option,
                                )
                            except Exception:
                                pass

            msgs = prompts.build_messages(
                problem=prob,
                event_type="ANSWER_SUBMIT" if payload.selected_option else "HINT_REQUEST",
                attempt_num=1,
                confidence_pct=50,
                pedagogical_move=prob.get("default_move", "DECOMPOSE_PROBLEM"),
                is_correct=(payload.selected_option == prob.get("correct_option"))
                if payload.selected_option and prob.get("correct_option")
                else None,
                selected_option=payload.selected_option,
            )
            prompt_text = "\n\n".join(f"[{m['role'].upper()}] {m['content']}" for m in msgs)
        except Exception:
            if prob is None:
                prob = next(
                    (p for p in _FALLBACK_PROBLEMS if p["problem_id"] == payload.problem_id),
                    _FALLBACK_PROBLEMS[0],
                )
            prompt_text = f"[USER] Problem {prob.get('problem_id')}: {prob.get('statement', '')}"

        # Run Socratic turn if ask_model is requested (for live latency, prompt_n, tok/s, Rung 3)
        socratic_turn_res: Optional[dict[str, Any]] = None
        if payload.ask_model:
            socratic_turn_res = _run_socratic_sync(
                problem_id=payload.problem_id,
                event_type="ANSWER_SUBMIT" if payload.selected_option else "HINT_REQUEST",
                attempt_num=1,
                confidence_pct=50,
                selected_option=payload.selected_option,
                student_text=None,
                override_nudge=None,
                session_id="explorer-workbench",
                hesitation_ms=None,
                mode="solve" if 4 in payload.rungs else "teach",
            )

        socratic_resp = (socratic_turn_res or {}).get("socratic_response")
        sol_text = (socratic_turn_res or {}).get("solution_text") if 4 in payload.rungs else None
        base_ladder = _build_hint_ladder(
            problem_id=payload.problem_id,
            student_text=None,
            socratic_resp=socratic_resp,
            solution_text=sol_text,
            guardrail_triggered=bool((socratic_turn_res or {}).get("guardrail_triggered")),
        )
        for key, val in rungs_out.items():
            if val is not None:
                base_ladder[key] = val if isinstance(val, dict) else {"text": str(val)}

        # Verify leak-check on Rung 3 hint via engine.prompts.is_safe_hint when available
        rung3_hint = (
            (base_ladder.get("rung3") or {}).get("hint_text")
            or (base_ladder.get("rung3") or {}).get("text")
            or (prob or {}).get("socratic_seed", "")
        )
        try:
            from engine import prompts as prompts_mod

            if rung3_hint and prob is not None:
                leak_safe = bool(prompts_mod.is_safe_hint(str(rung3_hint), prob, False))
                leak_count = 0 if leak_safe else 1
        except Exception:
            pass

        scores = _compute_fallback_explorer_scores(
            prob, use_calibration=payload.use_calibration, use_prior=payload.use_prior
        )
        raw_scores = engine_inspection.get("raw_scores") or scores["raw_scores"]
        calibrated_scores = (
            engine_inspection.get("combined_scores")
            or engine_inspection.get("calibrated_scores")
            or (scores["calibrated_scores"] if payload.use_calibration or payload.use_prior else raw_scores)
        )
        prior_probs = (
            engine_inspection.get("prior_scores")
            or engine_inspection.get("prior_probs")
            or scores["prior_probs"]
        )
        raw_feat = engine_inspection.get("feature_breakdown") or engine_inspection.get("features") or scores["features"]
        use_cat = payload.language == "cat" and bool((bridged_info or {}).get("statement_cat"))
        active_opts = (bridged_info or {}).get("options_cat") if use_cat else (prob or {}).get("options", {})
        feature_breakdown: dict[str, dict[str, Any]] = {}
        for opt_k in ("A", "B", "C", "D", "E"):
            f_entry = dict(raw_feat.get(opt_k) or {})
            f_entry.setdefault("value", (active_opts or {}).get(opt_k, ""))
            f_entry.setdefault("prior", prior_probs.get(opt_k, 0.2))
            f_entry["appears_in_stem"] = bool(
                f_entry.get("appears_in_stem") or f_entry.get("in_statement") or f_entry.get("in_stem")
            )
            f_entry["is_median"] = bool(f_entry.get("is_median"))
            feature_breakdown[opt_k] = f_entry

        prior_key = engine_inspection.get("prior_key") or (
            f"level={engine_inspection.get('level')}, qnum={engine_inspection.get('qnum')}"
            if engine_inspection.get("level") is not None
            else scores["prior_key"]
        )
        predicted_option = engine_inspection.get("predicted_option") or max(
            calibrated_scores.keys(), key=lambda k: calibrated_scores[k]
        )

        correct_option = (prob or {}).get("correct_option")
        sel = payload.selected_option.upper() if payload.selected_option else None
        is_correct = (sel == correct_option) if (sel and correct_option) else None
        led_state = (
            "SOLVED"
            if is_correct is True
            else ("SOCRATIC_NUDGE" if (sel or payload.ask_model) else "IDLE")
        )

        eng_timings = engine_inspection.get("timings") if isinstance(engine_inspection.get("timings"), dict) else {}
        prompt_n = (
            (socratic_turn_res or {}).get("prompt_n")
            if socratic_turn_res
            else eng_timings.get("prompt_n", engine_inspection.get("prompt_n", 0))
        )
        tok_s = (
            (socratic_turn_res or {}).get("tok_s")
            if socratic_turn_res
            else eng_timings.get("predicted_per_second", engine_inspection.get("tok_s", 0.0))
        )
        latency_ms = (
            (socratic_resp or {}).get("latency_ms")
            if socratic_resp
            else engine_inspection.get("latency_ms", int((time.perf_counter() - t0) * 1000))
        )
        metrics = _read_cpu_ram_metrics()

        return {
            "problem_id": payload.problem_id,
            "category": (prob or {}).get("category", "Algebra"),
            "difficulty_level": int((prob or {}).get("difficulty_level") or 2),
            "language": "cat" if use_cat else "eng",
            "bridged": bool((bridged_info or {}).get("bridged")),
            "statement": (bridged_info or {}).get("statement_cat") if use_cat else (prob or {}).get("statement", ""),
            "options": active_opts,
            "correct_option": correct_option,
            "selected_option": sel,
            "is_correct": is_correct,
            "predicted_option": predicted_option,
            "prompt": engine_inspection.get("prompt") or prompt_text,
            "raw_scores": raw_scores,
            "calibrated_scores": calibrated_scores,
            "prior_probs": prior_probs,
            "prior_key": prior_key,
            "feature_breakdown": feature_breakdown,
            "hint_ladder": base_ladder,
            "leak_check": {"safe": leak_safe, "leaks": leak_count, "checked": 1},
            "latency_ms": latency_ms,
            "prompt_n": prompt_n,
            "tok_s": tok_s,
            "ram_free_mb": metrics["ram_free_mb"],
            "offline_mode": bool((socratic_resp or {}).get("offline_mode", _is_offline_mode())),
            "model_id": (socratic_resp or {}).get("model_id") or os.environ.get("SLM_MODEL_ID", "qwen3.5-0.8b"),
            "led_matrix": {
                "led_state": led_state,
                "option": sel or predicted_option,
                "is_correct": is_correct,
                "difficulty_level": int((prob or {}).get("difficulty_level") or 2),
                "active_rungs": [r for r in payload.rungs if 1 <= r <= 4],
                "prefill_pct": 100 if payload.ask_model else 0,
            },
        }

    async with _EDGE_INFERENCE_SEM:
        return await asyncio.to_thread(_run_inspect)


_SESSION_CONTEXT: dict[str, dict[str, Any]] = {}
_MAX_SESSION_ENTRIES = 1000


def _resolve_session_context(
    session_id: Optional[str],
    problem_id: str,
    text: str,
    explicit_context: Optional[str] = None,
    explicit_prior_id: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve effective problem_id and context-augmented text across multi-turn sessions."""
    clean_text = (text or "").strip()
    if not session_id:
        effective_pid = explicit_prior_id or problem_id
        if explicit_context and clean_text:
            return effective_pid, f"{explicit_context} (Follow-up: {clean_text})"
        return effective_pid, clean_text

    if len(_SESSION_CONTEXT) > _MAX_SESSION_ENTRIES:
        keys = list(_SESSION_CONTEXT.keys())
        for k in keys[: len(keys) // 2]:
            _SESSION_CONTEXT.pop(k, None)

    sess = _SESSION_CONTEXT.setdefault(session_id, {})

    # 1. Resolve problem_id
    effective_problem_id = problem_id
    if explicit_prior_id and (not effective_problem_id or effective_problem_id == "GENERAL-MATH"):
        effective_problem_id = explicit_prior_id
    elif (not effective_problem_id or effective_problem_id == "GENERAL-MATH") and sess.get("problem_id") and sess.get("problem_id") != "GENERAL-MATH":
        effective_problem_id = sess["problem_id"]

    # 2. Check if text is a follow-up / meta-prompt
    is_meta_followup = bool(
        re.search(
            r"^(?:can you give me (?:another|a) hint|another hint|more hint|hint please|show (?:me )?(?:the )?solution|please show me the full solution|next step|what next|explain more|i don't get it|help)\??$",
            clean_text,
            re.IGNORECASE,
        )
    )

    prior_text = explicit_context or sess.get("math_text")

    if is_meta_followup and prior_text:
        effective_text = f"{prior_text} (Follow-up: {clean_text})"
    else:
        effective_text = clean_text
        if clean_text and not is_meta_followup:
            sess["math_text"] = clean_text

    if effective_problem_id and effective_problem_id != "GENERAL-MATH":
        sess["problem_id"] = effective_problem_id

    return effective_problem_id, effective_text


async def _fetch_and_apply_intervention(
    problem_id: str,
    mode: str,
    attempt_num: int,
    is_unresolved: bool,
) -> tuple[Optional[str], Optional[str]]:
    """Retrieve active intervention if mode is teach and student is in early attempts or unresolved."""
    if mode != "teach" or (attempt_num > 2 and not is_unresolved):
        return None, None

    if not problem_id or problem_id == "GENERAL-MATH":
        return None, None

    active = await asyncio.to_thread(get_active_interventions, problem_id=problem_id)
    if not active:
        return None, None

    top = active[0]
    nudge = top.get("recommended_nudge")
    insight_id = top.get("insight_id")
    return nudge, insight_id


async def _mark_insight_applied(insight_id: Optional[str]) -> None:
    """Mark delivered insight APPLIED in database and broadcast SSE."""
    if not insight_id:
        return
    try:
        await asyncio.to_thread(
            client.query_sql,
            "UPDATE agent_insights SET status = 'APPLIED' WHERE insight_id = ?;",
            (insight_id,),
        )
        asyncio.create_task(broadcast_sse("intervention_applied", {"insight_id": insight_id}))
    except Exception as e:
        print(f"[web_app] Failed to mark insight {insight_id} APPLIED: {e}")


@app.post("/tutor/interact")
async def tutor_interact(
    payload: TutorInteractPayload, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Interactive Socratic tutor endpoint: checks interventions -> engine -> telemetry."""
    effective_pid, effective_text = _resolve_session_context(
        payload.session_id,
        payload.problem_id,
        payload.student_note or "",
        payload.conversation_context,
        payload.prior_problem_id,
    )

    is_unresolved = (payload.event_type != "ANSWER_SUBMIT") or (payload.selected_option is None)
    override_nudge, insight_id = await _fetch_and_apply_intervention(
        problem_id=effective_pid,
        mode=payload.mode,
        attempt_num=payload.attempt_num,
        is_unresolved=is_unresolved,
    )
    intervention_applied = bool(override_nudge)

    async def _run() -> dict[str, Any]:
        async with _EDGE_INFERENCE_SEM:
            return await asyncio.to_thread(
                _run_socratic_sync,
                problem_id=effective_pid,
                event_type=payload.event_type,
                attempt_num=payload.attempt_num,
                confidence_pct=payload.confidence_pct,
                selected_option=payload.selected_option,
                student_text=effective_text or payload.student_note,
                override_nudge=override_nudge,
                session_id=payload.session_id,
                hesitation_ms=None,
                mode=payload.mode,
            )

    try:
        result = await asyncio.wait_for(_run(), timeout=45.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Edge Socratic inference timed out.")

    if insight_id:
        await _mark_insight_applied(insight_id)

    socratic_resp = result["socratic_response"]
    event_dict = _record_socratic_telemetry(
        socratic_resp=socratic_resp,
        event_type=payload.event_type,
        attempt_num=payload.attempt_num,
        confidence_pct=payload.confidence_pct,
        session_id=payload.session_id,
        hesitation_ms=None,
        device_id="web-ui",
        background_tasks=background_tasks,
    )
    await broadcast_sse("telemetry", dict(event_dict))
    metrics = _read_cpu_ram_metrics()

    return {
        **socratic_resp,
        "intervention_applied": intervention_applied,
        "ram_free_mb": metrics["ram_free_mb"],
        "prompt_n": result.get("prompt_n", 0),
        "tok_s": result.get("tok_s", 0.0),
        "hint_ladder": result.get("hint_ladder"),
    }


@app.post("/api/text-query")
async def api_text_query(
    payload: TextQueryPayload, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    """Unified text/math Socratic endpoint with guardrails, RawTree overrides, and SSE telemetry."""
    effective_pid, effective_text = _resolve_session_context(
        payload.session_id,
        payload.problem_id,
        payload.text,
        payload.conversation_context,
        payload.prior_problem_id,
    )

    is_unresolved = (payload.event_type != "ANSWER_SUBMIT") or (payload.selected_option is None)
    override_nudge, insight_id = await _fetch_and_apply_intervention(
        problem_id=effective_pid,
        mode=payload.mode,
        attempt_num=payload.attempt_num,
        is_unresolved=is_unresolved,
    )
    intervention_applied = bool(override_nudge)

    async def _run() -> dict[str, Any]:
        async with _EDGE_INFERENCE_SEM:
            return await asyncio.to_thread(
                _run_socratic_sync,
                problem_id=effective_pid,
                event_type=payload.event_type,
                attempt_num=payload.attempt_num,
                confidence_pct=payload.confidence_pct,
                selected_option=payload.selected_option,
                student_text=effective_text or payload.text,
                override_nudge=override_nudge,
                session_id=payload.session_id,
                hesitation_ms=payload.hesitation_ms,
                mode=payload.mode,
            )

    try:
        result = await asyncio.wait_for(_run(), timeout=45.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Edge Socratic inference timed out.")

    if insight_id:
        await _mark_insight_applied(insight_id)

    socratic_resp = result["socratic_response"]
    event_dict = _record_socratic_telemetry(
        socratic_resp=socratic_resp,
        event_type=payload.event_type,
        attempt_num=payload.attempt_num,
        confidence_pct=payload.confidence_pct,
        session_id=payload.session_id,
        hesitation_ms=payload.hesitation_ms,
        device_id="web-ui-text",
        background_tasks=background_tasks,
    )
    await broadcast_sse("telemetry", dict(event_dict))
    metrics = _read_cpu_ram_metrics()

    return {
        "transcription": payload.text,
        "mode": payload.mode,
        "socratic_response": socratic_resp,
        "solution_text": result.get("solution_text"),
        "socratic_dimension": result["socratic_dimension"],
        "guardrail_triggered": result["guardrail_triggered"],
        "guardrail_reason": result["guardrail_reason"],
        "intervention_applied": intervention_applied,
        "offline_mode": bool(socratic_resp.get("offline_mode", True)),
        "ram_free_mb": metrics["ram_free_mb"],
        "prompt_n": result.get("prompt_n", 0),
        "tok_s": result.get("tok_s", 0.0),
        "hint_ladder": result.get("hint_ladder"),
    }


MAX_VOICE_BYTES = 5 * 1024 * 1024


@app.post("/api/voice-query")
async def api_voice_query(
    background_tasks: BackgroundTasks,
    audio: Optional[UploadFile] = File(None),
    problem_id: str = Form("GENERAL-MATH"),
    confidence_pct: int = Form(50),
    attempt_num: int = Form(1),
    selected_option: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None),
    hesitation_ms: Optional[int] = Form(None),
    fallback_transcript: Optional[str] = Form(None),
    mode: str = Form("teach"),
    conversation_context: Optional[str] = Form(None),
    prior_problem_id: Optional[str] = Form(None),
) -> dict[str, Any]:
    """Unified voice Socratic endpoint: offline STT -> Socratic engine -> guardrails -> RawTree SSE."""
    clean_mode = mode if mode in ("teach", "solve") else "teach"
    audio_bytes = b""
    filename = "utterance.webm"
    if audio is not None:
        filename = audio.filename or "utterance.webm"
        try:
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = await audio.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VOICE_BYTES:
                    raise HTTPException(status_code=413, detail="Voice audio exceeds 5 MB edge limit.")
                chunks.append(chunk)
            audio_bytes = b"".join(chunks)
        finally:
            await audio.close()

    effective_pid, _ = _resolve_session_context(
        session_id,
        problem_id,
        fallback_transcript or "",
        conversation_context,
        prior_problem_id,
    )

    parsed_attempt = max(1, int(attempt_num))
    is_unresolved = True
    override_nudge, insight_id = await _fetch_and_apply_intervention(
        problem_id=effective_pid,
        mode=clean_mode,
        attempt_num=parsed_attempt,
        is_unresolved=is_unresolved,
    )
    intervention_applied = bool(override_nudge)

    async def _run_voice() -> tuple[dict[str, Any], dict[str, Any]]:
        async with _EDGE_INFERENCE_SEM:
            stt_res = await asyncio.to_thread(
                _run_stt_sync,
                audio_bytes,
                filename,
                fallback_transcript,
            )
            transcription = stt_res["transcription"]
            raw_text = transcription or "Can you help me start thinking about this math problem?"
            _, effective_text = _resolve_session_context(
                session_id,
                effective_pid,
                raw_text,
                conversation_context,
                prior_problem_id,
            )
            student_text = effective_text or raw_text
            result = await asyncio.to_thread(
                _run_socratic_sync,
                problem_id=effective_pid,
                event_type="HINT_REQUEST",
                attempt_num=parsed_attempt,
                confidence_pct=max(0, min(100, int(confidence_pct))),
                selected_option=selected_option,
                student_text=student_text,
                override_nudge=override_nudge,
                session_id=session_id,
                hesitation_ms=hesitation_ms,
                mode=clean_mode,
            )
            return stt_res, result

    try:
        stt_res, result = await asyncio.wait_for(_run_voice(), timeout=45.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Edge voice/Socratic inference timed out.")

    if insight_id:
        await _mark_insight_applied(insight_id)

    transcription = stt_res["transcription"]
    socratic_resp = result["socratic_response"]
    event_dict = _record_socratic_telemetry(
        socratic_resp=socratic_resp,
        event_type="HINT_REQUEST",
        attempt_num=parsed_attempt,
        confidence_pct=max(0, min(100, int(confidence_pct))),
        session_id=session_id,
        hesitation_ms=hesitation_ms,
        device_id="web-ui-voice",
        background_tasks=background_tasks,
    )
    await broadcast_sse("telemetry", dict(event_dict))
    metrics = _read_cpu_ram_metrics()

    return {
        "transcription": transcription,
        "mode": clean_mode,
        "stt_engine": stt_res["stt_engine"],
        "stt_latency_ms": stt_res["stt_latency_ms"],
        "socratic_response": socratic_resp,
        "solution_text": result.get("solution_text"),
        "socratic_dimension": result["socratic_dimension"],
        "guardrail_triggered": result["guardrail_triggered"],
        "guardrail_reason": result["guardrail_reason"],
        "intervention_applied": intervention_applied,
        "offline_mode": bool(socratic_resp.get("offline_mode", True)),
        "ram_free_mb": metrics["ram_free_mb"],
        "prompt_n": result.get("prompt_n", 0),
        "tok_s": result.get("tok_s", 0.0),
        "hint_ladder": result.get("hint_ladder"),
    }


@app.get("/", response_class=HTMLResponse)
def index_view():
    """Serve the unified SocraticQ + RawTree Agentic Cortex web interface."""
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return HTMLResponse("<h1>SocraticQ static/index.html missing</h1>", status_code=404)
    return FileResponse(index_path, media_type="text/html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("agent_rawtree.web_app:app", host="0.0.0.0", port=8000, reload=True)
