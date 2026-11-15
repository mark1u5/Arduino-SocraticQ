# SocraticQ — Architecture, AI-Safety & Resource Decisions (`DECISIONS.md`)

> **Context:** UPC Datathon 2026 Triple-Dip Submission (**Qualcomm & Arduino Uno Q €600**, **Best Agentic Use of RawTree €200**, **Comissió Tukey €200**).  
> **Theoretical Grounding:** Favero, Pérez-Ortiz, Käser & Oliver (2024), *"Enhancing Critical Thinking in Education by means of a Socratic Chatbot"* ([arXiv:2409.05511](https://arxiv.org/abs/2409.05511)).  
> **Review Method:** Every structural, safety, and resource decision below was audited by EGM subagents (`egm-zoom-out`, `egm-design`, `egm-inventive-catalyst`) before implementation.

---

## 1. Repository Unification (Decision D1 — EGM Zoom-Out)

- **Decision:** Unify all new features directly inside the existing `Datathon-FME-2026` repository (`engine/`, `agent_rawtree/`, `analytics_tukey/`, `hardware/`, `contracts/`) plus root entrypoints (`server.py`, `static/`, `launch.sh`, `ARDUINO_PROJECT_HUB.md`) rather than creating a disconnected `socratic-q/` duplicate tree.
- **Rationale:** Preserves all frozen contracts in `contracts/schemas.py`, keeps the working Arduino Uno Q MCU/LED bridge (`hardware/`) and RawTree SSE pipeline (`agent_rawtree/web_app.py`) intact, and guarantees zero split-brain state between hardware, web voice client, RawTree, and Tukey.
- **Consolidation:**
  - `agent_rawtree/web_app.py` remains the single FastAPI server on `:8000`, serving `static/index.html` at `/` and removing the legacy 350-line inline HTML template so there is only one UI to maintain.
  - Root `server.py` re-exports `agent_rawtree.web_app:app` so both `uvicorn server:app` and `uvicorn agent_rawtree.web_app:app` launch the identical unified backend.
  - Existing routes (`/ingest`, `/stream`, `/problems`, `/tutor/interact`, `/interventions/active`, `/stats`, `/health`) stay backward-compatible alongside new endpoints (`/api/voice-query`, `/api/text-query`, `/api/system-status`, `/api/guardrail-stats`).

---

## 2. QRB2210 Resource Budget & Concurrency (Decision D2 — EGM Design)

- **Hardware Envelope:** Arduino Uno Q — Qualcomm QRB2210 SoC (4x ARM Cortex-A53 @ 2.0 GHz, ~2 GB RAM, Debian Linux ARM64).
- **Peak Memory Budget (< 1.6 GB total to avoid OOM/swap thrashing):**
  - Debian OS + FastAPI + Uvicorn + SQLite WAL: ~280 MB
  - Quantized Local SLM (`llama-server` on `:8081` with `qwen3.5-0.8b` or `qwen2.5-math-1.5b-instruct-q4_k_m.gguf`, `-t 3 -c 512 --kv-unified`): ~650–1,050 MB
  - Local Offline STT (`sherpa-onnx` int8 Zipformer OR `pywhispercpp` / `whisper-cli` `tiny.en`, lazy-loaded): ~80–150 MB
  - **Off-Board Analytics:** Streamlit + Plotly (`analytics_tukey/app.py`, ~350–450 MB) runs off-board on a presenter laptop querying RawTree/mirror (`launch.sh --with-dashboard` opts in locally only when desired).
- **CPU & Contention Guard:**
  - An `asyncio.Semaphore(1)` / inference mutex serializes `engine/stt.py` and `engine/socratic_engine.py` so STT and SLM decoding never compete simultaneously across the 4 Cortex-A53 cores.
  - `llama-server` is pinned to 3 threads (`-t 3`) leaving 1 core responsive for Debian networking, FastAPI SSE, and Arduino UART bridge (`/dev/ttyHS1`).
  - `launch.sh` checks/configures 512 MB LZ4 `zram` advice on ARM64 Debian and avoids heavyweight dependencies (`torch`, `transformers`, `faster-whisper`).

---

## 3. Theoretical Grounding: arXiv:2409.05511 Socratic Taxonomy (Decision D3)

Following Favero et al. (*"Enhancing Critical Thinking in Education by means of a Socratic Chatbot"*, arXiv:2409.05511), the tutor refuses passive answer delivery ("AI off-loading") and scaffolds critical thinking through **Paul & Elder's 6 Socratic Dimensions**, mapped cleanly onto our frozen `PedagogicalMove` contract (zero breaking schema changes):

| Favero et al. (arXiv:2409.05511) Dimension | Frozen `PedagogicalMove` | Pedagogical Intent |
| :--- | :--- | :--- |
| **1. Clarification** | `DECOMPOSE_PROBLEM` | Isolate definitions, variables, or one smaller sub-problem. |
| **2. Probing Assumptions** | `COUNTEREXAMPLE_CHECK` | Challenge unexamined assumptions with a tiny concrete case or counterexample. |
| **3. Probing Reasons & Evidence** | `INVARIANT_NUDGE` | Require justification for what quantity/rule remains invariant across steps. |
| **4. Alternative Perspectives** | `PARITY_CHECK` | Reframe via symmetry, coloring, parity, or dual representation. |
| **5. Implications & Consequences** | `BOUNDARY_TEST` | Trace extreme, boundary, or worst-case logical consequences. |
| **6. Meta-Questioning** | `AFFIRM_CORRECT` / Judo Redirect | Turn focus back to *why* the reasoning holds or why an answer-shortcut fails. |

- **Prompt & Output Restraint (per arXiv:2409.05511):**
  - Strictly **one concise Socratic question** (`<= 240` chars, ends with `?`).
  - Zero empty sycophancy (`BANNED_PRAISE`), zero third-person `"the student"`, grounded solely in the student's active problem & immediate dialogue history (capped to compact state so sub-2B SLMs stay focused).

---

## 4. Strict AI-Safety & Math Tutoring Guardrails (Decision D4 — EGM Design + Inventive)

Sub-2B SLMs have weak instruction hierarchy and can hallucinate arithmetic or leak answers. We enforce a **3-Layer Defense-in-Depth Guardrail Pipeline** (`engine/prompts.py` + silent math verifier):

1. **Layer 1 — Pre-SLM Input Guardrail ("Socratic Judo"):**
   - Detects prompt injections / jailbreaks (`ignore previous`, `system prompt`, `jailbreak`, `DAN`, `act as`, `developer mode`), encoded bypasses (base64/hex blobs), binary-search extraction (`is x > 5`, `is it option B?`), direct cognitive offloading (`just give me the answer`, `solve this for me`, `what is x`), and off-topic/unsafe abuse.
   - **Never crashes or lectures harshly:** Routes directly (0 SLM tokens spent!) to an engaging arXiv:2409.05511 **Meta-Questioning / Counterexample Judo** response and records structured safety telemetry (`misconception_tag = "JAILBREAK_ATTEMPT" | "OFFLOADING_ATTEMPT" | "BINARY_SEARCH_PROBE" | "UNSAFE_OFFTOPIC"`).
2. **Layer 2 — Silent In-Process Math Oracle (Zero-Dep `ast` Verifier):**
   - For general algebra/arithmetic/linear/quadratic/calculus-derivative prompts (e.g., `2x + 5 = 17`, `x^2 - 5x + 6 = 0`, `3/4 + 1/2`), a safe stdlib `ast` & regex solver computes `forbidden_targets` (numeric values + simplified equivalents like `0.5` / `1/2` / `x = 6`) **silently** behind the scenes (<5 ms, <1 MB RAM) and separates them from `allowed_givens` already present in the student's prompt.
   - Never displays `forbidden_targets` to the student; uses them strictly to validate output safety and diagnose student numeric misconceptions!
3. **Layer 3 — Fail-Closed Post-SLM Output Guardrail:**
   - Rejects any SLM candidate that: lacks `?`, exceeds 240 chars, uses banned praise, parrots >72% of the problem stem, reveals `A-E` options, prints variable solution assignments (`x = 6`, `answer is 42`), or contains any `forbidden_target` not in `allowed_givens`.
   - On rejection, fails closed to a verified arXiv:2409.05511 Socratic template.

---

## 5. General Math + Hybrid Voice + RawTree/Tukey Loop (Decision D5)

- **General Math Scope:** `engine/cangur_bank.py` retains the curated CAM Proves Cangur quick-test library while expanding open-concept matching across **Algebra, Geometry, Calculus (derivatives/rates/limits), Number Theory, Combinatorics, and Word Problems**.
- **Hybrid Edge Voice Pipeline (`engine/stt.py` + `static/app.js`):**
  - Browser captures audio via `MediaRecorder` (`.webm`/`.wav`) with live `AnalyserNode` waveform visualization and sends to `POST /api/voice-query`.
  - Backend `engine/stt.py` runs offline `pywhispercpp` / `sherpa-onnx` / `whisper-cli` (`tiny.en`) when installed on QRB2210, and supports an optional browser `SpeechRecognition` fallback transcript + clean degraded fallback when developing off-board without native STT binaries.
  - Client also tracks **hesitation / utterance pause latency** (`solve_elapsed_ms`) paired with the **Confidence Dial** (`confidence_pct`) to expose Dunning-Kruger / overconfident-hesitation gaps in RawTree & Tukey.
- **HelmCode Off-Board Cloud Acceleration (Strictly Gated):**
  - `engine/socratic_engine.py` stays **100% offline-first** (`offline_mode=True` whenever `llama-server` or deterministic Socratic verifier handles the turn).
  - Optional HelmCode API fallback (`HELMCODE_BASE_URL` / `HELMCODE_API_KEY`, trying `glm-5.3`, `qwen-2.5-coder`, `deepseek-v3`) ONLY activates when explicitly enabled (`ENABLE_HELMCODE_FALLBACK=1`) and `llama-server` is unreachable off-board—never silently breaking the offline Qualcomm demo.
- **Tukey EDA Additions (`analytics_tukey/`):**
  - Keep all 8 existing Tukey EDA charts intact and add **Socratic Hint-Depth Survival / Drop-off Curve** + **AI-Safety & Socratic Judo Audit** querying live from RawTree / local SQLite mirror.

---

## 6. M3Kang 38% Option Recovery via Multilingual ID Alignment & VLM Distillation (Decision D6 v2.0)

- **The Problem (arXiv:2601.16218):**
  - The Qualcomm M3Kang dataset stores answer options exclusively inside problem images.
  - The baseline `prepare_m3kang.py` (RapidOCR ONNX) discarded 38% of problems (~669 problems) leaving `"options": {}` due to:
    1. Visual/diagrammatic options (drawings, geometric nets, puzzle pieces).
    2. Broken fractions ($\frac{1}{2}$ read as `12`, generating identical option collisions).
    3. Catalan typography/layout differences breaking horizontal regex heuristics.
- **Decision & 3-Phase Strategy (`laptop/salvar_38.py` v2.0):**
  1. **Phase 1 — Cross-Lingual Transfer with Strict Language Isolation:**
     - Uses `are_options_language_independent()` to ensure that only pure numbers, algebraic formulas, and international units are shared across canonical `id`s (`lvl-X_YYYY_ZZ`).
     - Natural language options and geometric descriptions are strictly isolated and never cross-pollinated between Catalan and English.
  2. **Phase 2 — Multimodal VLM Distillation with Native Image & Language Prompts:**
     - For problems lacking options, invokes `glm5.3-flash` (with `max_tokens=2500` to prevent reasoning truncation) on the native language image (`cat` image with Catalan prompt for Catalan; `eng` image for English).
     - Strictly enforces `len(set(options.values())) == 5` to reject any duplicate option hallucinations.
  3. **Phase 3 — Heuristic Fraction Reconstruction & Split Support:**
     - Implements `reconstruct_fractions()` to fix OCR-broken fractions (`1 2` -> `1/2`, monotonic fraction recovery) and discards collided duplicates.
     - Supports `--val-years` and deterministic SHA-256 partitioning (`--val-fraction`).
- **Audited Results:**
  - Real coverage reaches **95.3%** (3,330 / 3,494 problems with 5 distinct valid options).
  - **0 duplicate option collisions** (`len(set) == 5` verified across all records).
  - **Zero language contamination:** Catalan problems feature native Catalan geometric descriptions (e.g. *"peça amb osca a la part inferior"*, *"triangle amb estrella grisa"*).
  - Generates audited `train_recuperat.jsonl` (2,932 problems) and `val_recuperat.jsonl` (562 problems).

---

## 7. On-Device Evaluation & RAG Retrieval Benchmark (Decision D7 — Steps 10, 11, 12)

- **Execution Context:**
  - Board: Qualcomm QRB2210 quad-core ARM Cortex-A53 (Debian ARM64, 4 GB RAM) connected via ADB (`2930722764`).
  - Serving infrastructure: `math-llm.service` (`Qwen_Qwen3.5-0.8B-Q8_0.gguf` on `:8081`) and `math-embed.service` (`bge-small-en-v1.5-q8_0.gguf` on `:8082`).
- **Step 10 (ADB Data Transfer):**
  - Transferred `data/train.jsonl` (2,932 lines, 1.2 MB) and `data/val.jsonl` (562 lines, 240 KB).
  - Verified on-device line counts: `2932` train, `562` val (`3494` total).
- **Step 11 (Baseline Evaluation — 50 validation problems):**
  - **Accuracy:** 10 / 50 (20.0%) [Floor D: 22.0%, Random: 20.0%].
  - **Latency:** Mean 9.9 s, median 9.4 s, worst 15.5 s (0 timeouts over 60 s; 50/50 `ok`).
  - **Language breakdown:** Catalan 27.3% (6/22) vs. English 14.3% (4/28).
  - **Severe Letter Bias:** Predicted A: 20, B: 4, C: 1, D: 25, E: 0 (the model heavily favored A and D).
- **Step 12.1 (RAG Vector Index Generation):**
  - Built `data/index.db` (6.6 MB) using `build_index.py --only-with-options`.
  - Embedded **2,786 training problems** in 1,050 s (~2.7 embeddings/s) with `bge-small-en-v1.5`.
- **Step 12.2 (RAG Retrieval Evaluation — k=1, 50 validation problems):**
  - **Accuracy:** 10 / 50 (20.0%).
  - **Latency:** Mean 23.1 s, median 22.2 s, worst 34.4 s (all well within 60 s rule; 50/50 `ok`).
  - **Letter Bias Elimination:** Predictions shifted to **A: 13, B: 8, C: 9, D: 13, E: 7** (near-uniform distribution across all options, removing the degenerate A/D bias of the raw 0.8B model).
  - Logged to `results/ablations.csv`.

---

## 8. Speech-to-Text Deployment on Qualcomm QRB2210 via whisper.cpp (Decision D8)

- **Motivation & Edge Constraints:**
  - Need a speech-to-text pipeline to allow students to verbally state math problems to the Arduino UNO Q tutor.
  - The board has a quad-core ARM Cortex-A53 CPU and 4 GB RAM (with ~1.1 GB used by Qwen 0.8B + BGE embeddings). Python STT engines (PyTorch, heavy pip dependencies) would crash or cause memory thrashing.
- **Architectural Decision:**
  - Selected native C/C++ **`whisper.cpp`** built with ARM NEON SIMD and OpenMP on 4 CPU threads.
  - Compiled directly on the board (`/home/arduino/whisper.cpp/build/bin/whisper-cli`).
  - Model: Multilingual **`ggml-tiny-q5_1.bin`** (30.1 MB on disk, ~50 MB RAM at runtime, supports English, Catalan, and Spanish).
- **Integration & Verification:**
  - Created Python bridge `copilot/stt.py` (`transcribe(audio_path, language="auto")`).
  - Created end-to-end voice assistant CLI `ask_voice.py` on the board.
  - Live benchmark on Qualcomm QRB2210:
    - Audio input: 11.0 s JFK speech WAV sample.
    - STT transcription latency: **6.67 s** (100% exact text).
    - Qwen 0.8B reasoning latency: **23.24 s**.
    - Total voice-to-answer latency: **29.91 s** (well within the 60 s offline requirement).
    - Remaining available RAM: **2.8 GiB** (zero OOM risk).
