# SocraticQ + RawTree Agentic Cortex — Team Execution Plan (UPC Datathon 2026)

> **Mission:** Execute a zero-conflict, parallelized 24-hour build targeting a **€1,000 Triple-Dip**:
> 1. **Main Track (€600) — Qualcomm & Arduino Uno Q:** *SocraticQ*, an offline edge Socratic math mentor running quantized SLMs directly on the QRB2210 Debian Linux SoC + physical Arduino MCU interface.
> 2. **Side Challenge 1 (€200) — Best Agentic Use of RawTree:** Autonomous AI agent using RawTree (`JSON-in, SQL-out`) as a real-time analytical scratchpad and cognitive memory loop to diagnose classroom bottlenecks and emit interventions.
> 3. **Side Challenge 2 (€200) — Comissió Tukey:** Exploratory data visualization dashboard scored on **Utility**, **Adequacy** (boxplots, time-series, distributions), and **Coherence**.

---

## 1. Unified System Architecture Diagram

SocraticQ separates **real-time offline edge tutoring** (works with Wi-Fi disabled for the Qualcomm jury) from the **asynchronous RawTree cognitive loop & Tukey analytics layer** (queues locally when offline, flushes via JSON-in to RawTree when connected).

```text
+---------------------------------------------------------------------------------------------------+
|                             ARDUINO UNO Q (EDGE HARDWARE - OFFLINE FIRST)                         |
|                                                                                                   |
|  [Physical Controls]                   [Arduino MCU (Real-Time)]          [QRB2210 Debian Linux]  |
|   - Rotary Dial (Problem / Step)  --->  Reads GPIO / ADC / Buttons   UART  +-------------------+  |
|   - Hint Request Button           --->  Debounces & formats JSON  =======> | Hardware Bridge   |  |
|   - Answer Submit / Confidence    --->  Drives RGB LEDs / Buzzer  <======= | (PySerial / Sock) |  |
|                                         (Thinking/Hint/Solved)     Cmds    +---------+---------+  |
|                                                                                      |            |
|                                                                            Python Call / IPC      |
|                                                                                      v            |
|  +-------------------------------------------------------------------------------+   |            |
|  | Local Socratic Engine (/engine)                                               |<--+            |
|  |  - CAM Proves Cangur Corpus (2007-2024, indexed SQLite/JSON)                  |                |
|  |  - Quantized Local SLM (Qwen2.5-Math / SmolLM2 via llama.cpp / ONNX)          |                |
|  |  - Never leaks final answer; outputs Socratic nudge + pedagogical state       |                |
|  +---------------------------------------+---------------------------------------+                |
|                                          |                                                        |
|                                          v (Telemetry Event JSON + Local SQLite Spool)            |
+------------------------------------------|--------------------------------------------------------+
                                           |
                                           | HTTPS POST (JSON-in) when online / demo sync
                                           v
+---------------------------------------------------------------------------------------------------+
|                         RAWTREE AGENTIC CORTEX & ANALYTICAL SCRATCHPAD                            |
|                                                                                                   |
|  +-----------------------------------+         +-----------------------------------------------+  |
|  | RawTree Cloud / Team Workspace    | <=====> | Autonomous RawTree Agent (/agent_rawtree)     |  |
|  |  - telemetry_events (JSON-in)     | SQL-out |  - Powered by HelmCode (GLM 5.3)              |  |
|  |  - agent_insights (Scratchpad)    |         |  - Loop: Observe -> Hypothesize (SQL) -> Act  |  |
|  |  - shop.catalog / shop.events     | JSON-in |  - Detects bottlenecks (e.g., Combinatorics   |  |
|  |    (shared benchmark tables)      |         |    hint loops) & writes interventions/packets   |  |
|  +-----------------+-----------------+         +-----------------------+-----------------------+  |
|                    |                                                   |                          |
+--------------------|---------------------------------------------------|--------------------------+
                     | SQL-out Queries                                   | FastAPI / WebSocket
                     v                                                   v
+---------------------------------------------------+  +--------------------------------------------+
| Comissio Tukey Dashboard (/analytics_tukey)       |  | Local Student & Mentor Web UI              |
|  - Streamlit + Plotly (Utility/Adequacy/Coherence)|  |  - Live problem view, Socratic chat log    |
|  - Boxplots: Time-to-insight by Cangur category   |  |  - Real-time agent intervention banners    |
|  - Distributions: Hint depth & cognitive load     |  |  - Hardware telemetry monitor              |
|  - Time-series: Error cluster drift & agent ROI   |  +--------------------------------------------+
+---------------------------------------------------+
```

