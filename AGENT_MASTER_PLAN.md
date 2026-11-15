# SocraticQ + Uno Q Edge Exam — Multi-Agent Master Plan & Audit (`AGENT_MASTER_PLAN.md`)

> **Purpose:** Gitignored shared battleplan (excluded via `.git/info/exclude`) coordinating independent coding agents across `/engine`, `/datathon-UPC-maths-qualcomm-arduino-unoq-main`, `/agent_rawtree`, `/hardware`, `/analytics_tukey`, and live Arduino Uno Q board deployment (`arduino@10.198.33.225`).
>
> **Strict `AGENTS.md` Write-Scope Reminder:**
> - **Dev 1 (`/hardware`)** | **Dev 2 (`/engine`)** | **Dev 3 (`/agent_rawtree`)** | **Dev 4 (`/analytics_tukey`)**
> - Never modify `contracts/`, `TEAM_EXECUTION_PLAN.md`, or `.env.example` from a feature branch.
> - Run `python -m contracts.check_contracts` before committing any changes.

---

## 1. Live Arduino Uno Q (`10.198.33.225`) Diagnostic Snapshot

### 1.1 Hardware, OS & Active Processes
- **Host / Kernel:** `Linux uno-q 6.16.7-g0dd6551ae96b #1 SMP PREEMPT Tue Sep 23 12:46:06 UTC 2025 aarch64 GNU/Linux` (Qualcomm QRB2210, 4× ARM Cortex-A53).
- **Memory & Storage:** `3,669 MB` RAM total (`848 MB` used, `1,518 MB` free, `2,821 MB` available, `0 MB` swap used); `/dev/mmcblk0p68` `9.8G` total (`6.7G` used, `2.6G` free, `72%`).
- **Running Services:**
  - `PID 435` (`:8081`): `/home/arduino/llama.cpp/build/bin/llama-server -m /home/arduino/models/Qwen_Qwen3.5-0.8B-Q8_0.gguf --host 127.0.0.1 --port 8081 --threads 4 --ctx-size 4096 --parallel 1 --cache-ram 0 --ctx-checkpoints 2 --jinja --reasoning off --reasoning-budget 0 --alias qwen3.5-0.8b` (`{"status":"ok"}`).
  - `PID 434` (`:8082`): `/home/arduino/llama.cpp/build/bin/llama-server -m /home/arduino/models/bge-small-en-v1.5-q8_0.gguf --embedding --host 127.0.0.1 --port 8082 --threads 4 --ctx-size 512 --parallel 1 --cache-ram 0 --batch-size 512 --ubatch-size 512` (`{"status":"ok"}`).
  - `PID 6758` (`:8765`): `/usr/bin/python3 /home/arduino/Datathon-FME-2026/chat_server.py --host 0.0.0.0 --port 8765`.
  - `PID 605`: `/usr/bin/arduino-router --unix-port /var/run/arduino-router.sock --serial-port /dev/ttyHS1 --serial-baudrate 115200`.

### 1.2 On-Board Benchmark History (`/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/results/ablations.csv`)

| Run Name | Input | `n` | `rag_k` | Accuracy | Mean Latency | Worst Latency | Over 60s Limit | Root Observation |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| `test1` | `data/val.jsonl` | 1 | 0 | 100.0% | 16.2s | 16.2s | 0 | Single problem sanity test |
| `baseline` | `data/val.jsonl` | 50 | 0 | 20.0% | 9.9s | 15.5s | 0 | Raw `copilot.llm` baseline (matches random guess / bias) |
| `rag1` | `data/val.jsonl` | 50 | 1 | 20.0% | 23.1s | 34.4s | 0 | RAG example prompt prefill adds +13.2s avg latency |
| `round2_probe` | `/tmp/probe2.jsonl` | 2 | 0 | 0.0% | 25.0s | 27.9s | 0 | Probe run |
| `round2_calibrated_rag` | `data/val.jsonl` | 50 | 0 | **22.0%** | 20.9s | 32.8s | 0 | Calibration + cyclic shifts beat baseline (`11/50`) |
| `iter1_canary15` | `/tmp/canary15.jsonl` | 15 | 0 | **6.7%** | **39.0s** | **52.0s** | 0 | Un-gated PoT/CoT + shifts thrashed KV cache (`--ctx-checkpoints 2`) & matched distractors |

**Key Takeaway from `round2_calibrated_rag` vs `iter1_canary15`:**
- When `enable_pot_cot=True` ran on the Uno Q in `iter1_canary15`, mean latency doubled from `20.9s` to `39.0s` (worst `52.0s`) and accuracy collapsed from `22.0%` to `6.7%` because:
  1. Switching between MCQ system prompt, PoT system prompt, and CoT system prompt invalidated the 2-checkpoint KV cache (`--ctx-checkpoints 2`), forcing full prompt re-eval on every call.
  2. `Qwen3.5-0.8B` frequently emitted buggy Python/CoT arithmetic that happened to match one of the A–E distractor numbers and overrode calibrated logprobs + priors.
- Local laptop working tree already disables `enable_pot_cot: bool = False` by default in `engine/socratic_engine.py:1966`, whereas the board still has the slower PoT/CoT path active in `solve_mcq_calibrated()`.

