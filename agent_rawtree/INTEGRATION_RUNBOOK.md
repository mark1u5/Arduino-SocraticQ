# SocraticQ — Agent-Executable Integration Runbook (Side Challenges ↔ Main Stack)

> **Status:** READY FOR EXECUTION — deterministic, copy-pasteable specification.
> **Companion:** [`INTEGRATION_IMPACT_REPORT.md`](./INTEGRATION_IMPACT_REPORT.md) (rationale & approval artifact).
> **Baseline:** `main` @ `c233fd8`. Executor: any AI agent (or human) with repo access. No prior conversation context required.

---

## §0 Mission, identity & execution protocol

### 0.1 Mission
Wire the two side challenges into the main stack by making `chat_server.py` (a) emit contract-valid telemetry and (b) apply RawTree agent interventions — teach mode only. Everything else (engine, hardware, contracts) stays untouched.

### 0.2 Mandatory startup (AGENTS.md §1 — do this FIRST, before any edit)
1. Read `SYNC_BOARD.md`, `TEAM_EXECUTION_PLAN.md`, `contracts/schemas.py`.
2. Run `python -m contracts.check_contracts` → expect `All frozen contracts and fixtures verified.`
3. `git status --short` must be clean (or contain only files you are about to create/edit below).

### 0.3 Approvals gate
- Steps 1–2, 6, 8 touch `/agent_rawtree` and `/analytics_tukey` only → always in scope (Dev 3 / Dev 4 folders).
- Steps 4, 5, 7 touch ROOT files (`chat_server.py`, `static/chat.js`, `launch.sh`) → require an explicit human "go" (the repo owner's instruction to execute this runbook counts). If you cannot obtain it, execute Steps 1–2, 6, 8 only and stop.

### 0.4 Non-negotiable rules (violating any = abort)
| # | Rule |
|---|---|
| R1 | Never edit `contracts/`, `TEAM_EXECUTION_PLAN.md`, `.env.example` (Tech-Lead-only on `main`). |
| R2 | Never invent/rename JSON keys. Only existing contract fields (`TelemetryEvent`, `AgentInsight`) and the existing `intervention_applied` response key. |
| R3 | All `chat_server.py` hooks are lazy-imported, try/except-wrapped, and never block the student response (telemetry emit runs on a daemon thread). |
| R4 | `agent_override_nudge` is passed **only when `mode == "teach"`**. Never in `solve` mode. |
| R5 | Never commit `*.db`, `.env`, model weights. Verify `git status` before every commit. |
| R6 | Execute steps **in order** 1→8; do not skip the verification gate of a step before starting the next. |
| R7 | If a step's `old_text` anchor is not found verbatim, STOP and report — the baseline has drifted. Re-diff against `main` first. |

### 0.5 Editing conventions used in this runbook
- Each step gives `FILE`, `ANCHOR` (exact old text, verified against `main` @ `c233fd8`), and `NEW` text.
- Indentation is significant: all Python anchors are inside functions (4-space indent).
- After each edit run the step's `VERIFY` command; expected output is stated.

---

## STEP 1 — Create `agent_rawtree/telemetry_builder.py` (new file, ~95 lines)

**Why:** one stdlib-only builder shared by FastAPI (`evt-web-`) and chat server (`evt-chat-`) so the two servers can never drift apart in event construction.

Create the file with EXACTLY this content:

```python
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
```

**VERIFY:** `.venv\Scripts\python.exe -c "from agent_rawtree.telemetry_builder import build_telemetry_event as b; e=b(socratic_resp={'problem_id':'CAM-2023-N4-17','category':'Combinatorics','latency_ms':1200,'difficulty_level':2,'is_correct':False,'misconception_tag':'ignored_parity_invariant','pedagogical_move':'PARITY_CHECK'}, event_type='HINT_REQUEST', attempt_num=1, confidence_pct=30, session_id=None, hesitation_ms=None, device_id='smoke')"` → no exception; print `e['event_id']` starts with `evt-chat-`.

## STEP 2 — `agent_rawtree/rawtree_client.py`: honor `RAWTREE_LOCAL_DB` (2-line change)

**ANCHOR** (verbatim, in `RawTreeClient.__init__`):
```python
        # Local SQLite database fallback path
        if local_db_path is None:
            self.local_db_path = Path(__file__).resolve().parent / "local_rawtree.db"
        else:
            self.local_db_path = Path(local_db_path)
```
**NEW:**
```python
        # Local SQLite database fallback path. RAWTREE_LOCAL_DB lets several
        # processes (chat server, autonomous agent, Tukey dashboard) share one
        # store without code coordination; default preserves historic path.
        if local_db_path is None:
            env_db = os.getenv("RAWTREE_LOCAL_DB", "").strip()
            self.local_db_path = Path(env_db) if env_db else Path(__file__).resolve().parent / "local_rawtree.db"
        else:
            self.local_db_path = Path(local_db_path)
```
(`os` is already imported at the top of the file — do NOT add imports.)

**VERIFY:** `.venv\Scripts\python.exe -c "import os; os.environ['RAWTREE_LOCAL_DB']='agent_rawtree/__probe.db'; from agent_rawtree.rawtree_client import RawTreeClient; c=RawTreeClient(force_offline=True); import pathlib; print(c.local_db_path)"` → prints `agent_rawtree\__probe.db`. Then delete `agent_rawtree/__probe.db*`.

## STEP 3 (OPTIONAL) — `agent_rawtree/web_app.py`: delegate to the shared builder

Behavior-preserving refactor. **Skip if time-boxed** — chat_server works without it; the only cost of skipping is duplicated construction logic.

**ANCHOR** — the body of `_record_socratic_telemetry` between its docstring and the final `return event_dict` (everything from `prob_map = ...` down to `background_tasks.add_task(...)`). Replace that whole middle section with a builder call, keeping the function signature and last two lines:

```python
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
```

**VERIFY:** `.venv\Scripts\python.exe -m contracts.check_contracts` still passes; `.venv\Scripts\python.exe -c "import agent_rawtree.web_app"` imports cleanly (requires fastapi in the venv; skip the import check if fastapi is absent — the file change is still valid).

## STEP 4 — `chat_server.py`: the four guarded hooks (THE behavioral change)

### 4a. Insert helpers after `_call_socratic_turn`

**ANCHOR** — the end of `_call_socratic_turn` (verbatim):
```python
    filtered = kwargs if accepts_var_kw else {k: v for k, v in kwargs.items() if k in params}
    return socratic_engine.generate_socratic_turn(**filtered)
```
Insert IMMEDIATELY AFTER it (module level, no indent) — the helper block in the next section. Note: `json`, `os`, `urllib.request` are already imported at the top of `chat_server.py` — do NOT add imports.

**Helper block (paste after the anchor above, module level, no indent):**

```python
# ---------------------------------------------------------------------------
# RawTree closed-loop hooks (Side Challenge 1) — lazy, guarded, never blocking.
# ---------------------------------------------------------------------------

_MIRROR: dict[str, Any] = {"client": None, "tried": False}


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
```

**VERIFY (after 4a):** `.venv\Scripts\python.exe -c "import ast; ast.parse(open('chat_server.py', encoding='utf-8').read())"` → exit 0.

### 4b. `_text_query`: fetch intervention + pass override (teach-only)

**ANCHOR** — the full head of `_text_query` (verbatim, lines 210–229 of baseline):
```python
def _text_query(body: dict[str, Any]) -> dict[str, Any]:
    mode = body.get("mode") if body.get("mode") in ("teach", "solve") else "teach"
    lang = _normalize_lang(body.get("language"))
    session_id = str(body.get("session_id") or "").strip() or None
    problem_id = str(body.get("problem_id") or "GENERAL-MATH")
    user_text = str(body.get("text") or "").strip()
    prior_history = _get_session_history(session_id)

    with _INFER_LOCK:
        raw = _call_socratic_turn(
            problem_id=problem_id,
            event_type=body.get("event_type") or "HINT_REQUEST",
            attempt_num=max(1, int(body.get("attempt_num") or 1)),
            confidence_pct=max(0, min(100, int(body.get("confidence_pct") or 50))),
            selected_option=body.get("selected_option"),
            student_note=user_text or None,
            mode=mode,
            language=lang,
            history=prior_history,
        )
```
**NEW:**
```python
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
```
(`_call_socratic_turn` filters kwargs against the engine signature; `generate_socratic_turn` accepts `agent_override_nudge` — verified at `engine/socratic_engine.py:1369`.)

### 4c. `_text_query`: mark applied + emit telemetry + flip the response flag

**ANCHOR** — the return block at the end of `_text_query` (verbatim):
```python
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
        "intervention_applied": False,
        "offline_mode": bool(resp.get("offline_mode", True)),
        "prompt_n": prompt_n,
        "tok_s": tok_s,
        **_mem(),
    }
```
**NEW:**
```python
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
```
(`threading` is already imported at the top of `chat_server.py`.)

**Voice inheritance note:** `_voice_query` builds a `body` dict and calls `_text_query(body)` — voice turns therefore get interventions + telemetry automatically. No edits to `_voice_query`.

**VERIFY (after 4b+4c):** AST parse exit 0; then `.venv\Scripts\python.exe -c "import os; os.environ['RAWTREE_LOCAL_DB']='agent_rawtree/__probe2.db'; import chat_server; print('ok')"` → `ok`. Delete `agent_rawtree/__probe2.db*` afterwards.

## STEP 5 — `static/chat.js`: surface interventions in the chat UI (~2 lines)

**ANCHOR** (verbatim, inside `async function send(...)`, line ~532):
```js
      const answer = mode === "solve" ? (data.solution_text || r.hint_text) : r.hint_text;
```
**NEW:**
```js
      let answer = mode === "solve" ? (data.solution_text || r.hint_text) : r.hint_text;
      if (data.intervention_applied) answer = "> ⚡ **Agent intervention applied** — RawTree closed-loop nudge\n\n" + answer;
```
Renders via the existing `md()` markdown renderer in `typeOut`/`renderThread` — no new CSS, no unknown anchors. This is the visible Side-Challenge-1 proof for judges.

**VERIFY:** `node --check static/chat.js` (if node is unavailable, visually confirm the edit sits inside the `try` block after `const r = data.socratic_response || {};`).

## STEP 6 — `analytics_tukey/queries.py` + `app.py`: add "Mode C: Board Mirror"

### 6a. `queries.py` — dispatcher branch

**ANCHOR** (verbatim, in `run_query`):
```python
    active_source = (source or os.environ.get("DATA_SOURCE", "mock")).lower()

    if active_source == "rawtree":
```
**NEW:**
```python
    active_source = (source or os.environ.get("DATA_SOURCE", "mock")).lower()

    if active_source == "mirror":
        return _run_mirror_query(sql)

    if active_source == "rawtree":
```

### 6b. `queries.py` — new runner after `_run_local_query`

**ANCHOR** (verbatim):
```python
def _run_local_query(sql: str) -> pd.DataFrame:
    conn = init_local_db()
    return pd.read_sql_query(sql, conn)
```
Append AFTER it:
```python
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
```

### 6c. `app.py` — sidebar radio

**ANCHOR** (verbatim):
```python
    source_choice = st.radio(
        "Data Source",
        options=["Mode A: Local Mock (Offline)", "Mode B: Live RawTree (SQL-out)"],
        index=0,
    )

    is_rawtree = "Live RawTree" in source_choice
    source_key = "rawtree" if is_rawtree else "mock"
```
**NEW:**
```python
    source_choice = st.radio(
        "Data Source",
        options=[
            "Mode A: Local Mock (Offline)",
            "Mode B: Live RawTree (SQL-out)",
            "Mode C: Board Mirror (RAWTREE_LOCAL_DB)",
        ],
        index=0,
    )

    if "Board Mirror" in source_choice:
        source_key = "mirror"
    elif "Live RawTree" in source_choice:
        source_key = "rawtree"
    else:
        source_key = "mock"
```

**VERIFY:** `.venv\Scripts\python.exe -m analytics_tukey.queries` → still runs (Mode A default); then `.venv\Scripts\python.exe -c "from analytics_tukey.queries import run_query; df = run_query('SELECT COUNT(*) AS n FROM telemetry_events', source='mirror'); print(len(df), df.columns.tolist())"` → `1 ['n']` (0 rows is fine; empty mirror returns `n=0`, not an exception).

## STEP 7 — `launch.sh`: `--chat` flag + shared store export

**ANCHOR 7a** (verbatim):
```bash
    --mock)
      export SOCRATIC_MOCK=1
      AGENT_MOCK_FLAG="--mock"
      ;;
```
**NEW:**
```bash
    --mock)
      export SOCRATIC_MOCK=1
      AGENT_MOCK_FLAG="--mock"
      ;;
    --chat)
      CHAT_SERVER="yes"
      ;;
```
**ANCHOR 7b** (verbatim, variable init near top):
```bash
WITH_DASHBOARD="auto"
AGENT_MOCK_FLAG=""
```
**NEW:**
```bash
WITH_DASHBOARD="auto"
AGENT_MOCK_FLAG=""
CHAT_SERVER="no"
```
**ANCHOR 7c** (verbatim, step 5):
```bash
# 5. Start unified FastAPI backend on :8000
ASGI_APP="agent_rawtree.web_app:app"
if [[ -f "$ROOT_DIR/server.py" ]]; then
  ASGI_APP="server:app"
fi
echo "[launch.sh] Starting FastAPI ($ASGI_APP) on http://0.0.0.0:8000 ..."
"$PYTHON_BIN" -m uvicorn "$ASGI_APP" --host 0.0.0.0 --port 8000 &
PIDS+=("$!")
```
**NEW:**
```bash
# 5. Start web backend on :8000 — chat_server.py (offline stdlib, --chat) or FastAPI
export RAWTREE_LOCAL_DB="${RAWTREE_LOCAL_DB:-$ROOT_DIR/agent_rawtree/local_rawtree.db}"
if [[ "$CHAT_SERVER" == "yes" ]]; then
  echo "[launch.sh] Starting chat_server.py (offline stdlib) on http://0.0.0.0:8000 ..."
  "$PYTHON_BIN" chat_server.py --host 0.0.0.0 --port 8000 &
else
  ASGI_APP="agent_rawtree.web_app:app"
  if [[ -f "$ROOT_DIR/server.py" ]]; then
    ASGI_APP="server:app"
  fi
  echo "[launch.sh] Starting FastAPI ($ASGI_APP) on http://0.0.0.0:8000 ..."
  "$PYTHON_BIN" -m uvicorn "$ASGI_APP" --host 0.0.0.0 --port 8000 &
fi
PIDS+=("$!")
```
(`chat_server.py` already accepts `--host/--port`. The agent loop (step 6 of launch.sh) and Streamlit RAM guard (step 7) stay untouched.)

**VERIFY:** `bash -n launch.sh` → exit 0 (syntax only; runtime check happens in §9 gate 5).

## STEP 8 — Create `agent_rawtree/tests/test_chat_telemetry.py` (new file, ~95 lines)

Create with EXACTLY this content (stdlib unittest; mirrors `tests/test_rawtree.py` style):

```python
"""Regression tests: chat-server closed loop (telemetry + interventions).

Covers the INTEGRATION_RUNBOOK.md contract:
1. build_telemetry_event -> valid contract TelemetryEvent, persisted in mirror.
2. Seeded ACTIVE insight is fetched and marked APPLIED.
3. Mirror unavailability degrades silently (no exception escapes).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agent_rawtree.rawtree_client import RawTreeClient
from agent_rawtree.telemetry_builder import build_telemetry_event


def _sample_response(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "problem_id": "CAM-2023-N4-17",
        "category": "Combinatorics",
        "difficulty_level": 2,
        "hint_text": "What stays constant if you swap two rows of the board?",
        "lcd_summary": "Parity check",
        "pedagogical_move": "PARITY_CHECK",
        "is_correct": False,
        "misconception_tag": "ignored_parity_invariant",
        "latency_ms": 1200,
        "model_id": "qwen3.5-0.8b",
        "offline_mode": True,
    }
    base.update(overrides)
    return base


class ChatTelemetryLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.client = RawTreeClient(
            local_db_path=Path(self._tmp.name) / "mirror.db",
            force_offline=True,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_builder_produces_contract_valid_event(self) -> None:
        evt = build_telemetry_event(
            socratic_resp=_sample_response(),
            event_type="HINT_REQUEST",
            attempt_num=1,
            confidence_pct=30,
            session_id="sess-test",
            hesitation_ms=None,
            device_id="chat-server",
        )
        self.assertTrue(evt["event_id"].startswith("evt-chat-"))
        self.assertEqual(evt["category"], "Combinatorics")
        self.assertEqual(evt["confidence_pct"], 30)
        self.assertIn("misconception_tag", evt)
        # Insertion into the mirror must succeed (validation runs inside).
        self.assertTrue(self.client.insert_telemetry_event(evt))
        rows = self.client.query_sql("SELECT COUNT(*) AS n FROM telemetry_events;")
        self.assertEqual(rows[0]["n"], 1)

    def test_intervention_lifecycle_fetch_and_apply(self) -> None:
        from contracts.schemas import validate_agent_insight

        insight = {
            "insight_id": "ins-test-01",
            "created_at_utc": "2026-11-15T10:00:00Z",
            "cycle_num": 1,
            "insight_type": "INTERVENTION",
            "target_category": "Combinatorics",
            "target_problem_id": "CAM-2023-N4-17",
            "severity_score": 0.9,
            "sql_executed": "SELECT 1;",
            "evidence_summary": "3 students stuck on parity.",
            "recommended_nudge": "What invariant survives each move?",
            "status": "ACTIVE",
        }
        validate_agent_insight(dict(insight))
        self.assertTrue(self.client.insert_agent_insight(insight))

        rows = self.client.query_sql(
            "SELECT insight_id, recommended_nudge FROM agent_insights "
            "WHERE status IN ('ACTIVE', 'APPLIED') AND target_problem_id = ? "
            "ORDER BY severity_score DESC LIMIT 1;",
            ("CAM-2023-N4-17",),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recommended_nudge"], insight["recommended_nudge"])

        self.client.query_sql(
            "UPDATE agent_insights SET status = 'APPLIED' WHERE insight_id = ?;",
            ("ins-test-01",),
        )
        after = self.client.query_sql("SELECT status FROM agent_insights WHERE insight_id = 'ins-test-01';")
        self.assertEqual(after[0]["status"], "APPLIED")

    def test_no_intervention_returns_none_shape(self) -> None:
        rows = self.client.query_sql(
            "SELECT insight_id FROM agent_insights WHERE status IN ('ACTIVE', 'APPLIED');"
        )
        self.assertEqual(rows, [])

    def test_clamps_invalid_confidence(self) -> None:
        evt = build_telemetry_event(
            socratic_resp=_sample_response(),
            event_type="ANSWER_SUBMIT",
            attempt_num=2,
            confidence_pct=250,
            session_id=None,
            hesitation_ms=None,
            device_id="chat-server",
        )
        self.assertEqual(evt["confidence_pct"], 100)


if __name__ == "__main__":
    unittest.main()
```

**VERIFY:** `.venv\Scripts\python.exe -m unittest agent_rawtree.tests.test_chat_telemetry -v` → 4 tests OK.

## §9 Final verification gates (run ALL, in order, after Step 8)

| Gate | Command (repo root) | Expected |
|---|---|---|
| G1 Contracts | `.venv\Scripts\python.exe -m contracts.check_contracts` | `All frozen contracts and fixtures verified.` |
| G2 Syntax | `.venv\Scripts\python.exe -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['chat_server.py','agent_rawtree/telemetry_builder.py','agent_rawtree/rawtree_client.py','agent_rawtree/web_app.py','analytics_tukey/queries.py','analytics_tukey/app.py']]; print('syntax ok')"` | `syntax ok` |
| G3 New tests | `.venv\Scripts\python.exe -m unittest agent_rawtree.tests.test_chat_telemetry -v` | 4 tests OK |
| G4 Regression | `.venv\Scripts\python.exe -m unittest agent_rawtree.tests.test_rawtree -v` and `.venv\Scripts\python.exe -m unittest hardware.tests.test_bridge -v` and `.venv\Scripts\python.exe -m engine.test_engine` | all pass, unchanged counts |
| G5 Tukey | `.venv\Scripts\python.exe -m analytics_tukey.queries` | runs, Mode A intact |
| G6 E2E closed loop | start agent: `start .venv\Scripts\python.exe -m agent_rawtree.autonomous_agent --once --mock`; then POST a teach turn to `chat_server.py`; then query the mirror | mirror has ≥1 `evt-chat-` row; if the agent emitted an insight for that problem, response has `intervention_applied: true` and insight status `APPLIED` |
| G7 Clean tree | `git status --short` | ONLY the files listed in §11 commit protocol |

G6 note: `--once --mock` emits a heuristic insight for a bottleneck problem; if it targets a different `problem_id` than your chat turn, `intervention_applied` is `false` — that is correct behavior (problem-scoped targeting). To force a match, chat on `CAM-2023-N4-17`.

## §10 Rollback matrix (each step independently revertible)

| Step | File(s) | Revert |
|---|---|---|
| 1 | `agent_rawtree/telemetry_builder.py` | delete file |
| 2 | `agent_rawtree/rawtree_client.py` | `git checkout -- agent_rawtree/rawtree_client.py` |
| 3 (opt) | `agent_rawtree/web_app.py` | `git checkout -- agent_rawtree/web_app.py` |
| 4 | `chat_server.py` | `git checkout -- chat_server.py` |
| 5 | `static/chat.js` | `git checkout -- static/chat.js` |
| 6 | `analytics_tukey/queries.py`, `app.py` | `git checkout -- analytics_tukey/queries.py analytics_tukey/app.py` |
| 7 | `launch.sh` | `git checkout -- launch.sh` |
| 8 | `agent_rawtree/tests/test_chat_telemetry.py` | delete file |

Degradation guarantee: every chat_server hook fails soft — if Steps 1/2 are missing, `_get_mirror()` prints `[chat] RawTree mirror unavailable` once and the tutor behaves exactly as baseline.

## §11 Environment decision table + commit protocol

| Env var | Values | Effect |
|---|---|---|
| `RAWTREE_LOCAL_DB` | path | Shared store for chat server + agent + Tukey Mode C (launch.sh exports it). Unset → historic `agent_rawtree/local_rawtree.db`. |
| `CHAT_TELEMETRY_MODE` | `mirror` (default) / `ingest` / `auto` / `off` | `ingest`+`INGEST_URL` → POST to FastAPI; `auto` → ingest if `INGEST_URL` set else mirror; `off` → emit nothing. Exactly one emit path per turn — no double counting. |
| `INGEST_URL` | URL | Only consulted by the chat server when mode is `ingest`/`auto`. |

**Commit protocol (AGENTS.md §2/§4):**
1. `python -m contracts.check_contracts` (done at G1).
2. `git status --short` → must list ONLY: `agent_rawtree/INTEGRATION_IMPACT_REPORT.md`, `agent_rawtree/INTEGRATION_RUNBOOK.md`, `agent_rawtree/telemetry_builder.py`, `agent_rawtree/rawtree_client.py`, `agent_rawtree/web_app.py`, `agent_rawtree/tests/test_chat_telemetry.py`, `chat_server.py`, `static/chat.js`, `analytics_tukey/queries.py`, `analytics_tukey/app.py`, `launch.sh`. Any other path → STOP.
3. Never `git add` `*.db` / `.env` / weights.
4. Commit message template: `feat(closed-loop): wire chat_server telemetry + RawTree interventions (runbook steps 1-8)`.

---
*End of runbook. Anchors verified against `main` @ `c233fd8` on 2026-11-15. If any anchor fails to match verbatim, the baseline has moved — re-diff before executing (Rule R7).*