---

## 2. Strict Directory Structure & Ownership Matrix

To achieve **zero Git merge conflicts**, every developer owns an exclusive root directory. **No developer edits files outside their folder.** Integration happens exclusively through the frozen data contracts in Section 3.

```text
Datathon-FME-2026/
├── README.md                     # Project overview (Dev 4 updates at H20 after freeze)
├── TEAM_EXECUTION_PLAN.md        # Master source of truth (frozen contracts)
├── .env.example                  # Shared template (keys/URLs; never commit .env)
├── hardware/                     # [DEV 1 EXCLUSIVE]
│   ├── firmware/
│   │   └── socraticq_mcu.ino     # Arduino sketch: dials, buttons, RGB LEDs, UART JSON
│   ├── bridge/
│   │   ├── serial_daemon.py      # Reads MCU UART -> calls /engine & spools telemetry
│   │   └── mock_mcu.py           # Keyboard/CLI simulator for testing without board
│   └── requirements.txt
├── engine/                       # [DEV 2 EXCLUSIVE]
│   ├── data/
│   │   ├── parse_cangur.py       # CAM dataset (2007-2024) parser -> structured JSONL/SQLite
│   │   └── distill_socratic.py   # HelmCode GLM 5.3 synthetic Socratic pair generator
│   ├── models/                   # Local GGUF / ONNX quantized weights (.gitignored)
│   ├── socratic_engine.py        # Public `generate_socratic_hint(...)` API + llama.cpp wrapper
│   ├── prompts.py                # Socratic guardrails (zero answer leakage)
│   └── requirements.txt
├── agent_rawtree/                # [DEV 3 EXCLUSIVE]
│   ├── rawtree_client.py         # JSON-in / SQL-out HTTP client + retry/offline buffer
│   ├── schema.sql                # DDL / table definitions for telemetry_events & agent_insights
│   ├── autonomous_agent.py       # HelmCode GLM 5.3 Observe->SQL->Reflect->Write loop
│   ├── web_app.py                # FastAPI local UI + telemetry ingest endpoint (/ingest)
│   └── requirements.txt
└── analytics_tukey/              # [DEV 4 EXCLUSIVE]
    ├── app.py                    # Streamlit + Plotly Tukey-grade dashboard
    ├── queries.py                # SQL queries pulling from RawTree -> DataFrames
    ├── charts.py                 # Boxplots, ECDF/violin distributions, time-series, error clusters
    ├── docs/
    │   ├── arduino_project_hub.md# Hardware write-up & wiring diagram for Qualcomm submission
    │   └── demo_script.md        # Stage cues & judge Q&A cheat sheet
    └── requirements.txt
```

### Ownership & Parallelism Rules

| Developer | Exclusive Folder | Core Deliverable | Mock / Unblock Strategy (Hours 0–6) |
| :--- | :--- | :--- | :--- |
| **Dev 1** | `/hardware` | MCU firmware + PySerial/Unix socket bridge | If board wiring is in progress, test `serial_daemon.py` against `mock_mcu.py` stdin JSON lines. |
| **Dev 2** | `/engine` | CAM parser + QRB2210 quantized SLM + `socratic_engine.py` | Build `socratic_engine.py` with a `--mock` deterministic fallback first so Dev 1 & 3 can call the exact signature immediately. |
| **Dev 3** | `/agent_rawtree` | RawTree client + GLM 5.3 autonomous agent + FastAPI local app | Seed 200 synthetic `telemetry_events` from CAM categories into RawTree at H4 so Dev 3 & Dev 4 have live SQL data immediately. |
| **Dev 4** | `/analytics_tukey` | Tukey Streamlit/Plotly dashboard + Project Hub docs + Pitch | Build dashboard against a local CSV/DuckDB mirror matching `schema.sql` first, then flip `DATA_SOURCE=rawtree` once Dev 3's client is live. |

### Git Branching Discipline
- `main` stays deployable at all times.
- Work on `dev1/hardware`, `dev2/engine`, `dev3/agent`, `dev4/tukey`.
- Pull `main` and merge only touches inside your own directory; cross-folder contract changes require a 2-minute verbal sync and an update to `TEAM_EXECUTION_PLAN.md`.

---

## 3. Exact Data & API Contracts