### 1.3 Code & Data Drift Between Laptop Repo and Uno Q Board
1. **Engine & Copilot Drift:**
   - Laptop `engine/socratic_engine.py` (`2,902` lines), `engine/prompts.py` (`1,137` lines), `engine/cangur_bank.py` (`594` lines) vs. Board `/home/arduino/Datathon-FME-2026/engine/` (`2,611`, `960`, `551` lines).
   - Laptop `datathon-UPC-maths-qualcomm-arduino-unoq-main/copilot/` has `teach_engine.py` and updated `rag.py` (`clean_stem`, `_normalize_stem`); Board `/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/copilot/` does not have `teach_engine.py` yet.
2. **Dataset Drift:**
   - Board `/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/` only has `train.jsonl` (`1.2M`), `val.jsonl` (`235K`), and `index.db` (`6.4M`) — it does **not** yet have `train_recuperat.jsonl` (salvaged 95.3% option coverage) or the newly provided official 2026 practice dataset (`datathon-qualcomm-m3kang-format/data/demo-00000-of-00001.parquet`, 50 problems in `eng` + `cat` = 100 rows, 80 `text_only`, 20 with figures).
   - Board `/home/arduino/Datathon-FME-2026/` only loads the 12 curated problems in `engine/data/cangur_problems.json` because `M3KANG_SEARCH_PATHS` in `engine/cangur_bank.py:17-21` looks for `datathon-UPC-maths-qualcomm-arduino-unoq-main/data/` (with `-main`) instead of `/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/` (no `-main`).

---

## 2. Forensic Post-Mortem: `"How many diagonals does a decagon have?"`

**Observed Chatbot Failure:**
- **Question:** `"How many diagonals does a decagon have?"`
- **Output:** `Solve` | `qwen3.5-0.8b-fallback` | `Clarification` | `31.0s` | `offline`
  - `Step 1 (Logic): Plug a tiny concrete number or truth assignment back into every given condition.`
  - `Step 2 (Similar Geometry pattern): The exterior angles of any convex polygon always sum to 360 degrees.`

### Exact 6-Step Failure Trace

1. **Suggestion Chip Payload (`static/chat.js:37`, `static/chat.js:221-235`):**
   - Clicking `"Decagon diagonals"` in `Solve` mode sends `POST /api/text-query` to `chat_server.py` (`:8765` on board) with:
     `{"problem_id": "GENERAL-MATH", "text": "How many diagonals does a decagon have?", "event_type": "HINT_REQUEST", "mode": "solve"}`.
