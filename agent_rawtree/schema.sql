-- DDL definitions for SocraticQ RawTree schema
-- Owned by Dev 3 (agent_rawtree)

-- 1. Student Telemetry Events (from Edge Arduino Uno Q + Web App)
CREATE TABLE IF NOT EXISTS telemetry_events (
    event_id          VARCHAR(64) PRIMARY KEY,
    session_id        VARCHAR(64) NOT NULL,
    device_id         VARCHAR(32) NOT NULL,
    ts_utc            TIMESTAMP NOT NULL,
    problem_id        VARCHAR(32) NOT NULL,
    cangur_year       INTEGER NOT NULL,
    category          VARCHAR(32) NOT NULL,
    difficulty_level  INTEGER NOT NULL,
    event_type        VARCHAR(24) NOT NULL,
    attempt_num       INTEGER NOT NULL,
    confidence_pct    INTEGER NOT NULL,
    is_correct        BOOLEAN,
    misconception_tag VARCHAR(64),
    pedagogical_move  VARCHAR(48),
    solve_elapsed_ms  INTEGER NOT NULL,
    slm_latency_ms    INTEGER NOT NULL,
    offline_cached    BOOLEAN NOT NULL DEFAULT FALSE
);

-- 2. Agent Insights & Interventions (Cognitive Scratchpad & Edge Nudges)
CREATE TABLE IF NOT EXISTS agent_insights (
    insight_id        VARCHAR(64) PRIMARY KEY,
    created_at_utc    TIMESTAMP NOT NULL,
    cycle_num         INTEGER NOT NULL,
    insight_type      VARCHAR(32) NOT NULL,
    target_category   VARCHAR(32) NOT NULL,
    target_problem_id VARCHAR(32),
    severity_score    DOUBLE PRECISION NOT NULL,
    sql_executed      TEXT NOT NULL,
    evidence_summary  TEXT NOT NULL,
    recommended_nudge TEXT NOT NULL,
    status            VARCHAR(16) NOT NULL
);

-- 3. Agent Meta-Cognition & Trace Telemetry (Tracks agent operations, SQL latency, token cost)
-- Answers RawTree Challenge Goal: 'An agent that tracks its own work: tool calls, traces and costs land in RawTree'
CREATE TABLE IF NOT EXISTS agent_traces (
    trace_id          VARCHAR(64) PRIMARY KEY,
    ts_utc            TIMESTAMP NOT NULL,
    cycle_num         INTEGER NOT NULL,
    agent_role        VARCHAR(32) NOT NULL,
    model_id          VARCHAR(48) NOT NULL,
    sql_executed      TEXT NOT NULL,
    sql_latency_ms    INTEGER NOT NULL,
    findings_count    INTEGER NOT NULL,
    action_taken      VARCHAR(64) NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    estimated_cost_usd DOUBLE PRECISION NOT NULL
);
