"""Zero-dependency SocraticQ chat server for the Arduino UNO Q (stdlib only).

Serves the chat UI in ``static/`` and implements the JSON contract for:

    GET  /                          -> static/index.html (chat UI)
    GET  /static/<file>             -> static assets
    GET  /problems                  -> Cangur bank (EN + CAT fields, no answer keys)
    GET  /api/system-status         -> model / RAM / prompt_n / tok_s / whisper HUD
    GET  /api/problem/<pid>/ladder  -> 4-Rung Hint Ladder via socratic_engine.get_hint_rung
    POST /api/text-query            -> engine.socratic_engine.generate_socratic_turn
    POST /api/voice-query           -> engine.stt + generate_socratic_turn
    POST /api/ocr                   -> engine.ocr_engine.ocr_problem_from_image

Run:  python3 chat_server.py [--port 8765]
"""

from __future__ import annotations

import argparse
import email.policy
import inspect
import json
import math
import mimetypes
import os
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from engine import cangur_bank, socratic_engine, stt

try:
    from engine import ocr_engine
except ImportError:
    ocr_engine = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
_INFER_LOCK = threading.Lock()  # one SLM/STT call at a time on the QRB2210
_SESSIONS_LOCK = threading.Lock()
_SESSIONS: dict[str, list[dict[str, str]]] = {}
_MAX_SESSIONS = 100
_MAX_HISTORY_MSGS = 12  # 6 user/assistant pairs

_LAST: dict[str, Any] = {
    "latency_ms": 0,
    "model_id": os.environ.get("SLM_MODEL_ID", "qwen3.5-0.8b"),
    "prompt_n": 0,
    "tok_s": 0.0,
}


def _normalize_lang(lang: str | None) -> str:
    raw = (lang or "").strip().lower()
    if raw in ("cat", "ca", "ca-es", "catalan", "català"):
        return "cat"
    return "eng"


def _problems(lang: str | None = None) -> list[dict[str, Any]]:
    norm_lang = _normalize_lang(lang) if lang else None
    if hasattr(cangur_bank, "get_public_problems"):
        try:
            items = cangur_bank.get_public_problems(language=norm_lang or "eng")
            if isinstance(items, list):
                return items
        except Exception:
            pass

    out: list[dict[str, Any]] = []
    for pid in cangur_bank.list_problem_ids():
        p = cangur_bank.get_problem(pid)
        if not p:
            continue
        item: dict[str, Any] = {
            k: p[k]
            for k in ("problem_id", "year", "category", "difficulty_level", "statement", "options")
            if k in p
        }
        if p.get("statement_cat"):
            item["statement_cat"] = p["statement_cat"]
            item["options_cat"] = p.get("options_cat") or p.get("options") or {}
            if norm_lang == "cat":
                item["statement"] = p["statement_cat"]
                item["options"] = item["options_cat"]
        out.append(item)
    return out


def _mem() -> dict[str, int]:
    info: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0]) // 1024
    except OSError:
        pass
    return {"ram_total_mb": info.get("MemTotal", 0), "ram_free_mb": info.get("MemAvailable", 0)}