2. **Category Misclassified as `Logic` (`engine/cangur_bank.py:39, 273-307`, `engine/data/cangur_problems.json:323-346`):**
   - `get_problem("GENERAL-MATH")` returns `None`, triggering `match_concept_guide("GENERAL-MATH", "How many diagonals does a decagon have?")`.
   - `concept_guides["geometry"]["keywords"]` (`["triangle", "angle", "polygon", "perimeter", "area", "rectangle", "circle"]`) and `EXTRA_KEYWORDS["Geometry"]` do **not** contain `"decagon"` (nor `heptagon`, `octagon`, `nonagon`, `dodecagon`) or `"diagonal"` / `"diagonals"` / `"vertices"` / `"sides"`.
   - `best_hits` stays `0`; `_semantic_guide_key()` calls `_embed()` on `:8082` with a `1.0s` timeout (`cangur_bank.py:248`). When `:8082` fails or times out (or on the board's older `cangur_bank.py`), `best_key` stays at the initial fallback `"algebra_logic"` (`cangur_bank.py:280`), whose category is `"Logic"` and `intuition_hook` is `"Plug a tiny concrete number or truth assignment back into every given condition."`.
   - *(Note: `_infer_category()` at `cangur_bank.py:108-110` also uses raw substring `kw in lower`, causing false matches like `"odd"` in `"model"` or `"sum"` in `"assume"`.)*
3. **Math Oracle Miss (`engine/prompts.py:589-980`):**
   - `extract_math_oracle()` and `_solve_general_word_problem()` have **no** solver for polygon diagonals $n(n-3)/2$ (nor polygon interior angle sum $(n-2)\times 180^\circ$, so the `"Hexagon angles"` suggestion chip also misses the oracle!). Thus `oracle["solutions"] == []` and `det_final == None`.
4. **RAG Retrieval Pollutes `Solve` Fallback (`engine/cangur_bank.py:17-21, 411-442`, `engine/socratic_engine.py:442-448, 516-522`):**
   - On the board, `retrieve_similar_problems()` searches only the 12 problems in `cangur_problems.json`.
   - `CAM-2020-N5-22` (`"Each interior angle of a regular polygon measures 150 degrees. How many sides does the polygon have?"`) wins on `"How many ... does ... have"` lexical overlap and returns its `intuition_hook`: `"The exterior angles of any convex polygon always sum to 360 degrees."`.
   - When `_build_deterministic_solution()` runs its final fallback (`socratic_engine.py:442-448`, or on the board before the `is_open_prompt` category filter was added), it outputs `Step 1 (Logic)` + `Step 2 (Similar Geometry pattern)`.
5. **`30.0s` SLM Timeout on `max_tokens=160` + `1.0s` Embed Timeout = `31.0s` (`engine/socratic_engine.py:64, 535-547`):**
   - In `mode == "solve"`, `_generate_turn_internal()` (`socratic_engine.py:537`) unconditionally calls `_call_llama_server(solve_msgs, model_id, max_tokens=160, extract_question=False, stop=["Student:"])` **even if `det_final` is already solved**, and when `det_final` is `None`, generating `160` tokens + prefill (~140 tokens) on QRB2210 exceeds `SLM_TIMEOUT_SEC = 30.0` (`socratic_engine.py:64`), returning `None`.
   - `urllib.request.urlopen(..., timeout=30.0)` aborts at `30.0s` (+ `~1.0s` from `_embed` timeout / RAG scoring = **`31.0s`**) and sets `model_id = "qwen3.5-0.8b-fallback"` (`socratic_engine.py:547`).
   - Additionally, `_call_llama_completion_with_status()` (`socratic_engine.py:180-195`) does not send `"chat_template_kwargs": {"enable_thinking": False}`, and `_extract_first_question()` (`socratic_engine.py:150-154`) does not strip unclosed `<think>` blocks if `llama-server` is started without `--reasoning off`.
6. **Badge Shows `Clarification` (`engine/socratic_engine.py:549, 711`, `engine/prompts.py:31-38`):**
   - Because `final_answer` is `None`, line `549` sets `move = "DECOMPOSE_PROBLEM"`, which `SOCRATIC_DIMENSIONS` maps to `"Clarification"`.

---

## 3. Agent Work Packages (Bit-by-Bit Implementation Checklist)

Each Work Package (WP) is scoped to a single directory so parallel agents can work without merge conflicts.

---

### WP-1 — Dev 2 (`/engine`): Expand Symbolic & Word-Problem Math Oracle (`engine/prompts.py`, `engine/socratic_engine.py`)
**Goal:** Instantly solve (`<5ms`, 100% precision) all chatbot suggestion prompts, common contest math families, and text-only problems from the 2026 Qualcomm Practice Set (`datathon-qualcomm-m3kang-format`) in both English and Catalan.

- [ ] **1.1 Polygon & Geometry Solvers in `_solve_general_word_problem()` (`engine/prompts.py:589-748`):**
  - Add bilingual polygon side map (`triangle`/`triangle`: 3, `quadrilateral`/`quadrilàter`: 4, `pentagon`/`pentàgon`: 5, `hexagon`/`hexàgon`: 6, `heptagon`/`heptàgon`: 7, `octagon`/`octàgon`: 8, `nonagon`/`enneàgon`: 9, `decagon`/`decàgon`: 10, `hendecagon`: 11, `dodecagon`/`dodecàgon`: 12, plus `(\d+)-gon` / `(\d+)-sided polygon` / `polígon de (\d+) costats`).
  - **Polygon diagonals:** $n(n - 3) / 2$ (e.g., `"How many diagonals does a decagon have?"` $\to$ `35`) + inverse (given $D$ diagonals, solve $n(n-3)/2 = D$ for $n$).
  - **Polygon angles:** Interior angle sum $(n-2)\times 180^\circ$ (e.g., `"What is the sum of the interior angles of a hexagon?"` $\to$ `720`), regular interior angle $(n-2)\times 180^\circ / n$, exterior angle $360^\circ / n$, inverse $n = 360 / (180 - \theta)$.
  - **Rectangle tiling into largest equal squares (`lvl-1_2026_14`):** `W × H` cut into equal squares as large as possible $\to g = \gcd(W, H) \implies (W/g)\cdot(H/g)$ (`12 × 8` $\to$ `6`).
  - **Cuboid completed into smallest cube (`lvl-1_2026_15`):** `a × b × c` block without taking apart $\to \max(a,b,c)^3 - a\cdot b\cdot c$ (`2 × 3 × 4` $\to$ `40`).

- [ ] **1.2 Combinatorics & Counting Solvers (`engine/prompts.py:589-748`):**
  - **Handshakes / round-robin pairs:** $n$ people/teams each shake hands / play once $\to n(n-1)/2$ (`10 people` $\to$ `45`).
  - **Even-sum card pairs (`lvl-3_2026_5`):** Two distinct cards from $1..N$ with even sum $\to \binom{\lceil N/2\rceil}{2} + \binom{\lfloor N/2\rfloor}{2}$ ($N=9 \to 16$).
  - **Digit-counting patterns:**
    - 2-digit numbers with both digits odd/even (`lvl-1_2026_9` $\to$ `25`).
    - Whole numbers $1..N$ containing digit $d$ at least once (`lvl-1_2026_13` $\to$ `19`).
    - 3-digit numbers with no zero digits where $|abc - cba| = D$ (`lvl-4_2026_5`: $D=495 \to 40$).
    - 3-digit numbers with exactly two equal digits (`lvl-3_2026_11` $\to$ `243`).
  - **Coin payment & optimization:**
    - Exact amount $T$ using two coin types $c_1, c_2$ (`lvl-2_2026_12`: 20c with 2c & 5c $\to$ `3`).
    - $N$ coins totaling $T$ with 3 coin types and minimum 1 of each (`lvl-0_2026_9` $\to$ `3`).
  - **Cycle walks, coin tosses, ball draws, subset sums:**
    - Square/cycle walk of $k$ steps returning to start (`lvl-3_2026_6`: $k=4 \to 6$).
    - Fair coin $N$ tosses with consecutive heads (`lvl-6_2026_3`: $N=4 \to 1/2$ or $8/16$).
    - Balls without replacement until blue appears on draw $k$ (`lvl-5_2026_9` $\to$ `3/10`).
    - Subsets of $\{1..n\}$ whose sum is divisible by $m$ (`lvl-7_2026_10`: $n=8, m=4 \to 64$).
  - **Knights & liars at round table (`lvl-2_2026_13`):** $N$ people saying `"Both of my neighbours are liars"` $\to \lfloor N/2 \rfloor$ ($N=5 \to 2$).

- [ ] **1.3 Number Theory Solvers (`engine/prompts.py:589-748`):**
  - **Last digit of $a^b$ (`lvl-2_2026_5`):** `pow(a, b, 10)` ($7^{2026} \to 9$).
  - **Smallest integer $>1$ with common remainder $r$ mod $d_1..d_k$ (`lvl-1_2026_8`):** $\text{lcm}(d_1,\dots,d_k) + r$ (`61`).
  - **Modular age/number range search (`lvl-0_2026_16`):** Scan $[L, U]$ for $x \equiv 0 \pmod{m_1}, x+1 \equiv 0 \pmod{m_2}, x+2 \equiv 0 \pmod{m_3}$ (`64`).
  - **Square divisors of $M$ (`lvl-3_2026_10`):** Count $n \ge 1$ such that $n^2 \mid M$ ($M=3600 \to 15$).
  - **Smallest integer with digit product $K$ (`lvl-4_2026_10`):** Greedy factoring by digits $9..2$ ($K=120 \to 358$).
  - **4-digit `aXYb` divisible by 90 (`lvl-4_2026_9`):** Solve $b=0$ and $(a+X+Y+b) \equiv 0 \pmod 9$ (`4`).
  - **Factorial sum modulo $m$ (`lvl-6_2026_8`):** $\sum_{k=1}^N k! \bmod m$ (`9`).
  - **Ordered pairs $1/x + 1/y = 1/n$ (`lvl-5_2026_8`):** Count divisors of $n^2$ ($n=6 \to 9$).
  - **Polynomial divisibility count (`lvl-7_2026_8`):** Count $1 \le n \le N$ with $m \mid (an^2+bn+c)$ (`29`).
  - **Numbers $<1000$ equal to $k \times \text{digit\_sum}$ (`lvl-7_2026_9`):** Brute-force $1..999$ (`1`).
  - **Minimum quotient of two-group partition of $\{1..N\}$ (`lvl-4_2026_11`):** Largest prime $> N/2$ (`10 -> 7`).

- [ ] **1.4 Algebra, Sequences & Word-Problem Solvers (`engine/prompts.py:589-748`):**
  - **Alternating step kangaroo (`lvl-0_2026_2`):** $+a, -b$ over $k$ jumps (`10`).
  - **Climbing frog/snail (`lvl-0_2026_7`):** $+u$ forward, $-d$ back to reach $H$ (`6`).
  - **Digital clock equal digits (`lvl-0_2026_8`):** Count `HH:MM` with 4 identical digits (`3`).
  - **Transitive race ordering (`lvl-0_2026_15`):** Topological sort from `"X finished ahead/behind Y"` (returns name).
  - **Runner ahead/behind ratio (`lvl-2_2026_14`):** $N$ runners, $r\times$ ahead as behind (`17`).
  - **Bus schedule (`lvl-1_2026_1`):** First bus `HH:MM`, period $P$ min, arrival `HH:MM` $\to$ next departure (`9:10`).
  - **Fraction class difference (`lvl-1_2026_7`):** $p/q$ girls, $d$ more girls than boys (`30`).
  - **Largest sum of two 2-digit numbers from 4 digits (`lvl-2_2026_6`):** Sort descending and pair tens/ones (`75`).
  - **Max weekdays in $D$ days (`lvl-2_2026_7`):** $\lceil D / 7 \rceil$ (`11`).
  - **Three ages product $P$ & sum $S$ (`lvl-4_2026_4`):** Triple search $\to \max$ (`8`).
  - **Quadratic / factored integer inequalities (`lvl-5_2026_3`, `lvl-6_2026_4`):** Sum/count integers satisfying $n^2+bn+c < 0$ (`39`) or $(x^2-a)(x^2-b) < 0$ (`4`).
  - **Functional equation $a f(x) + b f(1/x) = c x$ (`lvl-6_2026_7`):** Solve 2×2 system at $x_0, 1/x_0$ (`1`).
  - **Digit-sum recurrence (`lvl-7_2026_3`):** $a_{k+1} = a_k + S(a_k)$ (`62`).

- [ ] **1.5 Add Rich Step Guides in `_ORACLE_STEP_GUIDES` (`engine/socratic_engine.py:366-384`):**
  - Add clean 2-step pedagogical explanations for each new `oracle["kind"]` (`polygon_diagonals`, `polygon_angles`, `handshakes`, `last_digit_pow`, etc.) so `Solve` mode displays crystal-clear steps (e.g. `Step 1: From each of the 10 vertices, you can draw diagonals to 10 - 3 = 7 non-adjacent vertices.` / `Step 2: Divide by 2 to avoid counting each diagonal twice: 10 * 7 / 2 = 35.` / `Final Answer: 35`).

**Verification:**
```bash
python -m engine.test_engine
python -c "from engine.socratic_engine import generate_socratic_turn; print(generate_socratic_turn('GENERAL-MATH', 'HINT_REQUEST', 1, 50, student_note='How many diagonals does a decagon have?', mode='solve'))"
```

---

### WP-2 — Dev 2 (`/engine`): Fix Category Classification, RAG Batching, Guardrails & Sub-8s Latency (`engine/cangur_bank.py`, `engine/socratic_engine.py`, `engine/prompts.py`, `engine/data/cangur_problems.json`)
**Goal:** Eliminate the `31.0s` fallback stall, fix `Logic` misclassifications, stop Explorer UI from locking the LLM semaphore, and fix false-positive offloading guardrails.

- [ ] **2.1 Fix Keyword & Category Classification (`engine/cangur_bank.py:37-113, 273-321`, `engine/data/cangur_problems.json:271-347`):**
  - Expand `EXTRA_KEYWORDS` and `concept_guides`:
    - `Geometry`: add `decagon`, `decàgon`, `heptagon`, `heptàgon`, `octagon`, `octàgon`, `nonagon`, `dodecagon`, `diagonal`, `diagonals`, `vertex`, `vertices`, `vèrtex`, `interior`, `exterior`, `regular`, `convex`, `radius`, `diameter`, `cube`, `cub`, `volume`, `volum`, `side`, `sides`.
    - `Combinatorics`: add `handshake`, `handshakes`, `encuixada`, `choose`, `subset`, `subsets`, `committee`, `coin`, `coins`, `moneda`, `monedes`, `dice`, `dau`, `card`, `cards`, `carta`, `cartes`, `knight`, `liar`, `tournament`.
    - `NumberTheory`: add `odd`, `even`, `parell`, `senar`, `gcd`, `lcm`, `mcd`, `mcm`, `factorial`, `divisor`, `divisors`, `square`, `quadrat perfecte`.
    - `Algebra`: add `fraction`, `fractions`, `fracció`, `fraccions`, `denominator`, `denominators`, `numerator`, `sequence`, `successió`, `function`, `funció`, `inequality`, `desigualtat`.
  - Replace substring `kw in lower` in `_infer_category()` (`line 108`) and `match_concept_guide()` (`line 283`) with word-boundary regex (`re.search(rf"\b{re.escape(kw)}\b", haystack)`) so `"odd"` does not match `"model"` and `"sum"` does not match `"assume"`.
  - When `_infer_category()` finds category hits (`best_hits > 0`), map directly to a matching category guide **before** calling `_semantic_guide_key()` or defaulting to `"algebra_logic"`.
  - Parse inline `(A) ... (B) ... (C) ... (D) ... (E) ...` options inside `match_concept_guide()` when pasted in free text.

- [ ] **2.2 Eliminate the Unbatched `:8082` Embedding Timeout & Fix Board Search Paths (`engine/cangur_bank.py:17-22, 411-442`):**
  - Add `/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/train_recuperat.jsonl` and `/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/train.jsonl` to `M3KANG_SEARCH_PATHS`.
  - In `_score_problems()` (`lines 417-432`), **never** send `> 24` texts in a single HTTP request to `:8082`. Use `:8082` embeddings only when `len(candidates) <= 24`, or chunk in small batches with a short timeout — for the full 1,400+ problem bank in chat mode, use stopword-filtered + category-weighted lexical Jaccard/BM25 (`{"how", "many", "does", "the", "a", "an", "is", "are", "of", "in", "to", "what", "which", "have"}` removed) so retrieval finishes in `<5ms` instead of timing out `:8082` for `1.0s` and starving CPU threads.

- [ ] **2.3 Fix `Solve` Mode Timeout, Thinking-Tag Stripping & Explorer Semaphore Lockout (`engine/socratic_engine.py:150-215, 529-548, 2438-2701`):**
  - In `_generate_turn_internal()` (`lines 529-548`, `mode == "solve"`):
    - **Short-circuit deterministic solves:** If `det_final is not None` (solved by problem bank, `extract_math_oracle`, or `verify_equation_options`), **skip `_call_llama_server` completely** and return `det_solution` + `det_final` in `<5ms` (`model_id = f"{model_id}-oracle"`).
    - When `det_final is None`: drop `max_tokens` from `160` to `72` (and `timeout_sec=14.0`) with `stop=["Student:", "\n\n\n"]`, and omit verbose `ex['statement']` in `build_solve_messages()` (`engine/prompts.py:1048-1057`) when `rag_examples` is just a generic fallback, keeping prefill small so `:8081` completes in `<14s` instead of timing out at `30s`.
  - In `_call_llama_completion_with_status()` (`lines 180-195`): include `"chat_template_kwargs": {"enable_thinking": False}` in the request JSON body so Qwen3.5-0.8B never consumes tokens inside `<think>` even on servers started without `--reasoning off`.
  - In `_extract_first_question()` (`lines 150-154`): strip both closed `<think>.*?</think>` and unclosed `<think>.*$` blocks before extracting the guiding question.
  - In `get_hint_rung()` (`line 2438`) and `inspect_problem()` (`line 2585`): add `allow_model: bool = False` (default `False` for fast Explorer browsing) so clicking Next/Random or toggling calibration checkboxes in Explorer returns in `<50ms` instead of making 2–3 sequential 30s LLM calls under `_EDGE_INFERENCE_SEM`.

- [ ] **2.4 Refine `_OFFLOADING_RE` for Custom Algebra Questions (`engine/prompts.py:73-83`):**
  - Allow custom equations containing arithmetic/variables in `GENERAL-MATH` Teach mode (e.g., `"What is x in 2x + 3 = 11?"` or `"What is x equal to when 3x = 12?"`) to reach the Socratic tutor instead of being rejected by `_OFFLOADING_RE`.

**Verification:**
```bash
python -m engine.test_engine
python -m contracts.check_contracts
```

---

### WP-3 — Exam Pipeline (`/datathon-UPC-maths-qualcomm-arduino-unoq-main` & `engine/socratic_engine.py`): Final 20-Problem Offline Exam Readiness
**Goal:** Maximize accuracy on the official 2026 Practice Set (`demo-00000-of-00001.parquet`: 50 problems × 2 langs = 100 rows, 40 text-only + 10 figure) and guarantee `worst_s <= 25s` (limit `60s`) on the Uno Q.

- [ ] **3.1 Prepare & Audit Official 2026 Practice Dataset:**
  - Run `python3 datathon-UPC-maths-qualcomm-arduino-unoq-main/laptop/prepare_m3kang.py --parquet datathon-qualcomm-m3kang-format/data/demo-00000-of-00001.parquet --languages eng,cat --val-fraction 0 --out datathon-UPC-maths-qualcomm-arduino-unoq-main/data/demo --save-images`.
  - Verify OCR option recovery rate on all 100 rows (`eng` + `cat`) in `data/demo/train.jsonl`; inspect any rows with `options == {}` and patch `parse_options()` in `laptop/prepare_m3kang.py` if any text options failed OCR.

- [ ] **3.2 Bilingual Catalan/English Oracle Coverage (`engine/socratic_engine.py:1739-1751, 2024-2036`):**
  - Verify `normalize_catalan_math_keywords()` translates Catalan terms needed by WP-1's new word-problem solvers (or ensure WP-1's regexes handle both Catalan and English directly) so accuracy on `cat` matches `eng`.
  - Keep `enable_pot_cot=False` by default and restrict cyclic shifts (`max_shifts=1`, only when `sorted_p1[0] - sorted_p1[1] < 0.25` and `remaining > 16.0s`) to hold `mean_s <= 14s` and `worst_s <= 25s` on QRB2210.

