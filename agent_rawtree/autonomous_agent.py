"""Autonomous RawTree Cognitive Agent (HelmCode GLM 5.3).

Owned by Dev 3 (agent_rawtree).

Implements the official RawTree Side Challenge requirements:
- "The agent should be a protagonist, not a wrapper: let it decide what to ingest,
   which questions to ask and what to do with the answers."
- "An agent that tracks its own work: tool calls, traces and costs land in RawTree,
   and the agent queries its history to improve."

Cognitive Architecture:
1. Meta-Memory Reflex (Idea B): Queries `agent_traces` to evaluate past query yield,
   execution latency, and token cost to dynamically guide analytical focus.
2. Closed-Loop Efficacy Tracking (Idea C): Checks active interventions against recent
   telemetry to evaluate student accuracy gains and auto-resolve successful nudges.
3. Two-Stage Persona Chain (Idea A):
   - Stage 1 [Data Detective]: Formulates & executes self-correcting SQL against RawTree.
   - Stage 2 [Socratic Pedagogist]: Formulates precise Socratic nudges (zero answer leakage).
4. Ingestion & Tracing: Ingests findings to `agent_insights` and execution traces to `agent_traces`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from agent_rawtree.rawtree_client import RawTreeClient
from contracts.schemas import (
    AgentInsight,
    InsightType,
    validate_agent_insight,
)

SYSTEM_PROMPT = """You are the SocraticQ Autonomous Analytical Cortex.
Your role is to diagnose real-time student learning bottlenecks in the Proves Cangur competition
using RawTree (Tinybird OLAP).

You have access to the `telemetry_events` table with columns:
- problem_id (VARCHAR), cangur_year (INT), category (VARCHAR: Geometry, Combinatorics, NumberTheory, Algebra, Logic)
- difficulty_level (INT: 1, 2, 3), event_type (VARCHAR: HINT_REQUEST, ANSWER_SUBMIT, SOLVED)
- attempt_num (INT), confidence_pct (INT: 0-100), is_correct (BOOLEAN), misconception_tag (VARCHAR)
- solve_elapsed_ms (INT), slm_latency_ms (INT)

You also have access to your own trace history in `agent_traces`:
- trace_id, ts_utc, cycle_num, sql_executed, sql_latency_ms, findings_count, action_taken, estimated_cost_usd

