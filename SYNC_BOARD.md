# SYNC_BOARD.md — Live Integration Status

> Updated at each 2-hour sync on `main`. Agents: read this to know whether to run against `--mock` or live endpoints.

**Last Updated:** Hour 18+ (All 4 Tracks Integrated & Verified on `main`)

## Track Readiness
- [x] **Dev 1 (`/hardware`)**: LIVE & READY — `socraticq_mcu.ino`, `unoq_app.py`, `serial_daemon.py`, `mock_mcu.py`, offline SQLite spool, and `BOTTLENECK_ALERT` purple LED closed loop (`python -m unittest hardware.tests.test_bridge`).
  - Uno Q Serial/Bridge: Arduino RouterBridge (`unoq_app.py`) + UART/stdin (`serial_daemon.py`)
- [x] **Dev 2 (`/engine`)**: LIVE & READY — offline-capable `socratic_engine.py`, curated Proves Cangur bank (`cangur_bank.py`), zero-leakage guardrails (`prompts.py`), and `llama.cpp` `:8081` integration with deterministic fallback (`python -m engine.test_engine`).
  - Model on QRB2210: `qwen3.5-0.8b` (with deterministic offline fallback)
- [x] **Dev 3 (`/agent_rawtree`)**: LIVE & READY — `RawTreeClient` (live HTTP + offline SQLite mirror), GLM 5.3 two-stage `autonomous_agent.py` (with heuristic fallback), FastAPI `/ingest`, `/stream` SSE, `/problems`, `/tutor/interact`, `/interventions/active`, and `seed_events.py`.
  - Turnkey data connector: `from agent_rawtree.queries import fetch_tukey_datasets`
  - Seed dataset: 200 telemetry events + 3 insights + 3 traces ready in store.
  - Handoff doc for Dev 4: `agent_rawtree/DEV4_HANDOFF.md`
  - FastAPI `/ingest` URL: `http://127.0.0.1:8000/ingest`
  - Active interventions: `GET /interventions/active` (triggers Uno Q purple LED)
- [x] **Dev 4 (`/analytics_tukey`)**: LIVE & READY — dual-mode `queries.py` (Mode A offline mock / Mode B live RawTree), 8 Plotly Tukey EDA charts (`charts.py`), Streamlit cockpit (`app.py`), `docs/arduino_project_hub.md`, and `docs/demo_script.md`.
- [x] **Data Engineering & Benchmark (`laptop/salvar_38.py`)**: LIVE & READY — Salvaged the 38% M3Kang dataset drop (arXiv:2601.16218) with v2.0 quality audit. Reached **95.3% audited option coverage** (3,330/3,494 problems with 5 distinct options, 0 collisions, zero language contamination). Consolidates `train_recuperat.jsonl` (2,932 rows) and `val_recuperat.jsonl` (562 rows) with native Catalan/English descriptions for geometric options and reconstructed LaTeX fractions.
- [x] **On-Device Evaluation (Steps 10, 11, 12 on Uno Q)**: LIVE & VERIFIED — Datasets pushed over ADB (`2,932` train / `562` val lines). Baseline evaluation completed (20.0% accuracy, 9.9s avg latency). RAG vector index built (`data/index.db`, 6.6 MB, 2,786 problems). RAG evaluation (k=1) completed (20.0% accuracy, 23.1s avg latency, eliminating model letter bias).
- [x] **On-Device Speech-to-Text (`whisper.cpp` + `copilot/stt.py`)**: LIVE & VERIFIED — Native `whisper.cpp` compiled with NEON SIMD on QRB2210 ARM64. Quantized model `ggml-tiny-q5_1.bin` (30.1 MB, ~50 MB RAM) installed in `/home/arduino/models/`. End-to-end voice-to-LLM pipeline tested live with `ask_voice.py` (STT 6.67s, Qwen 0.8B 23.24s, total 29.91s, zero OOM, 2.8 GB RAM available).

## Integration Gates
- [x] **Gate 0 (H1):** Frozen contracts verified (`python -m contracts.check_contracts`).
- [x] **Gate 1 (H10):** `mock_mcu` / `serial_daemon.py` -> `socratic_engine.py` -> `/ingest` -> RawTree -> Tukey end-to-end smoke run verified.
- [x] **Gate 2 (H15):** RawTree `agent_insights` closed-loop intervention visibly updates edge tutor + purple LED (`BOTTLENECK_ALERT`), while correct submissions return green (`SOLVED`).
- [x] **Gate 3 (Data/M3Kang):** 3-Phase recovery verified (`laptop/salvar_38.py`: Phase 1 multilingual cross-referencing + Phase 2 HelmCode `glm5.3-flash` VLM distillation + Phase 3 split preservation).
- [x] **Gate 4 (Uno Q Edge Benchmark):** Steps 10, 11, and 12 executed live on Qualcomm QRB2210 via ADB. Baseline (9.9s) and RAG (23.1s) ablations logged to `results/ablations.csv`.
- [x] **Feature Freeze (H18):** Contracts, schema, and UI verified across all 4 tracks.

## Official Qualcomm Final Exam & Practice Set (`datathon-qualcomm-m3kang-format/`)
- **Practice Set:** 50 novel 2026 Kangaroo-style problems (`eng` + `cat` = 100 rows, 40 `text_only`, 10 with figures, balanced A–E).
- **Final Hidden Exam:** **20 novel 2026 problems** (not from M3Kang), 1 language each (`eng` or `cat`), levels `0–7`, 4 with figures, executed via `predict.py` on the Arduino UNO Q after **12:00 last call** (≤60 s/problem, 100% offline).
- **Commands:**
  - `python3 laptop/prepare_m3kang.py --parquet datathon-qualcomm-m3kang-format/data/demo-00000-of-00001.parquet --languages eng,cat --val-fraction 0 --out data/demo --save-images`
  - `python3 eval.py --input data/demo/train.jsonl --limit 0 --name demo`