These contracts are **frozen at Hour 1**. All four developers code directly against these schemas.

### 3.1 Hardware UART JSON Payload (MCU <-> Linux Daemon)

Newline-delimited JSON (`\n` terminated) over UART (`115200` baud; verify exact device node on the Uno Q image, e.g., `/dev/ttyHS1` or `/dev/ttyACM0`, against the Qualcomm starter repo).

**MCU -> Linux Daemon (Event Frame)**
```json
{
  "schema_v": 1,
  "device_id": "unoq-01",
  "seq": 42,
  "ts_ms": 184290,
  "event_type": "HINT_REQUEST",
  "problem_id": "CAM-2023-N4-17",
  "dial_category_idx": 2,
  "confidence_dial": 35,
  "selected_option": "C",
  "attempt_num": 2
}
```

- `event_type` enum: `"PROBLEM_SELECT"` | `"HINT_REQUEST"` | `"ANSWER_SUBMIT"` | `"CONFIDENCE_UPDATE"` | `"SESSION_RESET"`
- `dial_category_idx` (`0..4`): `0=All`, `1=Geometry`, `2=Combinatorics`, `3=NumberTheory`, `4=Algebra`
- `confidence_dial` (`0..100`): Analog potentiometer mapped to student self-assessed certainty.
- `selected_option`: `"A"`..`"E"` or `null` when `event_type != "ANSWER_SUBMIT"`.

**Linux Daemon -> MCU (Feedback Frame)**
```json
{
  "schema_v": 1,
  "ack_seq": 42,
  "led_state": "SOCRATIC_NUDGE",
  "rgb": [255, 180, 0],
  "buzz_pattern": "SHORT_DOUBLE",
  "lcd_line1": "Check parity of",
  "lcd_line2": "the corner tiles"
}
```
- `led_state` enum: `"IDLE"` (blue), `"THINKING"` (pulsing cyan), `"SOCRATIC_NUDGE"` (amber), `"SOLVED"` (green), `"BOTTLENECK_ALERT"` (purple, triggered by RawTree agent intervention).

---

### 3.2 Socratic Engine Function Signature & JSON Response (`/engine/socratic_engine.py`)

Dev 1's `serial_daemon.py` and Dev 3's `web_app.py` import and invoke this exact Python interface:

```python
from typing import Literal, Optional, TypedDict

class SocraticResponse(TypedDict):
    problem_id: str
    category: Literal["Geometry", "Combinatorics", "NumberTheory", "Algebra", "Logic"]
    difficulty_level: int          # 1 (3-pt) | 2 (4-pt) | 3 (5-pt) Cangur tier
    hint_text: str                 # <= 240 chars; strictly Socratic (never states final option A-E)
    lcd_summary: str               # <= 32 chars for optional 16x2 display / LED tag
    pedagogical_move: Literal[
        "DECOMPOSE_PROBLEM",
        "COUNTEREXAMPLE_CHECK",
        "INVARIANT_NUDGE",
        "PARITY_CHECK",
        "BOUNDARY_TEST",
        "AFFIRM_CORRECT"
    ]
    is_correct: Optional[bool]     # None if event_type == "HINT_REQUEST"
    misconception_tag: str         # e.g., "off_by_one_counting", "angle_sum_confusion", "none"
    latency_ms: int
    model_id: str                  # e.g., "qwen2.5-math-1.5b-q4_k_m"
    offline_mode: bool

def generate_socratic_hint(
    problem_id: str,
    event_type: Literal["PROBLEM_SELECT", "HINT_REQUEST", "ANSWER_SUBMIT"],
    attempt_num: int,
    confidence_pct: int,
    selected_option: Optional[str] = None,
    student_note: Optional[str] = None,
    agent_override_nudge: Optional[str] = None,
) -> SocraticResponse:
    ...
```

**Example JSON Output:**
```json
{
  "problem_id": "CAM-2023-N4-17",
  "category": "Combinatorics",
  "difficulty_level": 2,
  "hint_text": "You picked C with 35% confidence. Before counting every path, what happens if you color the grid like a chessboard—can two adjacent steps land on the same color?",
  "lcd_summary": "Try chessboard parity",
  "pedagogical_move": "PARITY_CHECK",
  "is_correct": false,
  "misconception_tag": "ignored_parity_invariant",
  "latency_ms": 640,
  "model_id": "qwen2.5-math-1.5b-q4_k_m",
  "offline_mode": true
}
```

