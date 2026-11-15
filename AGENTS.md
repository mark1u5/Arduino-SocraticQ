# AGENTS.md — Shared Agent Constitution (SocraticQ + RawTree + Tukey)

> **Every AI coding agent working in this repository MUST follow these rules.**
> Humans talk verbally; agents synchronize through `main`, `TEAM_EXECUTION_PLAN.md`, `SYNC_BOARD.md`, and `contracts/`.

---

## 1. Mandatory Startup Check
Before planning or editing any file, **read**:
1. `SYNC_BOARD.md` — see what is currently live vs. mocked across the 4 tracks.
2. `TEAM_EXECUTION_PLAN.md` — master architecture, API/SQL contracts, and milestones.
3. `contracts/schemas.py` — frozen Python types and payload field names.

Also identify which developer you are assisting (ask once if unknown, or infer from the git branch `dev1/*`, `dev2/*`, `dev3/*`, `dev4/*`).

---

## 2. Strict Write-Scope Isolation (Zero Merge Conflicts)
You may **READ** any file in the repository, but you may **ONLY CREATE OR EDIT** files inside your assigned folder:

| Developer | Branch Prefix | Exclusive Write Folder | Owned Scope |
| :--- | :--- | :--- | :--- |
| **Dev 1** | `dev1/` | `/hardware` | Arduino MCU sketch, GPIO/LEDs/dials, PySerial bridge, `mock_mcu.py` |
| **Dev 2** | `dev2/` | `/engine` | CAM Proves Cangur parser, quantized SLM on QRB2210, `socratic_engine.py` |
| **Dev 3** | `dev3/` | `/agent_rawtree` | RawTree JSON-in/SQL-out client, `schema.sql`, GLM 5.3 agent, FastAPI `/ingest` |
| **Dev 4** | `dev4/` | `/analytics_tukey` | Streamlit + Plotly Tukey dashboard, Project Hub docs, stage pitch script |

### Forbidden Actions
- **NEVER** edit another developer's folder.
- **NEVER** edit `contracts/`, `TEAM_EXECUTION_PLAN.md`, or `.env.example` from a feature branch; contract changes require a verbal team sync and a single Tech Lead commit on `main`.
- **NEVER** commit `.env`, API keys, `*.db`, or `.gguf`/`.onnx` model weights.

---

## 3. Contract & Mock-First Rules
1. **Import from `contracts/schemas.py`** rather than redefining `SocraticResponse`, UART frames, or telemetry event fields locally.
2. **Never invent new JSON keys or rename existing ones** (`confidence_pct` vs `confidence_dial` are explicitly defined per layer in `contracts/schemas.py`).
3. **Never block on a teammate.** Use the golden payloads in `contracts/fixtures/` or a `--mock` flag until `SYNC_BOARD.md` marks the upstream module as live.
4. **Keep `engine/` offline-capable:** `generate_socratic_hint()` MUST work with zero internet access (for the Qualcomm Arduino Uno Q offline demo).

---

## 4. Verification Before Every Commit/Merge to `main`
Run the contract smoke test from repo root:
```bash
python -m contracts.check_contracts
```
And verify `git status` shows changes **only** inside your exclusive folder.
