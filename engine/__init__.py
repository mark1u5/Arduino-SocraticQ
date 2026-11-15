"""Offline Socratic math mentor engine for SocraticQ."""

from typing import Any


def __getattr__(name: str) -> Any:
    if name in {
        "generate_socratic_hint",
        "generate_socratic_turn",
        "inspect_problem",
        "get_hint_rung",
        "solve_mcq_calibrated",
        "fit_priors_from_jsonl",
        "normalize_catalan_math_keywords",
    }:
        from engine import socratic_engine

        return getattr(socratic_engine, name)
    raise AttributeError(f"module 'engine' has no attribute {name!r}")


__all__ = [
    "generate_socratic_hint",
    "generate_socratic_turn",
    "inspect_problem",
    "get_hint_rung",
    "solve_mcq_calibrated",
    "fit_priors_from_jsonl",
    "normalize_catalan_math_keywords",
]