---

### 3.3 RawTree Agent Schema & Core SQL Queries (`/agent_rawtree/schema.sql`)

All team-owned tables are prefixed or scoped to the team workspace/API key per RawTree hackathon rules (alongside read-only awareness of shared `shop.catalog` and `shop.events` if we include an optional cross-domain benchmark or store-style resource allocation check).

#### Table 1: `telemetry_events` (JSON-in ingestion from Edge / Web UI)
```sql
CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id          VARCHAR(64) PRIMARY KEY,
    session_id        VARCHAR(64) NOT NULL,
    device_id         VARCHAR(32) NOT NULL,
    ts_utc            TIMESTAMP NOT NULL,
    problem_id        VARCHAR(32) NOT NULL,
    cangur_year       INTEGER NOT NULL,
    category          VARCHAR(32) NOT NULL,
    difficulty_level  INTEGER NOT NULL,
    event_type        VARCHAR(24) NOT NULL,      -- HINT_REQUEST | ANSWER_SUBMIT | SOLVED
    attempt_num       INTEGER NOT NULL,
    confidence_pct    INTEGER NOT NULL,          -- 0..100 from hardware dial
    is_correct        BOOLEAN,
    misconception_tag VARCHAR(64),
    pedagogical_move  VARCHAR(48),
    solve_elapsed_ms  INTEGER NOT NULL,
    slm_latency_ms    INTEGER NOT NULL,
    offline_cached    BOOLEAN NOT NULL DEFAULT FALSE
);
```

#### Table 2: `agent_insights` (Autonomous RawTree Scratchpad & Interventions)
```sql
CREATE TABLE IF NOT EXISTS agent_insights (
    insight_id        VARCHAR(64) PRIMARY KEY,
    created_at_utc    TIMESTAMP NOT NULL,
    cycle_num         INTEGER NOT NULL,
    insight_type      VARCHAR(32) NOT NULL,      -- BOTTLENECK | CALIBRATION_GAP | INTERVENTION
    target_category   VARCHAR(32) NOT NULL,
    target_problem_id VARCHAR(32),
    severity_score    DOUBLE PRECISION NOT NULL, -- 0.0..1.0
    sql_executed      TEXT NOT NULL,             -- Exact SQL the agent wrote & ran
    evidence_summary  TEXT NOT NULL,             -- Quantitative finding from SQL-out
    recommended_nudge TEXT NOT NULL,             -- Fed back to Socratic Engine / Mentor UI
    status            VARCHAR(16) NOT NULL       -- ACTIVE | APPLIED | RESOLVED
);
```

#### Core Analytical SQL Queries (Used by Dev 3 Agent & Dev 4 Tukey Dashboard)

1. **Detect Classroom Bottlenecks (High Attempts + Low Solve Rate + Misconception Clustering):**
```sql
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
WHERE ts_utc >= NOW() - INTERVAL '2 hours'
GROUP BY category, problem_id
HAVING COUNT(*) >= 3 AND AVG(attempt_num) >= 2.2
ORDER BY avg_attempts DESC, accuracy_rate ASC
LIMIT 10;
```

2. **Dunning-Kruger / Calibration Gap (Hardware Confidence Dial vs. Actual Correctness):**
```sql
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
WHERE event_type = 'ANSWER_SUBMIT' AND is_correct IS NOT NULL
GROUP BY category, difficulty_level, confidence_band
ORDER BY category, difficulty_level;
```

3. **Tukey Spread Query (Raw Observations for Boxplots, IQR & Outlier Detection):**
```sql
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
ORDER BY ts_utc DESC
LIMIT 2000;
```

4. **Shared RawTree Table Readiness / Cross-Check (Optional Bonus Verification):**
```sql
-- Verify connectivity against RawTree shared baseline tables before running team loop
SELECT table_schema, COUNT(*) AS row_count
FROM (
    SELECT 'shop.catalog' AS table_schema FROM shop.catalog
    UNION ALL
    SELECT 'shop.events' AS table_schema FROM shop.events
) t
GROUP BY table_schema;
```

---

## 4. Hourly Milestones (Hours 0–24)