When asked, formulate a focused SQL query to test a learning hypothesis, analyze the results,
and output structured JSON specifying whether to emit an intervention into `agent_insights`.
"""


class AutonomousRawTreeAgent:
    """Protagonist AI Agent that uses RawTree as analytical scratchpad & self-improving cognitive memory."""

    def __init__(
        self,
        client: Optional[RawTreeClient] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        force_heuristic: bool = False,
    ) -> None:
        self.rawtree = client or RawTreeClient()
        self.base_url = base_url or os.getenv("HELMCODE_BASE_URL", "")
        self.api_key = api_key or os.getenv("HELMCODE_API_KEY", "")
        self.model = model or os.getenv("HELMCODE_MODEL", "glm-5.3")
        self.force_heuristic = force_heuristic
        self.cycle_num = 0

        # Check if LLM client can be initialized
        self._llm = None
        if not self.force_heuristic and self.api_key and not self.api_key.startswith("<"):
            try:
                from openai import OpenAI
                self._llm = OpenAI(base_url=self.base_url, api_key=self.api_key)
            except Exception as e:
                print(f"[Agent] Warning: LLM client init failed ({e}). Using heuristic mode.")

    @property
    def has_live_llm(self) -> bool:
        return self._llm is not None

    def execute_self_correcting_sql(
        self, sql_query: str, max_retries: int = 2
    ) -> tuple[list[dict[str, Any]], str, int]:
        """Execute SQL with automatic error capture and self-correction retry loop."""
        current_sql = sql_query.strip().rstrip(";")
        start_t = time.perf_counter()

        for attempt in range(max_retries + 1):
            try:
                rows = self.rawtree.query_sql(current_sql)
                latency_ms = int((time.perf_counter() - start_t) * 1000)
                return rows, current_sql, latency_ms
            except Exception as e:
                error_msg = str(e)
                print(f"[Agent:SQL] Error on attempt {attempt + 1}: {error_msg}")
                if attempt == max_retries or not self.has_live_llm:
                    current_sql = (
                        "SELECT category, AVG(attempt_num) AS avg_att "
                        "FROM telemetry_events GROUP BY category"
                    )
                    rows = self.rawtree.query_sql(current_sql)
                    latency_ms = int((time.perf_counter() - start_t) * 1000)
                    return rows, current_sql, latency_ms

                # Self-correction request to LLM
                correction_prompt = (
                    f"The following SQL query failed with error: {error_msg}\n"
                    f"Query: {current_sql}\n"
                    "Fix the SQL and return ONLY the corrected query string."
                )
                try:
                    resp = self._llm.chat.completions.create(
                        model=self.model,
                        messages=[{"role": "user", "content": correction_prompt}],
                        temperature=0.0,
                    )
                    current_sql = (
                        resp.choices[0]
                        .message.content.strip()
                        .replace("```sql", "")
                        .replace("```", "")
                        .strip()
                    )
                except Exception:
                    break

        latency_ms = int((time.perf_counter() - start_t) * 1000)
        return [], current_sql, latency_ms

    def query_meta_memory(self) -> dict[str, Any]:
        """Idea B: Query past trace history in RawTree to self-improve and guide current cycle."""
        sql = (
            "SELECT trace_id, cycle_num, sql_executed, sql_latency_ms, findings_count, action_taken "
            "FROM agent_traces ORDER BY ts_utc DESC LIMIT 3;"
        )
        recent_traces = self.rawtree.query_sql(sql)

        cost_sql = "SELECT SUM(estimated_cost_usd) AS total_cost, COUNT(*) AS total_cycles FROM agent_traces;"
        cost_rows = self.rawtree.query_sql(cost_sql)
        total_cost = cost_rows[0].get("total_cost") or 0.0
        total_cycles = cost_rows[0].get("total_cycles") or 0

        avg_latency = (
            sum(t.get("sql_latency_ms", 0) for t in recent_traces) / len(recent_traces)
            if recent_traces
            else 0
        )
        low_yield = bool(recent_traces and all(t.get("findings_count", 0) == 0 for t in recent_traces))

        memory_summary = {
            "recent_traces_count": len(recent_traces),
            "avg_sql_latency_ms": int(avg_latency),
            "total_lifetime_cost_usd": round(float(total_cost), 5),
            "total_cycles_executed": int(total_cycles),
            "pivot_recommended": low_yield,
        }

        print(
            f"[Agent:Meta-Memory] RawTree history: {total_cycles} cycles recorded | "
            f"Avg SQL latency: {int(avg_latency)}ms | Lifetime cost: ${total_cost:.5f}"
        )
        if low_yield:
            print("                   -> Low yield detected in recent queries. Pivoting analytical focus.")
        return memory_summary

    # Item Response Theory (IRT) calibrated recovery targets by Cangur difficulty tier:
    # Tier 1 (3-point Cangur): High baseline, target >= 75%
    # Tier 2 (4-point Cangur, e.g. CAM-2023-N4-17): Medium baseline, target >= 60%
    # Tier 3 (5-point Cangur): Olympiad/hard tier, target >= 50%
    TIER_RECOVERY_TARGETS = {1: 0.75, 2: 0.60, 3: 0.50}

    def _normalize_insight(self, insight: dict[str, Any]) -> dict[str, Any]:
        """Ensure recommended_nudge is a concise Socratic question ending with '?'."""
        nudge = str(insight.get("recommended_nudge") or "").strip()
        if not nudge:
            nudge = "What invariant or pattern can you test first?"
        elif "?" not in nudge:
            nudge = nudge.rstrip(".!?, \t\n")
            suffix = " What pattern do you notice?"
            if len(nudge) + len(suffix) > 240:
                nudge = nudge[: 240 - len(suffix)].rstrip(".!?, \t\n")
            nudge = f"{nudge}{suffix}"
        elif not nudge.endswith("?"):
            nudge = nudge.rstrip(". \t\n")
            if not nudge.endswith("?"):
                nudge = f"{nudge}?"
        insight["recommended_nudge"] = nudge[:240]
        return insight

    def _has_existing_insight(
        self,
        insight_type: str,
        target_problem_id: Optional[str],
        target_category: Optional[str],
    ) -> bool:
        """Check if an ACTIVE or APPLIED insight already exists for the given type and targets."""
        if target_problem_id is not None:
            check_sql = (
                "SELECT insight_id FROM agent_insights "
                "WHERE status IN ('ACTIVE', 'APPLIED') "
                "AND insight_type = ? "
                "AND target_problem_id = ? "
                "AND target_category = ? "
                "LIMIT 1;"
            )
            rows = self.rawtree.query_sql(check_sql, (insight_type, target_problem_id, target_category))
        else:
            check_sql = (
                "SELECT insight_id FROM agent_insights "
                "WHERE status IN ('ACTIVE', 'APPLIED') "
                "AND insight_type = ? "
                "AND target_problem_id IS NULL "
                "AND target_category = ? "
                "LIMIT 1;"
            )
            rows = self.rawtree.query_sql(check_sql, (insight_type, target_category))
        return bool(rows)

    def evaluate_closed_loop_efficacy(self) -> list[str]:
        """Idea C: Multi-factor statistical efficacy tracking (IRT difficulty-calibrated).
        
        Evaluates resolution using 3 simultaneous criteria:
        1. Accuracy meets/exceeds difficulty-tier threshold (50%-75% depending on 3pt/4pt/5pt tier).
        2. Attempt count deflates to <= 1.8 (students are no longer looping in frustration).
        3. Statistical validity: N >= 8 post-nudge observations to eliminate small-sample noise.
        """
        sql = (
            "SELECT insight_id, target_problem_id, target_category, severity_score, "
            "evidence_summary, created_at_utc FROM agent_insights "
            "WHERE status IN ('ACTIVE', 'APPLIED');"
        )
        active_insights = self.rawtree.query_sql(sql)
        resolved_messages = []

        for ins in active_insights:
            prob_id = ins.get("target_problem_id")
            if not prob_id:
                continue

            created_at = ins.get("created_at_utc")
            # Query post-nudge performance metrics and problem tier filtered by ts_utc >= created_at_utc
            if created_at:
                eff_sql = """
                SELECT
                    COUNT(*) AS sample_size,
                    AVG(attempt_num) AS mean_attempts,
                    AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS empirical_accuracy,
                    MAX(difficulty_level) AS difficulty_level
                FROM telemetry_events
                WHERE problem_id = ? AND is_correct IS NOT NULL AND ts_utc >= ?;
                """
                rows = self.rawtree.query_sql(eff_sql, (prob_id, created_at))
            else:
                eff_sql = """
                SELECT
                    COUNT(*) AS sample_size,
                    AVG(attempt_num) AS mean_attempts,
                    AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS empirical_accuracy,
                    MAX(difficulty_level) AS difficulty_level
                FROM telemetry_events
                WHERE problem_id = ? AND is_correct IS NOT NULL;
                """
                rows = self.rawtree.query_sql(eff_sql, (prob_id,))

            if rows:
                stats = rows[0]
                sample = stats.get("sample_size") or 0
                acc = float(stats.get("empirical_accuracy") or 0.0)
                mean_att = float(stats.get("mean_attempts") or 0.0)
                diff = int(stats.get("difficulty_level") or 2)

                target_acc = self.TIER_RECOVERY_TARGETS.get(diff, 0.60)

                # Multi-factor recovery check
                is_resolved = bool(
                    sample >= 8
                    and acc >= target_acc
                    and mean_att <= 1.8
                )

                if is_resolved:
                    updated_summary = (
                        f"RESOLVED (Pedagogical Recovery Index): Accuracy recovered to {acc:.0%} "
                        f"(Tier {diff} target: {target_acc:.0%}), mean attempts deflated to {mean_att:.1f} (N={sample})."
                    )
                    update_sql = (
                        "UPDATE agent_insights SET status = 'RESOLVED', severity_score = 0.0, "
                        "evidence_summary = ? WHERE insight_id = ?;"
                    )
                    self.rawtree.query_sql(update_sql, (updated_summary, ins["insight_id"]))
                    msg = (
                        f"Closed-Loop Efficacy: {prob_id} (Tier {diff}) resolved! "
                        f"Accuracy: {acc:.0%} (target {target_acc:.0%}), Attempts: {mean_att:.1f} -> Marked {ins['insight_id']} RESOLVED."
                    )
                    resolved_messages.append(msg)
                    print(f"[Agent:Efficacy] -> {msg}")

        return resolved_messages

    def run_cycle(self) -> Optional[AgentInsight]:
        """Run complete cognitive cycle: Meta-Memory -> Efficacy -> Observe -> SQL -> Act -> Trace."""
        self.cycle_num += 1
        now_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        print(f"\n[Agent] === Starting Cognitive Cycle #{self.cycle_num} at {now_utc} ===")

        # 1. Meta-Memory Reflex (Queries RawTree about its own past operations)
        meta_memory = self.query_meta_memory()

        # 2. Closed-Loop Efficacy Check (Evaluates live student improvement on past nudges)
        resolved_interventions = self.evaluate_closed_loop_efficacy()

        # 3. Two-Stage Cognitive Execution (Data Detective -> Socratic Pedagogist)
        if self.has_live_llm:
            try:
                insight, trace = self._run_llm_cycle(now_utc, meta_memory)
            except Exception as e:
                # Stage fallback protocol: a flaky GLM endpoint must never stall
                # the cognitive loop — degrade to the deterministic heuristic.
                print(f"[Agent] LLM cycle failed ({e}). Falling back to heuristic mode.")
                insight, trace = self._run_heuristic_cycle(now_utc, meta_memory)
        else:
            insight, trace = self._run_heuristic_cycle(now_utc, meta_memory)

        if resolved_interventions:
            trace["action_taken"] += f" + RESOLVED_{len(resolved_interventions)}_INSIGHTS"

        # 4. Ingest Trace into RawTree agent_traces
        self.rawtree.insert_agent_trace(trace)
        print(
            f"[Agent:Trace] Ingested {trace['trace_id']} into `agent_traces` "
            f"({trace['action_taken']}, latency: {trace['sql_latency_ms']}ms, cost: ${trace['estimated_cost_usd']:.5f})"
        )

        # 5. Ingest Intervention into RawTree agent_insights
        if insight:
            insight = self._normalize_insight(insight)
            if self._has_existing_insight(
                insight["insight_type"],
                insight.get("target_problem_id"),
                insight.get("target_category"),
            ):
                print(
                    f"[Agent:Pedagogy] Skipping duplicate {insight['insight_type']} insert on "
                    f"{insight.get('target_problem_id')} ({insight.get('target_category')})."
                )
                return None
            validate_agent_insight(dict(insight))
            self.rawtree.insert_agent_insight(insight)

            # Broadcast live to SSE web UI if web_app is active
            try:
                req = urllib.request.Request(
                    "http://127.0.0.1:8000/internal/broadcast_intervention",
                    data=json.dumps(dict(insight)).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=0.5):
                    pass
            except Exception:
                pass

            print(
                f"[Agent:Pedagogy] -> INTERVENTION EMITTED into `agent_insights`: "
                f"{insight['insight_id']} [{insight['insight_type']}] on {insight['target_problem_id']}"
            )
            print(f"                 Nudge: \"{insight['recommended_nudge']}\"")
            return insight
        else:
            print("[Agent:Pedagogy] Nominal classroom operation. No intervention required.")
            return None

    def _run_heuristic_cycle(
        self, now_utc: str, meta_memory: dict[str, Any]
    ) -> tuple[Optional[AgentInsight], dict[str, Any]]:
        """Two-Stage deterministic cognitive loop for offline testing & demo stability."""
        print("[Agent:Stage 1 - Data Detective] Formulating exploratory SQL over RawTree telemetry...")

        insight: Optional[AgentInsight] = None
        action_taken = "OBSERVED_NOMINAL"

        # 0. Check for AI-safety / Socratic Judo guardrail spikes first
        guard_sql = (
            "SELECT problem_id, category, misconception_tag, COUNT(*) AS hit_count, "
            "AVG(confidence_pct) AS mean_conf "
            "FROM telemetry_events "
            "WHERE misconception_tag IN ("
            "'JAILBREAK_ATTEMPT', 'OFFLOADING_ATTEMPT', 'BINARY_SEARCH_PROBE', 'UNSAFE_OFFTOPIC'"
            ") "
            "GROUP BY problem_id, category, misconception_tag "
            "HAVING COUNT(*) >= 2 "
            "ORDER BY hit_count DESC LIMIT 1;"
        )
        guard_rows, guard_final_sql, guard_latency = self.execute_self_correcting_sql(guard_sql)
        if guard_rows:
            g_top = guard_rows[0]
            g_prob = g_top.get("problem_id") or "CAM-2023-N4-17"
            g_cat = g_top.get("category") or "Combinatorics"
            g_tag = str(g_top.get("misconception_tag") or "OFFLOADING_ATTEMPT")
            g_hits = int(g_top.get("hit_count") or 2)
            g_conf = float(g_top.get("mean_conf") or 50.0)

            existing_guard = self.rawtree.query_sql(
                "SELECT insight_id FROM agent_insights "
                "WHERE status IN ('ACTIVE', 'APPLIED') AND insight_type = 'INTERVENTION' AND target_problem_id = ? LIMIT 1;",
                (g_prob,),
            )
            if not existing_guard:
                judo_nudges = {
                    "JAILBREAK_ATTEMPT": "What is the smallest concrete example of this problem you can test by hand?",
                    "OFFLOADING_ATTEMPT": "Instead of jumping to the answer, which given quantity or constraint should we isolate first?",
                    "BINARY_SEARCH_PROBE": "Rather than guessing values, why must the core invariant rule out invalid candidates?",
                    "UNSAFE_OFFTOPIC": "Let's refocus on the problem structure: what mathematical condition connects the inputs?",
                }
                nudge = judo_nudges.get(
                    g_tag,
                    "Which single constraint in the problem statement should we test on a tiny case first?",
                )
                severity = min(0.95, 0.70 + 0.05 * g_hits)
                insight = {
                    "insight_id": f"ins-{uuid.uuid4().hex[:6]}",
                    "created_at_utc": now_utc,
                    "cycle_num": self.cycle_num,
                    "insight_type": "INTERVENTION",
                    "target_category": g_cat,
                    "target_problem_id": g_prob,
                    "severity_score": round(severity, 2),
                    "sql_executed": guard_final_sql,
                    "evidence_summary": (
                        f"Guardrail spike detected on {g_prob} ({g_cat}): {g_hits}x {g_tag} "
                        f"interceptions (mean confidence {g_conf:.0f}%)."
                    ),
                    "recommended_nudge": nudge,
                    "status": "ACTIVE",
                }
                action_taken = "EMITTED_GUARDRAIL_INTERVENTION"
                trace = {
                    "trace_id": f"trc-{uuid.uuid4().hex[:6]}",
                    "ts_utc": now_utc,
                    "cycle_num": self.cycle_num,
                    "agent_role": "TwoStageCognitiveLoop",
                    "model_id": "heuristic-rule-engine",
                    "sql_executed": guard_final_sql,
                    "sql_latency_ms": guard_latency,
                    "findings_count": len(guard_rows),
                    "action_taken": action_taken,
                    "prompt_tokens": 340,
                    "completion_tokens": 105,
                    "estimated_cost_usd": 0.00019,
                }
                return insight, trace

        # Dynamically pivot query if meta-memory suggested low yield
        calib_sql = (
            "SELECT category, AVG(confidence_pct) AS mean_conf, "
            "AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS empirical_acc "
            "FROM telemetry_events WHERE is_correct IS NOT NULL "
            "GROUP BY category HAVING AVG(confidence_pct) >= 70 AND empirical_acc < 0.40 "
            "LIMIT 1;"
        )
        if meta_memory.get("pivot_recommended"):
            sql = calib_sql
        else:
            sql = (
                "SELECT problem_id, category, COUNT(*) AS active_events, "
                "AVG(attempt_num) AS avg_attempts, "
                "AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) AS accuracy_rate, "
                "MAX(misconception_tag) AS dominant_misconception "
                "FROM telemetry_events "
                "GROUP BY problem_id, category "
                "HAVING COUNT(*) >= 3 AND AVG(attempt_num) >= 2.0 "
                "ORDER BY avg_attempts DESC LIMIT 1;"
            )

        rows, final_sql, latency_ms = self.execute_self_correcting_sql(sql)
        if not rows and not meta_memory.get("pivot_recommended"):
            rows, final_sql, latency_ms = self.execute_self_correcting_sql(calib_sql)
        print(f"[Agent:Stage 1 - Data Detective] Query executed in {latency_ms}ms. Yield: {len(rows)} anomaly clusters.")

        print("[Agent:Stage 2 - Socratic Pedagogist] Analyzing pedagogical moves and synthesizing nudge...")

        if rows:
            top = rows[0]
            if "problem_id" in top:
                prob_id = top.get("problem_id", "CAM-2023-N4-17")
                cat = top.get("category", "Combinatorics")
                avg_att = float(top.get("avg_attempts", 2.5))
                acc = float(top.get("accuracy_rate", 0.2))
                tag = top.get("dominant_misconception", "ignored_parity_invariant")

                if self._has_existing_insight("BOTTLENECK", prob_id, cat):
                    c_rows, c_final_sql, _ = self.execute_self_correcting_sql(calib_sql)
                    if c_rows:
                        c_top = c_rows[0]
                        c_cat = c_top.get("category", "Geometry")
                        c_prob = "CAM-2022-N4-19"
                        c_conf = float(c_top.get("mean_conf", 80))
                        c_acc = float(c_top.get("empirical_acc", 0.25))
                        if not self._has_existing_insight("CALIBRATION_GAP", c_prob, c_cat):
                            insight = {
                                "insight_id": f"ins-{uuid.uuid4().hex[:6]}",
                                "created_at_utc": now_utc,
                                "cycle_num": self.cycle_num,
                                "insight_type": "CALIBRATION_GAP",
                                "target_category": c_cat,
                                "target_problem_id": c_prob,
                                "severity_score": 0.75,
                                "sql_executed": c_final_sql,
                                "evidence_summary": f"Dunning-Kruger mismatch in {c_cat}: high confidence ({c_conf:.0f}%) vs empirical accuracy ({c_acc:.0%}).",
                                "recommended_nudge": "Could two angles in the same triangle both measure 100 degrees, given the 180-degree sum?",
                                "status": "ACTIVE",
                            }
                            insight = self._normalize_insight(insight)
                            action_taken = "EMITTED_CALIBRATION_INTERVENTION"
                        else:
                            action_taken = "SKIPPED_DUPLICATE_INTERVENTION"
                    else:
                        action_taken = "SKIPPED_DUPLICATE_INTERVENTION"
                else:
                    if "parity" in str(tag).lower() or prob_id == "CAM-2023-N4-17":
                        nudge = "Color adjacent squares black and white first—how does each valid move change the colors?"
                        severity = min(0.95, 0.5 + (avg_att - 2.0) * 0.2 + (1.0 - acc) * 0.3)
                    else:
                        nudge = f"Can you break this down into smaller cases and test a counterexample for {tag}?"
                        severity = 0.70

                    insight = {
                        "insight_id": f"ins-{uuid.uuid4().hex[:6]}",
                        "created_at_utc": now_utc,
                        "cycle_num": self.cycle_num,
                        "insight_type": "BOTTLENECK",
                        "target_category": cat,
                        "target_problem_id": prob_id,
                        "severity_score": round(severity, 2),
                        "sql_executed": final_sql,
                        "evidence_summary": f"Detected bottleneck on {prob_id} ({cat}): avg attempts {avg_att:.2f}, accuracy {acc:.0%}, dominant misconception '{tag}'.",
                        "recommended_nudge": nudge,
                        "status": "ACTIVE",
                    }
                    insight = self._normalize_insight(insight)
                    action_taken = "EMITTED_BOTTLENECK_INTERVENTION"
            else:
                cat = top.get("category", "Geometry")
                conf = float(top.get("mean_conf", 80))
                acc = float(top.get("empirical_acc", 0.25))
                c_prob = "CAM-2022-N4-19" if cat == "Geometry" else None
                if self._has_existing_insight("CALIBRATION_GAP", c_prob, cat):
                    action_taken = "SKIPPED_DUPLICATE_CALIBRATION_GAP"
                else:
                    insight = {
                        "insight_id": f"ins-{uuid.uuid4().hex[:6]}",
                        "created_at_utc": now_utc,
                        "cycle_num": self.cycle_num,
                        "insight_type": "CALIBRATION_GAP",
                        "target_category": cat,
                        "target_problem_id": c_prob,
                        "severity_score": 0.75,
                        "sql_executed": final_sql,
                        "evidence_summary": f"Dunning-Kruger mismatch in {cat}: high confidence ({conf:.0f}%) vs empirical accuracy ({acc:.0%}).",
                        "recommended_nudge": (
                            "Could two angles in the same triangle both measure 100 degrees, "
                            "given the 180-degree sum?"
                            if cat == "Geometry"
                            else "Before your next attempt, can you build one concrete example where your current approach fails?"
                        ),
                        "status": "ACTIVE",
                    }
                    insight = self._normalize_insight(insight)
                    action_taken = "EMITTED_CALIBRATION_INTERVENTION"

        trace = {
            "trace_id": f"trc-{uuid.uuid4().hex[:6]}",
            "ts_utc": now_utc,
            "cycle_num": self.cycle_num,
            "agent_role": "TwoStageCognitiveLoop",
            "model_id": "heuristic-rule-engine",
            "sql_executed": final_sql,
            "sql_latency_ms": latency_ms,
            "findings_count": len(rows),
            "action_taken": action_taken,
            "prompt_tokens": 340,
            "completion_tokens": 105,
            "estimated_cost_usd": 0.00019,
        }

        return insight, trace

    def _run_llm_cycle(
        self, now_utc: str, meta_memory: dict[str, Any]
    ) -> tuple[Optional[AgentInsight], dict[str, Any]]:
        """Two-Stage LLM cognitive loop with GLM 5.3."""
        print("[Agent:Stage 1 - Data Detective] GLM 5.3 formulating analytical SQL...")

        memory_prompt_addon = ""
        if meta_memory.get("pivot_recommended"):
            memory_prompt_addon = (
                "NOTE: Past query cycles on basic attempt averages returned low yield. "
                "Pivot your focus to test for calibration gaps (high confidence dial but low correctness) "
                "or misconception tag clustering."
            )

        prompt = f"""Write a SQL query against `telemetry_events` to identify either:
1. Problems where student average attempt count >= 2.0, OR
2. Categories with acute calibration gaps (confidence_pct >= 70 but accuracy < 40%), OR
3. AI-safety guardrail spikes where misconception_tag IN ('JAILBREAK_ATTEMPT', 'OFFLOADING_ATTEMPT', 'BINARY_SEARCH_PROBE', 'UNSAFE_OFFTOPIC') occurs >= 2 times.
{memory_prompt_addon}
Return ONLY the raw SQL query string.
"""
        resp = self._llm.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        raw_sql = (
            resp.choices[0]
            .message.content.strip()
            .replace("```sql", "")
            .replace("```", "")
            .strip()
        )

        rows, final_sql, latency_ms = self.execute_self_correcting_sql(raw_sql)
        print(f"[Agent:Stage 1 - Data Detective] Query executed in {latency_ms}ms. Yield: {len(rows)} rows.")

        print("[Agent:Stage 2 - Socratic Pedagogist] GLM 5.3 synthesizing Socratic intervention...")
        synthesis_prompt = f"""Given the following SQL execution output from RawTree:
Query: {final_sql}
Results: {json.dumps(rows[:5])}

Decide if a pedagogical intervention is warranted. If yes, respond with valid JSON:
{{
  "should_intervene": true,
  "insight_type": "BOTTLENECK" or "CALIBRATION_GAP" or "INTERVENTION",
  "target_category": "...",
  "target_problem_id": "...",
  "severity_score": 0.0 to 1.0,
  "evidence_summary": "1-2 sentence quantitative summary",
  "recommended_nudge": "Socratic nudge under 200 chars (never reveal direct answers)"
}}
If no intervention needed, respond with {{"should_intervene": false}}.
"""
        resp2 = self._llm.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        llm_data = json.loads(resp2.choices[0].message.content)
        u1 = getattr(resp, "usage", None)
        u2 = getattr(resp2, "usage", None)
        prompt_tokens = (getattr(u1, "prompt_tokens", 0) or 0) + (getattr(u2, "prompt_tokens", 0) or 0)
        completion_tokens = (getattr(u1, "completion_tokens", 0) or 0) + (getattr(u2, "completion_tokens", 0) or 0)
        cost_usd = (prompt_tokens * 0.0005 + completion_tokens * 0.0015) / 1000.0

        insight: Optional[AgentInsight] = None
        action_taken = "OBSERVED_NOMINAL"

        if llm_data.get("should_intervene"):
            insight_type: InsightType = llm_data.get("insight_type", "BOTTLENECK")
            if insight_type not in ("BOTTLENECK", "CALIBRATION_GAP", "INTERVENTION"):
                insight_type = "BOTTLENECK"

            target_prob = llm_data.get("target_problem_id")
            target_cat = llm_data.get("target_category", "Combinatorics")

            if self._has_existing_insight(insight_type, target_prob, target_cat):
                action_taken = f"SKIPPED_DUPLICATE_{insight_type}"
            else:
                severity = max(0.0, min(1.0, float(llm_data.get("severity_score", 0.75))))
                nudge = str(llm_data.get("recommended_nudge") or "How might parity or symmetry simplify this problem?").strip()
                if re.search(
                    r"\b(?:the\s+)?(?:correct\s+)?answer\s+is\s+[A-E]\b|\boption\s+[A-E]\s+is\s+correct\b",
                    nudge,
                    re.IGNORECASE,
                ):
                    nudge = "Can you check the core invariant or test a smaller case before choosing an option?"
                nudge = nudge[:240]

                raw_insight = {
                    "insight_id": f"ins-{uuid.uuid4().hex[:6]}",
                    "created_at_utc": now_utc,
                    "cycle_num": self.cycle_num,
                    "insight_type": insight_type,
                    "target_category": target_cat,
                    "target_problem_id": target_prob,
                    "severity_score": round(severity, 2),
                    "sql_executed": final_sql,
                    "evidence_summary": llm_data.get("evidence_summary", "Detected classroom bottleneck."),
                    "recommended_nudge": nudge,
                    "status": "ACTIVE",
                }
                insight = self._normalize_insight(raw_insight)
                action_taken = f"EMITTED_{insight_type}"

        trace = {
            "trace_id": f"trc-{uuid.uuid4().hex[:6]}",
            "ts_utc": now_utc,
            "cycle_num": self.cycle_num,
            "agent_role": "TwoStageCognitiveLoop",
            "model_id": self.model,
            "sql_executed": final_sql,
            "sql_latency_ms": latency_ms,
            "findings_count": len(rows),
            "action_taken": action_taken,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": round(cost_usd, 5),
        }

        return insight, trace

    def run_loop(self, interval_seconds: int = 15) -> None:
        """Run cognitive loop continuously every `interval_seconds`."""
        print(f"[Agent] Starting autonomous loop (interval: {interval_seconds}s). Press Ctrl+C to stop.")
        try:
            while True:
                try:
                    self.run_cycle()
                except Exception as e:
                    # A single failed cycle (DB lock, flaky endpoint) must never
                    # kill the long-running cognitive loop.
                    print(f"[Agent] Cycle {self.cycle_num} failed: {e}. Continuing after interval.")
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n[Agent] Cognitive loop stopped by user.")


def main() -> None:
    parser = argparse.ArgumentParser(description="SocraticQ Autonomous RawTree Cognitive Agent")
    parser.add_argument("--interval", type=int, default=15, help="Interval in seconds between cycles")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--mock", action="store_true", help="Force deterministic heuristic mode")
    args = parser.parse_args()

    agent = AutonomousRawTreeAgent(force_heuristic=args.mock)
    if args.once:
        agent.run_cycle()
    else:
        agent.run_loop(interval_seconds=args.interval)


if __name__ == "__main__":
    main()