- [ ] **3.3 Harden `predict.py` & Fix `copilot/teach_engine.py` / `copilot/embed.py`:**
  - In `predict.py`: handle optional missing keys gracefully (`problem.get("id") or problem.get("problem_id")`), and verify `eval.py` runs cleanly on `data/demo/train.jsonl`.
  - In `copilot/embed.py`: add a timed cooldown circuit-breaker when `:8082` is unreachable so `rag > 0` does not hang 60s per problem if the embed server restarts.
  - Check `copilot/teach_engine.py` imports and ensure no `ImportError` occurs if `explorer/server.py` or tests import it.

**Verification:**
```bash
python3 eval.py --input data/demo/train.jsonl --limit 0 --name demo_v2
python3 predict.py --input data/demo/train.jsonl --output /tmp/demo_preds.csv
```

---

### WP-4 — Dev 3 (`/agent_rawtree`): Fix Closed-Loop Interventions, Multi-Turn Chat Context & SQLite WAL
**Goal:** Make RawTree interventions actually take effect in `socratic_engine.py` without locking conversations into permanent repeats, preserve context across follow-up chips, and eliminate SQLite locking / `?` interpolation bugs.

- [ ] **4.1 Fix `recommended_nudge` Missing `?` (`agent_rawtree/autonomous_agent.py:418-423, 558-564`, `agent_rawtree/seed_events.py:199`):**
  - `engine/prompts.py:1077` (`is_safe_hint`) rejects any nudge without `"?"`. Currently, `autonomous_agent.py` lines 419, 422, 558, 564 and `seed_events.py` line 199 end with `"."`, causing `socratic_engine.py:573-577` to silently drop every override nudge!
  - Rewrite all heuristic and fallback nudges to end with a concise Socratic question mark (`?`), and in `_normalize_insight()` / line 565 ensure any nudge lacking `?` gets a Socratic prompt question appended.