| Window | Dev 1 (`/hardware`) | Dev 2 (`/engine`) | Dev 3 (`/agent_rawtree`) | Dev 4 (`/analytics_tukey`) |
| :--- | :--- | :--- | :--- | :--- |
| **H0–H2** *(Foundation & Freeze)* | Inspect Uno Q repo (`datathon-UPC-maths-qualcomm-arduino-unoq`), confirm pinout, UART device path, and flash LED/button blink test. | Download/verify CAM Proves Cangur dataset (2007–2024). Write `parse_cangur.py` to normalize problems, options A–E, answers, and categories. | Obtain RawTree API key & endpoint. Test `JSON-in` / `SQL-out` against `shop.catalog`/`shop.events` and create `telemetry_events` & `agent_insights`. | Scaffold Streamlit app + `charts.py` with local mock DataFrame matching `telemetry_events`. Define Tukey palette, typography, and layout. |
| **H2–H6** *(Core Silos)* | Implement `socraticq_mcu.ino` (dial, hint button, submit button, RGB LED states) + `mock_mcu.py`. | Quantize/deploy local SLM (`Qwen2.5-Math-1.5B` or `SmolLM2`) on QRB2210 via `llama.cpp` / ONNX. Benchmark tokens/sec & RAM. | Build `rawtree_client.py` + FastAPI `/ingest` endpoint. Generate 200 realistic Cangur telemetry events to seed RawTree. | Build core Tukey charts: Category Solve-Time Boxplots (with IQR & outliers), Confidence Calibration ECDF/Violin, and Time-Series Error Drift. |
| **H6–H12** *(Intelligence & Integration Gate 1 at H10)* | Build `serial_daemon.py` calling Dev 2's `generate_socratic_hint()` and forwarding telemetry JSON to Dev 3's `/ingest` (with local SQLite spool). | Use HelmCode (GLM 5.3) to synthetically distill 500 Socratic hints over CAM problems for few-shot/LoRA/prompt tuning. Lock zero-answer-leakage regex/checks. | Implement `autonomous_agent.py` (GLM 5.3 loop): runs every 30s, writes dynamic SQL against RawTree, detects bottlenecks, inserts into `agent_insights`. | Connect `queries.py` to Dev 3's live RawTree SQL-out endpoint. Add "Agent Insights Timeline" panel showing live SQL & interventions. |
| **H12–H16** *(Closed-Loop Polish — Gate 2 at H15)* | Add offline-resilience: when Wi-Fi is cut, `serial_daemon.py` queues telemetry locally and triggers purple LED when cached agent interventions arrive. | Optimize QRB2210 inference latency (<1.5s per hint): warm model daemon, KV-cache reuse, strict max tokens (`<=80`). | Wire `agent_insights` back to `web_app.py` and `socratic_engine.py` (`agent_override_nudge`) so the agent visibly changes hardware tutoring behavior. | Add Tukey residual/error-cluster heatmap (Misconception × Category) + side-by-side "Before vs. After Agent Intervention" comparison chart. |
| **H16–H20** *(Feature Freeze at H18)* | Stress-test 50 consecutive hardware interactions offline on battery/USB. Lock enclosure/breadboard neatly so wires cannot pop on stage. | Run automated eval on 50 CAM problems: verify 0% direct answer leakage and <2s latency on QRB2210. | Verify RawTree agent handles bad/empty SQL gracefully (self-correcting SQL retry loop with GLM 5.3) and logs clean audit trail. | Draft Arduino Project Hub documentation (`arduino_project_hub.md`) with wiring photos, architecture diagram, and metric screenshots. |
| **H20–H24** *(Rehearsal & Submissions)* | Pack hardware kit + backup USB cables + offline hotspot fallback. Verify instant boot-to-ready script. | Pre-warm SLM on Uno Q 15 minutes before judging. Verify zero external API calls needed in `engine/`. | Clear junk test rows, re-seed crisp baseline classroom cohort in RawTree, verify live agent loop runs cleanly. | Finalize 3-minute stage pitch (`demo_script.md`), record 60s backup demo video, submit all 3 challenge forms before deadline. |

---

## 5. Setup & Environment Guide

> **Note on Hackathon Credentials:** Fill in the exact venue-provided URLs, API keys, and hardware device nodes in `.env` at the root (never commit `.env` to Git).

