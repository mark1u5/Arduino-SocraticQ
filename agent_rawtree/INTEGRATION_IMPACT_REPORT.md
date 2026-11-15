# SocraticQ — Integration Impact Report: Side Challenges ↔ Main Stack

> **Status:** PROPOSAL — NOT IMPLEMENTED (explicitly: no code has been changed)
> **Author:** Dev 3 agent (working branch: `main` @ `c233fd8`; agent constitution scope: `/agent_rawtree`)
> **Baseline analyzed:** `main` @ `c233fd8` (post pull; +4,900/−608 lines incl. `chat_server.py`, chat UI, 4 new engine modules)
> **Date:** 2026-11-15
> **Purpose:** Explain how connecting Side Challenge 1 (RawTree Agentic Cortex) and Side Challenge 2 (Comissió Tukey) to the rest of the code changes the repository — file by file — under the Arduino Uno Q QRB2210 RAM constraint. This document contains **no implemented changes**.

---

## 1. Executive summary — the shape of the change

The integration is **almost entirely additive plumbing at the seams, not a rewrite**. Measured in files:

| Category | Count | Files |
|---|---|---|
| **New files** | 2 | `agent_rawtree/telemetry_builder.py` (extracted, stdlib-only), `agent_rawtree/tests/test_chat_telemetry.py` |
| **Modified (small, surgical)** | 6 | `chat_server.py`, `agent_rawtree/rawtree_client.py`, `agent_rawtree/web_app.py`, `analytics_tukey/queries.py`, `analytics_tukey/app.py`, `launch.sh` |
| **Modified (cosmetic/UI)** | 2 | `static/chat.js` (+banner), `static/chat.css` (+banner style) |
| **Docs/config only** | 3 | `.env.example` (Tech Lead only), `README.md`/`SYNC_BOARD.md` status lines |
| **Touched internally, zero code change** | 3 | `engine/`, `hardware/`, `contracts/` |
| **Unchanged** | everything else | `autonomous_agent.py`, `agent_rawtree/queries.py`, `charts.py`, `server.py`, `schema.sql`, `mesh.py`, `sync_channel.py`, all `datathon-*` dirs |

Worth-it verdict (RAM-constrained board): **yes for the stdlib-only slice** — single-digit MB RSS against ~2.8 GiB free during inference (measured, DECISIONS.md D8), reusing the SQLite spool pattern `hardware/` already ships on the board. It improves *system* completeness (the two €200 side challenges) and creates the only learning loop in the product (calibration + intervention efficacy); it does **not** move MCQ math accuracy (driven by the RAG index + letter priors + symbolic solvers, all already merged in `engine/`).

## 2. Background — how the side challenges work today

### Side Challenge 1 — Best Agentic Use of RawTree (`/agent_rawtree`)
Closed cognitive loop on three tables (`agent_rawtree/schema.sql`, mirroring frozen contracts in `contracts/schemas.py`):

- `telemetry_events` — every student interaction (confidence, correctness, misconception tag, solve time, SLM latency)
- `agent_insights` — interventions the agent emits (severity, SQL evidence, Socratic nudge, lifecycle `ACTIVE → APPLIED → RESOLVED`)
- `agent_traces` — agent meta-cognition (SQL executed, latency, findings, token cost)

**Data plane** (`rawtree_client.py`): official RawTree HTTP API (JSON-in `POST /v1/tables/{table}`, SQL-out `POST /v1/query`) with a **transparent offline SQLite mirror** (`local_rawtree.db`, WAL + `busy_timeout=5000`), Postgres→SQLite SQL dialect adaptation, and contract validation before every insert.

**Ingestion paths (two today):**
1. Hardware: `hardware/bridge/unoq_app.py` → engine → `TelemetrySpool` (offline SQLite on the board) → `IngestClient` flush to `POST :8000/ingest`.
2. Web: FastAPI `/api/text-query` + `/api/voice-query` → `generate_socratic_turn()` → `_record_socratic_telemetry()` → insert + SSE broadcast on `/stream`.

