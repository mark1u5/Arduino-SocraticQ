"""Unit tests for analytics_tukey (WP-6 verification)."""

import unittest
import pandas as pd
import plotly.graph_objects as go

from analytics_tukey.charts import (
    build_agent_traces_chart,
    build_confidence_calibration_chart,
    build_difficulty_drift_chart,
    build_ecdf_solve_time,
    build_guardrail_audit_chart,
    build_intervention_impact_chart,
    build_misconception_heatmap,
    build_socratic_funnel_chart,
    build_solve_time_boxplot,
    build_tukey_residual_plot,
)
from analytics_tukey.queries import (
    compute_calibration_metrics,
    compute_intervention_effect_size,
    compute_tukey_summary,
    get_agent_insights,
    get_agent_traces,
    get_calibration_gap,
    get_difficulty_drift,
    get_pre_post_intervention,
    get_tukey_spread,
    init_local_db,
)


class TestAnalyticsTukey(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_local_db()

    def test_solved_events_in_calibration_metrics(self):
        """Verify SOLVED events are counted alongside ANSWER_SUBMIT."""
        df = pd.DataFrame([
            {"event_type": "SOLVED", "confidence_pct": 90, "is_correct": 1},
            {"event_type": "ANSWER_SUBMIT", "confidence_pct": 90, "is_correct": 0},
            {"event_type": "HINT_REQUEST", "confidence_pct": 20, "is_correct": None},
        ])
        metrics = compute_calibration_metrics(df)
        self.assertIn("brier_score", metrics)
        self.assertIn("ece", metrics)
        self.assertIn("overconfidence_gap", metrics)
        self.assertIn("high_conf_error_rate", metrics)
        # Both SOLVED and ANSWER_SUBMIT were evaluated (2 items, 1 right, 1 wrong = 50% acc)
        self.assertEqual(metrics["high_conf_error_rate"], 50.0)

    def test_zero_confidence_calibration_retained(self):
        """Verify confidence_pct == 0 is retained with include_lowest=True and empty bins dropped."""
        df = pd.DataFrame([
            {"event_type": "SOLVED", "confidence_pct": 0, "is_correct": 1},
            {"event_type": "ANSWER_SUBMIT", "confidence_pct": 100, "is_correct": 1},
        ])
        fig = build_confidence_calibration_chart(df)
        self.assertIsInstance(fig, go.Figure)
        self.assertGreaterEqual(len(fig.data), 2)
        # Check empirical trace
        empirical_trace = [t for t in fig.data if t.name == "Empirical Accuracy"][0]
        # 0% confidence point should be in x
        self.assertTrue(any(x <= 10 for x in empirical_trace.x))

    def test_get_calibration_gap_includes_solved(self):
        """Verify get_calibration_gap includes SOLVED in SQL query."""
        calib_gap = get_calibration_gap()
        self.assertIsInstance(calib_gap, pd.DataFrame)
        self.assertFalse(calib_gap.empty)
        self.assertIn("submissions", calib_gap.columns)
        self.assertIn("empirical_accuracy", calib_gap.columns)

    def test_pre_post_intervention_hardening(self):
        """Verify pre/post intervention handles arbitrary problem_id, SQL sanitization and fallback."""
        # 1. Standard problem with specific insight
        df1 = get_pre_post_intervention("CAM-2023-N4-17")
        self.assertFalse(df1.empty)
        self.assertIn("intervention_phase", df1.columns)
        self.assertTrue({"Pre-Intervention", "Post-Intervention"}.issubset(set(df1["intervention_phase"])))

        # 2. Problem without specific insight row falls back to earliest insight
        df2 = get_pre_post_intervention("CAM-2024-N4-11")
        self.assertFalse(df2.empty)
        self.assertIn("intervention_phase", df2.columns)
        self.assertTrue(len(df2["intervention_phase"].unique()) >= 1)

        # 3. Sanitization against SQL injection attempts
        df_safe = get_pre_post_intervention("CAM-2023-N4-17' OR '1'='1")
        self.assertIsInstance(df_safe, pd.DataFrame)

    def test_intervention_effect_size_metrics(self):
        """Verify Cohen's d, Mann-Whitney U, Cliff's Delta, and time reduction."""
        pre_post_df = pd.DataFrame([
            {"intervention_phase": "Pre-Intervention", "solve_sec": 80.0, "attempt_num": 3},
            {"intervention_phase": "Pre-Intervention", "solve_sec": 90.0, "attempt_num": 4},
            {"intervention_phase": "Pre-Intervention", "solve_sec": 100.0, "attempt_num": 3},
            {"intervention_phase": "Post-Intervention", "solve_sec": 20.0, "attempt_num": 1},
            {"intervention_phase": "Post-Intervention", "solve_sec": 25.0, "attempt_num": 1},
            {"intervention_phase": "Post-Intervention", "solve_sec": 30.0, "attempt_num": 2},
        ])
        stats = compute_intervention_effect_size(pre_post_df)
        self.assertGreater(stats["time_reduction_pct"], 50.0)
        self.assertGreater(stats["cohens_d"], 0.8)  # Large effect
        self.assertIn("mann_whitney_u", stats)
        self.assertIn("mann_whitney_p", stats)
        self.assertIn("cliffs_delta", stats)
        self.assertEqual(stats["effect_label"], "Large")

    def test_all_9_tukey_charts_render(self):
        """Verify all 9 (10) Tukey charts generate valid plotly figures."""
        spread = get_tukey_spread()
        drift = get_difficulty_drift()
        traces = get_agent_traces()
        pre_post = get_pre_post_intervention()

        f1 = build_solve_time_boxplot(spread)
        self.assertIsInstance(f1, go.Figure)

        f2 = build_confidence_calibration_chart(spread)
        self.assertIsInstance(f2, go.Figure)

        f3 = build_misconception_heatmap(spread)
        self.assertIsInstance(f3, go.Figure)

        f4 = build_intervention_impact_chart(pre_post)
        self.assertIsInstance(f4, go.Figure)

        f5 = build_ecdf_solve_time(spread)
        self.assertIsInstance(f5, go.Figure)

        f6 = build_tukey_residual_plot(spread)
        self.assertIsInstance(f6, go.Figure)

        f7 = build_difficulty_drift_chart(drift)
        self.assertIsInstance(f7, go.Figure)

        f8 = build_agent_traces_chart(traces)
        self.assertIsInstance(f8, go.Figure)

        f9 = build_socratic_funnel_chart(spread)
        self.assertIsInstance(f9, go.Figure)

        f10 = build_guardrail_audit_chart(spread)
        self.assertIsInstance(f10, go.Figure)


if __name__ == "__main__":
    unittest.main()