- [ ] **4.2 Deduplicate `BOTTLENECK` / `CALIBRATION_GAP` Inserts & Fix Efficacy Loop (`agent_rawtree/autonomous_agent.py:189-243, 409-457`):**
  - Query `agent_insights` for existing `ACTIVE` or `APPLIED` insights with the same `(insight_type, target_problem_id, target_category)` before inserting a duplicate row every 15s cycle.
  - In `evaluate_closed_loop_efficacy()` (`lines 189-243`), select `WHERE status IN ('ACTIVE', 'APPLIED')` (currently `'ACTIVE'` only, so applied insights can never resolve) and filter `telemetry_events` by `AND ts_utc >= ?` (`created_at_utc`) so pre-intervention failures don't block resolution.

- [ ] **4.3 Mark Consumed Interventions `'APPLIED'` & Preserve Follow-Up Context (`agent_rawtree/web_app.py:1459-1625`, `static/chat.js`, `static/app.js`):**
  - In `/tutor/interact`, `/api/text-query`, and `/api/voice-query`: only pass `override_nudge` in `mode == "teach"` when `attempt_num <= 2` or unresolved, avoid matching `target_problem_id IS NULL` across unrelated problem IDs, and mark the insight `'APPLIED'` once delivered.
  - Add `conversation_context` / prior question propagation so clicking `"Can you give me another hint?"` or `"Please show me the full solution."` on `GENERAL-MATH` does not lose the student's original math problem.