**The brain** (`autonomous_agent.py`, standalone CLI): `run_cycle()` = meta-memory reflex (queries own `agent_traces`) → closed-loop efficacy check (auto-resolves successful nudges) → two-stage GLM 5.3 chain (Data Detective writes self-correcting SQL; Socratic Pedagogist emits zero-leakage nudge) → inserts insight + trace. `--mock` runs a pure-stdlib heuristic path.

**Closing the loop back to students:** `web_app.py::_fetch_and_apply_intervention()` finds an active insight for the problem, feeds `recommended_nudge` to the engine as `agent_override_nudge` (the engine natively supports this parameter), marks it `APPLIED`, returns `intervention_applied: true`. Hardware polls `/interventions/active` → purple `BOTTLENECK_ALERT` LED; UI gets SSE pushes.

### Side Challenge 2 — Comissió Tukey (`/analytics_tukey`)
Pure SQL-out consumer — never writes, zero coupling to engine/hardware:

- `queries.py` — dual mode: **A** in-memory SQLite with contract-valid synthetic dataset (bimodal solve times, Dunning-Kruger clusters, pre/post intervention shift on `CAM-2023-N4-17`); **B** same analytical SQL against live RawTree with graceful fallback.
- `charts.py` — 10 Plotly builders (boxplots, ECDF, calibration, Tukey residuals, Socratic funnel, guardrail audit, misconception heatmap, difficulty drift, intervention impact, trace timeline).
- `app.py` — Streamlit `:8501`; Mode A/B sidebar; tabs compute Brier, ECE, Cohen's d, Mann-Whitney U, Cliff's delta.
- Turnkey connector: `from agent_rawtree.queries import fetch_tukey_datasets`.

### The gap (why they are currently disconnected)
Commit `c233fd8` introduced a **second, parallel runtime**: `chat_server.py` (stdlib, offline) serves the same `static/index.html` chat UI but calls the engine directly — **no telemetry, no interventions, no RawTree import at all** (`intervention_applied` hardcoded `False`), while FastAPI `server.py` has all of it. Both default to port 8000. The new UI loads only `chat.js`; the old cortex UI (`app.js`) with SSE/intervention panels is orphaned. Result: an offline demo produces zero `telemetry_events` → the agent has nothing to diagnose → Tukey shows only mock data — the split-brain state that DECISIONS.md D1 was written to prevent.

## 3. Design principles determining every change

1. **Contracts are frozen** — consume `TelemetryEvent`/`AgentInsight`/`SocraticResponse`; never extend. The loop hook (`agent_override_nudge`) was designed into the engine from day one and is simply unused by the chat server.
2. **Write-scope isolation (AGENTS.md §2)** — `/agent_rawtree` + `/analytics_tukey` edits are unambiguous Dev 3/Dev 4 scope; root files (`chat_server.py`, `static/`, `launch.sh`, `.env.example`) need a team blessing.
3. **Offline-first degradation** — every hook in `chat_server.py` is lazy-imported, try/except-wrapped, and runs on a background thread; the student's hint never waits on plumbing.
4. **No new JSON keys** — the response envelope already contains `intervention_applied`; flipping it to a real value is a one-token change.
5. **On-board = stdlib-only; heavy stays off-board** — GLM agent, Streamlit, pandas, FastAPI on the laptop (already `launch.sh` behavior via the ≥3584 MB guard).

## 4. Data flow — before / after

**Today (post `c233fd8`) — the split-brain:**

```text
Student → chat.js → chat_server.py → engine → hint        (dead end: no telemetry, no interventions)
Student → chat.js → server.py (FastAPI) → engine → hint   (telemetry + SSE + agent interventions)
                                    ↓ ingest
     RawTree mirror/live ← autonomous_agent ← Tukey dashboard (sees only what FastAPI ingested)
hardware/bridge ──spool──▶ POST :8000/ingest ─────────────▶ (same store)
```