### 5.1 Environment Template (`.env.example`)
```bash
# RawTree Configuration (Challenge 1)
RAWTREE_API_URL="https://<venue-rawtree-host>"
RAWTREE_API_KEY="<your-team-rawtree-api-key>"
RAWTREE_TEAM_SCHEMA="<your-team-namespace>"

# HelmCode GLM 5.3 Configuration (Distillation + Autonomous Agent)
HELMCODE_BASE_URL="https://<venue-helmcode-endpoint>/v1"
HELMCODE_API_KEY="<your-helmcode-api-key>"
HELMCODE_MODEL="glm-5.3"

# Edge Hardware & Engine
UNOQ_SERIAL_PORT="/dev/ttyHS1"   # Verify on QRB2210 (could be /dev/ttyACM0 or /dev/ttyUSB0)
UNOQ_BAUD_RATE="115200"
SLM_MODEL_PATH="./engine/models/qwen2.5-math-1.5b-instruct-q4_k_m.gguf"
INGEST_URL="http://127.0.0.1:8000/ingest"
OFFLINE_SPOOL_DB="./hardware/spool.db"
```

### 5.2 Arduino Uno Q (QRB2210 Debian Linux) Setup
1. **Clone Qualcomm Starter Repo & Project Repo:**
   ```bash
   git clone https://github.com/mpous/datathon-UPC-maths-qualcomm-arduino-unoq.git ~/unoq-starter
   git clone <your-private-repo-url> ~/Datathon-FME-2026
   cd ~/Datathon-FME-2026
   ```
2. **System Dependencies & Serial Permissions (Debian ARM64):**
   ```bash
   sudo apt-get update && sudo apt-get install -y \
     build-essential cmake git python3-venv python3-dev sqlite3
   sudo usermod -aG dialout $USER
   # Log out and back in (or `newgrp dialout`) so PySerial can read the MCU UART
   ```
3. **Python Virtual Environments & Local SLM (`llama-cpp-python` or `llama.cpp`):**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install pyserial fastapi uvicorn streamlit plotly pandas requests openai
   # Build llama-cpp-python on ARM64 with OpenBLAS/CPU optimizations
   CMAKE_ARGS="-DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS" pip install llama-cpp-python
   ```
   *(If `llama-cpp-python` wheel compilation is slow on the board, build `llama.cpp` CLI binary directly with `cmake -B build && cmake --build build -j4` and invoke `llama-server` on `127.0.0.1:8081` as a persistent local daemon — zero cold-start latency between hints.)*

### 5.3 RawTree API Connection Verification (Dev 3 & Dev 4)
Run a quick smoke test in `/agent_rawtree/rawtree_client.py` verifying both **SQL-out** on shared tables and **JSON-in** to `telemetry_events`:
```bash
source .venv/bin/activate
python -c "
from agent_rawtree.rawtree_client import RawTreeClient
client = RawTreeClient()
print(client.query_sql('SELECT COUNT(*) AS n FROM shop.catalog'))
client.create_tables_from_file('agent_rawtree/schema.sql')
print('RawTree schema ready.')
"
```

### 5.4 HelmCode GLM 5.3 Configuration (Dev 2 & Dev 3)
Use an OpenAI-compatible client pointed at `HELMCODE_BASE_URL` for both Dev 2's synthetic Socratic distillation (`/engine/data/distill_socratic.py`) and Dev 3's autonomous RawTree agent (`/agent_rawtree/autonomous_agent.py`):
```python
import os
from openai import OpenAI

llm = OpenAI(
    base_url=os.environ["HELMCODE_BASE_URL"],
    api_key=os.environ["HELMCODE_API_KEY"],
)
```

### 5.5 Local Services & Streamlit Dashboard Launch
Open three terminals (or a `tmux` session) for full-stack local testing:
```bash
# Terminal 1: FastAPI Local Web App + Telemetry Ingest + Autonomous RawTree Agent
uvicorn agent_rawtree.web_app:app --host 0.0.0.0 --port 8000

# Terminal 2: Autonomous RawTree Cognitive Loop (Observe -> SQL -> Intervene)
python -m agent_rawtree.autonomous_agent --interval 20

# Terminal 3: Comissio Tukey Streamlit Dashboard
streamlit run analytics_tukey/app.py --server.port 8501