def _slm_up() -> bool:
    url = os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:8081").rstrip("/")
    url = url.split("/v1")[0].split("/completion")[0]
    try:
        with urllib.request.urlopen(url + "/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _status() -> dict[str, Any]:
    up = _slm_up()
    return {
        "device_name": "Arduino UNO Q (QRB2210)" if Path("/sys/firmware/devicetree").exists() else "local",
        "model_id": _LAST["model_id"],
        "slm_reachable": up,
        "model_status": "Local SLM Offline" if up else "Deterministic Offline",
        "offline_mode": True,
        "whisper_available": stt.is_whisper_available(),
        "last_latency_ms": _LAST["latency_ms"],
        "prompt_n": _LAST["prompt_n"],
        "tok_s": _LAST["tok_s"],
        **_mem(),
    }


def _build_hint_ladder(
    pid: str,
    mode: str = "teach",
    solution_text: str | None = None,
    hint_text: str | None = None,
    model_id: str | None = None,
    latency_ms: int | None = None,
) -> dict[str, Any]:
    """Assemble the 4-rung hint ladder dict expected by the UI."""
    ladder: dict[str, Any] = {}
    if hasattr(socratic_engine, "get_hint_rung") and pid not in ("FREE-MATH", "GENERAL-MATH", ""):
        try:
            r1 = socratic_engine.get_hint_rung(pid, 1)
            nb = r1.get("neighbor")
            ladder["rung1"] = {
                "available": bool(nb),
                "problem_id": nb.get("problem_id") if nb else None,
                "category": nb.get("category") if nb else None,
                "difficulty_level": nb.get("difficulty_level") if nb else None,
                "statement": (nb.get("statement_cat") or nb.get("statement")) if nb else None,
                "intuition_hook": (nb.get("intuition_hook") if nb else None) or r1.get("hint_text", ""),
            }
        except Exception:
            ladder["rung1"] = {"available": False, "intuition_hook": "Try a smaller concrete case first."}

        try:
            r2 = socratic_engine.get_hint_rung(pid, 2)
            ti = r2.get("topic_inference") or {}
            ladder["rung2"] = {
                "topic": ti.get("topic", "Math"),
                "strategy": ti.get("strategy_label") or r2.get("hint_text", ""),
                "pedagogical_move": ti.get("default_move", "DECOMPOSE_PROBLEM"),
                "socratic_dimension": "Concept Mapping",
                "votes": ti.get("votes"),
            }
        except Exception:
            ladder["rung2"] = {"topic": "Math", "strategy": "Break into smaller steps", "pedagogical_move": "DECOMPOSE_PROBLEM"}
    else:
        ladder["rung1"] = {"available": False, "intuition_hook": "Test a smaller case or plug in simple numbers."}
        ladder["rung2"] = {"topic": "General Math", "strategy": "Identify knowns, unknowns, and constraints.", "pedagogical_move": "DECOMPOSE_PROBLEM"}

    ladder["rung3"] = {
        "hint_text": hint_text or "",
        "model_id": model_id or _LAST["model_id"],
        "latency_ms": latency_ms,
    }
    ladder["rung4"] = {
        "gated": mode != "solve",
        "solution_text": solution_text or "",
    }
    return ladder


def _get_session_history(session_id: str | None) -> list[dict[str, str]]:
    if not session_id:
        return []
    with _SESSIONS_LOCK:
        return list(_SESSIONS.get(session_id, []))


def _append_session_history(session_id: str | None, user_text: str, assistant_text: str) -> None:
    if not session_id or (not user_text and not assistant_text):
        return
    with _SESSIONS_LOCK:
        if session_id not in _SESSIONS and len(_SESSIONS) >= _MAX_SESSIONS:
            oldest = next(iter(_SESSIONS))
            _SESSIONS.pop(oldest, None)
        hist = _SESSIONS.setdefault(session_id, [])
        if user_text:
            hist.append({"role": "user", "content": user_text[:500]})
        if assistant_text:
            hist.append({"role": "assistant", "content": assistant_text[:500]})
        if len(hist) > _MAX_HISTORY_MSGS:
            _SESSIONS[session_id] = hist[-_MAX_HISTORY_MSGS:]


def _call_socratic_turn(**kwargs: Any) -> dict[str, Any]:
    """Invoke socratic_engine.generate_socratic_turn, passing only supported kwargs."""
    sig = inspect.signature(socratic_engine.generate_socratic_turn)
    params = sig.parameters
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    filtered = kwargs if accepts_var_kw else {k: v for k, v in kwargs.items() if k in params}
    return socratic_engine.generate_socratic_turn(**filtered)


# ---------------------------------------------------------------------------
# RawTree closed-loop hooks (Side Challenge 1) — lazy, guarded, never blocking.
# ---------------------------------------------------------------------------

_MIRROR: dict[str, Any] = {"client": None, "tried": False}
_AUTO_CYCLE_LOCK = threading.Lock()
_AUTO_CYCLE_STATE: dict[str, Any] = {"turns": 0, "running": False}


def _get_mirror() -> Any | None:
    """Lazy RawTreeClient bound to the local SQLite mirror (stdlib-only).

    Mirrors the hardware TelemetrySpool pattern: board-local, WAL, zero network.
    """
    if not _MIRROR["tried"]:
        _MIRROR["tried"] = True
        try:
            from agent_rawtree.rawtree_client import RawTreeClient

            _MIRROR["client"] = RawTreeClient(force_offline=True)
        except Exception as exc:  # offline degradation path
            print(f"[chat] RawTree mirror unavailable: {exc}")
    return _MIRROR["client"]


def _fetch_active_intervention(problem_id: str) -> dict[str, Any] | None:
    """Highest-severity ACTIVE/APPLIED intervention for the problem (or category)."""
    cli = _get_mirror()
    if cli is None or not problem_id or problem_id in ("FREE-MATH", "GENERAL-MATH", ""):
        return None
    try:
        rows = cli.query_sql(
            "SELECT insight_id, recommended_nudge FROM agent_insights "
            "WHERE status IN ('ACTIVE', 'APPLIED') AND target_problem_id = ? "
            "ORDER BY severity_score DESC, created_at_utc DESC LIMIT 1;",
            (problem_id,),
        )
        if rows:
            return rows[0]
        prob = cangur_bank.get_problem(problem_id) or {}
        cat = str(prob.get("category") or "")
        if cat:
            rows = cli.query_sql(
                "SELECT insight_id, recommended_nudge FROM agent_insights "
                "WHERE status IN ('ACTIVE', 'APPLIED') AND target_problem_id IS NULL "
                "AND target_category = ? "
                "ORDER BY severity_score DESC, created_at_utc DESC LIMIT 1;",
                (cat,),
            )
            if rows:
                return rows[0]
    except Exception as exc:
        print(f"[chat] intervention fetch failed: {exc}")
    return None


def _mark_insight_applied(insight_id: str) -> None:
    cli = _get_mirror()
    if cli is None or not insight_id:
        return
    try:
        cli.query_sql("UPDATE agent_insights SET status = 'APPLIED' WHERE insight_id = ?;", (insight_id,))
    except Exception as exc:
        print(f"[chat] mark-applied failed: {exc}")


def _emit_telemetry(event: dict[str, Any]) -> None:
    """Emit one TelemetryEvent: POST /ingest when configured, else local mirror."""
    mode = (os.environ.get("CHAT_TELEMETRY_MODE") or "mirror").strip().lower()
    if mode == "off":
        return
    ingest_url = (os.environ.get("INGEST_URL") or "").strip()
    if mode in ("ingest", "auto") and ingest_url:
        try:
            req = urllib.request.Request(
                ingest_url,
                data=json.dumps(event).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=2.0).read()
            return
        except Exception as exc:
            print(f"[chat] ingest POST failed ({exc}); using local mirror.")
    cli = _get_mirror()
    if cli is None:
        return
    try:
        from contracts.schemas import validate_telemetry_event

        validate_telemetry_event(dict(event))
        cli.insert_telemetry_event(event)
    except Exception as exc:
        print(f"[chat] mirror telemetry failed: {exc}")


def _run_rawtree_agent_cycle_once() -> dict[str, Any]:
    """Run one diagnostic cycle of AutonomousRawTreeAgent in offline/mock mode."""
    cli = _get_mirror()
    if cli is None:
        return {"ok": False, "error": "RawTree mirror unavailable"}
    from agent_rawtree.autonomous_agent import AutonomousRawTreeAgent

    agent = AutonomousRawTreeAgent(client=cli, force_heuristic=True)
    insight = agent.run_cycle()
    return {"ok": True, "insight": dict(insight) if insight else None}


def _maybe_run_auto_cycle() -> None:
    """Every 3 student turns, trigger one background RawTree diagnostic cycle."""
    should_run = False
    with _AUTO_CYCLE_LOCK:
        _AUTO_CYCLE_STATE["turns"] += 1
        if _AUTO_CYCLE_STATE["turns"] % 3 == 0 and not _AUTO_CYCLE_STATE["running"]:
            _AUTO_CYCLE_STATE["running"] = True
            should_run = True
    if not should_run:
        return

    def _worker() -> None:
        try:
            _run_rawtree_agent_cycle_once()
        except Exception as exc:
            print(f"[chat] background RawTree cycle failed: {exc}")
        finally:
            with _AUTO_CYCLE_LOCK:
                _AUTO_CYCLE_STATE["running"] = False

    threading.Thread(target=_worker, daemon=True).start()


def _text_query(body: dict[str, Any]) -> dict[str, Any]:
    mode = body.get("mode") if body.get("mode") in ("teach", "solve") else "teach"
    lang = _normalize_lang(body.get("language"))
    session_id = str(body.get("session_id") or "").strip() or None
    problem_id = str(body.get("problem_id") or "GENERAL-MATH")
    user_text = str(body.get("text") or "").strip()
    prior_history = _get_session_history(session_id)

    # Closed loop (Side Challenge 1): apply the RawTree agent's latest active
    # intervention as an engine override — teach mode ONLY, so solve-mode
    # answers stay bit-identical (zero accuracy regression).
    override_nudge: str | None = None
    override_insight_id: str | None = None
    if mode == "teach":
        intr = _fetch_active_intervention(problem_id)
        if intr:
            override_nudge = str(intr.get("recommended_nudge") or "").strip()[:240] or None
            override_insight_id = str(intr.get("insight_id") or "") or None

    with _INFER_LOCK:
        raw = _call_socratic_turn(
            problem_id=problem_id,
            event_type=body.get("event_type") or "HINT_REQUEST",
            attempt_num=max(1, int(body.get("attempt_num") or 1)),
            confidence_pct=max(0, min(100, int(body.get("confidence_pct") or 50))),
            selected_option=body.get("selected_option"),
            student_note=user_text or None,
            agent_override_nudge=override_nudge,
            mode=mode,
            language=lang,
            history=prior_history,
        )

    meta_keys = {
        "socratic_dimension",
        "guardrail_triggered",
        "guardrail_reason",
        "mode",
        "rag_example_id",
        "solution_text",
        "final_answer",
        "prompt_n",
        "tok_s",
    }
    resp = {k: v for k, v in raw.items() if k not in meta_keys}
    prompt_n = int(raw.get("prompt_n") or 0)
    tok_s = float(raw.get("tok_s") or 0.0)
    latency_ms = int(resp.get("latency_ms") or 0)
    model_id = str(resp.get("model_id") or _LAST["model_id"])

    _LAST.update(latency_ms=latency_ms, model_id=model_id, prompt_n=prompt_n, tok_s=tok_s)

    assistant_reply = (
        str(raw.get("solution_text") or resp.get("hint_text") or "")
        if mode == "solve"
        else str(resp.get("hint_text") or "")
    )
    _append_session_history(session_id, user_text, assistant_reply)

    ladder = _build_hint_ladder(
        problem_id,
        mode=mode,
        solution_text=raw.get("solution_text"),
        hint_text=resp.get("hint_text"),
        model_id=model_id,
        latency_ms=latency_ms,
    )

    if override_insight_id:
        _mark_insight_applied(override_insight_id)

    try:
        from agent_rawtree.telemetry_builder import build_telemetry_event

        evt = build_telemetry_event(
            socratic_resp=resp,
            event_type=str(body.get("event_type") or "HINT_REQUEST"),
            attempt_num=max(1, int(body.get("attempt_num") or 1)),
            confidence_pct=max(0, min(100, int(body.get("confidence_pct") or 50))),
            session_id=session_id,
            hesitation_ms=None,
            device_id="chat-server",
            event_id_prefix="evt-chat",
        )
        threading.Thread(target=_emit_telemetry, args=(evt,), daemon=True).start()
    except Exception as exc:
        print(f"[chat] telemetry build failed: {exc}")

    _maybe_run_auto_cycle()

    return {
        "transcription": user_text,
        "language": lang,
        "mode": mode,
        "socratic_response": resp,
        "solution_text": raw.get("solution_text"),
        "final_answer": raw.get("final_answer"),
        "hint_ladder": ladder,
        "socratic_dimension": raw.get("socratic_dimension"),
        "guardrail_triggered": bool(raw.get("guardrail_triggered")),
        "guardrail_reason": raw.get("guardrail_reason"),
        "intervention_applied": bool(override_nudge),
        "offline_mode": bool(resp.get("offline_mode", True)),
        "prompt_n": prompt_n,
        "tok_s": tok_s,
        **_mem(),
    }


def _parse_multipart(content_type: str, raw_body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """Parse multipart/form-data using stdlib email.parser (Python 3.13+ safe)."""
    header_bytes = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1")
    msg = BytesParser(policy=email.policy.default).parsebytes(header_bytes + raw_body)
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename is not None:
            files[name] = (filename, payload)
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    return fields, files


def _voice_query(content_type: str, raw_body: bytes) -> tuple[dict[str, Any], int]:
    fields, files = _parse_multipart(content_type, raw_body)
    lang = _normalize_lang(fields.get("language"))
    fallback_text = (fields.get("fallback_transcript") or fields.get("transcript") or "").strip()

    audio_entry = files.get("audio")
    if audio_entry:
        filename, audio_bytes = audio_entry
        stt_res = stt.transcribe_bytes(
            audio_bytes,
            filename=filename or "utterance.wav",
            language=lang,
            fallback_transcript=fallback_text,
        )
        transcribed = (stt_res.get("text") or "").strip()
        stt_ms = int(stt_res.get("stt_latency_ms") or 0)
        used_fallback = bool(stt_res.get("used_fallback"))
    else:
        transcribed = fallback_text
        stt_ms = 0
        used_fallback = True

    if not transcribed and not fields.get("selected_option"):
        return {"detail": "No speech recognized and no fallback transcript provided."}, 400

    body: dict[str, Any] = {
        "problem_id": fields.get("problem_id") or "GENERAL-MATH",
        "text": transcribed,
        "event_type": fields.get("event_type") or ("ANSWER_SUBMIT" if fields.get("selected_option") else "HINT_REQUEST"),
        "attempt_num": fields.get("attempt_num") or 1,
        "confidence_pct": fields.get("confidence_pct") or 50,
        "selected_option": fields.get("selected_option"),
        "session_id": fields.get("session_id"),
        "mode": fields.get("mode") or "teach",
        "language": lang,
    }
    result = _text_query(body)
    result["transcription"] = transcribed
    result["stt_latency_ms"] = stt_ms
    result["stt_used_fallback"] = used_fallback
    return result, 200


def _ocr_query(content_type: str, raw_body: bytes) -> tuple[dict[str, Any], int]:
    """Handle POST /api/ocr with multipart image upload, JSON path/base64, or raw bytes."""
    import base64

    ctype_lower = content_type.lower()
    image_input: Any = None
    if "multipart/form-data" in ctype_lower:
        fields, files = _parse_multipart(content_type, raw_body)
        entry = files.get("image") or files.get("file")
        if entry:
            image_input = entry[1]
        elif fields.get("image_path"):
            image_input = fields["image_path"]
    elif "application/json" in ctype_lower:
        body = json.loads(raw_body or b"{}")
        if body.get("image_base64"):
            b64 = str(body["image_base64"]).split(",", 1)[-1]
            image_input = base64.b64decode(b64)
        elif body.get("image_path"):
            image_input = str(body["image_path"])
    elif raw_body:
        image_input = raw_body

    if not image_input:
        return {"detail": "No image provided (expected multipart 'image'/'file', JSON 'image_path'/'image_base64', or raw image bytes)."}, 400

    if ocr_engine is None:
        return {"detail": "ocr_engine module is not available on this device."}, 501
    return ocr_engine.ocr_problem_from_image(image_input), 200


def _quantile(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolation quantile matching pandas.Series.quantile."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = p * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    frac = pos - lo
    return float(sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo]))


def _compute_tukey_box(values: list[float]) -> dict[str, Any]:
    """Compute Tukey 5-number summary, whiskers, and outlier points in pure stdlib."""
    xs = sorted(float(v) for v in values if v is not None)
    n = len(xs)
    if n == 0:
        return {
            "count": 0, "min": 0.0, "q1": 0.0, "median": 0.0, "q3": 0.0, "max": 0.0,
            "iqr": 0.0, "mean": 0.0, "trimean": 0.0, "midhinge": 0.0, "quartile_skew": 0.0,
            "lower_fence": 0.0, "upper_fence": 0.0, "whisker_low": 0.0, "whisker_high": 0.0,
            "outliers": [], "outlier_count": 0, "outlier_pct": 0.0,
        }
    q1 = _quantile(xs, 0.25)
    med = _quantile(xs, 0.50)
    q3 = _quantile(xs, 0.75)
    iqr = q3 - q1
    lower_fence = q1 - 1.5 * iqr
    upper_fence = q3 + 1.5 * iqr
    inliers = [x for x in xs if lower_fence <= x <= upper_fence]
    whisker_low = inliers[0] if inliers else xs[0]
    whisker_high = inliers[-1] if inliers else xs[-1]
    outliers = [round(x, 2) for x in xs if x < lower_fence or x > upper_fence]
    mean_v = sum(xs) / n
    trimean = (q1 + 2.0 * med + q3) / 4.0
    midhinge = (q1 + q3) / 2.0
    q_skew = ((q3 - med) - (med - q1)) / iqr if iqr > 0 else 0.0
    return {
        "count": n,
        "min": round(xs[0], 2),
        "q1": round(q1, 2),
        "median": round(med, 2),
        "q3": round(q3, 2),
        "max": round(xs[-1], 2),
        "iqr": round(iqr, 2),
        "mean": round(mean_v, 2),
        "trimean": round(trimean, 2),
        "midhinge": round(midhinge, 2),
        "quartile_skew": round(q_skew, 3),
        "lower_fence": round(lower_fence, 2),
        "upper_fence": round(upper_fence, 2),
        "whisker_low": round(whisker_low, 2),
        "whisker_high": round(whisker_high, 2),
        "outliers": outliers[:40],
        "outlier_count": len(outliers),
        "outlier_pct": round((len(outliers) / n) * 100.0, 1),
    }


def _compute_calibration_stdlib(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute Brier score, ECE (10 bins), calibration curve, and Dunning-Kruger points."""
    answered = [
        e for e in events
        if e.get("event_type") in ("ANSWER_SUBMIT", "SOLVED") and e.get("is_correct") is not None
    ]
    if not answered:
        answered = [e for e in events if e.get("is_correct") is not None]

    if not answered:
        return {
            "brier_score": 0.25,
            "ece": 0.0,
            "overconfidence_gap": 0.0,
            "high_conf_error_rate": 0.0,
            "overall_accuracy": 0.0,
            "answered_count": 0,
            "bins": [],
            "dunning_kruger_points": [],
        }

    brier_sum = 0.0
    correct_sum = 0.0
    high_conf_rows: list[tuple[float, float]] = []
    bins_data: list[list[tuple[float, float]]] = [[] for _ in range(10)]

    for e in answered:
        conf_pct = max(0.0, min(100.0, float(e.get("confidence_pct") or 0.0)))
        conf = conf_pct / 100.0
        actual = 1.0 if bool(e.get("is_correct")) else 0.0
        brier_sum += (conf - actual) ** 2
        correct_sum += actual
        if conf_pct >= 70.0:
            high_conf_rows.append((conf, actual))
        b_idx = min(9, int(conf_pct // 10.0))
        bins_data[b_idx].append((conf, actual))

    n = len(answered)
    brier = brier_sum / n
    overall_acc = (correct_sum / n) * 100.0

    if high_conf_rows:
        hc_conf = sum(c for c, _ in high_conf_rows) / len(high_conf_rows)
        hc_acc = sum(a for _, a in high_conf_rows) / len(high_conf_rows)
        overconf_gap = hc_conf - hc_acc
        high_conf_error_rate = (sum(1 for _, a in high_conf_rows if a == 0.0) / len(high_conf_rows)) * 100.0
    else:
        overconf_gap = 0.0
        high_conf_error_rate = 0.0

    ece = 0.0
    cal_bins: list[dict[str, Any]] = []
    for i, grp in enumerate(bins_data):
        lo, hi = i * 10, (i + 1) * 10
        if grp:
            m_conf = sum(c for c, _ in grp) / len(grp)
            m_acc = sum(a for _, a in grp) / len(grp)
            ece += (len(grp) / n) * abs(m_conf - m_acc)
            cal_bins.append({
                "bin_label": f"{lo}-{hi}%",
                "bin_mid": (lo + hi) / 2.0,
                "count": len(grp),
                "mean_confidence_pct": round(m_conf * 100.0, 1),
                "empirical_accuracy_pct": round(m_acc * 100.0, 1),
            })

    # Dunning-Kruger quadrant points (aggregated per problem_id + confidence_band, plus live chat points)
    dk_points: list[dict[str, Any]] = []
    for e in answered[:120]:
        dk_points.append({
            "event_id": str(e.get("event_id") or ""),
            "problem_id": str(e.get("problem_id") or ""),
            "category": str(e.get("category") or "Algebra"),
            "confidence_pct": int(e.get("confidence_pct") or 50),
            "is_correct": bool(e.get("is_correct")),
            "solve_sec": round(float(e.get("solve_elapsed_ms") or 0) / 1000.0, 2),
            "is_live_chat": str(e.get("event_id") or "").startswith("evt-chat-"),
        })

    return {
        "brier_score": round(brier, 3),
        "ece": round(ece, 3),
        "overconfidence_gap": round(overconf_gap, 3),
        "high_conf_error_rate": round(high_conf_error_rate, 1),
        "overall_accuracy": round(overall_acc, 1),
        "answered_count": n,
        "bins": cal_bins,
        "dunning_kruger_points": dk_points,
    }


def _compute_pre_post_stdlib(
    events: list[dict[str, Any]],
    insights: list[dict[str, Any]],
    target_problem_id: str = "CAM-2023-N4-17",
) -> dict[str, Any]:
    """Compute Pre vs Post intervention solve time, accuracy, Cohen's d, Mann-Whitney U, Cliff's delta."""
    prob_insights = [i for i in insights if i.get("target_problem_id") == target_problem_id and i.get("created_at_utc")]
    all_insights = [i for i in insights if i.get("created_at_utc")]
    cutoff = None
    if prob_insights:
        cutoff = min(str(i["created_at_utc"]) for i in prob_insights)
    elif all_insights:
        cutoff = min(str(i["created_at_utc"]) for i in all_insights)

    target_events = [e for e in events if e.get("problem_id") == target_problem_id]
    pre_evts: list[dict[str, Any]] = []
    post_evts: list[dict[str, Any]] = []
    for e in target_events:
        ts = str(e.get("ts_utc") or "")
        if cutoff and ts >= cutoff:
            post_evts.append(e)
        else:
            pre_evts.append(e)

    # Fallback: if all events landed on one side of cutoff, split by event timestamp median or leave empty
    pre_sec = [float(e.get("solve_elapsed_ms") or 0) / 1000.0 for e in pre_evts]
    post_sec = [float(e.get("solve_elapsed_ms") or 0) / 1000.0 for e in post_evts]

    pre_ans = [1.0 if bool(e.get("is_correct")) else 0.0 for e in pre_evts if e.get("is_correct") is not None]
    post_ans = [1.0 if bool(e.get("is_correct")) else 0.0 for e in post_evts if e.get("is_correct") is not None]
    pre_acc = round((sum(pre_ans) / len(pre_ans)) * 100.0, 1) if pre_ans else 0.0
    post_acc = round((sum(post_ans) / len(post_ans)) * 100.0, 1) if post_ans else 0.0

    pre_att = round(sum(float(e.get("attempt_num") or 1) for e in pre_evts) / len(pre_evts), 2) if pre_evts else 0.0
    post_att = round(sum(float(e.get("attempt_num") or 1) for e in post_evts) / len(post_evts), 2) if post_evts else 0.0

    if not pre_sec or not post_sec:
        return {
            "target_problem_id": target_problem_id,
            "cutoff_ts": cutoff,
            "pre_count": len(pre_evts),
            "post_count": len(post_evts),
            "pre_median": round(_quantile(sorted(pre_sec), 0.5), 1) if pre_sec else 0.0,
            "post_median": round(_quantile(sorted(post_sec), 0.5), 1) if post_sec else 0.0,
            "time_reduction_pct": 0.0,
            "pre_accuracy": pre_acc,
            "post_accuracy": post_acc,
            "accuracy_shift_pp": round(post_acc - pre_acc, 1),
            "pre_attempts": pre_att,
            "post_attempts": post_att,
            "cliffs_delta": 0.0,
            "cohens_d": 0.0,
            "mann_whitney_u": 0.0,
            "mann_whitney_p": 1.0,
            "effect_label": "Insufficient Split",
        }

    pre_med = _quantile(sorted(pre_sec), 0.5)
    post_med = _quantile(sorted(post_sec), 0.5)
    red_pct = ((pre_med - post_med) / pre_med) * 100.0 if pre_med > 0 else 0.0

    n1, n2 = len(pre_sec), len(post_sec)
    greater = sum(1 for x in pre_sec for y in post_sec if x > y)
    less = sum(1 for x in pre_sec for y in post_sec if x < y)
    equal = sum(1 for x in pre_sec for y in post_sec if x == y)

    cliffs_d = (greater - less) / (n1 * n2)
    u1 = greater + 0.5 * equal
    u2 = n1 * n2 - u1
    mw_u = min(u1, u2)

    mu_u = (n1 * n2) / 2.0
    sigma_u = (n1 * n2 * (n1 + n2 + 1) / 12.0) ** 0.5
    if sigma_u > 0:
        z_score = (mw_u - mu_u) / sigma_u
        mw_p = math.erfc(abs(z_score) / (2.0 ** 0.5))
    else:
        mw_p = 1.0

    m1 = sum(pre_sec) / n1
    m2 = sum(post_sec) / n2
    if n1 > 1 and n2 > 1:
        v1 = sum((x - m1) ** 2 for x in pre_sec) / (n1 - 1)
        v2 = sum((y - m2) ** 2 for y in post_sec) / (n2 - 1)
        s_pooled = (((n1 - 1) * v1 + (n2 - 1) * v2) / (n1 + n2 - 2)) ** 0.5
        cohens_d = (m1 - m2) / s_pooled if s_pooled > 0 else 0.0
    else:
        cohens_d = 0.0

    if abs(cliffs_d) >= 0.474:
        label = "Large"
    elif abs(cliffs_d) >= 0.33:
        label = "Medium"
    elif abs(cliffs_d) >= 0.147:
        label = "Small"
    else:
        label = "Negligible"

    return {
        "target_problem_id": target_problem_id,
        "cutoff_ts": cutoff,
        "pre_count": n1,
        "post_count": n2,
        "pre_median": round(pre_med, 1),
        "post_median": round(post_med, 1),
        "time_reduction_pct": round(red_pct, 1),
        "pre_accuracy": pre_acc,
        "post_accuracy": post_acc,
        "accuracy_shift_pp": round(post_acc - pre_acc, 1),
        "pre_attempts": pre_att,
        "post_attempts": post_att,
        "cliffs_delta": round(cliffs_d, 2),
        "cohens_d": round(cohens_d, 2),
        "mann_whitney_u": round(mw_u, 1),
        "mann_whitney_p": round(mw_p, 4),
        "effect_label": label,
    }


def _ensure_demo_seeded_safe(cli: Any) -> dict[str, Any]:
    """Auto-seed local_rawtree.db only if empty, using agent_rawtree.seed_demo."""
    try:
        from agent_rawtree.seed_demo import ensure_demo_seeded

        return ensure_demo_seeded(client=cli)
    except Exception as exc:
        print(f"[chat] ensure_demo_seeded skipped: {exc}")
        return {"seeded": False, "error": str(exc)}


def _build_tukey_dashboard() -> dict[str, Any]:
    """Query local RawTree SQLite mirror and compute all Tukey & RawTree metrics in pure stdlib."""
    cli = _get_mirror()
    if cli is None:
        return {"error": "RawTree mirror unavailable", "kpis": {}, "boxplots": {"by_category": [], "by_difficulty": []}}

    _ensure_demo_seeded_safe(cli)

    events = cli.query_sql("SELECT * FROM telemetry_events ORDER BY ts_utc DESC;")
    insights = cli.query_sql("SELECT * FROM agent_insights ORDER BY created_at_utc DESC;")
    try:
        traces = cli.query_sql("SELECT * FROM agent_traces ORDER BY ts_utc DESC;")
    except Exception:
        traces = []

    live_chat_count = sum(1 for e in events if str(e.get("event_id") or "").startswith("evt-chat-"))
    active_ins = sum(1 for i in insights if i.get("status") == "ACTIVE")
    applied_ins = sum(1 for i in insights if i.get("status") == "APPLIED")
    resolved_ins = sum(1 for i in insights if i.get("status") == "RESOLVED")

    all_solve_sec = [float(e.get("solve_elapsed_ms") or 0) / 1000.0 for e in events]
    overall_tukey = _compute_tukey_box(all_solve_sec)
    calib = _compute_calibration_stdlib(events)
    pre_post = _compute_pre_post_stdlib(events, insights, target_problem_id="CAM-2023-N4-17")

    # Boxplots by category
    by_cat_map: dict[str, list[float]] = {}
    by_diff_map: dict[str, list[float]] = {}
    for e in events:
        sec = float(e.get("solve_elapsed_ms") or 0) / 1000.0
        cat = str(e.get("category") or "Algebra")
        diff = f"Tier {int(e.get('difficulty_level') or 2)}"
        by_cat_map.setdefault(cat, []).append(sec)
        by_diff_map.setdefault(diff, []).append(sec)

    box_by_cat = [{"group": k, **_compute_tukey_box(v)} for k, v in sorted(by_cat_map.items())]
    box_by_diff = [{"group": k, **_compute_tukey_box(v)} for k, v in sorted(by_diff_map.items())]

    # Misconception frequency breakdown
    misc_map: dict[str, list[dict[str, Any]]] = {}
    move_map: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        tag = e.get("misconception_tag")
        if tag and str(tag).lower() != "none":
            misc_map.setdefault(str(tag), []).append(e)
        mv = e.get("pedagogical_move")
        if mv:
            move_map.setdefault(str(mv), []).append(e)

    misconceptions = []
    for tag, rows in sorted(misc_map.items(), key=lambda kv: len(kv[1]), reverse=True):
        ans = [1.0 if bool(r.get("is_correct")) else 0.0 for r in rows if r.get("is_correct") is not None]
        misconceptions.append({
            "misconception_tag": tag,
            "count": len(rows),
            "avg_confidence_pct": round(sum(float(r.get("confidence_pct") or 0) for r in rows) / len(rows), 1),
            "accuracy_pct": round((sum(ans) / len(ans)) * 100.0, 1) if ans else 0.0,
            "avg_solve_sec": round(sum(float(r.get("solve_elapsed_ms") or 0) for r in rows) / len(rows) / 1000.0, 1),
        })

    pedagogical_moves = []
    for mv, rows in sorted(move_map.items(), key=lambda kv: len(kv[1]), reverse=True):
        ans = [1.0 if bool(r.get("is_correct")) else 0.0 for r in rows if r.get("is_correct") is not None]
        pedagogical_moves.append({
            "pedagogical_move": mv,
            "count": len(rows),
            "resolution_rate_pct": round((sum(ans) / len(ans)) * 100.0, 1) if ans else 0.0,
            "avg_confidence_pct": round(sum(float(r.get("confidence_pct") or 0) for r in rows) / len(rows), 1),
            "avg_solve_sec": round(sum(float(r.get("solve_elapsed_ms") or 0) for r in rows) / len(rows) / 1000.0, 1),
        })

    recent_events = []
    for e in events[:30]:
        row = dict(e)
        row["is_live_chat"] = str(e.get("event_id") or "").startswith("evt-chat-")
        row["solve_sec"] = round(float(e.get("solve_elapsed_ms") or 0) / 1000.0, 2)
        recent_events.append(row)

    return {
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kpis": {
            "total_events": len(events),
            "live_chat_events": live_chat_count,
            "insights_total": len(insights),
            "insights_active": active_ins,
            "insights_applied": applied_ins,
            "insights_resolved": resolved_ins,
            "overall_accuracy_pct": calib["overall_accuracy"],
            "median_solve_sec": overall_tukey["median"],
            "iqr_solve_sec": overall_tukey["iqr"],
            "brier_score": calib["brier_score"],
            "ece": calib["ece"],
            "overconfidence_gap": calib["overconfidence_gap"],
            "high_conf_error_rate": calib["high_conf_error_rate"],
        },
        "pre_post_intervention": pre_post,
        "boxplots": {
            "by_category": box_by_cat,
            "by_difficulty": box_by_diff,
        },
        "calibration": {
            "bins": calib["bins"],
            "dunning_kruger_points": calib["dunning_kruger_points"],
        },
        "misconceptions": misconceptions[:12],
        "pedagogical_moves": pedagogical_moves,
        "agent_insights": insights[:30],
        "agent_traces": traces[:20],
        "recent_events": recent_events,
    }


def _handle_rawtree_run_cycle() -> tuple[dict[str, Any], int]:
    """Run one RawTree autonomous agent diagnostic cycle and return latest insights & traces."""
    cli = _get_mirror()
    if cli is None:
        return {"ok": False, "detail": "RawTree mirror unavailable"}, 503
    _ensure_demo_seeded_safe(cli)
    res = _run_rawtree_agent_cycle_once()
    insights = cli.query_sql("SELECT * FROM agent_insights ORDER BY created_at_utc DESC LIMIT 10;")
    try:
        traces = cli.query_sql("SELECT * FROM agent_traces ORDER BY ts_utc DESC LIMIT 10;")
    except Exception:
        traces = []
    return {
        "ok": bool(res.get("ok")),
        "new_insight": res.get("insight"),
        "latest_insights": insights,
        "latest_traces": traces,
        "counts": {
            "insights": len(cli.query_sql("SELECT insight_id FROM agent_insights;")),
            "traces": len(cli.query_sql("SELECT trace_id FROM agent_traces;")),
        },
    }, 200


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj: Any, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        if path == "/problems":
            lang = qs.get("language", [None])[0]
            return self._json(_problems(lang))
        if path in ("/api/system-status", "/health"):
            return self._json(_status())
        if path == "/api/tukey-dashboard":
            return self._json(_build_tukey_dashboard())
        if path.startswith("/api/problem/") and path.endswith("/ladder"):
            pid = urllib.parse.unquote(path[len("/api/problem/") : -len("/ladder")].strip("/"))
            return self._json({"problem_id": pid, "hint_ladder": _build_hint_ladder(pid)})
        if path == "/":
            rel = "index.html"
        elif path == "/tukey":
            rel = "tukey.html"
        else:
            rel = path.removeprefix("/static/")
        f = (STATIC / rel).resolve()
        if STATIC in f.parents and f.is_file():
            return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self._json({"detail": "Not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length > 0 else b""
            if path == "/api/text-query":
                body = json.loads(raw_body or b"{}")
                return self._json(_text_query(body))
            if path == "/api/voice-query":
                ctype = self.headers.get("Content-Type") or ""
                payload, code = _voice_query(ctype, raw_body)
                return self._json(payload, code)
            if path == "/api/ocr":
                ctype = self.headers.get("Content-Type") or ""
                payload, code = _ocr_query(ctype, raw_body)
                return self._json(payload, code)
            if path == "/api/rawtree-run-cycle":
                payload, code = _handle_rawtree_run_cycle()
                return self._json(payload, code)
            return self._json({"detail": "Not found"}, 404)
        except Exception as exc:  # surface engine/STT errors to the UI instead of hanging
            self._json({"detail": str(exc)}, 500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {self.address_string()} {fmt % args}")


def _ensure_self_signed_cert(cert_path: str, key_path: str) -> tuple[str, str]:
    """Generate a standard self-signed TLS cert with SANs via openssl if missing."""
    cp, kp = Path(cert_path), Path(key_path)
    if cp.is_file() and kp.is_file():
        return str(cp), str(kp)
    cp.parent.mkdir(parents=True, exist_ok=True)
    kp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(kp), "-out", str(cp),
            "-days", "30", "-nodes",
            "-subj", "/CN=10.198.33.225",
            "-addext", "subjectAltName=IP:10.198.33.225,IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return str(cp), str(kp)


class _TLSThreadingHTTPServer(ThreadingHTTPServer):
    """Perform TLS handshakes inside worker threads so stalled clients never block accept()."""

    ssl_context: ssl.SSLContext | None = None

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        if self.ssl_context is not None:
            try:
                request.settimeout(5.0)
                request = self.ssl_context.wrap_socket(request, server_side=True)
                request.settimeout(None)
            except Exception:
                self.shutdown_request(request)
                return
        super().process_request_thread(request, client_address)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--https", action="store_true", help="Serve over HTTPS using a self-signed TLS cert")
    ap.add_argument("--cert", default="/tmp/socraticq_cert.pem")
    ap.add_argument("--key", default="/tmp/socraticq_key.pem")
    a = ap.parse_args()
    httpd = _TLSThreadingHTTPServer((a.host, a.port), Handler)
    scheme = "http"
    if a.https or os.environ.get("SOCRATICQ_HTTPS", "").strip() in {"1", "true", "yes"}:
        cert_file, key_file = _ensure_self_signed_cert(a.cert, a.key)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
        httpd.ssl_context = ctx
        scheme = "https"
    print(f"SocraticQ chat on {scheme}://{a.host}:{a.port}")
    httpd.serve_forever()