**After the integration:**

```text
Student → chat.js → chat_server.py ─┬─▶ engine (hint, maybe with agent_override_nudge)
                                    ├─▶ RawTreeClient(force_offline) → local_rawtree.db  (telemetry JSON-in)
                                    └─◀ agent_insights (intervention fetched BEFORE inference)
autonomous_agent --interval 20 → queries the same store → emits insights → next turn applies them
Tukey dashboard (Mode C: Local Mirror) → reads the same store → live boxplots/calibration/effect-size
hardware/bridge → unchanged (spool + POST /ingest when a FastAPI is up)
```

One store, three writers (chat server, hardware bridge flush, agent), two readers (agent, Tukey) — safe via SQLite WAL, which `RawTreeClient._get_sqlite_conn()` already enables.

## 5. File-by-file impact

### A. `contracts/` — ZERO changes
Every new payload is built to the existing `TelemetryEvent` TypedDict and passes `validate_telemetry_event()`. No new table is needed, so no Tech Lead contract commit is required. `python -m contracts.check_contracts` passes unmodified.

### B. `agent_rawtree/` (Dev 3 write scope)

| File | Change | Detail |
|---|---|---|
| `telemetry_builder.py` | **NEW (~80 lines)** | Extracts `web_app.py::_record_socratic_telemetry` (lines 681–725) into a pure stdlib function: engine response + context → contract-valid `TelemetryEvent`. Uses `cangur_bank` for `cangur_year`; category clamped to the 5 `MathCategory` values; `event_id` prefix `evt-chat-` (distinct from `evt-web-`) so double-counting is auditable. |
| `rawtree_client.py` | +2 lines | Honor `RAWTREE_LOCAL_DB` env var as default `local_db_path` (today hardcoded to `agent_rawtree/local_rawtree.db`). Lets three processes agree on one store with zero code coordination. |
| `web_app.py` | refactor, behavior-identical | `_record_socratic_telemetry` delegates to the shared builder (prevents server drift). No endpoint signature changes. |
| `autonomous_agent.py` | **NO change** | Operates via `RawTreeClient`, so it automatically targets whatever store the env points to; `--mock` vs GLM is a launch flag. |
| `queries.py` | **NO change** | `fetch_tukey_datasets()` + the 6 SQL constants already work against the mirror. |
| `tests/test_chat_telemetry.py` | **NEW** | (1) chat turn → exactly one valid row in mirror; (2) seeded insight → override reaches engine, `intervention_applied` true; (3) mirror failure → hint still returned. Style mirrors `tests/test_rawtree.py`. |

### C. `chat_server.py` (root — needs team blessing)
The only file with genuine behavior change. Insertions inside `_text_query`, all lazy + guarded:

1. **Imports inside the handler** (never module-top) so the server still boots without those modules.
2. **Intervention fetch before inference** — one indexed `SELECT` on `agent_insights` (`status IN ('ACTIVE','APPLIED')` for the `problem_id`, the same filter `autonomous_agent._has_existing_insight` uses). Found → `recommended_nudge` becomes `agent_override_nudge`; insight marked `APPLIED`.
3. **Accuracy guard** — override passed **only when `mode == "teach"`**. In `solve` mode (answer computation) the parameter is never set, so the main-track MCQ path stays bit-identical to today.
4. **Telemetry emit after inference** — background thread; if `INGEST_URL` is set → POST to FastAPI (reuses existing ingestion); else → `insert_telemetry_event()` into the local mirror. Exactly one path per deployment via env (prevents double-counting).
5. Response: `"intervention_applied": False` → `bool(override_nudge)`. No new keys.

On-board cost: `RawTreeClient` = urllib + sqlite3 (both stdlib), ~5–15 MB RSS; mirror <5 MB after a full demo day (~10k events × ~300 B). Failures are invisible to the student.