- [ ] **4.4 Fix `rawtree_client.py` Parameter Replacement & SQLite Locking (`agent_rawtree/rawtree_client.py:96-106, 240-250`):**
  - In `query_sql()` (`lines 241-249`), split `live_sql` on `"?"` once (`parts = live_sql.split("?")`) and interleave formatted values instead of calling `.replace("?", rep, 1)` in a loop (which corrupts SQL whenever an earlier parameter string contains a `?`!).
  - In `_get_sqlite_conn()` (`lines 96-98`), pass `timeout=15.0` and run `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`.

**Verification:**
```bash
python -m unittest discover -s agent_rawtree -p "test_*.py"
```

---

### WP-5 — Dev 1 (`/hardware`): Fix Uno Q 8×13 Matrix RPC, Immediate `THINKING` Cue & Spool Unification
**Goal:** Ensure custom 8×13 LED matrix glyphs render reliably on the physical STM32U585 MCU, show `THINKING` cyan feedback immediately when waiting on inference, and unify the offline SQLite spool path.

- [ ] **5.1 Decouple `Frame` Import & Match 104-Byte `draw()` Payload (`hardware/bridge/unoq_app.py:26-32, 516-536`):**
  - Import `App, Bridge` separately from `Frame` so missing `Frame` in `arduino.app_utils` never sets `App = None, Bridge = None`.
  - Pass a flat 104-element byte array (`[int(cell) for row in resolved_grid for cell in row]`) to `Bridge.call("draw", ...)` matching `socraticq_mcu.ino:173-178` (`draw(std::vector<uint8_t> newFrame)`).

