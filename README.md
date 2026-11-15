# SocraticQ — Offline Edge Math Copilot on Arduino UNO Q

**UPC Datathon FME 2026** — An offline-first, deterministic Kangaroo Math (*Proves Cangur*) copilot and Socratic tutor running on the **Arduino UNO Q** (Qualcomm QRB2210 4 GB + Arduino MCU), paired with **RawTree** autonomous SQL diagnostics and a **Comissió Tukey** exploratory analytics cockpit.

> **Note on M3Kang Data:** Per the [M3Kang](https://huggingface.co/datasets/qualcomm/M3Kang) research-only license, no M3Kang `.jsonl`, `.parquet`, images, or `index.db` files are included in this repository. Only the 5 synthetic sample problems (`sample_problems.jsonl`) and reproducible scripts are committed.

---

## 1. Architecture Overview

```text
+-----------------------------------------------------------------------+
|                    ARDUINO UNO Q (100% OFFLINE)                       |
|                                                                       |
|  [Browser UI + Mic (HTTPS :8765)]     [Arduino MCU (socraticq_mcu)]   |
|       |  WebAudio PCM + Image              |  UART / RouterBridge     |
|       v                                    v                          |
|  [chat_server.py]  <--->  [engine/ + copilot/pipeline.py]             |
|       |                        |-- llama-server :8081 (Qwen3.5-0.8B)  |
|       |                        |-- llama-server :8082 (bge-small)     |
|       |                        +-- whisper.cpp  (ggml-tiny-q5_1)      |
|       v                                                               |
|  [agent_rawtree/] (JSON-in / SQL-out + SQLite offline mirror)         |
|       |                                                               |
|       v                                                               |
|  [analytics_tukey/] (Live Tukey EDA + Boxplots + Pre/Post Charts)     |
+-----------------------------------------------------------------------+
```

All cross-module payloads are validated against frozen standard-library contracts in [`contracts/schemas.py`](contracts/schemas.py).

---

## 2. Our Strategy for `predict.py` (Accuracy + ≤60s Latency)

Running a 0.8B model on a 4-core ARM CPU (`~11.5 tok/s` prefill, `~5.6 tok/s` decode) under a strict 60-second limit fails if you ask for long Chain-of-Thought or uncalibrated 1-token answers (the official baseline gets **22.5%** with heavy `D`/`A` bias). Our cascade in [`engine/mcq_optimizer.py`](engine/mcq_optimizer.py) and [`datathon-UPC-maths-qualcomm-arduino-unoq-main/copilot/pipeline.py`](datathon-UPC-maths-qualcomm-arduino-unoq-main/copilot/pipeline.py) solves this in 5 stages:

1. **OCR Artifact Normalization ([`engine/option_normalizer.py`](engine/option_normalizer.py)):**
   Cleans RapidOCR glitches (`"2 0"` → `"20"`, `"1 16"` → `"16"`, `"一1/3"` → `"-1/3"`, LaTeX `{,}` decimals, `"8: 45"` → `"8:45"`) and parses exact `Fraction` values and Catalan names.
2. **Parameterized Neuro-Symbolic Solvers (<5 ms) ([`engine/symbolic_solvers.py`](engine/symbolic_solvers.py)):**
   Deterministic Python solvers (arithmetic, modular/LCM, 3D polyhedra/Euler, Pythagoras, digit combinatorics, probability, races, clocks, coins, knights & liars) verified uniquely against the 5 options `A–E`.
3. **Multilingual Bridge & Zero-Token Hybrid RAG ([`copilot/pipeline.py`](datathon-UPC-maths-qualcomm-arduino-unoq-main/copilot/pipeline.py), [`copilot/rag.py`](datathon-UPC-maths-qualcomm-arduino-unoq-main/copilot/rag.py)):**
   `bge-small-en-v1.5` is English-only and `Qwen3.5-0.8B` scores higher in English than Catalan. When a Catalan problem has an English sibling by `id`, the bridge supplies clean English text (never touching labels) and computes quadratic similarity-weighted `knn_votes` from `data/index.db` without inflating prompt tokens.
4. **Damped Empirical Logprob Calibration (SLM Path):**
   Extracts `top_logprobs=20` from `root ::= [A-E]` on `Qwen3.5-0.8B` and subtracts a **0.6× damped empirical mean logprob baseline** (`_EMPIRICAL_LP_BASELINE`), removing positional bias toward `A` without crushing `A`/`B` to zero. When the top-2 margin is narrow (`<0.22`), a **KV-cached Micro-PoT** step reuses the shared prompt prefix.
5. **Visual Fallback ([`engine/visual_solver.py`](engine/visual_solver.py)) & 3-Zone OCR ([`engine/ocr_engine.py`](engine/ocr_engine.py)):**
   Handles `text_only=False` problems using structural figure rules, `(is_visual, level, qnum)` smoothed priors, `knn_votes`, and 1-pass calibrated SLM logprobs.

---

## 3. Replicate Step-by-Step on Arduino UNO Q

### Step 1: Clone & Verify Contracts
```bash
git clone https://github.com/mark1u5/SocraticQ-Arduino-UnoQ.git
cd SocraticQ-Arduino-UnoQ
python3 -m contracts.check_contracts
```

### Step 2: Set Up `llama.cpp` & Models on the Board
Follow [`datathon-UPC-maths-qualcomm-arduino-unoq-main/README.md`](datathon-UPC-maths-qualcomm-arduino-unoq-main/README.md) (Steps 0–7) using [`config.env`](datathon-UPC-maths-qualcomm-arduino-unoq-main/config.env) (`Qwen_Qwen3.5-0.8B-Q8_0.gguf` on `:8081` with `--reasoning off`, `bge-small-en-v1.5-q8_0.gguf` on `:8082`).

### Step 3: Prepare M3Kang Data (On Laptop) & Copy to Board
```bash
cd datathon-UPC-maths-qualcomm-arduino-unoq-main
python3 -m venv .venv && source .venv/bin/activate
pip install -r laptop/requirements.txt
python3 laptop/prepare_m3kang.py --languages eng,cat
# Optional 3-phase option recovery (requires VLM endpoint):
# python3 laptop/salvar_38.py
scp data/train.jsonl data/val.jsonl arduino@<BOARD_IP>:~/SocraticQ-Arduino-UnoQ/datathon-UPC-maths-qualcomm-arduino-unoq-main/data/
```

### Step 4: Build the Retrieval Index & Run Official Evaluation (On Board)
```bash
cd ~/SocraticQ-Arduino-UnoQ/datathon-UPC-maths-qualcomm-arduino-unoq-main
python3 build_index.py --input data/train.jsonl --db data/index.db --only-with-options
python3 predict.py --input data/sample_problems.jsonl --output results/smoke.csv
python3 eval.py --input data/val.jsonl --limit 50 --rag 1 --name standard_50_calibrated
```

### Step 5: Launch the Offline HTTPS Web Copilot + Voice (Whisper.cpp) + Tukey Cockpit
Browsers require HTTPS for `navigator.mediaDevices.getUserMedia` on LAN IPs. `chat_server.py --https` auto-generates a self-signed TLS certificate via `openssl`:
```bash
cd ~/SocraticQ-Arduino-UnoQ
python3 chat_server.py --host 0.0.0.0 --port 8765 --https
```
Open `https://<BOARD_IP>:8765/` for the Socratic Voice + OCR Chat UI, and `https://<BOARD_IP>:8765/static/tukey.html` for the **Comissió Tukey & RawTree Live** dashboard.

---

## 4. Measured Results (Arduino UNO Q QRB2210)

| Benchmark | Accuracy | Mean Time | Max Time | Timeouts (>60 s) | Notes |
| --- | --- | --- | --- | --- | --- |
| Official Baseline (`README.md` §11.3, 40 `eng` text-only) | 22.5% (`9/40`) | 8.7 s | 12.7 s | 0 | Heavy `D`/`A` bias (`21 D`, `14 A`) |
| **Ours — `val.jsonl` (`--limit 50 --rag 1`, `standard_50_calibrated`)** | **34.0%** (`17/50`) | **8.3 s** | **30.9 s** | **0** | **`40.0%` on SLM (`ok_logprob`), `42.9%` on `eng`** |

### What Didn't Work (Lessons Learned on QRB2210)
- **`Qwen2.5-Math-1.5B-Instruct-Q4_K_M`:** Prefill alone ran at `4.3–5.2 tok/s` (`22.5–37.2 s` before generating 1 token), causing frequent timeouts with CoT under the 60s limit.
- **Unconstrained Short-CoT (`max_tokens=80`) on `Qwen3.5-0.8B`:** Took `31–56 s` per problem and degraded accuracy (`2/12`) due to noisy intermediate arithmetic.
- **Full `1.0×` Logprob Debiasing:** Over-corrected `E` (`-6.58`) and `D` (`-3.44`), dropping `A` to 0 predictions (`24.0%`). Damping calibration to `0.6×` recovered `A`/`B` balance and lifted accuracy to **34.0%**.

---

## 5. Credits & License
- **Code License:** [MIT](LICENSE)
- **M3Kang Dataset:** Torres-Camps et al., 2026 ([arXiv:2601.16218](https://arxiv.org/abs/2601.16218)), Qualcomm — research use only.
- **Models & Tools:** `llama.cpp`, `Qwen3.5-0.8B`, `bge-small-en-v1.5`, `whisper.cpp`, `RapidOCR`.
