# Step 13 Brainstorm: Optimizing Exam Accuracy (50%), OCR Recovery (38%), and the Live Demo (50%)

> **Hardware & Rules Anchor (Arduino UNO Q — QRB2210, 3.6 GB RAM):**
> - **Prefill:** ~11 tok/s | **Decode:** ~5–6 tok/s | **Embeddings (`bge-small`):** <0.3 s/query
> - **Exam Constraints:** 1 letter (`A`–`E`), ≤60 s/problem, 100% offline, deterministic (`temperature=0`, fixed `seed`), fallback `D`
> - **Baseline to Beat:** `22.5%` (9/40) — currently *below* Always-`D` (`25.0%`), with heavy letter bias (`D` 21×, `A` 14×)
> - **Demo Constraints:** 3 minutes on stage, 100% offline, **8×13 LED matrix only** (no external modules), guides without giving away the answer

---

## 0. First 15 Minutes: Ask the Organizers 3 Questions
Before committing engineering hours to assumptions, ask the organizers:
1. **Test schema:** Does the hidden test `jsonl` include images, or only pre-OCR'd `options` (where ~38% may be `{}`)?
2. **Test source:** Are hidden test problems disjoint in `id` and `year` from M3Kang, or drawn from other years/languages?
3. **Twin retrieval:** Is `predict.py` allowed to reuse the answer of a near-identical training problem with a *different* `id` (e.g., cross-level Kangaroo twin) via embedding similarity?

---

## Part A: Maximizing Hidden Exam Accuracy (50% of Final Grade)

### Core Insight: Shift from "LLM as Oracle" to "LLM as One Weak Sensor"
At 11 tok/s prefill and 6 tok/s decode, chain-of-thought reasoning does not fit (and scored `17.5%` at 26.3 s in Step 11.3). Meanwhile, `0.8B` one-shot prompting is dominated by letter bias (`22.5%`).
The winning move is **decode-free logprob scoring + cheap feature fusion** — keeping the board to **one cached prefill per problem** and doing the rest with priors, structure, and tiny stdlib math.

### Ranked Accuracy Ideas