- [ ] **5.2 Prevent `applyFeedback()` from Overwriting Custom Matrix Frames (`hardware/firmware/socraticq_mcu.ino:173-214, 280-292`):**
  - Track a `bool customFrameActive` flag set inside `draw()` (or cleared on `PROBLEM_SELECT` / `IDLE`) so `applyFeedback()` updates RGB LEDs, buzzer, and `Monitor` logs without overwriting `currentFrame` with generic bitmaps.
  - Reset `attemptNumber = 0;` when `lastCategory` changes in `loop()` (`line 283`).

- [ ] **5.3 Immediate `THINKING` Emission, Category `0` Fix & Spool Path (`hardware/bridge/serial_daemon.py:342-389`, `hardware/bridge/unoq_app.py:507`):**
  - Add an optional `on_frame` callback in `SocraticDaemon.process_event()` so `thinking_feedback(event)` is sent to the MCU **before** blocking on HTTP `/interventions/active` and `self.engine(...)`.
  - Fix `dial_idx == 0` in `serial_daemon.py:356` (`category = CATEGORY_BY_DIAL.get(dial_idx)` instead of `None if dial_idx == 0`) so `"Logic"` bottlenecks are fetched.
  - Unify `OFFLINE_SPOOL_DB` default in `unoq_app.py:507` to `"hardware/spool.db"` to match `serial_daemon.py:471` and `.env.example:373`.

**Verification:**
```bash
python -m unittest hardware.tests.test_bridge
```

---

### WP-6 — Dev 4 (`/analytics_tukey`): Fix Calibration Queries, Pre/Post Chart & Wire Remaining Tukey Charts
**Goal:** Ensure hardware `'SOLVED'` events are counted accurately in calibration metrics, Chart 5 never degrades to a single pre-intervention box, and all 9 Tukey charts + statistical tests are exposed in the Streamlit cockpit.

- [ ] **6.1 Include `'SOLVED'` Events & Fix `pd.cut` Edge Cases (`analytics_tukey/charts.py:124-150`, `analytics_tukey/queries.py:659, 804`):**
  - Replace `event_type = 'ANSWER_SUBMIT'` / `== "ANSWER_SUBMIT"` with `event_type IN ('ANSWER_SUBMIT', 'SOLVED')` (plus `is_correct.notna()`).
  - Pass `include_lowest=True` to `pd.cut()` in `build_confidence_calibration_chart()` (`line 136`) so `confidence_pct == 0` is not dropped as `NaN`, and drop empty bins before plotting the calibration curve.

- [ ] **6.2 Harden `get_pre_post_intervention()` (`analytics_tukey/queries.py:718-749`):**
  - Allow any `insight_type` targeting `target_problem_id` (not strictly `insight_type = 'BOTTLENECK'`), fall back to the earliest insight timestamp if no problem-specific row exists, and sanitize `target_problem_id` instead of raw string interpolation.