### D. `static/chat.js` + `chat.css` (root — UI owner)
Verified: `chat.js` currently has **zero** references to interventions — the field arrives in the response and is dropped. ~20 lines to render a purple "⚡ Agent intervention applied" notice when the flag is true, matching the existing dark theme. This is the visible proof for Side Challenge 1 judges that the agent affects the student experience. No changes to message flow, mic handling, or ladder rendering.

### E. `engine/` — ZERO changes
The integration reaches the engine only through the **existing** `agent_override_nudge` and `mode` parameters. `socratic_engine.py`, `cangur_bank.py`, `mcq_optimizer.py`, `symbolic_solvers.py`, `visual_solver.py`, `stt.py` are untouched → the 20% RAG-benchmark accuracy and the 45 s offline latency envelope are preserved by construction. `python -m engine.test_engine` must pass unchanged before/after.

### F. `hardware/` — ZERO changes
`unoq_app.py` already implements `TelemetrySpool` (offline SQLite on the board) + `IngestClient` (flush to `POST :8000/ingest`) + polling `/interventions/active` for the purple `BOTTLENECK_ALERT` LED. The MCU contract (`UartEventFrame`/`UartFeedbackFrame`) is untouched. Compatibility note: `INGEST_URL` defaults to `:8000/ingest`; if the demo runs `chat_server.py` instead of FastAPI, the bridge keeps spooling locally until a consumer appears (designed behavior), or points at the live server via the existing env var.

### G. `analytics_tukey/` (Dev 4 write scope)

| File | Change | Detail |
|---|---|---|
| `queries.py` | +~40 lines | Add **"Mode C: Local Mirror"** beside Modes A/B: loader functions (`get_tukey_spread`, `get_pre_post_intervention`, `get_agent_insights`, `get_agent_traces`, …) gain a branch building pandas frames via `RawTreeClient.query_sql`. **All `compute_*` functions untouched** — Brier/ECE/Cohen's d/Mann-Whitney/Cliff's delta operate on DataFrames and are source-agnostic. |
| `app.py` | +1 sidebar radio option | Existing Mode A/B radio gains "Mode C: Board Mirror". Tabs/metrics/charts need no edits. |
| `charts.py` | **NO change** | All 10 builders take DataFrames. |

Tukey "lights up" with almost no code because it was architected as a pure SQL-out consumer; it only lacked a source pointing at the board's store.

### H. Root orchestration & config

| File | Change |
|---|---|
| `launch.sh` | Add `--chat` flag: start `chat_server.py` on :8000 **instead of** uvicorn (mutually exclusive — resolves the latent port collision). Keep the agent loop (step 6) and the RAM-guarded Streamlit decision (step 7) as-is. Export `RAWTREE_LOCAL_DB` so chat server, agent, and dashboard agree on the store. |
| `.env.example` | +2 optional keys: `RAWTREE_LOCAL_DB=./agent_rawtree/local_rawtree.db`, `CHAT_TELEMETRY_MODE=mirror|ingest`. **Tech-Lead-only on `main`** per AGENTS.md §2 — verbal sync, single commit. |
| `server.py` | Untouched (still re-exports the FastAPI app). |
| `README.md`, `SYNC_BOARD.md` | Docs only: quickstart + status lines. |
| `datathon-*/`, `IDEAS.md`, `ARDUINO_PROJECT_HUB.md` | Untouched. |

## 6. What does NOT change — the reassurance list

1. **The math.** Engine, optimizers, solvers, priors, RAG: untouched; `solve` mode receives no override.
2. **The contracts.** No new/renamed keys anywhere in `contracts/`.
3. **The hardware demo.** MCU sketch, bridge, spool, purple-LED closed loop: untouched.
4. **The agent's brain.** `autonomous_agent.py` runs as-is; only *where it looks* changes (env), not *how it thinks*.
5. **The statistics.** All Tukey math is source-agnostic; only the loader gains a branch.
6. **Latency budget.** Telemetry write is a background thread; the intervention read is one indexed SELECT on a table with at most a few dozen rows — both ≪ the 45 s inference timeout.