| Rank | Idea | Expected Gain | Effort | Latency Cost |
| :--- | :--- | :--- | :--- | :--- |
| **1** | **Prior-Based Fallback (kill hardcoded `D`)** — Compute argmax letter prior per `(level, question_number_band)` on `train.jsonl`. Use for timeouts AND whenever `options == {}` (don't even call the LLM when blind). | `+1–3%` (25%→27%+) | 30 min | **Saves ~9 s** on empty rows |
| **2** | **Contextual Logprob Calibration** — Request `top_logprobs=5` (`max_tokens=1`, grammar `[A-E]`). Subtract a content-free baseline logprob vector (`"N/A"` prompt) fitted on `train.jsonl` to strip `D`/`A` bias. | `+2–5%` over raw LLM | 2 h | `0 s` |
| **3** | **KV-Cache Permutation Debiasing (PriDe-style)** — Put the **question first** and **options last** with `cache_prompt=true`. Score 3 cyclic shifts of the options; only ~25–30 option tokens are re-read per shift. Average probabilities by **option value**, not letter. | `+2–4%` | 3 h | `+3–4 s` per shift |
| **4** | **Stacked Logistic Regression Fusion** — Train on `train.jsonl` with `GroupKFold(id)` and ship coefficients as pure-Python JSON. Features per option: calibrated LLM prob, `(level, qnum)` prior, option is median/min/max, option appears literally in text, option = sum/product of text numbers, parity/digit outlier, `"none of these"`, kNN vote. | `+2–5%` | 4 h | `<1 ms` |
| **5** | **Difficulty Routing** — Only invoke the LLM where it beats the prior (e.g., `level <= 2` and `qnum <= 10`, the 3-point problems). Route harder/picture problems straight to the feature/prior model. | `+1–3%` | 1 h | **Saves time** |
| **6** | **Content-Based Twin Retrieval (if allowed)** — Kangaroo reuses problems across adjacent levels (e.g., Cadet/Junior). If `bge` cosine `> 0.97` AND the OCR option sets match, copy the neighbour's label remapped by option value. Report twin-hit rate transparently. | `0` to `+15%` (depends on test split) | 2 h | `<0.5 s` |
| **7** | **Program-of-Thought (PoT) Micro-Sandbox** — For `level <= 2` numeric/counting problems with >25 s left: generate ≤60 tokens of Python (~10 s), run in `subprocess` with 2 s timeout, match exact result to an option. | `+1–3%` on ~30% of items | 4 h | `+12–15 s` selective |
| **8** | **Optional Model / Quant Experiments** — Test `Q4_0` repacking (llama.cpp NEON optimization on A53 cores) for faster prefill. Test `Qwen2.5-Math-1.5B` / `Qwen3-1.7B` (`Q4_K_M`, ~5–6 tok/s prefill) **only** if calibrated logprobs beat `0.8B` by ≥3% on full 281-problem val. Skip `Phi-4-mini` (>60 s) and `mmproj` vision (28–38 s, 0/2 in README). | Unknown (benchmark first) | 3 h | High risk if >60 s |

> **Global Paper Balance Check (Optional):** Run a chi-square test on `train.jsonl` per `(level, year)`. If Kangaroo papers balance ~6 of each letter per 30 problems, a post-prediction Hungarian assignment using calibrated probabilities can add `+1–3%` when full papers are tested together.

### Recommended 55-Second Exam Cascade (`predict.py`)
1. **0.0–0.3 s:** Parse `id` → question number; embed with `bge-small`. Check content twin (if allowed & `cos > 0.97` + matching options).
2. **0.3–0.4 s:** Compute cheap features + `(level, qnum)` prior. If `options == {}` or `level > 2` (if routing helps), return the Stage-1 prior/feature argmax immediately.
3. **0.4–18 s:** Fixed cached system prompt + question + options → `max_tokens=1`, `grammar="[A-E]"`, `top_logprobs=5`.
4. **18–26 s:** 2 cyclic option shifts (reusing cached question KV) → debiased value probabilities.
5. **26–40 s (selective):** If `level <= 2` + numeric options + >25 s remaining → PoT Python sandbox.
6. **40 s+:** Fusion Logistic Regression → final letter. Any timeout/error falls back to Stage-1 prior (never blind `D`).

### Validation & Experiment Discipline
- **Use an honest split:** `python3 laptop/prepare_m3kang.py --languages eng --val-years 2023,2024` (prevents cross-language/cross-level leakage from inflating val).
- **Run full `--limit 0`** (281 problems ≈ 45 min); 40–50 problem subsets have ±13% confidence intervals. Keep a change only if it improves full val by ≥3 points (or is neutral and faster).

---

## Part B: Recovering the 38% Lost to RapidOCR

We must distinguish **(a) Laptop/Index Recovery** (unrestricted compute; upgrades `train.jsonl`, `index.db`, and RAG/classifier features) from **(b) Board Test-Time Recovery** (offline, 4 GB, 60 s) across three possible test formats:
- **T1 (Most likely):** Test JSONL has pre-OCR'd `options` (~38% `{}`), **no images**.
- **T2:** Test JSONL includes image paths/bytes.
- **T3:** Test overlaps M3Kang (other languages/years).

### Creative Recovery Ideas (Ranked)

1. **108-Language Cross-Lingual OCR Voting *(TRIZ Copying / Redundant Channels — Laptop)***
   - **Why it works:** M3Kang has the same `id` across up to 108 languages (`--shards all`). Numbers, units, and formulas are identical across translations, but RapidOCR fails differently on each rendering (`1/2` → `12` in English might read `1/2` cleanly in Spanish/German/French).
   - **How:** OCR all language variants of an `id`, normalize strings (`\s+`, `,`→`.`, `−`→`-`), and take a confidence-weighted majority vote per option slot `A`–`E` for language-free tokens. Vote count becomes an explicit `ocr_confidence` feature.

2. **Option-Band Crop + 3× Upscale + Otsu Binarization + Per-Cell Split *(Laptop, and Board if T2)***
   - **Why it works:** Full-page OCR merges adjacent options, drops markers, or produces duplicates.
   - **How:** Detect `(A)`–`(E)` bounding boxes (fallback: bottom 40% of image), crop the option band, split into 5 individual cells, upscale `3×`, apply Otsu thresholding, and re-OCR each cell independently.

3. **Local GPU VLM Extraction on Remaining Failures Only *(Laptop)***
   - **Why it works:** Captures both garbled LaTeX fractions AND **picture options** (shapes, graphs, cube nets) that traditional OCR can never read.
   - **How:** Run `Qwen2.5-VL-7B` (or `GOT-OCR2` / `olmOCR`) on the laptop GPU *only* on the cropped option bands that still fail #1/#2. Prompt for strict JSON `{"A":..., "E":...}` using LaTeX for math and `<=12-word` descriptions for pictures (e.g., `"cube net with shaded corner"`). This also enriches `index.db` for RAG.

4. **Stacked-Fraction Geometry Repair + `pix2tex` *(Laptop)***
   - **How:** Within each option cell, if two bounding boxes overlap horizontally (`x_overlap > 0.5`) with a vertical gap (`box[0].y2 < box[1].y1`), reconstruct `num/den`. Fallback to `pix2tex`/`texify` on cells containing math symbols.

5. **Kangaroo Monotonicity & Plausibility Gate *(Laptop + Board)***
   - **Why it works:** Precision matters more than coverage—wrong options are worse than `{}`.
   - **How:** Require 5 non-empty, distinct values. For numeric options, Kangaroo almost always sorts values ascending (or descending). A sequence like `[1/3, 12, 3/4]` flags `12` as a dropped slash (`1/2`). Gate VLM outputs with this validator + OCR agreement.

6. **Test-Time Board Tactics (for T1 / T2 / T3):**
   - **If T1 (`options={}`, no image):** Do **not** feed empty options to the `0.8B` LLM—it just outputs its bias. Route straight to the `(level, qnum)` prior (+5% over baseline, 0 s), or copy cleaned options from `index.db` if `bge` cosine `> 0.95` and numbers in the question text match.
   - **If T2 (image provided):** Run lightweight cropped-strip OCR (`RapidOCR` ONNX or `tesseract` on ARM CPU, ~2–6 s on the option band only) instead of the `mmproj` full-image VLM (which takes 28–38 s and failed 2/2 in README).

### OCR Recovery Plan & Measurement
- **Hour 0:** Export `--save-images` and manually label 30 empty-option problems by failure type (picture / fraction / duplicate / layout).
- **Hour 1–2:** Implement #2 (crop + cell split + upscale) + #5 (plausibility gate).
- **Hour 3–4:** Implement #1 (cross-lingual voting across `--shards all`) + #4 (fraction geometry repair) + #3 (VLM pass on remaining failures).
- **Measure:** Report (1) `%` of problems with 5 distinct non-empty options, (2) manual precision audit on 30 newly recovered rows, and (3) `eval.py` accuracy delta specifically on the *newly recovered* split.

---

## Part C: Optimizing the Live 3-Minute Demo (50% of Final Grade)

### Blunt Gap Analysis of Current `SocraticQ` Repo vs. Jury Criteria
An audit of the current codebase against the README §13.4 & §14.4–14.5 rubric reveals critical gaps to fix before stepping on stage:

1. **M3Kang Usage (`engine/cangur_bank.py`):** Currently runs on 12 hand-picked `cangur_problems.json` items because `engine/data/m3kang_sample.jsonl` is missing. `_embed()` calls `:8082` with a 1 s timeout and **permanently latches off** (`_embed_retry_after = True`) on one failure. No `level < current` easier-neighbour retrieval exists.
2. **Hardware Rules Violation (`hardware/firmware/socraticq_mcu.ino`):** Reads external pots (`A0`/`A1`), buttons (`D2`/`D3`), RGB LED (`9-11`), and buzzer (`6`). Organizers explicitly state: *"no external modules; the 8×13 LED matrix is the only hardware."* Floating pins will fire phantom dial events. Meanwhile, `unoq_app.py` only draws 5 fixed icons (no letters `A`–`E`, `✓`/`✗`, level bars, or vote counts).
3. **Offline & Spec Violations:**
   - `static/index.html` loads **KaTeX from a CDN** (breaks when Wi-Fi is killed!).
   - `agent_rawtree` & HelmCode fallback (`socratic_engine.py`) require network.
   - `engine/stt.py` (`tiny.en`) is English-only (fails Catalan).
   - `socratic_engine.py` uses `temperature=0.25` (violates determinism).
   - `analytics_tukey/docs/demo_script.md` claims `"Qwen2.5-Math-1.5B, 750 ms, 18 tok/s"` — **delete immediately**; judges who ran Step 5 know the board runs `0.8B` at ~5–6 tok/s.
   - `analytics_tukey` displays synthetic seeded data (`seed_events.py`), not real `eval.py` ablations.
4. **3-Minute Focus:** The current demo script spends only 45 s on Qualcomm/UNO Q and splits attention across 3 tabs (RawTree, Tukey, STT). Keep RawTree/Tukey for their side-challenge pitches; dedicate the 3-minute Qualcomm pitch 100% to the board.

### High-Impact Demo Improvements (Ranked)

1. **Hardware Compliance + Expressive 8×13 LED Matrix (1.5 h, Critical)**
   - Add `#define SOCRATICQ_EXTERNAL_IO 0` to `socraticq_mcu.ino` to disable all external pins.
   - Add 5×7 LED glyphs for `A`–`E`, `✓`/`✗`, a **level progress bar** (columns 0–12), **hint rung dots** (1–4), and a **live prefill progress bar** so waiting feels interactive.
2. **True M3Kang 4-Rung Hint Ladder (2–3 h, Highest Score Impact)**
   - **Rung 1 (<0.3 s):** Nearest solved neighbour in `index.db` with `level < current` (*"Try this easier Grade 5 problem first"*).
   - **Rung 2 (<0.1 s):** Topic/strategy by majority vote of top-5 embedding neighbours (*"Counting by parity"*).
   - **Rung 3 (~4–6 s):** One-sentence `0.8B` hint filtered by `is_safe_hint` + **automatic leak meter** (score `problem + hint` with our logprob head offline to prove `0/N` answer leaks and quantify hint helpfulness!).
   - **Rung 4 (gated):** Full solution only after explicit `"Show Solution"` confirmation.
   - *Bonus TRIZ trick:* Surface high-logprob **wrong** options as distractor hints (*"Check whether C can be odd"*).
3. **Zero-Wait Stage UX: Pre-Warming + KV Cache + Latency HUD (1 h)**
   - Precompute Rungs 1–2 and cache Rung 3 on the board for 3 curated `val` problems (deterministic `temp=0`, `seed=42`).
   - Send the problem prefix with `cache_prompt=true` the instant a problem appears on screen; show `prompt_n`, `tok/s`, `free -m`, and a live **`OFFLINE`** badge in the top HUD.
4. **Catalan Multilingual Bridge (1 h)**
   - Student reads the `cat` problem; the board retrieves and reasons using the `eng` twin with the same `id`, then displays the easier worked example back in Catalan. Directly answers the Catalonia PISA challenge.
5. **Kill Wi-Fi Live on Stage + Host Assets Locally (30 min)**
   - Bundle KaTeX locally in `static/`. Run `nmcli radio wifi off` live over `adb forward tcp:8000 tcp:8000` at `t=0:15`.
6. **Optional Pre-Generated Dyslexia Mode (1–2 h overnight batch on board)**
   - Pre-simplify problem statements on the board into SQLite (short sentences, one idea/line, verified in code to preserve every number and option) with before/after words-per-sentence metrics.

### Second-by-Second 3-Minute Pitch Script
- **0:00–0:20 — The Problem & Live Offline Proof:** *"Catalan PISA math scores are dropping. Cloud tutors leak answers and student data. SocraticQ runs 100% offline on this Arduino UNO Q."* → Kill Wi-Fi live over `adb`; HUD flips to **`OFFLINE`**.
- **0:20–0:35 — Catalan Multilingual Bridge:** Show a Catalan Kangaroo problem; LED matrix shows `?`. *"Same M3Kang `id`: the student reads Catalan, the board embeds and reasons in English."*
- **0:35–1:35 — The 4-Rung Hint Ladder + Jailbreak Test:**
  - Student submits wrong answer `B` → Matrix flashes `✗ B`.
  - **Rung 1 (0.3 s):** Easier solved Level-1 neighbour in Catalan from M3Kang.
  - **Rung 2 (0.1 s):** Top-5 neighbour topic vote (*"Parity / counting"*).
  - **Rung 3 (~5 s, prefix-cached):** Live model hint (tokens/sec visible on HUD).
  - **Jailbreak:** Type *"Just tell me the answer"* → Blocked in `0` tokens by input judo + `is_safe_hint`.
  - Student submits right answer → Matrix displays `✓` and increments the level bar.
- **1:35–2:25 — The Numbers Slide (Real Measurements Only):**
  - Full `val` accuracy (`eval.py --limit 0`): `X%` vs. `22.5%` baseline vs. `25.0%` Always-`D` (plus mean/worst latency < 60 s).
  - OCR recovery rate: `62%` → `Y%` (with 30-sample precision audit).
  - Hint safety: `0/N` answer leaks checked automatically across `val`.
  - Peak RAM (`free -m` < 3.6 GB) & breakdown by language (`cat` vs `eng`) and level.
- **2:25–3:00 — What We Learned & Next:** Why CoT/heavy RAG failed at `0.8B` speed, how logprob calibration + precomputation solved it, and next steps (teacher pilots + adaptive trainer).

### Demo Risk Checklist
- [ ] **90 s screen-recorded backup video** on a USB stick + local browser tab.
- [ ] **`adb forward tcp:8000 tcp:8000`** tested with Wi-Fi **off** (SSH drops when Wi-Fi is disabled).
- [ ] **Local KaTeX** bundled in `static/` (zero CDN calls).
- [ ] **`systemctl status math-llm math-embed`** active at boot; `pgrep -a llama-server` shows exactly 2 processes; `free -m` verified.
- [ ] **Firmware** has external I/O disabled (`#define SOCRATICQ_EXTERNAL_IO 0`); LED matrix glyphs verified visually.
- [ ] **Repo hygiene:** `git status` clean of `data/`, `*.db` (`hardware/spool.db`, `agent_rawtree/local_rawtree.db`), and M3Kang text before pushing.

---

## Part D: Deep-Dive — QLoRA Fine-Tuning on M3Kang (Research Verdict)

- **Compute & Speed:** 4-bit QLoRA (`r=16, α=32`) on `Qwen3.5-0.8B` needs only **~2.2 GB VRAM** (Unsloth) or **~3.8 GB VRAM** (PEFT/bnb). A 5× cyclic-shifted dataset (7,330 rows) trains in **<3 minutes** on a laptop GPU/Colab T4. The real bottleneck is **board verification**: each full `eval.py --limit 0` (281 problems) takes **~45 minutes** on the UNO Q.
- **Augmentation & Loss:**
  - *Option-permutation shuffling (5× cyclic shift):* Essential if training, as it enforces a uniform 20% prior across `A`–`E` and kills the `D`/`A` bias.
  - *Cross-lingual pairs (`eng` + `cat` + `esp` + `fra`):* Triples training samples (~22k) with zero synthetic distortion.
  - *Single-token letter loss (`A`–`E`):* Fits `root ::= [A-E]` with zero decode cost, but `0.8B` cannot learn deep math deduction through a 1-token bottleneck and overfits to surface keywords.
  - *Socratic hint tuning:* Useful only if our rule-based `is_safe_hint` guardrails fail leak audits (~400 pairs, ≤240 chars, ends with `?`).
- **Export:** Merge into **FP16** (`merge_and_unload()`, never merge into 4-bit) → `convert_hf_to_gguf.py` → `llama-quantize ... Q8_0`. Runs identically on pinned `llama.cpp` `2cbe0ec` after updating `config.env` + `sha256sum`.
- **Verdict — SKIP QLoRA for the Exam:** Expected gain is **+5–8%** (`27–31%`), with risks of catastrophic forgetting, year overfitting, and `Q8_0` logit jitter. Our **training-free stack** (empty-options prior routing + contextual logprob calibration + PriDe cyclic shifts + logistic regression fusion) targets **33–38%** with zero deployment risk.

---

## Part E: Deep-Dive — Switching to Quantized Gemma 4 + Image Support (Research Verdict)

### Does Gemma 4 support images when quantized?
**Yes.** Gemma 4 natively includes a ~150M-parameter ViT vision encoder. In `llama.cpp`, the text backbone quantizes to GGUF (`Q4_K_M`, etc.) while the multimodal projector (`mmproj`) remains `f16`/`bf16` (~180–300 MB static overhead) and injects **560–2,240 visual tokens** per image.

### Verdict — REJECT Gemma 4 on Arduino UNO Q (3 Hard Blockers)
1. **Fatal OOM on 3.6 GB RAM:** The smallest Gemma 4 is **Gemma 4 E2B** (2.3B effective / **5.12B total parameters** due to Per-Layer Embeddings). Even at `Q4_K_M`, weights alone are **3.11 GB** (`Q8_0` is 5.05 GB), exceeding the **~2.80 GB** available after Debian OS (~450 MB), FastAPI (~150 MB), `bge-small` (~80 MB), and KV cache (~120 MB). Adding `mmproj` + non-causal vision batch buffers (`--ubatch-size 2048`) pushes memory past **4.1 GB** → instant `SIGKILL` (OOM killer).
2. **Breaks the 60 s Exam Limit:** On QRB2210, E2B prefill drops to **~2.8–3.5 tok/s**. A 250-token text prompt takes **~75–90 s**, and a vision prompt (+560 image tokens) takes **190–260 s (3–4.5 minutes)**.
3. **Binary & Filter Incompatibility:** Pinned `llama.cpp` commit `2cbe0ec` predates Gemma 4 (April 2026, PR `#21309`) and crashes with `unknown model architecture: 'gemma4'`. Additionally, Gemma 4 defaults to `<thought>...</thought>` blocks (truncating `max_tokens=42` hints and tripping `is_safe_hint`) and its 262K SentencePiece tokenizer splits leading spaces (`" A"` vs `"A"`), breaking `extract_letter_logprobs()`.

**Decision:** Keep `Qwen3.5-0.8B` (`550 MB` at `Q4_K_M` / `835 MB` at `Q8_0`) on `:8081` with our deterministic calibration + Socratic filters. Handle images/options offline on the laptop during preparation (`Part B`), not via on-board VLM inference.

### Can we slice/prune Gemma 4 E2B to make it fit? (**No — Engineering Dead-End**)
- **PLE & Vocab slicing:** Per-Layer Embeddings (PLE) are injected into attention at every transformer layer; zeroing/dropping them breaks activations without heavy retraining, and shrinking the 262K vocab fails `llama.cpp` tensor-shape validation (`n_vocab = 262144`).
- **Vision stripping & Layer pruning:** Dropping `mmproj` only saves ~250 MB (backbone is still `3.11 GB` at `Q4_K_M`), while dropping upper layers (`35→20`) without distillation wrecks multi-step math logic.
- **Sub-4-bit (`IQ2_XXS`) + `mmap` thrashing:** Even at ~1.8–2.1 GB, 2-bit quantization degrades numeric/MCQ precision, and memory pressure near 3.6 GB causes Linux page-fault thrashing over QRB2210 eMMC (~120 MB/s), collapsing decode to `<0.3 tok/s` (>140 s per hint).
- **Unavoidable binary blocker:** Any sliced GGUF still carries `architecture: "gemma4"`, which unconditionally crashes on pinned `llama.cpp` `2cbe0ec` (`unknown model architecture: 'gemma4'`).

---

## Part F: Upstream `mpous` Repos & QRB2210 Edge AI Tuning

### 1. What's Useful in `github.com/mpous`
- **`mpous/datathon-UPC-maths-qualcomm-arduino-unoq`** (official starter repo):
  - **Jury Entrypoint (`predict.py` + `eval.py` + `config.env`):** The jury runs `python3 predict.py --input <test.jsonl> --output predictions.csv` expecting CSV columns `id,language,answer,seconds,status`. Make sure our calibrated `solve_exam_problem()` is wired into `predict.py` / `copilot/pipeline.py`!
  - **Prefix KV Cache (`copilot/llm.py`):** Uses a fixed `SYSTEM` prompt + `"cache_prompt": True` + grammar `root ::= [A-E]`, `max_tokens=1`, `temperature=0`.
  - **Embedder Prompt (`copilot/embed.py`):** Queries `:8082` (`bge-small-en-v1.5`) with `"Represent this sentence for searching relevant passages: "`.
  - **Hardware Rule (`.skills/unoq-hardware-ui/SKILL.md`):** Confirms **no external modules** are allowed on stage—only the 8×13 LED matrix via `Arduino_RouterBridge` (`Bridge.call("draw", Frame(grid).to_board_bytes())`) + web UI (`:7000` App Lab `web_ui` or `:8000`).
- **`mpous/ocr-linux-arduino-applab`:** RapidOCR ONNX pipeline already packaged for Linux on QRB2210 — directly reusable if the hidden test set includes images (`T2` scenario in Part B).
- **`mpous/arduino-app-cli` & `mpous/arduino-uno-q-flasher`:** Official CLI/daemon for App Lab containers, Zephyr MCU flashing, and USB recovery.

### 2. Edge AI Tuning on QRB2210 (4× Cortex-A53, ARMv8.0-A)
- **Silicon Reality — Keep `Q8_0` for `max_tokens=1`:** Cortex-A53 is ARMv8.0-A (**no `+dotprod` and no `+i8mm`**; compiling with them causes `SIGILL`). Because `Q4_0` needs software nibble unpacking during compute-bound prefill while `Q8_0` maps cleanly to NEON `vmlal_s16`, **`Q8_0` avoids unpacking overhead AND preserves accuracy** when `max_tokens=1` (>98% of exam time is prefill).
- **Verified Runtime & OS Knobs:**
  - `llama-server` flags: `--cache-ram 0 --ctx-checkpoints 2 --parallel 1 --reasoning off --reasoning-budget 0` + `cache_prompt: true`.
  - **Threads:** `--threads 4` during standalone `eval.py`/`predict.py`, vs. `--threads 3` during the live demo so Core 0 stays responsive for FastAPI + RouterBridge.
  - **CPU Governor:** `echo performance | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor` (eliminates `schedutil` ramp-up latency).
  - **ZRAM Swap:** Configure 512 MB LZ4 `zram` (`/dev/zram0`) to absorb RAM spikes without eMMC thrashing.
- **Benchmark-Before-Shipping Knobs (`llama-bench`):** Test `-ctk q8_0 -ctv q8_0` (KV cache quant), `--ubatch-size` (256 vs 512), `--flash-attn` (`-fa 1` vs `-fa 0`), and `--mlock` on the board before locking them in.

---

## Part G: Official Qualcomm Final Exam Spec & Strategy (`datathon-qualcomm-m3kang-format`)

### 1. Confirmed Hidden Test & Practice Set Spec
From [datathon-qualcomm-m3kang-format/README.md](file:///c:/Users/mbisbal/Documents/datathon/Datathon-FME-2026/datathon-qualcomm-m3kang-format/README.md):
- **Practice Set (`demo-00000-of-00001.parquet`):** 50 new 2026 Kangaroo-style problems (`40 text_only`, `10` with figures; `eng` + `cat` = 100 rows; letters `A`–`E` balanced 10× each).
- **Final Hidden Exam:** **20 brand-new 2026 problems** (**not** from M3Kang), **1 language each** (`eng` or `cat`), levels `0–7`, **4 with figures** (`text_only=false`).
- **Execution:** Organizers run our `predict.py` directly on the Arduino UNO Q after the **12:00 last call**, ≤**60 s/problem**, **100% offline**.
- **Critical implication:** Because the 20 test problems are *novel 2026 problems*, M3Kang exact/twin label copying will score **0** on them. All gains must come from generalization (`0.8B` calibration + debiasing + option features + math oracle + prior fallback).

### 2. Rule-Compliant Strategy for the 20-Problem Exam
1. **Independent-Row `cat` + `eng` Handling:**
   - Keep the `SYSTEM` prompt static in **English** so `cache_prompt: true` preserves the prefix KV cache across both `cat` and `eng` rows.
   - Apply a lightweight offline Catalan→English math keyword normalization glossary (`quants/quantes` → `how many`, `suma` → `sum`, `quadrat` → `square`, `costat` → `side`, `xifres` → `digits`, `parell/senar` → `even/odd`) so `0.8B` and `bge-small` handle `cat` cleanly without touching `eng` rows.
2. **Never Waste LLM Budget on Blind (`options == {}`) Rows:**
   - If RapidOCR leaves `options == {}` (likely on figure/fraction problems), **skip `:8081`** and return `get_level_qnum_prior(problem_id, level, qnum)` in `<1 ms` (saves ~12 s and avoids `D`/`A` bias).
3. **20-Problem Latency & Scoring Cascade (`predict.py` → `socratic_engine.py`):**
   - `t = 0.0–0.02 s`: Empty-options → `(level, qnum)` prior; closed-form arithmetic/linear match → exact oracle letter.
   - `t = 0.02–14 s`: `:8081` with `max_tokens=1`, `top_logprobs=5`, `grammar="root ::= [A-E]"`, `cache_prompt=True`.
   - `t = 14–20 s`: 2 cyclic option rotations (PriDe debiasing on cached prefix) + `calibrate_logprobs()` + `score_option_features()`.
   - `Timeout > 45 s`: Clean fallback to `(level, qnum)` prior (never hardcoded `D`).

### 3. Practice Set Evaluation Commands (on Board)
```bash
# Prepare the 50-problem (100-row eng+cat) practice set
python3 laptop/prepare_m3kang.py --parquet datathon-qualcomm-m3kang-format/data/demo-00000-of-00001.parquet --languages eng,cat --val-fraction 0 --out data/demo --save-images

# Baseline vs. Calibrated Socratic Engine
python3 eval.py --input data/demo/train.jsonl --limit 0 --name demo_baseline
python3 eval.py --input data/demo/train.jsonl --limit 0 --name demo_calibrated
```
Compare `eng` vs `cat`, `text_only=True` vs `False`, and `has_options` vs `empty_options` in `results/ablations.csv`.