- [ ] **6.3 Expose Remaining Tukey Charts & Metrics (`analytics_tukey/app.py:13-25, 203-273`, `analytics_tukey/docs/arduino_project_hub.md:22-60`):**
  - Wire `build_misconception_heatmap`, `build_ecdf_solve_time`, `build_tukey_residual_plot`, `build_difficulty_drift_chart`, `build_agent_traces_chart`, `compute_calibration_metrics` (ECE & Brier Score), and `compute_intervention_effect_size` into `analytics_tukey/app.py`.
  - Clear `os.environ["RAWTREE_API_KEY"]` when emptied in the sidebar (`app.py:112-113`).
  - Update `analytics_tukey/docs/arduino_project_hub.md` pinout (`A0` Category, `A1` Confidence, `D2` Hint, `D3` Submit, default `SOCRATICQ_EXTERNAL_IO 0`) and model (`Qwen3.5-0.8B` `:8081` + `bge-small` `:8082`) to match the actual firmware and board.

**Verification:**
```bash
python -m py_compile analytics_tukey/app.py analytics_tukey/charts.py analytics_tukey/queries.py
```

---

### WP-7 — Board Sync (`arduino@10.198.33.225`) & Live End-to-End Verification
**Goal:** Deploy all fixes from WP-1–WP-4 to the live Uno Q board, update `chat_server.py` + `static/chat.js`, sync the salvaged + practice datasets, and verify both the chatbot and `predict.py` / `eval.py` live on hardware.

- [ ] **7.1 Sync Code & Datasets to Uno Q over SSH/SCP (`10.198.33.225`):**
  - Push updated `engine/` (`cangur_bank.py`, `prompts.py`, `socratic_engine.py`, `data/cangur_problems.json`) to `/home/arduino/Datathon-FME-2026/engine/`.
  - Push updated `datathon-UPC-maths-qualcomm-arduino-unoq-main/copilot/`, `predict.py`, `eval.py`, `data/train_recuperat.jsonl`, and generated `data/demo/` (`train.jsonl` + `images/`) to `/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/`.
  - Update `/home/arduino/Datathon-FME-2026/chat_server.py` (pass `hint_ladder`, support multi-turn context) and `/home/arduino/Datathon-FME-2026/static/chat.js` (preserve original custom prompt on `GENERAL-MATH` follow-ups; render fallback `A–E` buttons when `options == {}`).

- [ ] **7.2 Live Board Smoke Tests (`10.198.33.225`):**
  - Restart `chat_server.py` on `:8765` and verify via `POST http://127.0.0.1:8765/api/text-query`:
    - `"How many diagonals does a decagon have?"` (`mode: "solve"`) returns `Geometry`, `Final Answer: 35`, latency `< 100ms` (zero `31.0s` fallback!).
    - `"How many diagonals does a decagon have?"` (`mode: "teach"`) returns a relevant Geometry Socratic question without leaking `35`.
    - `"What is the sum of the interior angles of a hexagon?"` (`mode: "solve"`) returns `Geometry`, `Final Answer: 720`.
    - `"10 people each shake hands with everyone else once..."` (`mode: "teach"`) classifies as `Combinatorics` and returns a clean Socratic nudge.
  - Run `python3 eval.py --input data/demo/train.jsonl --limit 0 --name demo_final` on the board and confirm `over_limit == 0` (`worst_s < 45s`) and clear accuracy gain over the `20.0%` baseline.

---

## 4. Inference Optimization Notes (WiCi-Inspired Systems Principles for QRB2210)

While **WiCi One** (wireless GPU offloading) is ineligible for the 100% offline Qualcomm exam and **InferSSD** (NVMe SSD expert streaming) is unnecessary because `Qwen_Qwen3.5-0.8B-Q8_0.gguf` (`~850 MB`) already fits in RAM (`2,821 MB` available) on slow eMMC (`2.6 GB` free), three concrete systems principles directly apply to our Uno Q stack:

- [ ] **4.1 Strict KV-Cache State Residency & Prefix Pinning (`:8081`):**
  - Currently `:8081` runs with `--cache-ram 0 --ctx-checkpoints 2` (`PID 435`). Enabling `--cache-ram` (using part of the `2,821 MB` free RAM) and preserving an identical `System + Problem + Options` prefix across Pass 1 and cyclic option shifts (`cache_prompt=True`) avoids PoT/CoT KV-cache thrashing (`iter1_canary15` spiked to `39.0s` mean / `52.0s` worst).
- [ ] **4.2 Payload Deduplication & Pre-Computed Disk Vectors (`:8082`):**
  - Instead of re-embedding problems live over `:8082`, read pre-computed vectors from `data/index.db` (or restrict `:8082` to at most a single query string or small `<=24` curated batch), eliminating the `1.0s` HTTP timeout and background CPU contention.
- [ ] **4.3 Zero-Round-Trip Symbolic Fast Path & Token Cap:**
  - Short-circuit `:8081` in `<5ms` whenever `extract_math_oracle()` solves the prompt (WP-1 & WP-2), and cap fallback `solve` generation from `max_tokens=160` down to `72–80` tokens so inference finishes well under `SLM_TIMEOUT_SEC = 30.0s`.