## 7. RAM & disk budget recap (on-board delta)

| Item | Cost | Notes |
|---|---|---|
| `RawTreeClient` + builder import | ~5–15 MB RSS, lazy | urllib/sqlite3 stdlib; `requests` never loads (optional import fails silently) |
| Mirror DB growth | <5 MB/day of demo | vs. 30 MB whisper model + ~800 MB GGUF |
| Background thread + WAL write | ~0 latency | pattern already used by `TelemetrySpool` on the board |
| Agent `--mock` (if unpaired) | ~10 MB | optional; GLM agent stays on laptop |
| Streamlit (Tukey) | 0 on board | `launch.sh` RAM guard ≥3584 MB already enforces off-board (DECISIONS.md D2) |

Context: D8 measured **2.8 GiB free** with SLM + whisper running; peak budget target <1.6 GB (D2). The integration adds single-digit MB.

## 8. Risks & mitigations

| Risk | Mitigation designed into the plan |
|---|---|
| Port 8000 collision (two servers) | `launch.sh --chat` makes them mutually exclusive |
| Double-counted telemetry | One emit path per deployment (`CHAT_TELEMETRY_MODE`), plus `evt-chat-` vs `evt-web-` ID prefixes |
| Multi-process SQLite contention | Already handled: WAL + `busy_timeout=5000` in `RawTreeClient`; ops rule = one writer of each kind |
| Override perturbing answers | `mode == "teach"` gate; `solve` path untouched; agent regex-sanitizes nudges for answer leakage before insertion |
| Demo breaks from new plumbing | Every hook try/except + background thread; failures invisible to the student |
| Merge conflicts | 1 new file in Dev 3 scope + surgical inserts; root-file edits need team blessing (AGENTS.md §2) |
| Accidental commit of DB | `local_rawtree.db` must stay untracked (AGENTS.md §2 forbids `*.db` commits); check `git status` pre-commit |

## 9. Sequencing with verification gates (when approved)

1. `telemetry_builder.py` + `rawtree_client.py` env → `python -m contracts.check_contracts` + new unit tests.
2. `chat_server.py` hooks → `python -m engine.test_engine` (regression) + manual chat turn → row appears in mirror.
3. Agent loop against shared store → `python -m agent_rawtree.autonomous_agent --once --mock` → insight → next turn shows `intervention_applied: true`.
4. Tukey Mode C → `python -m analytics_tukey.queries` + `streamlit run analytics_tukey/app.py`.
5. `launch.sh --chat` + end-to-end airplane-mode rehearsal; `git status` shows changes only within approved scope.

## 10. Effort & ownership

**Effort:** ~1.5–2 h; ~180 net-new lines, ~60 modified lines; zero contract/hardware/engine changes.

**Approvals needed before implementation:**
- Team blessing for root-file edits: `chat_server.py`, `static/chat.js`, `launch.sh`; `.env.example` via Tech Lead on `main`.
- Dev 4 sign-off on the Mode C branch in `analytics_tukey/queries.py` (or Dev 3 implements on `dev3/` with Dev 4 review).

**One-line summary per subsystem:** contracts: unchanged · engine: unchanged · hardware: unchanged · agent_rawtree: +1 module, 2-line client tweak, refactor for reuse · chat_server: +4 guarded hooks (the only behavioral change) · static: +1 banner · analytics_tukey: +1 data-source option · launch.sh: +1 flag · everything else: untouched.

---
*End of report. Status: PROPOSAL ONLY — no code has been modified. Implementation awaits explicit approval.*

> **➡ Execution:** this report is the *approval artifact*. The step-by-step, agent-executable implementation specification (exact code, anchors, verification gates, rollback) lives in the companion file [`agent_rawtree/INTEGRATION_RUNBOOK.md`](./INTEGRATION_RUNBOOK.md). Any AI agent may execute the runbook after the approvals in its §0 are satisfied.