# Terminal 4 (On Uno Q): Hardware Serial Bridge (use --mock on laptops without board)
python -m hardware.bridge.serial_daemon
```

---

## 6. Unified 3-Minute Live Stage Demo Script

**Goal:** Show all three prize tracks firing from **one continuous physical interaction** in 180 seconds, with zero dead air.

### Pre-Stage Checklist (T-5 Minutes)
- Uno Q powered on, `llama-server` pre-warmed (first dummy query already executed).
- Laptop screen split or clean browser tabs ready:
  1. Tab 1: Local SocraticQ Student/Bridge Log (`:8000`)
  2. Tab 2: RawTree Autonomous Agent Live Console (showing SQL queries & scratchpad writes)
  3. Tab 3: Comissió Tukey Streamlit Dashboard (`:8501`)
- Classroom baseline seeded: 40 synthetic student sessions struggling on `CAM-2023-N4-17` (Combinatorics).

---

### Stage Choreography (0:00 – 3:00)

| Time | Speaker | Track Triggered | On-Stage Action & Exact Script |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:30** | **Dev 4 (Pitch Lead)** | *Hook & Problem* | *"In Catalonia, thousands of students tackle Proves Cangur every year, yet AI tutors either give away the answer or require cloud Wi-Fi. Meet **SocraticQ**: a €600-class offline edge mentor on the Arduino Uno Q, paired with an autonomous RawTree cognitive loop and a John Tukey diagnostic cockpit."* |
| **0:30 – 1:15** | **Dev 1 + Dev 2** *(Holding Uno Q)* | **Main Track: Qualcomm & Arduino Uno Q (€600)** | **Action:** Dev 1 visibly **unplugs Wi-Fi / toggles Airplane Mode** on the demo display.<br>**Dev 1:** Turns the physical rotary dial to `Combinatorics` (`CAM-2023-N4-17`), sets the **Confidence Dial** low (`30%`), and presses the **Hint Button**.<br>**Visual Cue:** MCU LED pulses cyan (`THINKING`), then turns amber (`SOCRATIC_NUDGE`) in `<1.2s`.<br>**Dev 2 (points to Tab 1):** *"Running 100% offline on the QRB2210 with a quantized local SLM, SocraticQ refuses to spoil option C. Instead, it reads the student's low confidence dial and asks a targeted chessboard-parity question."* |
| **1:15 – 2:05** | **Dev 3** | **Side Challenge 1: Best Agentic Use of RawTree (€200)** | **Action:** Reconnect sync / click **"Flush Edge Spool"** (or run live on secondary connection).<br>**Dev 3 (switches to Tab 2):** *"Once telemetry lands via JSON-in into RawTree's `telemetry_events`, our autonomous GLM 5.3 agent wakes up. Watch its cognitive scratchpad: it didn't hardcode alerts—it wrote and executed its own `GROUP BY` SQL query against RawTree, discovered that 78% of students are stuck on `ignored_parity_invariant` on this exact Cangur problem, and wrote an `INTERVENTION` row into `agent_insights`."*<br>**Visual Cue:** Uno Q LED flashes **purple (`BOTTLENECK_ALERT`)** as the proactive intervention propagates back to the edge mentor. |
| **2:05 – 2:45** | **Dev 4** | **Side Challenge 2: Comissió Tukey (€200)** | **Action:** Switch to **Tab 3 (Tukey Dashboard)**.<br>**Dev 4:** *"Raw averages hide classroom failure. Following John Tukey's exploratory principles, our dashboard reads directly from RawTree SQL-out to expose distribution shape, not just means:"*<br>1. Point to **Boxplots**: *"Look at the bimodal whisker spread in Combinatorics—half the class solves in 35s, while the upper outlier tail stalls past 210s."*<br>2. Point to **Confidence Calibration Plot**: *"Cross-referencing the hardware confidence dial against empirical accuracy reveals overconfident misconception clusters."*<br>3. Point to **Time-Series Intervention Line**: *"After the RawTree agent pushed the parity nudge, median attempts dropped from 3.4 to 1.6."* |
| **2:45 – 3:00** | **Dev 4** | *Closing* | *"Offline edge tutoring on Arduino Uno Q, autonomous SQL cognition in RawTree, and Tukey-grade statistical clarity—three layers, one closed pedagogical loop. Thank you!"* |

### Stage Fallback Protocol
- **If venue Wi-Fi dies completely during demo:** Keep Uno Q running offline for the entire Qualcomm demo (0:30–1:30), then switch RawTree Agent & Tukey Dashboard to `--local-mirror` mode (SQLite/DuckDB copy of the exact RawTree schema) while transparently noting the offline fallback.
- **If the physical dial wire loosens:** `serial_daemon.py --mock` accepts identical JSON payloads from keyboard shortcuts (`[1]` Hint, `[2]` Submit Wrong, `[3]` Submit Correct) so the software loop never stalls.
