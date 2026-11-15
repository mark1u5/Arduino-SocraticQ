"""Offline Socratic engine implementing contracts.schemas.SocraticEngineFn."""

import argparse
import ast
import collections
import fractions
import itertools
import json
import math
import os
import re
import socket
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

from contracts.schemas import (
    EngineEventType,
    PedagogicalMove,
    SocraticResponse,
    validate_socratic_response,
)
from engine.cangur_bank import (
    _load_bank,
    bridge_cat_to_eng,
    get_easier_neighbor,
    get_problem,
    infer_topic_from_neighbors,
    list_problem_ids,
    localize_problem,
    match_concept_guide,
    normalize_language,
    retrieve_similar_problems,
)
from engine.prompts import (
    SOCRATIC_DIMENSIONS,
    _asks_compound_target,
    _parse_constant_math,
    build_messages,
    build_solve_messages,
    clamp_text,
    extract_math_oracle,
    inspect_student_input,
    is_safe_hint,
    verify_equation_options,
)

DEFAULT_SERVER_URL = "http://127.0.0.1:8081/v1/chat/completions"
DEFAULT_MODEL_ID = "qwen3.5-0.8b"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 42
MCQ_LETTERS: tuple[str, ...] = ("A", "B", "C", "D", "E")
# Measured empirically on Arduino UNO Q (Qwen3.5-0.8B-Q8_0, :8081) with content-free "N/A" prompt
DEFAULT_BASELINE_LOGPROBS: dict[str, float] = {
    "A": -0.481432,
    "B": -1.493458,
    "C": -2.433861,
    "D": -3.309879,
    "E": -3.647951,
}
HELMCODE_MODELS = ("glm-5.3", "qwen-2.5-coder", "deepseek-v3")
SLM_TIMEOUT_SEC = float(os.environ.get("SLM_TIMEOUT_SEC", "30.0"))
_server_retry_after = 0.0  # ponytail: 5s cooldown after connection error avoids Windows 0.66s/call stall
_INFERENCE_LOCK = threading.Lock()
_LAST_LLM_STATS: dict[str, Any] = {"prompt_n": 0, "tok_s": 0.0}

MOVE_LCD_LABELS: dict[PedagogicalMove, str] = {
    "DECOMPOSE_PROBLEM": "Break into smaller steps",
    "COUNTEREXAMPLE_CHECK": "Test a tiny case!",
    "INVARIANT_NUDGE": "Find what stays fixed",
    "PARITY_CHECK": "Try chessboard parity",
    "BOUNDARY_TEST": "Test worst/edge case",
    "AFFIRM_CORRECT": "Correct! Explain why",
}


def _route_pedagogical_move(
    problem: dict[str, Any],
    event_type: EngineEventType,
    attempt_num: int,
    confidence_pct: int,
    is_correct: Optional[bool],
    agent_override_nudge: Optional[str],
) -> PedagogicalMove:
    if event_type == "PROBLEM_SELECT":
        return "DECOMPOSE_PROBLEM"
    if event_type == "ANSWER_SUBMIT" and is_correct is None and not problem.get("is_open_prompt"):
        return "DECOMPOSE_PROBLEM"
    if is_correct is True:
        return "AFFIRM_CORRECT"
    if is_correct is False and confidence_pct >= 70:
        return "COUNTEREXAMPLE_CHECK"
    if agent_override_nudge or attempt_num >= 3:
        return "INVARIANT_NUDGE"
    return problem["default_move"]


_GREETING_RE = re.compile(
    r"(?i)^(?:hi|hello|hey|hola|bon\s+dia|bona\s+tarda|bona\s+nit|ei|salut|good\s+(?:morning|afternoon|evening))[\s!?.]*$"
)
_CONFUSION_ONLY_RE = re.compile(
    r"(?i)^(?:help|ajuda|test|ok|okay|yes|no|sí|si|idk|dunno|stuck|hint|pista|explain|explica|no\s+ho\s+s[eé]|no\s+entenc|no\s+ho\s+entenc|\?+|!+|\.+)$"
)


def _classify_short_or_greeting(
    student_note: Optional[str],
    history: Optional[list[dict[str, str]]],
) -> Optional[str]:
    """Return 'greeting' or 'unclear' if the input should short-circuit without calling the SLM."""
    ut = (student_note or "").strip()
    if not ut:
        return None
    if _GREETING_RE.fullmatch(ut):
        return "greeting"
    has_math_now = bool(re.search(r"(?:\d|[=+\-*/^<>]|triangle|angle|polygon|circle|probabilit|equaci|deriv|integr|sum|area|perimet)", ut, re.I))
    if has_math_now:
        return None
    has_active_history = bool(
        history
        and any(
            isinstance(h, dict)
            and str(h.get("content") or "").strip()
            and re.search(r"(?:\d|[=+\-*/^<>]|triangle|angle|polygon|circle|probabilit|equaci)", str(h.get("content") or ""), re.I)
            for h in history
        )
    )
    if has_active_history:
        return None
    if len(ut) <= 3 or _CONFUSION_ONLY_RE.fullmatch(ut):
        return "unclear"
    return None


def _clean_math_output(text: str) -> str:
    """Ensure LaTeX/math delimiters are balanced and clean for UI rendering."""
    if not text:
        return text
    out = text.strip()
    # Replace raw \pmod{m} with readable (mod m) if not inside $...$
    out = re.sub(r"\\pmod\s*\{([^}]+)\}", r"(mod \1)", out)
    # Remove unmatched trailing $ if odd count (e.g. from truncation)
    if out.count("$") % 2 == 1:
        last_idx = out.rfind("$")
        out = out[:last_idx] + out[last_idx + 1 :]
    # Remove unmatched \( or \[
    if out.count("\\(") > out.count("\\)"):
        idx = out.rfind("\\(")
        out = out[:idx] + out[idx + 2 :]
    if out.count("\\[") > out.count("\\]"):
        idx = out.rfind("\\[")
        out = out[:idx] + out[idx + 2 :]
    return " ".join(out.split()).strip() if "\n" not in out else out.strip()


_CONCEPTUAL_Q_RE = re.compile(
    r"(?i)^(?:what\s+(?:is|are)|how\s+(?:does|do|can|to)|why\s+(?:does|do|is)|explain|define"
    r"|qu[eè]\s+[eé]s|qu[eè]\s+s[oó]n|com\s+(?:funciona|es\s+calcula|es\s+fa)|per\s+qu[eè]|explica)\b"
)


def _is_conceptual_question(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.strip()
    if "=" in t:
        return False
    return bool(_CONCEPTUAL_Q_RE.search(t))


def _conceptual_fallback(text: str, lang: str) -> Optional[str]:
    """Friendly 1-2 sentence explanation + tiny concrete example + gentle check question."""
    low = text.lower()
    if any(w in low for w in ("pythagor", "pitàgor", "pitagor")):
        return (
            "El teorema de Pitàgores diu que en un triangle rectangle, $a^2 + b^2 = c^2$ (per exemple, $3^2 + 4^2 = 5^2$, és a dir $9 + 16 = 25$). Si els catets fan $6$ i $8$, quant fa $c^2$?"
            if lang == "cat"
            else "Pythagoras' theorem says that in a right triangle, $a^2 + b^2 = c^2$ (for example, $3^2 + 4^2 = 5^2$ since $9 + 16 = 25$). If the two legs are $6$ and $8$, what is $c^2$?"
        )
    if any(w in low for w in ("prime", "primer", "primers")):
        return (
            "Un nombre primer és un enter més gran que $1$ que només es pot dividir exactament per $1$ i per ell mateix, com $2, 3, 5, 7$. Per exemple, $6 = 2 \\times 3$ no és primer. El nombre $9$ és primer o compost?"
            if lang == "cat"
            else "A prime number is an integer greater than $1$ divisible only by $1$ and itself, like $2, 3, 5, 7$ (whereas $6 = 2 \\times 3$ is not). Is $9$ prime or composite?"
        )
    if any(w in low for w in ("triangle",)) and any(w in low for w in ("area", "àrea")):
        return (
            "L'àrea d'un triangle és $\\frac{\\text{base} \\times \\text{altura}}{2}$ perquè és exactament la meitat d'un rectangle (per exemple, base $4$ i altura $3$ dona $\\frac{4 \\times 3}{2} = 6$). Quina àrea tindria amb base $10$ i altura $4$?"
            if lang == "cat"
            else "A triangle's area is $\\frac{\\text{base} \\times \\text{height}}{2}$ because it is half of a rectangle (for example, base $4$ and height $3$ gives $\\frac{4 \\times 3}{2} = 6$). What would the area be for base $10$ and height $4$?"
        )
    if any(w in low for w in ("percent", "percentatge")):
        return (
            "Un percentatge vol dir parts de cada $100$: per exemple, $25\\%$ de $80$ és $\\frac{25}{100} \\times 80 = 20$. Quant seria el $10\\%$ de $50$?"
            if lang == "cat"
            else "A percentage means parts per $100$: for example, $25\\%$ of $80$ is $\\frac{25}{100} \\times 80 = 20$. What would $10\\%$ of $50$ be?"
        )
    if any(w in low for w in ("probabilit",)):
        return (
            "La probabilitat és $\\frac{\\text{casos favorables}}{\\text{casos totals}}$ quan tots són igual de possibles (per exemple, treure cara en una moneda és $\\frac{1}{2}$). Quina és la probabilitat de treure un $6$ en un dau de $6$ cares?"
            if lang == "cat"
            else "Probability is $\\frac{\\text{favorable outcomes}}{\\text{total outcomes}}$ when outcomes are equally likely (for example, flipping heads on a coin is $\\frac{1}{2}$). What is the probability of rolling a $6$ on a fair 6-sided die?"
        )
    return None


def _detect_student_claim(student_note: Optional[str]) -> Optional[tuple[str, str]]:
    """Extract a student's proposed variable value like 'I think x = 11' or 'x = 11'."""
    if not student_note:
        return None
    m = re.search(
        r"(?i)(?:i\s+think|maybe|is\s+it|guess|got|crec\s+que|potser|tinc)?\s*\b([a-z])\s*=\s*(-?\d+(?:\.\d+)?(?:/\d+)?)\b",
        student_note,
    )
    if m:
        return m.group(1), m.group(2)
    return None


def _fallback_hint(
    problem: dict[str, Any],
    event_type: EngineEventType,
    attempt_num: int,
    confidence_pct: int,
    is_correct: Optional[bool],
    selected_option: Optional[str],
    agent_override_nudge: Optional[str],
    oracle: Optional[dict[str, Any]] = None,
    student_note: Optional[str] = None,
    language: str = "eng",
) -> str:
    lang = normalize_language(language)
    if event_type == "PROBLEM_SELECT" and not (student_note and student_note.strip()):
        msg = (
            f"Abans de calcular, quina estructura o quantitat d'aquest problema de {problem['category']} es manté fixa?"
            if lang == "cat"
            else f"Before calculating, what structure or quantity in this {problem['category']} problem stays fixed?"
        )
        return clamp_text(msg, 240, append_question_mark=True)
    if event_type == "ANSWER_SUBMIT" and selected_option not in {"A", "B", "C", "D", "E"} and not problem.get("is_open_prompt"):
        msg = (
            "Tria una opció A-E o explica quin primer pas has provat per poder comprovar el teu raonament?"
            if lang == "cat"
            else "Pick option A-E or share what first step you tried so we can test your reasoning?"
        )
        return msg
    if is_correct is True:
        hook = problem["intuition_hook"].rstrip(".!?")
        if confidence_pct < 50:
            msg = (
                f"Resposta correcta ({selected_option}), fins i tot amb {confidence_pct}% de confiança. Per què es compleix aquesta regla: {hook}?"
                if lang == "cat"
                else f"Right answer ({selected_option}), even at {confidence_pct}% confidence. Why must that rule hold: {hook}?"
            )
            return clamp_text(msg, 240, append_question_mark=True)
        msg = (
            f"Confirmat ({selected_option}). Com aplicaries '{hook}' en una versió més gran d'aquest problema?"
            if lang == "cat"
            else f"Confirmed ({selected_option}). How would you use '{hook}' on a larger version of this problem?"
        )
        return clamp_text(msg, 240, append_question_mark=True)
    if agent_override_nudge and is_safe_hint(agent_override_nudge, problem, is_correct, oracle=oracle):
        return clamp_text(agent_override_nudge, 240, append_question_mark=True)

    # Check conceptual "what is / how does" questions first
    if student_note and _is_conceptual_question(student_note):
        conc = _conceptual_fallback(student_note, lang)
        if conc and is_safe_hint(conc, problem, is_correct, oracle=oracle):
            return clamp_text(conc, 240, append_question_mark=True)

    # Smart student-claim & equation-specific Socratic guidance (never parrot the problem back)
    claim = _detect_student_claim(student_note)
    o_kind = (oracle or {}).get("kind")
    o_sols = (oracle or {}).get("solutions") or []
    note_low = (student_note or "").lower()
    wants_smaller = attempt_num >= 2 or any(
        w in note_low for w in ("stuck", "another hint", "smaller hint", "encallat", "altra pista", "pista més petita")
    )

    if claim and (not o_sols or claim[1] not in o_sols):
        var_n, guess_v = claim
        msg = (
            f"Comprovem junts {var_n} = {guess_v}: si ho substitueixes a l'equació original, què obtens al costat esquerre comparat amb el dret?"
            if lang == "cat"
            else f"Let's check {var_n} = {guess_v} together: if you substitute it into the original equation, what value do you get on the left side compared to the right?"
        )
        if is_safe_hint(msg, problem, is_correct, oracle=oracle):
            return clamp_text(msg, 240, append_question_mark=True)

    if o_kind == "quadratic":
        if wants_smaller:
            msg = (
                "Si escrivim (x - a)(x - b) = 0, a * b ha de donar el terme independent i a + b el coeficient central. Quins dos enters positius compleixen això?"
                if lang == "cat"
                else "If we write (x - a)(x - b) = 0, then a * b equals the constant term and a + b equals the middle coefficient's opposite. Which small positive integers work?"
            )
        else:
            msg = (
                "En aquesta equació quadràtica, quins dos nombres tenen com a producte el terme independent i com a suma el coeficient del terme en x?"
                if lang == "cat"
                else "For this quadratic equation, which two numbers multiply to the constant term and add up to the middle coefficient?"
            )
        if is_safe_hint(msg, problem, is_correct, oracle=oracle):
            return clamp_text(msg, 240, append_question_mark=True)

    if o_kind == "linear":
        if wants_smaller:
            msg = (
                "Pas concret: desfés primer el terme que suma o resta a la incògnita als dos costats. Quina equació més senzilla et queda?"
                if lang == "cat"
                else "Next small step: undo the constant added or subtracted next to the variable on both sides first. What simpler equation is left?"
            )
        else:
            msg = (
                "Per aïllar la incògnita, quin terme constant pots desfer primer als dos costats de l'equació?"
                if lang == "cat"
                else "To isolate the unknown, which constant term can you undo from both sides of the equation first?"
            )
        if is_safe_hint(msg, problem, is_correct, oracle=oracle):
            return clamp_text(msg, 240, append_question_mark=True)

    if o_kind == "derivative":
        msg = (
            "Aplicant la regla de la potència d/dx(x^n) = n*x^(n-1), què passa amb l'exponent i el coeficient de cada terme?"
            if lang == "cat"
            else "Using the power rule d/dx(x^n) = n*x^(n-1), how do the exponent and coefficient of each term change?"
        )
        if is_safe_hint(msg, problem, is_correct, oracle=oracle):
            return clamp_text(msg, 240, append_question_mark=True)

    if o_kind == "arithmetic":
        msg = (
            "Si ho fas pas a pas respectant parèntesis, multiplicacions i sumes (o buscant un denominador comú per a fraccions), quin és el primer càlcul que pots fer?"
            if lang == "cat"
            else "Taking it one step at a time (parentheses/multiplication first, or a common denominator for fractions), what is the very first small calculation you can do?"
        )
        if is_safe_hint(msg, problem, is_correct, oracle=oracle):
            return clamp_text(msg, 240, append_question_mark=True)

    if is_correct is False and confidence_pct >= 70:
        candidate = clamp_text(
            f"Has triat {selected_option} amb {confidence_pct}% de confiança. Quina condició de l'enunciat falla si la comproves?"
            if lang == "cat"
            else f"You picked {selected_option} with {confidence_pct}% confidence. {problem['socratic_seed']}",
            240,
            append_question_mark=True,
        )
    elif attempt_num >= 2 and selected_option:
        candidate = clamp_text(
            f"Has triat {selected_option} amb {confidence_pct}% de confiança. Quin primer pas pots verificar?"
            if lang == "cat"
            else f"You picked {selected_option} with {confidence_pct}% confidence. {problem['socratic_seed']}",
            240,
            append_question_mark=True,
        )
    else:
        seed = (
            "Quina relació o dada inicial d'aquest problema pots aïllar primer per comprovar-la?"
            if lang == "cat"
            else problem["socratic_seed"]
        )
        candidate = clamp_text(seed, 240, append_question_mark=True)

    if is_safe_hint(candidate, problem, is_correct, oracle=oracle):
        return candidate
    fallback_msg = (
        f"Quina relació o regla d'aquest problema de {problem['category']} pots aïllar primer?"
        if lang == "cat"
        else f"What single relationship or rule in this {problem['category']} problem can you isolate first?"
    )
    return clamp_text(fallback_msg, 240, append_question_mark=True)


def _strip_think_tags(raw_text: str) -> str:
    text = re.sub(r"(?is)<think>.*?</think>", "", raw_text or "")
    text = re.sub(r"(?is)<think>.*$", "", text)
    return text.strip()


def _extract_first_question(raw_text: str) -> str:
    # Strip closed and unclosed thinking tags and stray Q:/Problem: prefixes
    text = _strip_think_tags(raw_text)
    text = re.sub(r"^(?:Q|Question|Problem|Pregunta|Problema)\s*:\s*", "", text, flags=re.I).strip()
    if "?" in text:
        text = text.split("?", 1)[0].strip() + "?"
    return text


def _call_llama_completion_with_status(
    messages: list[dict[str, str]],
    model_id: str,
    max_tokens: int = 42,
    stop: Optional[list[str]] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int = DEFAULT_SEED,
    cache_prompt: bool = True,
    logprobs: Optional[bool] = None,
    top_logprobs: Optional[int] = None,
    grammar: Optional[str] = None,
    url: Optional[str] = None,
    timeout_sec: Optional[float] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    global _server_retry_after
    if os.environ.get("SOCRATIC_MOCK", "0") == "1":
        return None, "mock"
    now = time.monotonic()
    if now < _server_retry_after:
        return None, "cooldown"

    target_url = url or os.environ.get("LLAMA_SERVER_URL", DEFAULT_SERVER_URL)
    effective_timeout = SLM_TIMEOUT_SEC if timeout_sec is None else max(0.05, float(timeout_sec))
    body: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "seed": seed,
        "cache_prompt": cache_prompt,
        "stop": stop if stop is not None else ["\n\n", "Student:", "Options:"],
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if logprobs is not None:
        body["logprobs"] = logprobs
    if top_logprobs is not None:
        body["top_logprobs"] = top_logprobs
    if grammar is not None:
        body["grammar"] = grammar

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        target_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with _INFERENCE_LOCK:
        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                return json.loads(resp.read().decode("utf-8")), "ok"
        except urllib.error.URLError as err:
            if isinstance(err.reason, (TimeoutError, socket.timeout)):
                return None, "timeout"
            _server_retry_after = time.monotonic() + 5.0
            return None, "error"
        except (TimeoutError, socket.timeout):
            return None, "timeout"
        except json.JSONDecodeError:
            return None, "error"


def _call_llama_completion(
    messages: list[dict[str, str]],
    model_id: str,
    max_tokens: int = 42,
    stop: Optional[list[str]] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int = DEFAULT_SEED,
    cache_prompt: bool = True,
    logprobs: Optional[bool] = None,
    top_logprobs: Optional[int] = None,
    grammar: Optional[str] = None,
    url: Optional[str] = None,
    timeout_sec: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    data, _ = _call_llama_completion_with_status(
        messages=messages,
        model_id=model_id,
        max_tokens=max_tokens,
        stop=stop,
        temperature=temperature,
        seed=seed,
        cache_prompt=cache_prompt,
        logprobs=logprobs,
        top_logprobs=top_logprobs,
        grammar=grammar,
        url=url,
        timeout_sec=timeout_sec,
    )
    return data


def _call_llama_server(
    messages: list[dict[str, str]],
    model_id: str,
    max_tokens: int = 42,
    extract_question: bool = True,
    stop: Optional[list[str]] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int = DEFAULT_SEED,
    cache_prompt: bool = True,
    timeout_sec: Optional[float] = None,
    student_note: Optional[str] = None,
) -> Optional[str]:
    data = _call_llama_completion(
        messages=messages,
        model_id=model_id,
        max_tokens=max_tokens,
        stop=stop,
        temperature=temperature,
        seed=seed,
        cache_prompt=cache_prompt,
        timeout_sec=timeout_sec,
    )
    if not data:
        return None
    try:
        timings = data.get("timings") or {}
        usage = data.get("usage") or {}
        _LAST_LLM_STATS["prompt_n"] = int(timings.get("prompt_n") or usage.get("prompt_tokens") or 0)
        _LAST_LLM_STATS["tok_s"] = round(float(timings.get("predicted_per_second") or 0.0), 2)
        raw = _strip_think_tags(str(data["choices"][0]["message"]["content"]))
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if extract_question:
        q = _extract_first_question(raw)
        if q and not q.endswith("?"):
            q = q.rstrip(".!,;:") + "?"
        if student_note and q and q.lower().strip() == student_note.lower().strip():
            return None
        return q
    return raw.strip()


def _call_helmcode_api(
    messages: list[dict[str, str]],
    problem: dict[str, Any],
    is_correct: Optional[bool],
    oracle: dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    """Optional gated online fallback when ENABLE_HELMCODE_FALLBACK=1 and local SLM fails."""
    if os.environ.get("ENABLE_HELMCODE_FALLBACK") != "1" or os.environ.get("SOCRATIC_MOCK", "0") == "1":
        return None, None
    api_key = os.environ.get("HELMCODE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None, None

    url = os.environ.get("HELMCODE_API_URL") or (
        os.environ.get("HELMCODE_BASE_URL", "https://api.helmcode.dev/v1").rstrip("/") + "/chat/completions"
    )
    timeout_sec = float(os.environ.get("HELMCODE_TIMEOUT_SEC", "8.0"))
    env_model = os.environ.get("HELMCODE_MODEL")
    models = list(dict.fromkeys([m for m in (env_model, *HELMCODE_MODELS) if m]))

    for model_name in models:
        payload = json.dumps(
            {
                "model": model_name,
                "messages": messages,
                "max_tokens": 60,
                "temperature": DEFAULT_TEMPERATURE,
                "seed": DEFAULT_SEED,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            candidate = _extract_first_question(data["choices"][0]["message"]["content"])
            if candidate and is_safe_hint(candidate, problem, is_correct, oracle=oracle):
                return clamp_text(candidate, 240), model_name
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError):
            continue
    return None, None


def _extract_final_answer_from_text(
    text: str,
    options: Optional[dict[str, str]] = None,
) -> tuple[Optional[str], Optional[str]]:
    final_ans: Optional[str] = None
    m = re.search(r"(?im)final\s+answer\s*[:=]\s*(.+)$", text)
    if m:
        final_ans = m.group(1).strip().rstrip(".")
    pred_opt: Optional[str] = None
    if options:
        search_area = final_ans or text.splitlines()[-1] if text.strip() else ""
        opt_m = re.search(r"\b([A-E])\b", search_area)
        if opt_m and opt_m.group(1) in options:
            pred_opt = opt_m.group(1)
    return final_ans, pred_opt


def _match_oracle_to_option(
    oracle: dict[str, Any],
    options: dict[str, str],
) -> Optional[str]:
    solutions = oracle.get("solutions") or []
    if not solutions or not options:
        return None
    norm_sols = [s.strip().lower() for s in solutions]
    for opt, val in options.items():
        val_norm = str(val).strip().lower()
        if val_norm in norm_sols:
            return opt
        try:
            val_num = float(val_norm)
            for s in norm_sols:
                if abs(float(s) - val_num) < 1e-6:
                    return opt
        except ValueError:
            continue
    return None


_ORACLE_STEP_GUIDES: dict[str, tuple[str, str]] = {
    "linear": (
        "Move constant terms to one side and variable terms to the other.",
        "Divide by the variable coefficient to isolate the unknown.",
    ),
    "quadratic": (
        "Rewrite the equation in standard form ax^2 + bx + c = 0.",
        "Factor or apply the quadratic formula to find all real roots.",
    ),
    "derivative": (
        "Apply the power rule d/dx(x^n) = n*x^(n-1) and linearity term-by-term.",
        "Simplify the coefficients and exponents.",
    ),
    "arithmetic": (
        "Follow order of operations (parentheses, powers, multiplication/division, addition/subtraction).",
        "Combine the terms to the exact simplified value.",
    ),
    "polygon_diagonals": (
        "From each vertex of an n-sided polygon, you can draw diagonals to n - 3 non-adjacent vertices.",
        "Multiply by n and divide by 2 to avoid counting each diagonal twice: n * (n - 3) / 2.",
    ),
    "polygon_diagonals_inv": (
        "Set the diagonal formula n(n - 3)/2 equal to the given count D and solve the quadratic n^2 - 3n - 2D = 0.",
        "Find the positive integer root n = (3 + sqrt(9 + 8D)) / 2 for the number of vertices.",
    ),
    "polygon_angle_sum": (
        "Decompose an n-sided polygon into n - 2 triangles from one vertex.",
        "Multiply by 180 degrees to get the total interior angle sum: (n - 2) * 180 degrees.",
    ),
    "polygon_regular_interior_angle": (
        "Divide the total interior angle sum (n - 2) * 180 degrees equally among all n vertices.",
        "Evaluate (n - 2) * 180 / n to find the measure of each interior angle.",
    ),
    "polygon_exterior_angle": (
        "The sum of the exterior angles of any convex polygon is always 360 degrees.",
        "Divide 360 degrees by n to find each exterior angle in a regular polygon.",
    ),
    "polygon_interior_to_sides": (
        "Use the exterior angle supplementary relation: exterior angle = 180 - interior angle.",
        "Divide 360 degrees by the exterior angle to determine the number of sides: n = 360 / (180 - theta).",
    ),
    "rectangle_equal_squares": (
        "Find the greatest common divisor gcd(W, H) of the dimensions to get the side length of the largest equal square.",
        "Multiply the number of squares along the width and height: (W / gcd) * (H / gcd).",
    ),
    "cuboid_to_cube": (
        "Determine the edge of the smallest enclosing cube without disassembling: s = max(a, b, c).",
        "Subtract the volume of the block from the volume of the full cube: s^3 - (a * b * c).",
    ),
    "handshakes": (
        "In a group of n people, each person pairs with n - 1 other people.",
        "Divide by 2 because each handshake involves 2 people: n * (n - 1) / 2.",
    ),
    "even_sum_card_pairs": (
        "A sum of two cards is even if both are even or both are odd.",
        "Sum the combinations: comb(even_count, 2) + comb(odd_count, 2).",
    ),
    "two_digit_odd_even": (
        "Determine the number of valid choices for the tens digit and units digit independently.",
        "Multiply the number of choices: tens_choices * units_choices.",
    ),
    "digit_occurrence_count": (
        "Count the occurrences of the digit in the units place and tens place in the given range.",
        "Subtract any overlaps where the digit appears twice (like 77) to get the count of unique numbers.",
    ),
    "reversed_diff_three_digit": (
        "Express the difference |abc - cba| = 99 * |a - c| and set it equal to the given difference.",
        "Determine valid ordered pairs (a, c) and multiply by the allowed choices for middle digit b.",
    ),
    "three_digit_two_equal": (
        "Choose which 2 of the 3 digit positions share the same value (3 choices).",
        "Count assignments of non-zero or distinct digits avoiding triple-identical numbers.",
    ),
    "coin_payment_ways": (
        "Set up the linear Diophantine relation c1 * x + c2 * y = T.",
        "Count non-negative integer pairs (x, y) that satisfy the total amount.",
    ),
    "coin_combination_opt": (
        "Set up equations for total coins and total value using at least 1 of each coin.",
        "Maximize the highest denomination coins while leaving valid amounts for the remaining coins.",
    ),
    "cycle_walk": (
        "Track the step-by-step transition counts or parity of returns on the cycle/square vertices.",
        "Count valid paths of length k that start and end at the origin vertex.",
    ),
    "consecutive_heads": (
        "Compute the total possible outcomes 2^N for N fair coin tosses.",
        "Count outcomes containing at least two consecutive heads using Fibonacci/recurrence counting.",
    ),
    "balls_without_replacement": (
        "Compute the probability of drawing the specified sequence without replacement.",
        "Multiply the conditional probabilities for each successive draw.",
    ),
    "knights_and_liars_circle": (
        "At a round table where each says 'Both neighbours are liars', truth-tellers cannot sit next to each other.",
        "The maximum number of truth-tellers is the maximum independent set on an N-cycle: floor(N / 2).",
    ),
    "last_digit_pow": (
        "Observe the repeating cycle of last digits for powers of the base modulo 10.",
        "Compute exponent mod cycle length to find the last digit: pow(base, exp, 10).",
    ),
    "common_remainder": (
        "The number minus the remainder r must be a common multiple of the divisors d_1, ..., d_k.",
        "Compute the least common multiple: lcm(d_1, ..., d_k) + r.",
    ),
    "modular_age_range": (
        "Test candidate ages in the given range [L, U] that satisfy the first modular constraint.",
        "Check which candidate satisfies all subsequent modular conditions simultaneously.",
    ),
    "square_divisors": (
        "Find the prime factorization of M and extract exponents e_i for each prime.",
        "A square divisor requires even exponents <= e_i; count combinations of (floor(e_i / 2) + 1).",
    ),
    "digit_product_min": (
        "Greedily factor the target product by single digits from 9 down to 2 to minimize the number of digits.",
        "Sort the extracted factors in ascending order to form the smallest positive integer.",
    ),
    "divisible_by_90": (
        "For a number to be divisible by 90, it must end in 0 and the sum of all digits must be divisible by 9.",
        "Set the last digit to 0 and solve for the missing digit so the digit sum is a multiple of 9.",
    ),
    "factorial_sum_mod": (
        "Notice that for k >= m (or when k! contains all prime factors of m), k! is 0 mod m.",
        "Sum only the non-zero terms k! mod m for k up to the cutoff.",
    ),
    "reciprocal_sum_pairs": (
        "Rewrite 1/x + 1/y = 1/n as (x - n)(y - n) = n^2.",
        "Each positive integer divisor of n^2 corresponds to a unique ordered pair (x, y).",
    ),
    "poly_divisibility_count": (
        "Evaluate the polynomial modulo m across the residue cycle 0 to m - 1.",
        "Multiply matching residues by the number of complete periods in N and add remaining boundary matches.",
    ),
    "digit_sum_multiple": (
        "For numbers less than 1000, express n = 100a + 10b + c and test n = k * (a + b + c).",
        "Check all 3-digit candidate numbers that satisfy the proportionality condition.",
    ),
    "partition_quotient_min": (
        "To minimize the quotient of the products of two groups partitioning {1..N}, keep the unmatchable primes in the first group.",
        "The largest prime p in {1..N} with p > N/2 cannot be simplified and gives the minimum integer quotient.",
    ),
    "alternating_steps": (
        "Calculate the net displacement per complete 2-jump cycle (forward - backward).",
        "Multiply by the number of full cycles and add the remaining jump displacement if odd.",
    ),
    "climbing_frog": (
        "Each cycle advances the net distance forward - backward, but the final jump reaches the top without sliding back.",
        "Compute the jumps needed to reach the threshold (H - forward) and add the final jump.",
    ),
    "clock_equal_digits": (
        "List valid hours and minutes from 00:00 to 23:59 where all 4 digits are identical (e.g. d d : d d).",
        "Check valid clock digits 0, 1, 2 to count valid identical-digit timestamps.",
    ),
    "runner_ahead_behind": (
        "Let B be runners behind and A = r * B runners ahead, so A + B + 1 = N total runners.",
        "Solve (r + 1) * B = N - 1 to find B, then position = A + 1.",
    ),
    "bus_schedule": (
        "Convert the start time and arrival time into minutes from midnight.",
        "Find the smallest departure time >= arrival time by adding intervals of P minutes.",
    ),
    "fraction_class_difference": (
        "Set up the difference between the two subsets as a fraction of the total class size T.",
        "Divide the given numerical difference by the fractional difference to obtain the total class size.",
    ),
    "largest_sum_4_digits": (
        "Sort the 4 available digits in descending order to assign the largest digits to the tens places.",
        "Form two 2-digit numbers with the two largest in the tens places and add: 10*(d1 + d2) + (d3 + d4).",
    ),
    "max_weekdays": (
        "Divide the total number of days by 7 to find complete weeks.",
        "Add 1 if the remainder allows the specified weekday to occur an extra time: ceil(D / 7).",
    ),
    "three_ages": (
        "Factor the product P into three positive integers and check their sum against S.",
        "Identify the unique valid triple (a, b, c) and return the age of the oldest child.",
    ),
    "integer_inequality": (
        "Find the roots of the quadratic or factored polynomial.",
        "Count or sum the integers strictly between the roots where the expression is negative.",
    ),
    "functional_equation": (
        "Substitute x and 1/x to create a 2x2 system of linear equations in f(x) and f(1/x).",
        "Eliminate f(1/x) to solve for f(x) at the target value.",
    ),
    "digit_sum_recurrence": (
        "Compute each successive term by adding the sum of its decimal digits to the current value.",
        "Iterate the sequence step by step until reaching the desired index.",
    ),
    "queue_rank": (
        "Sum the rank from the front and the rank from the back.",
        "Subtract 1 because the person at that position was counted twice: front + back - 1.",
    ),
    "doubling_days": (
        "Notice that doubling each day means half full occurs exactly 1 day before completely full.",
        "Subtract the corresponding number of doubling intervals from the final day.",
    ),
    "age_group_shift": (
        "Each person in the group ages by k years over the time interval.",
        "Multiply the number of people by k and add to the current sum of ages: current_sum + n * k.",
    ),
    "weighted_avg": (
        "Compute the total sum for each group by multiplying count by mean.",
        "Add the group totals and divide by the combined total count: (n1*m1 + n2*m2) / (n1 + n2).",
    ),
    "pct_diff_total": (
        "Express the sale price as (1 - discount_rate) times the original price.",
        "Divide the sale price by (1 - discount_rate) to recover the original price.",
    ),
    "line_segment_sys": (
        "Set up the system of linear equations representing the adjacent segments on the line.",
        "Solve the system to find the length of each individual subsegment and sum to find the target length.",
    ),
    "two_type_sys": (
        "Assume all items are of the type with fewer parts (e.g. 2 legs) and find the excess parts.",
        "Divide the excess by the difference in parts per item to find the count of the second type.",
    ),
    "digit_perms": (
        "Choose the first digit from the non-zero allowed digits (k - 1 choices if 0 is present, else k).",
        "Multiply by the remaining choices for each subsequent position without repetition.",
    ),
}


def _build_worked_oracle_steps(
    statement: str,
    oracle: dict[str, Any],
    language: str = "eng",
) -> list[str]:
    """Generate actual problem-specific worked algebra/steps for oracle-solved problems."""
    lang = normalize_language(language)
    kind = oracle.get("kind") or ""
    solutions = oracle.get("solutions") or []
    sol_str = ", ".join(solutions)

    if kind == "linear":
        eq_m = re.search(r"([0-9a-zA-Z_+\-*/^().%\s]+=[0-9a-zA-Z_+\-*/^().%\s]+)", statement)
        eq_txt = eq_m.group(1).strip() if eq_m else statement.strip()
        if lang == "cat":
            return [
                f"Pas 1 (Equació lineal): Partim de `{eq_txt}`.",
                f"Pas 2 (Aïllar la incògnita): Agrupem els termes constants a un costat i dividim pel coeficient de la variable per obtenir {sol_str}.",
            ]
        return [
            f"Step 1 (Linear equation): Start with `{eq_txt}`.",
            f"Step 2 (Isolate variable): Move constant terms to the opposite side and divide by the variable's coefficient to obtain {sol_str}.",
        ]

    if kind == "quadratic":
        eq_m = re.search(r"([0-9a-zA-Z_+\-*/^().%\s]+=[0-9a-zA-Z_+\-*/^().%\s]+)", statement)
        eq_txt = eq_m.group(1).strip() if eq_m else statement.strip()
        if lang == "cat":
            return [
                f"Pas 1 (Equació quadràtica): Escrivim `{eq_txt}` en forma estàndard ax^2 + bx + c = 0.",
                f"Pas 2 (Factorització / fórmula quadràtica): Les arrels reals que satisfan l'equació són {sol_str}.",
            ]
        return [
            f"Step 1 (Quadratic equation): Write `{eq_txt}` in standard form ax^2 + bx + c = 0.",
            f"Step 2 (Factor / quadratic formula): Solving gives the exact roots {sol_str}.",
        ]

    if kind == "derivative":
        if lang == "cat":
            return [
                f"Pas 1 (Derivada): Apliquem la regla de la potència d/dx(x^n) = n*x^(n-1) a cada terme de `{statement.strip()}`.",
                f"Pas 2 (Simplificació): El resultat simplificat és {sol_str}.",
            ]
        return [
            f"Step 1 (Derivative): Apply the power rule d/dx(x^n) = n*x^(n-1) term-by-term to `{statement.strip()}`.",
            f"Step 2 (Simplify): Combining terms yields {sol_str}.",
        ]

    if kind == "arithmetic":
        if lang == "cat":
            return [
                f"Pas 1 (Aritmètica): Avaluem `{statement.strip()}` respectant la jerarquia d'operacions.",
                f"Pas 2 (Resultat): El valor exacte és {sol_str}.",
            ]
        return [
            f"Step 1 (Arithmetic): Evaluate `{statement.strip()}` following order of operations.",
            f"Step 2 (Compute): The exact value is {sol_str}.",
        ]

    if kind == "two_var_system":
        if lang == "cat":
            return [
                f"Pas 1 (Sistema de dues variables): Aïllem una variable de la primera equació i substituïm a la segona (`{statement.strip()}`).",
                f"Pas 2 (Resolució): Els valors que satisfan ambdues equacions són {sol_str}.",
            ]
        return [
            f"Step 1 (Two-variable system): Express one variable from the first equation and substitute into the second (`{statement.strip()}`).",
            f"Step 2 (Solve): The values satisfying both equations are {sol_str}.",
        ]

    if kind == "pigeonhole_same_color":
        if lang == "cat":
            return [
                "Pas 1 (Principi del colomar): En el pitjor cas, traiem com a màxim (k - 1) boles de cada color sense completar cap grup de k.",
                f"Pas 2 (Garantia): Amb 1 bola addicional assegurem tenir k del mateix color, donant {sol_str}.",
            ]
        return [
            "Step 1 (Pigeonhole principle): In the worst case, draw at most (k - 1) items from each color group without reaching k of any color.",
            f"Step 2 (Guarantee): Drawing 1 more item guarantees a match of k, giving {sol_str}.",
        ]

    if kind == "fraction_offset_pair":
        if lang == "cat":
            return [
                "Pas 1 (Relació fraccionària): Multipliquem el valor donat pel denominador de la fracció per trobar la primera quantitat.",
                f"Pas 2 (Diferència): Sumem o restem la diferència indicada per obtenir la quantitat demanada: {sol_str}.",
            ]
        return [
            "Step 1 (Fraction relation): Multiply the given part by the fraction denominator to find the base group size.",
            f"Step 2 (Offset): Apply the stated difference to find the target count: {sol_str}.",
        ]

    s1, s2 = _ORACLE_STEP_GUIDES.get(
        kind,
        ("Translate the problem conditions into equations.", "Evaluate the relation step by step."),
    )
    return [f"Step 1: {s1}", f"Step 2: {s2} -> {sol_str}"]


def _build_deterministic_solution(
    problem: dict[str, Any],
    oracle: dict[str, Any],
    rag_examples: list[dict[str, Any]],
    language: str = "eng",
) -> tuple[str, Optional[str], Optional[str]]:
    lang = normalize_language(language)
    correct_opt = problem.get("correct_option")
    options = problem.get("options") or {}
    if correct_opt:
        opt_val = options.get(correct_opt)
        final_ans = f"{correct_opt} ({opt_val})" if opt_val is not None else str(correct_opt)
        steps = [
            f"Step 1 ({problem['category']}): {problem['intuition_hook']}",
            f"Step 2: {problem.get('socratic_seed') or problem.get('default_lcd_summary', 'Apply the key relation step by step.')}",
            f"Final Answer: {final_ans}",
        ]
        return "\n".join(steps), final_ans, str(correct_opt)

    solutions = oracle.get("solutions") or []
    if solutions:
        sol_str = ", ".join(solutions)
        matched_opt = _match_oracle_to_option(oracle, options)
        final_ans = f"{matched_opt} ({sol_str})" if matched_opt else sol_str
        worked = _build_worked_oracle_steps(problem.get("statement", ""), oracle, language=lang)
        worked.append(f"Final Answer: {final_ans}")
        return "\n".join(worked), final_ans, matched_opt

    # For unsolved free-math / word problems, do NOT return canned _ORACLE_STEP_GUIDES stubs;
    # return a concise localized prompt-grounded analysis so SLM/PoT can take over when available.
    cat = str(problem.get("category") or "Algebra")
    if lang == "cat":
        steps = [
            f"Pas 1 ({cat}): Identifiquem les quantitats desconegudes i les condicions de l'enunciat.",
            "Pas 2: Escrivim les equacions o relacions pas a pas i comprovem el resultat.",
        ]
    else:
        steps = [
            f"Step 1 ({cat}): Identify the unknown quantities and translate each condition into a clear relation.",
            "Step 2: Solve the resulting relation step by step and verify against the problem statement.",
        ]
    if rag_examples:
        ex = rag_examples[0]
        if ex.get("intuition_hook"):
            steps.append(f"Related ({ex.get('category', cat)}): {ex['intuition_hook']}")
    return "\n".join(steps), None, None


def _generate_turn_internal(
    problem_id: str,
    event_type: EngineEventType,
    attempt_num: int,
    confidence_pct: int,
    selected_option: Optional[str] = None,
    student_note: Optional[str] = None,
    agent_override_nudge: Optional[str] = None,
    mode: Literal["teach", "solve"] = "teach",
    language: Literal["eng", "cat"] = "eng",
    history: Optional[list[dict[str, str]]] = None,
) -> tuple[SocraticResponse, bool, Optional[str], dict[str, Any]]:
    t0 = time.perf_counter()
    _LAST_LLM_STATS["prompt_n"] = 0
    _LAST_LLM_STATS["tok_s"] = 0.0
    lang = normalize_language(language)
    confidence_pct = max(0, min(100, int(confidence_pct)))
    attempt_num = max(1, int(attempt_num))
    norm_opt = (selected_option or "").strip().upper() or None

    raw_bank_prob = get_problem(problem_id)
    guide_query = student_note
    if raw_bank_prob is None and history:
        has_math_now = bool(student_note and re.search(r"(?:\d|[=+\-*/^]|triangle|angle|polygon|circle|probabilit|equaci)", student_note, re.I))
        if not has_math_now:
            for prev in reversed(history):
                if isinstance(prev, dict) and prev.get("role") == "user" and str(prev.get("content") or "").strip():
                    guide_query = str(prev["content"]).strip()
                    break
    problem = localize_problem(raw_bank_prob, lang) if raw_bank_prob is not None else match_concept_guide(problem_id, guide_query)
    correct_opt = problem.get("correct_option")

    is_correct: Optional[bool] = None
    misconception_tag = "none"
    if problem.get("is_open_prompt"):
        misconception_tag = "open_concept_probe"
    elif event_type == "ANSWER_SUBMIT":
        if norm_opt not in {"A", "B", "C", "D", "E"}:
            misconception_tag = "unselected_option"
        elif correct_opt is not None:
            is_correct = norm_opt == correct_opt
            if not is_correct:
                misconception_tag = problem.get("option_misconceptions", {}).get(
                    norm_opt, f"wrong_option_{problem['category'].lower()}"
                )

    # Fast 0ms short-circuit for greetings or unclear/gibberish inputs in open chat (no SLM call)
    if problem.get("is_open_prompt") or problem.get("problem_id") == "GENERAL-MATH":
        short_kind = _classify_short_or_greeting(student_note, history)
        if short_kind is not None:
            if short_kind == "greeting":
                msg = (
                    "Hola! Escriu o enganxa un problema de mates (o tria un problema Cangur) i explica'm què has provat per treballar-hi junts?"
                    if lang == "cat"
                    else "Hi! Paste or type a math problem (or select a Cangur problem) and tell me what you've tried so we can work through it together?"
                )
                lcd_msg = "Hola! Tria o escriu un repte" if lang == "cat" else "Hi! Share a math problem"
            else:
                msg = (
                    "Pots escriure l'enunciat complet del problema o l'equació amb què vols ajuda, i algun pas que hagis provat?"
                    if lang == "cat"
                    else "Could you share the full math problem or equation you'd like help with, plus any step you've tried so far?"
                )
                lcd_msg = "Escriu el problema complet" if lang == "cat" else "Share full math problem"
            response: SocraticResponse = {
                "problem_id": problem["problem_id"],
                "category": problem["category"],
                "difficulty_level": int(problem["difficulty_level"]),
                "hint_text": clamp_text(msg, 240, append_question_mark=True),
                "lcd_summary": clamp_text(lcd_msg, 32),
                "pedagogical_move": "DECOMPOSE_PROBLEM",
                "is_correct": is_correct,
                "misconception_tag": "greeting" if short_kind == "greeting" else "unclear_input",
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "model_id": os.environ.get("SLM_MODEL_ID", DEFAULT_MODEL_ID),
                "offline_mode": True,
            }
            validate_socratic_response(response)
            return response, False, None, {
                "mode": mode,
                "rag_example_id": None,
                "solution_text": msg if mode == "solve" else None,
                "final_answer": None,
                "prompt_n": 0,
                "tok_s": 0.0,
            }

    oracle = extract_math_oracle(problem.get("statement", ""))

    # Layer 1: Pre-SLM Socratic Judo guardrail (blocks jailbreaks & unsafe input in BOTH modes).
    guardrail = inspect_student_input(student_note, problem, mode=mode)
    if guardrail is not None:
        move: PedagogicalMove = guardrail["pedagogical_move"]
        hint_text = guardrail["hint_text"]
        if not is_safe_hint(hint_text, problem, is_correct, oracle=oracle):
            hint_text = clamp_text(
                f"What is the first concrete condition in this {problem['category']} problem you can check?",
                240,
            )
        response: SocraticResponse = {
            "problem_id": problem["problem_id"],
            "category": problem["category"],
            "difficulty_level": int(problem["difficulty_level"]),
            "hint_text": hint_text,
            "lcd_summary": clamp_text(guardrail["lcd_summary"], 32),
            "pedagogical_move": move,
            "is_correct": is_correct,
            "misconception_tag": guardrail["misconception_tag"],
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "model_id": "guardrail-socratic-judo",
            "offline_mode": True,
        }
        validate_socratic_response(response)
        return response, True, guardrail.get("guardrail_reason"), {
            "mode": mode,
            "rag_example_id": None,
            "solution_text": None,
            "final_answer": None,
        }

    is_chat = bool(problem.get("is_open_prompt") or problem.get("problem_id") == "GENERAL-MATH")
    if mode == "solve" and oracle.get("solutions"):
        rag_examples = []
    else:
        rag_examples = retrieve_similar_problems(
            problem.get("statement", ""),
            exclude_id=problem.get("problem_id"),
            top_k=1,
            chat_mode=is_chat,
        )
        if problem.get("is_open_prompt"):
            rag_examples = [ex for ex in rag_examples if ex.get("category") == problem.get("category")]
    rag_example_id = rag_examples[0]["problem_id"] if rag_examples else None
    rag_hook = rag_examples[0]["intuition_hook"] if rag_examples else None

    use_mock = os.environ.get("SOCRATIC_MOCK", "0") == "1"
    model_id = os.environ.get("SLM_MODEL_ID", DEFAULT_MODEL_ID)

    if mode == "solve":
        det_solution, det_final, _ = _build_deterministic_solution(problem, oracle, rag_examples, language=lang)
        solution_text = det_solution
        final_answer = det_final
        used_slm = False

        if det_final is not None:
            # Deterministic fast path (<5ms, zero LLM overhead)
            model_id = "mock-solve-v1" if use_mock else f"{model_id}-oracle"
        elif not use_mock:
            solve_msgs = build_solve_messages(
                problem,
                student_note=student_note,
                rag_examples=rag_examples,
                history=history,
                language=lang,
            )
            solve_t0 = time.perf_counter()
            slm_sol = _call_llama_server(
                solve_msgs,
                model_id,
                max_tokens=160,
                extract_question=False,
                stop=["Student:", "\n\n\n"],
                timeout_sec=32.0,
            )
            solve_elapsed = time.perf_counter() - solve_t0
            if slm_sol:
                solution_text = slm_sol
                used_slm = True
                final_answer, _ = _extract_final_answer_from_text(slm_sol, problem.get("options"))

            # Only try PoT if CoT returned fast (<18s) without a clean Final Answer
            if final_answer is None and slm_sol is not None and solve_elapsed < 18.0:
                pot_msgs = build_pot_messages(problem.get("statement", ""))
                pot_raw = _call_llama_server(
                    pot_msgs,
                    model_id,
                    max_tokens=160,
                    extract_question=False,
                    stop=["Student:"],
                    timeout_sec=25.0,
                )
                if pot_raw:
                    pot_ok, pot_val, _ = _run_pot_sandbox(pot_raw, timeout_s=1.0)
                    if pot_ok and pot_val is not None:
                        pot_code = _extract_pot_code(pot_raw)
                        final_answer = str(pot_val)
                        solution_text = (
                            f"Step 1 (Python PoT):\n```python\n{pot_code}\n```\nFinal Answer: {final_answer}"
                        )
                        used_slm = True

        if not used_slm and det_final is None:
            model_id = "mock-solve-v1" if use_mock else f"{model_id}-fallback"

        move = "AFFIRM_CORRECT" if final_answer else "DECOMPOSE_PROBLEM"
        solution_text = _clean_math_output(solution_text)
        summary_src = f"Final Answer: {final_answer}. {problem['intuition_hook']}" if final_answer else solution_text
        lcd_src = f"Ans: {final_answer}" if final_answer else problem.get("default_lcd_summary", "Step-by-step solve")
        response = {
            "problem_id": problem["problem_id"],
            "category": problem["category"],
            "difficulty_level": int(problem["difficulty_level"]),
            "hint_text": _clean_math_output(clamp_text(summary_src, 240)),
            "lcd_summary": clamp_text(lcd_src, 32),
            "pedagogical_move": move,
            "is_correct": is_correct,
            "misconception_tag": misconception_tag,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "model_id": model_id,
            "offline_mode": True,
        }
        validate_socratic_response(response)
        return response, False, None, {
            "mode": "solve",
            "rag_example_id": rag_example_id,
            "solution_text": solution_text,
            "final_answer": final_answer,
            "prompt_n": _LAST_LLM_STATS["prompt_n"],
            "tok_s": _LAST_LLM_STATS["tok_s"],
        }

    safe_nudge = (
        clamp_text(agent_override_nudge, 240, append_question_mark=True)
        if agent_override_nudge and is_safe_hint(agent_override_nudge, problem, is_correct, oracle=oracle)
        else None
    )

    move = _route_pedagogical_move(
        problem, event_type, attempt_num, confidence_pct, is_correct, safe_nudge
    )

    # Short-circuit instant events (dial rotation without a student note, solved affirmation,
    # explicit RawTree intervention), and deterministic oracle/conceptual teach paths (<5ms, zero :8081 stall).
    has_note = bool(student_note and student_note.strip())
    has_det_teach = bool(
        oracle.get("kind")
        or (has_note and _is_conceptual_question(student_note) and _conceptual_fallback(student_note or "", lang))
    )
    skip_slm = (
        use_mock
        or is_correct is True
        or bool(safe_nudge)
        or has_det_teach
        or (event_type == "PROBLEM_SELECT" and not has_note)
        or (event_type == "ANSWER_SUBMIT" and is_correct is None and not problem.get("is_open_prompt") and not has_note)
    )

    hint_text: Optional[str] = None
    used_slm = False
    offline_mode = True
    if not skip_slm:
        # Feed deterministic oracle Step 1 concept/clue (with final answer redacted) into compact user prompt
        oracle_clue = None
        if oracle.get("kind"):
            s1_guide = _ORACLE_STEP_GUIDES.get(oracle["kind"], (problem["intuition_hook"], ""))[0]
            oracle_clue = s1_guide
            for sol_item in oracle.get("solutions", []):
                if sol_item:
                    oracle_clue = re.sub(rf"\b{re.escape(str(sol_item))}\b", "[value]", oracle_clue)

        messages = build_messages(
            problem=problem,
            event_type=event_type,
            attempt_num=attempt_num,
            confidence_pct=confidence_pct,
            pedagogical_move=move,
            is_correct=is_correct,
            selected_option=norm_opt,
            student_note=student_note,
            agent_override_nudge=safe_nudge,
            rag_hook=rag_hook,
            oracle_clue=oracle_clue,
            history=history,
            language=lang,
        )
        teach_max_tokens = 80 if _is_conceptual_question(student_note) else 48
        candidate = _call_llama_server(
            messages,
            model_id,
            max_tokens=teach_max_tokens,
            extract_question=True,
            stop=["\n\n", "Student:"],
            timeout_sec=32.0,
            student_note=student_note,
        )
        if candidate and is_safe_hint(candidate, problem, is_correct, oracle=oracle):
            hint_text = clamp_text(candidate, 240, append_question_mark=True)
            used_slm = True
        else:
            hc_text, hc_model = _call_helmcode_api(messages, problem, is_correct, oracle)
            if hc_text and hc_model:
                hint_text = hc_text
                model_id = f"helmcode:{hc_model}"
                used_slm = True
                offline_mode = False

    if hint_text is None:
        hint_text = _fallback_hint(
            problem,
            event_type,
            attempt_num,
            confidence_pct,
            is_correct,
            norm_opt,
            safe_nudge,
            oracle=oracle,
            student_note=student_note,
            language=lang,
        )
        if not used_slm:
            if use_mock:
                model_id = "mock-socratic-v1"
            elif has_det_teach:
                model_id = f"{model_id}-oracle"
            else:
                model_id = f"{model_id}-fallback"

    hint_text = _clean_math_output(hint_text)
    lcd_raw = (
        safe_nudge
        if safe_nudge and not is_correct
        else (problem.get("default_lcd_summary") if move == problem.get("default_move") else MOVE_LCD_LABELS[move])
    )
    lcd_summary = clamp_text(lcd_raw or MOVE_LCD_LABELS[move], 32)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    response = {
        "problem_id": problem["problem_id"],
        "category": problem["category"],
        "difficulty_level": int(problem["difficulty_level"]),
        "hint_text": hint_text,
        "lcd_summary": lcd_summary,
        "pedagogical_move": move,
        "is_correct": is_correct,
        "misconception_tag": misconception_tag,
        "latency_ms": latency_ms,
        "model_id": model_id,
        "offline_mode": offline_mode,
    }
    validate_socratic_response(response)
    return response, False, None, {
        "mode": "teach",
        "rag_example_id": rag_example_id,
        "solution_text": None,
        "final_answer": None,
        "prompt_n": _LAST_LLM_STATS["prompt_n"],
        "tok_s": _LAST_LLM_STATS["tok_s"],
    }


def generate_socratic_hint(
    problem_id: str,
    event_type: EngineEventType,
    attempt_num: int,
    confidence_pct: int,
    selected_option: Optional[str] = None,
    student_note: Optional[str] = None,
    agent_override_nudge: Optional[str] = None,
) -> SocraticResponse:
    """Generate a contract-compliant Socratic hint (offline-first, deterministic fallback)."""
    response, _, _, _ = _generate_turn_internal(
        problem_id=problem_id,
        event_type=event_type,
        attempt_num=attempt_num,
        confidence_pct=confidence_pct,
        selected_option=selected_option,
        student_note=student_note,
        agent_override_nudge=agent_override_nudge,
        mode="teach",
    )
    return response


def generate_socratic_turn(
    problem_id: str,
    event_type: EngineEventType,
    attempt_num: int,
    confidence_pct: int,
    selected_option: Optional[str] = None,
    student_note: Optional[str] = None,
    agent_override_nudge: Optional[str] = None,
    mode: Literal["teach", "solve"] = "teach",
    language: Literal["eng", "cat"] = "eng",
    history: Optional[list[dict[str, str]]] = None,
) -> dict[str, Any]:
    """Extended turn helper for Web UI supporting both 'teach' (Socratic) and 'solve' (Reasoning) modes."""
    response, guardrail_triggered, guardrail_reason, extra = _generate_turn_internal(
        problem_id=problem_id,
        event_type=event_type,
        attempt_num=attempt_num,
        confidence_pct=confidence_pct,
        selected_option=selected_option,
        student_note=student_note,
        agent_override_nudge=agent_override_nudge,
        mode=mode,
        language=language,
        history=history,
    )
    return {
        **response,
        "socratic_dimension": SOCRATIC_DIMENSIONS[response["pedagogical_move"]],
        "guardrail_triggered": guardrail_triggered,
        "guardrail_reason": guardrail_reason,
        **extra,
    }


def _qnum_band(qnum: int) -> str:
    if qnum <= 10:
        return "1-10"
    if qnum <= 20:
        return "11-20"
    return "21-30"


def parse_level_qnum(
    problem_id: str,
    level: Optional[int] = None,
    qnum: Optional[int] = None,
) -> tuple[int, int]:
    """Parse (difficulty_level, question_number) from Cangur/M3Kang problem IDs."""
    parsed_lvl = level
    if parsed_lvl is None:
        lvl_m = re.search(r"(?:^|[-_])N(\d+)(?:[-_]|$)", problem_id, flags=re.I)
        if lvl_m:
            n_val = int(lvl_m.group(1))
            parsed_lvl = max(1, min(3, n_val - 2)) if n_val >= 3 else max(1, n_val)
        else:
            m3k_m = re.search(r"(?:^|[-_])lvl[-_]?(\d+)(?:[-_]|$)|^M3K-\d{4}-(\d+)-", problem_id, flags=re.I)
            if m3k_m:
                raw_l = int(m3k_m.group(1) or m3k_m.group(2))
                parsed_lvl = max(1, min(3, 1 + raw_l // 3))
            else:
                parsed_lvl = 2

    parsed_qnum = qnum
    if parsed_qnum is None:
        q_m = re.search(r"[-_](\d{1,2})$", problem_id.strip())
        parsed_qnum = int(q_m.group(1)) if q_m else 15

    return max(1, int(parsed_lvl)), max(1, int(parsed_qnum))


def _normalize_dist(counts: dict[str, float], alpha: float = 0.5) -> dict[str, float]:
    tie_break = {"A": 0.0, "B": 0.002, "C": 0.003, "D": 0.001, "E": 0.0}
    raw = {k: max(0.0, float(counts.get(k, 0.0))) + alpha + tie_break[k] for k in MCQ_LETTERS}
    total = sum(raw.values())
    return {k: round(raw[k] / total, 6) for k in MCQ_LETTERS}


def _normalize_row_level(row: dict[str, Any]) -> Optional[int]:
    if row.get("difficulty_level") is not None:
        return max(1, min(3, int(row["difficulty_level"])))
    if row.get("level") is not None:
        return max(1, min(3, 1 + int(row["level"]) // 3))
    return None


def _extract_row_qnum(row: dict[str, Any]) -> Optional[int]:
    if row.get("qnum") is not None:
        return int(row["qnum"])
    pid = str(row.get("problem_id") or row.get("id") or "").strip()
    q_m = re.search(r"[-_](\d{1,2})$", pid)
    if q_m:
        return int(q_m.group(1))
    txt_m = re.match(r"^\s*(\d{1,2})\.\s+", str(row.get("text") or row.get("statement") or ""))
    if txt_m:
        return int(txt_m.group(1))
    return None


def compute_level_qnum_priors(
    rows: Optional[Iterable[dict[str, Any]]] = None,
    alpha: float = 50.0,
) -> dict[Any, dict[str, float]]:
    """Compute smoothed A-E letter priors separating has_options=False from has_options=True (level, qnum_band)."""
    source_rows = list(rows) if rows is not None else list(_load_bank()["problems"].values())
    band_counts: dict[tuple[int, str], dict[str, float]] = {}
    lvl_counts: dict[tuple[int, str], dict[str, float]] = {}
    no_opts_counts: dict[str, float] = {k: 0.0 for k in MCQ_LETTERS}
    global_counts: dict[str, float] = {k: 0.0 for k in MCQ_LETTERS}

    for row in source_rows:
        ans = str(row.get("correct_option") or row.get("label") or "").strip().upper()
        if ans not in MCQ_LETTERS:
            continue
        global_counts[ans] += 1.0
        opts = row.get("options")
        has_opts = bool(isinstance(opts, dict) and any(k in MCQ_LETTERS and str(v).strip() for k, v in opts.items()))
        if not has_opts:
            no_opts_counts[ans] += 1.0
            continue
        pid = str(row.get("problem_id") or row.get("id") or "")
        explicit_lvl = _normalize_row_level(row)
        lvl, qnum = parse_level_qnum(
            pid,
            level=explicit_lvl,
            qnum=_extract_row_qnum(row),
        )
        band = _qnum_band(qnum)
        band_counts.setdefault((lvl, band), {k: 0.0 for k in MCQ_LETTERS})[ans] += 1.0
        lvl_counts.setdefault((lvl, "_all"), {k: 0.0 for k in MCQ_LETTERS})[ans] += 1.0

    priors: dict[Any, dict[str, float]] = {
        key: _normalize_dist(cnt, alpha=alpha) for key, cnt in band_counts.items()
    }
    for key, cnt in lvl_counts.items():
        priors[key] = _normalize_dist(cnt, alpha=alpha)
    if sum(no_opts_counts.values()) > 0:
        priors[("_no_opts", "_global")] = _normalize_dist(no_opts_counts, alpha=min(alpha, 5.0))
    priors["_global"] = _normalize_dist(global_counts, alpha=alpha)
    return priors


_PRIORS_FILE_CACHE: dict[tuple[str, int, float], dict[Any, dict[str, float]]] = {}


def fit_priors_from_jsonl(
    path: str | os.PathLike[str],
    alpha: float = 50.0,
) -> dict[Any, dict[str, float]]:
    """Load (level, qnum) letter priors from a training JSONL file, falling back to bank priors."""
    p = Path(path)
    if not p.is_file():
        return compute_level_qnum_priors(alpha=alpha)
    cache_key = (str(p.resolve()), int(p.stat().st_mtime_ns), float(alpha))
    cached = _PRIORS_FILE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    priors = compute_level_qnum_priors(rows if rows else None, alpha=alpha)
    _PRIORS_FILE_CACHE[cache_key] = priors
    return priors


def get_level_qnum_prior(
    problem_id: str,
    level: Optional[int] = None,
    qnum: Optional[int] = None,
    priors: Optional[dict[Any, dict[str, float]]] = None,
    has_options: bool = True,
) -> dict[str, float]:
    """Return normalized (level, qnum) prior probabilities over A-E (never blind D)."""
    table = priors if priors is not None else compute_level_qnum_priors()
    if not has_options and ("_no_opts", "_global") in table:
        dist = table[("_no_opts", "_global")]
    else:
        lvl, qn = parse_level_qnum(problem_id, level=level, qnum=qnum)
        band = _qnum_band(qn)
        dist = table.get((lvl, band)) or table.get((lvl, "_all")) or table.get("_global")
    if not dist:
        return _normalize_dist({}, alpha=1.0)
    return dict(dist)


def softmax_scores(scores: dict[str, float], keys: tuple[str, ...] = MCQ_LETTERS) -> dict[str, float]:
    """Numerically stable pure-stdlib softmax over a dict of letter scores."""
    if not keys:
        return {}
    vals = [float(scores.get(k, -20.0)) for k in keys]
    max_v = max(vals)
    exps = [math.exp(max(-50.0, min(50.0, v - max_v))) for v in vals]
    total = sum(exps) or 1.0
    return {k: round(e / total, 6) for k, e in zip(keys, exps)}


def extract_letter_logprobs(completion_payload: dict[str, Any]) -> dict[str, float]:
    """Extract A-E token logprobs from OpenAI / llama-server chat completion payloads."""
    out: dict[str, float] = {}
    try:
        choice = completion_payload["choices"][0]
        lp_root = choice.get("logprobs") or {}
        content = lp_root.get("content")
        if isinstance(content, list) and content:
            top_lp = content[0].get("top_logprobs") or []
        else:
            top_lp = lp_root.get("top_logprobs") or []

        items: list[tuple[str, float]] = []
        if isinstance(top_lp, dict):
            items = [(str(tok), float(val)) for tok, val in top_lp.items()]
        elif isinstance(top_lp, list):
            for entry in top_lp:
                if isinstance(entry, dict) and "token" in entry and "logprob" in entry:
                    items.append((str(entry["token"]), float(entry["logprob"])))

        for tok, val in items:
            clean = tok.strip().upper()
            if clean in MCQ_LETTERS:
                if clean not in out or val > out[clean]:
                    out[clean] = val
    except (KeyError, IndexError, TypeError, ValueError):
        return {}
    return out


def calibrate_logprobs(
    raw_logprobs: dict[str, float],
    baseline_logprobs: Optional[dict[str, float]] = None,
    missing_floor: float = -20.0,
) -> dict[str, float]:
    """Subtract content-free baseline logprob bias and return calibrated A-E probabilities."""
    if not raw_logprobs:
        return {k: 0.2 for k in MCQ_LETTERS}
    baseline = baseline_logprobs if baseline_logprobs is not None else DEFAULT_BASELINE_LOGPROBS
    present = [k for k in MCQ_LETTERS if k in raw_logprobs]
    if not present:
        return {k: 0.2 for k in MCQ_LETTERS}

    min_present = min(float(raw_logprobs[k]) for k in present)
    n_missing = len(MCQ_LETTERS) - len(present)
    if n_missing > 0:
        p_sum = sum(math.exp(min(0.0, float(raw_logprobs[k]))) for k in present)
        rem_mass = max(1e-9, 1.0 - min(0.999999, p_sum))
        rem_lp = math.log(max(1e-9, rem_mass / n_missing))
        eff_floor = max(float(missing_floor), min(min_present - 1.5, rem_lp))
    else:
        eff_floor = float(missing_floor)

    adjusted_present = {
        k: float(raw_logprobs[k]) - float(baseline.get(k, 0.0))
        for k in present
    }
    min_adj_present = min(adjusted_present.values())
    adjusted: dict[str, float] = {}
    for k in MCQ_LETTERS:
        if k in adjusted_present:
            adjusted[k] = adjusted_present[k]
        else:
            # Missing letters from top_logprobs must never outscore observed letters after baseline subtraction.
            adjusted[k] = min(eff_floor - float(baseline.get(k, 0.0)), min_adj_present - 0.5)
    return softmax_scores(adjusted)


def debias_cyclic_permutations(
    permuted_runs: list[tuple[dict[str, str], dict[str, float]]],
    baseline_logprobs: Optional[dict[str, float]] = None,
) -> dict[str, float]:
    """Average de-shifted probabilities by option value across cyclic option permutations.

    Never falls back to DEFAULT_BASELINE_LOGPROBS ("N/A" prompt logprobs); only subtracts
    baseline_logprobs when explicitly passed non-None.
    """
    if not permuted_runs:
        return {}
    eff_baseline: dict[str, float] = (
        baseline_logprobs if baseline_logprobs is not None else {k: 0.0 for k in MCQ_LETTERS}
    )
    value_totals: dict[str, float] = {}
    for letter_to_val, raw_lp in permuted_runs:
        probs = calibrate_logprobs(raw_lp, baseline_logprobs=eff_baseline)
        for letter, val in letter_to_val.items():
            norm_val = str(val).strip()
            value_totals[norm_val] = value_totals.get(norm_val, 0.0) + probs.get(letter, 0.0)
    total = sum(value_totals.values()) or 1.0
    return {val: round(score / total, 6) for val, score in value_totals.items()}


_CATALAN_MATH_GLOSSARY: tuple[tuple[str, str], ...] = (
    ("cap d'aquestes", "none of these"),
    ("cap de les anteriors", "none of these"),
    ("cap anterior", "none of these"),
    ("més gran", "greater"),
    ("mes gran", "greater"),
    ("més petit", "smaller"),
    ("mes petit", "smaller"),
    ("igual a", "equal to"),
    ("divisible per", "divisible by"),
    ("quantes vegades", "how many times"),
    ("quant val", "what is the value of"),
    ("quin és el valor", "what is the value"),
    ("quin es el valor", "what is the value"),
    ("en total", "in total"),
    ("dues xifres", "two digits"),
    ("tres xifres", "three digits"),
    ("quatre xifres", "four digits"),
    ("una xifra", "one digit"),
    ("d'un quadrat", "of a square"),
    ("d'un triangle", "of a triangle"),
    ("d'un rectangle", "of a rectangle"),
    ("d'un cercle", "of a circle"),
    ("l'àrea", "the area"),
    ("l'area", "the area"),
    ("l'angle", "the angle"),
    ("el perímetre", "the perimeter"),
    ("el perimetre", "the perimeter"),
    ("quants", "how many"),
    ("quantes", "how many"),
    ("suma", "sum"),
    ("quadrats", "squares"),
    ("quadrat", "square"),
    ("costats", "sides"),
    ("costat", "side"),
    ("xifres", "digits"),
    ("xifra", "digit"),
    ("parells", "even"),
    ("parell", "even"),
    ("senars", "odd"),
    ("senar", "odd"),
    ("queden", "left"),
    ("queda", "left"),
    ("rectangles", "rectangles"),
    ("rectangle", "rectangle"),
    ("triangles", "triangles"),
    ("triangle", "triangle"),
    ("perímetre", "perimeter"),
    ("perimetre", "perimeter"),
    ("àrea", "area"),
    ("area", "area"),
    ("producte", "product"),
    ("diferència", "difference"),
    ("diferencia", "difference"),
    ("meitat", "half"),
    ("doble", "double"),
    ("triple", "triple"),
    ("nombres", "numbers"),
    ("nombre", "number"),
    ("igual", "equal"),
    ("màxim", "maximum"),
    ("maxim", "maximum"),
    ("mínim", "minimum"),
    ("minim", "minimum"),
    ("mitjana", "average"),
    ("residu", "remainder"),
    ("múltiple", "multiple"),
    ("angles", "angles"),
    ("angle", "angle"),
    ("cercle", "circle"),
    ("radi", "radius"),
    ("diàmetre", "diameter"),
    ("diametre", "diameter"),
    ("cub", "cube"),
    ("arestes", "edges"),
    ("vèrtexs", "vertices"),
    ("vertexs", "vertices"),
    ("fracció", "fraction"),
    ("fraccio", "fraction"),
    ("percentatge", "percentage"),
    ("cadascun", "each"),
    ("cadascuna", "each"),
    ("cada", "each"),
    ("tauler", "board"),
    ("caselles", "squares"),
    ("casella", "square"),
    ("maneres", "ways"),
    ("manera", "way"),
    ("menys", "minus"),
    ("més", "plus"),
    ("impossible", "impossible"),
    ("bossa", "bag"),
    ("bosses", "bags"),
    ("boles", "balls"),
    ("bola", "ball"),
    ("doblegar", "fold"),
    ("doblega", "folds"),
    ("tira", "strip"),
    ("paper", "paper"),
    ("parelles", "pairs"),
    ("parella", "pair"),
    ("classe", "class"),
    ("alumnes", "students"),
    ("alumne", "student"),
    ("setmanes", "weeks"),
    ("setmana", "week"),
    ("mesos", "months"),
    ("mes", "month"),
    ("germanes", "sisters"),
    ("germana", "sister"),
    ("germans", "brothers"),
    ("germà", "brother"),
    ("edats", "ages"),
    ("edat", "age"),
    ("cases", "houses"),
    ("casa", "house"),
    ("carrer", "street"),
    ("ciutat", "city"),
    ("mur", "wall"),
    ("cantonada", "corner"),
    ("dreta", "right"),
    ("esquerra", "left"),
    ("files", "rows"),
    ("fila", "row"),
    ("columnes", "columns"),
    ("columna", "column"),
    ("cangur", "kangaroo"),
    ("monedes", "coins"),
    ("moneda", "coin"),
    ("dies", "days"),
    ("dia", "day"),
    ("hores", "hours"),
    ("hora", "hour"),
    ("minuts", "minutes"),
    ("minut", "minute"),
    ("anys", "years"),
    ("any", "year"),
    ("punts", "points"),
    ("punt", "point"),
    ("línia", "line"),
    ("linia", "line"),
    ("recta", "line"),
    ("segment", "segment"),
    ("pentàgon", "pentagon"),
    ("pentagon", "pentagon"),
    ("hexàgon", "hexagon"),
    ("hexagon", "hexagon"),
    ("polígon", "polygon"),
    ("poligon", "polygon"),
    ("cubs", "cubes"),
    ("pintar", "paint"),
    ("pintat", "painted"),
    ("ombrejada", "shaded"),
    ("ombrejat", "shaded"),
    ("blanc", "white"),
    ("negre", "black"),
    ("vermell", "red"),
    ("blau", "blue"),
    ("verd", "green"),
    ("groc", "yellow"),
)
_CATALAN_MATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(rf"(?<![A-Za-zÀ-ÿ0-9_]){re.escape(src)}(?![A-Za-zÀ-ÿ0-9_])", flags=re.IGNORECASE),
        dst,
    )
    for src, dst in _CATALAN_MATH_GLOSSARY
)


def normalize_catalan_math_keywords(text: str, language: Optional[str] = "cat") -> str:
    """Offline Catalan -> English mathematical keyword normalizer (leaves non-Catalan text untouched)."""
    if not text:
        return text
    if language is not None and language.strip().lower() not in {"cat", "ca", "catalan"}:
        return text
    out = text
    for pattern, repl in _CATALAN_MATH_PATTERNS:
        out = pattern.sub(repl, out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def _normalize_options_for_language(
    options: Optional[dict[str, str]],
    language: Optional[str],
) -> dict[str, str]:
    if not options:
        return {}
    if language is not None and language.strip().lower() in {"cat", "ca", "catalan"}:
        return {k: normalize_catalan_math_keywords(str(v), language="cat") for k, v in options.items()}
    return {k: str(v) for k, v in options.items()}


_UNIT_STRIP_RE = re.compile(
    r"(?i)(?:^|\s*)(?:cm2|cm²|cm3|cm³|cm|m2|m²|m3|m³|km|mm|dm|kg|mg|ml|hrs|hr|min|euros|eur|deg|°|€|\$|[gmlhs])(?:\s*)$"
)


def _parse_numeric_value(text: str) -> Optional[float]:
    cleaned = str(text or "").strip().replace("−", "-").replace(",", ".")
    if not cleaned:
        return None
    # Strip leading currency symbols (€, $)
    cleaned = re.sub(r"^[€$]\s*", "", cleaned).strip()
    # Strip trailing units (case-insensitive) when separated or attached to a digit
    if re.search(r"\d", cleaned):
        cleaned = re.sub(
            r"(?i)(?<=\d|\s)(?:cm2|cm²|cm3|cm³|cm|m2|m²|m3|m³|km|mm|dm|kg|mg|ml|hrs|hr|min|euros|eur|deg|°|€|\$|g|m|l|h|s)\s*$",
            "",
            cleaned,
        ).strip()
    # Handle LaTeX \frac{a}{b} or \dfrac{a}{b}
    latex_frac = re.fullmatch(
        r"([-+]?)\s*\\d?frac\{\s*([-+]?\d+(?:\.\d+)?)\s*\}\{\s*(\d+(?:\.\d+)?)\s*\}",
        cleaned,
    )
    if latex_frac:
        sign = -1.0 if latex_frac.group(1) == "-" else 1.0
        den = float(latex_frac.group(3))
        if den != 0.0:
            return sign * float(latex_frac.group(2)) / den
    pct_m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*%", cleaned)
    if pct_m:
        return float(pct_m.group(1)) / 100.0
    frac_m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", cleaned)
    if frac_m and float(frac_m.group(2)) != 0.0:
        return float(frac_m.group(1)) / float(frac_m.group(2))
    num_m = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned)
    if num_m:
        return float(cleaned)
    return None


def _is_prime_int(n: int) -> bool:
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


def score_option_features(
    statement: str,
    options: dict[str, str],
    oracle: Optional[dict[str, Any]] = None,
    knn_votes: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Pure-stdlib GroupKFold-validated (l2=50) option-structure & math-feature scorer."""
    if not options:
        return {"features": {}, "scores": {k: 0.2 for k in MCQ_LETTERS}}

    active_oracle = oracle if oracle is not None else extract_math_oracle(statement)
    oracle_opt = _match_oracle_to_option(active_oracle, options)
    stmt_lower = (statement or "").lower()
    stmt_nums: list[float] = []
    for tok in re.findall(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?:/\d+)?", statement or ""):
        val = _parse_numeric_value(tok)
        if val is not None:
            stmt_nums.append(val)

    pair_targets: set[float] = set()
    for i in range(len(stmt_nums)):
        for j in range(i + 1, len(stmt_nums)):
            a, b = stmt_nums[i], stmt_nums[j]
            pair_targets.add(round(a + b, 6))
            pair_targets.add(round(abs(a - b), 6))
            pair_targets.add(round(a * b, 6))

    parsed_vals: dict[str, Optional[float]] = {
        opt: _parse_numeric_value(val) for opt, val in options.items()
    }
    numeric_only = sorted(v for v in parsed_vals.values() if v is not None)
    n_num = len(numeric_only)
    median_val = statistics.median(numeric_only) if n_num >= 3 else None
    min_val = numeric_only[0] if n_num >= 3 else None
    max_val = numeric_only[-1] if n_num >= 3 else None
    second_max_val = numeric_only[-2] if n_num >= 3 else None
    span = (numeric_only[-1] - numeric_only[0]) if n_num >= 2 else 0.0
    min_stmt_num = min(stmt_nums) if stmt_nums else None
    max_stmt_num = max(stmt_nums) if stmt_nums else None

    # Check if numeric options form an arithmetic progression
    is_ap = False
    if n_num >= 4 and span > 1e-9:
        diffs = [numeric_only[i + 1] - numeric_only[i] for i in range(n_num - 1)]
        if max(diffs) - min(diffs) < 1e-6 * max(1.0, span):
            is_ap = True

    # Per-option gaps (min_gap, d2 = 2nd nearest or nearest gap, mean_gap)
    opt_min_gap: dict[str, float] = {}
    opt_d2: dict[str, float] = {}
    opt_mean_gap: dict[str, float] = {}
    for opt, v in parsed_vals.items():
        if v is None or n_num < 3 or span <= 1e-9:
            continue
        others = sorted(abs(v - ov) for o2, ov in parsed_vals.items() if o2 != opt and ov is not None)
        if others:
            opt_min_gap[opt] = others[0]
            opt_d2[opt] = others[1] if len(others) >= 2 else others[0]
            opt_mean_gap[opt] = sum(others) / len(others)

    min_d2_val = min(opt_d2.values()) if opt_d2 else None
    max_min_gap_val = max(opt_min_gap.values()) if opt_min_gap else None
    median_min_gap = statistics.median(opt_min_gap.values()) if len(opt_min_gap) >= 3 else None

    # Singleton flags across options
    frac_opts = [
        o for o, s in options.items()
        if ("/" in str(s) or "\\frac" in str(s) or "\\dfrac" in str(s)) and parsed_vals.get(o) is not None
    ]
    prime_opts = [
        o for o, v in parsed_vals.items()
        if v is not None and abs(v - round(v)) < 1e-9 and _is_prime_int(int(round(v)))
    ]
    round10_opts = [
        o for o, v in parsed_vals.items()
        if v is not None and abs(v - round(v)) < 1e-9 and int(round(v)) != 0 and int(round(v)) % 10 == 0
    ]

    # Text lengths
    opt_lens = {o: len(str(v).strip()) for o, v in options.items() if str(v).strip()}
    min_len = min(opt_lens.values()) if len(opt_lens) >= 3 else None
    max_len = max(opt_lens.values()) if len(opt_lens) >= 3 else None

    asks_min = 1.0 if re.search(
        r"\b(?:minimum|smallest|least|lowest|fewest|mínim|més\s+petit|més\s+petita|menys)\b",
        stmt_lower,
    ) else 0.0

    int_parities: dict[str, int] = {}
    for opt, v in parsed_vals.items():
        if v is not None and abs(v - round(v)) < 1e-9:
            int_parities[opt] = int(round(v)) % 2
    even_opts = [o for o, p in int_parities.items() if p == 0]
    odd_opts = [o for o, p in int_parities.items() if p == 1]
    outlier_parity_opt: Optional[str] = None
    if len(int_parities) >= 4:
        if len(even_opts) == 1:
            outlier_parity_opt = even_opts[0]
        elif len(odd_opts) == 1:
            outlier_parity_opt = odd_opts[0]

    features: dict[str, dict[str, float]] = {}
    raw_scores: dict[str, float] = {}
    for opt in MCQ_LETTERS:
        if opt not in options:
            raw_scores[opt] = -10.0
            continue
        val_str = str(options[opt]).strip()
        val_low = val_str.lower()
        num_v = parsed_vals.get(opt)

        f_oracle = 1.0 if opt == oracle_opt else 0.0
        f_median = 1.0 if (median_val is not None and num_v is not None and abs(num_v - median_val) < 1e-9) else 0.0
        f_min = 1.0 if (min_val is not None and num_v is not None and abs(num_v - min_val) < 1e-9) else 0.0
        f_max = 1.0 if (max_val is not None and num_v is not None and abs(num_v - max_val) < 1e-9) else 0.0
        f_extreme = 1.0 if (f_min > 0.0 or f_max > 0.0) else 0.0
        f_interior = 1.0 if (
            min_val is not None and max_val is not None and num_v is not None
            and (min_val + 1e-9 < num_v < max_val - 1e-9)
        ) else 0.0
        f_2nd_max = 1.0 if (
            second_max_val is not None and num_v is not None and abs(num_v - second_max_val) < 1e-9
        ) else 0.0
        f_lt_min_stem = 1.0 if (min_stmt_num is not None and num_v is not None and num_v < min_stmt_num - 1e-9) else 0.0
        f_gt_max_stem = 1.0 if (max_stmt_num is not None and num_v is not None and num_v > max_stmt_num + 1e-9) else 0.0
        f_frac_singleton = 1.0 if (len(frac_opts) == 1 and frac_opts[0] == opt) else 0.0
        f_prime_singleton = 1.0 if (len(prime_opts) == 1 and prime_opts[0] == opt) else 0.0
        f_round10_singleton = 1.0 if (len(round10_opts) == 1 and round10_opts[0] == opt) else 0.0

        f_rel_rank = 0.5
        if num_v is not None and n_num >= 2:
            rank_idx = sum(1 for v in numeric_only if v < num_v - 1e-9)
            f_rel_rank = rank_idx / float(n_num - 1)

        f_d2_norm = min(1.0, opt_d2[opt] / span) if (opt in opt_d2 and span > 1e-9) else 0.0
        f_min_gap_norm = min(1.0, opt_min_gap[opt] / span) if (opt in opt_min_gap and span > 1e-9) else 0.0
        f_mean_gap_norm = min(1.0, opt_mean_gap[opt] / span) if (opt in opt_mean_gap and span > 1e-9) else 0.0
        f_tightest_d2 = 1.0 if (min_d2_val is not None and opt in opt_d2 and abs(opt_d2[opt] - min_d2_val) < 1e-9) else 0.0
        f_dense_interior = 1.0 if (
            f_interior > 0.0 and median_min_gap is not None and opt in opt_min_gap and opt_min_gap[opt] <= median_min_gap + 1e-9
        ) else 0.0
        f_most_isolated = 1.0 if (
            max_min_gap_val is not None and opt in opt_min_gap and abs(opt_min_gap[opt] - max_min_gap_val) < 1e-9
        ) else 0.0
        f_non_ap_tight_d2 = 1.0 if (not is_ap and f_tightest_d2 > 0.0) else 0.0

        f_shortest = 1.0 if (min_len is not None and max_len is not None and min_len < max_len and opt_lens.get(opt) == min_len) else 0.0
        f_longest = 1.0 if (min_len is not None and max_len is not None and min_len < max_len and opt_lens.get(opt) == max_len) else 0.0
        f_asks_min_x_rank = asks_min * f_rel_rank

        f_in_stmt = 1.0 if (
            len(val_low) >= 2 and re.search(rf"(?<!\w){re.escape(val_low)}(?!\w)", stmt_lower)
        ) else 0.0
        f_sum_prod = 1.0 if (num_v is not None and round(num_v, 6) in pair_targets) else 0.0
        f_none_opt = 1.0 if any(
            tok in val_low
            for tok in ("none of", "impossible", "cannot", "cap d'aquestes", "cap de les anteriors", "cap anterior")
        ) else 0.0
        f_parity = 1.0 if opt == outlier_parity_opt else 0.0
        f_knn = float((knn_votes or {}).get(opt, 0.0))
        f_bias_d = 1.0 if opt == "D" else 0.0
        f_bias_e = 1.0 if opt == "E" else 0.0

        feat = {
            "is_oracle_match": f_oracle,
            "is_median": f_median,
            "is_extreme": f_extreme,
            "is_interior": f_interior,
            "is_2nd_max": f_2nd_max,
            "lt_min_stem": f_lt_min_stem,
            "gt_max_stem": f_gt_max_stem,
            "frac_singleton": f_frac_singleton,
            "prime_singleton": f_prime_singleton,
            "round10_singleton": f_round10_singleton,
            "is_dense_interior": f_dense_interior,
            "is_tightest_d2": f_tightest_d2,
            "is_most_isolated": f_most_isolated,
            "non_ap_tight_d2": f_non_ap_tight_d2,
            "in_statement": f_in_stmt,
            "is_sum_or_product": f_sum_prod,
            "is_none_of_these": f_none_opt,
            "parity_outlier": f_parity,
            "knn_vote": round(f_knn, 4),
        }
        features[opt] = feat
        raw_scores[opt] = (
            6.0 * f_oracle
            + 0.45 * f_knn
            - 0.25 * f_none_opt
            + 0.340 * f_interior
            + 0.279 * f_median
            + 0.125 * f_2nd_max
            + 0.120 * f_lt_min_stem
            + 0.116 * f_frac_singleton
            + 0.107 * f_dense_interior
            + 0.104 * f_bias_d
            + 0.103 * f_rel_rank
            + 0.102 * f_tightest_d2
            + 0.102 * f_prime_singleton
            + 0.096 * f_gt_max_stem
            + 0.067 * f_bias_e
            - 0.236 * f_d2_norm
            - 0.208 * f_shortest
            - 0.207 * f_min
            - 0.194 * f_mean_gap_norm
            - 0.189 * f_asks_min_x_rank
            - 0.169 * f_in_stmt
            - 0.147 * f_min_gap_norm
            - 0.142 * f_round10_singleton
            - 0.136 * f_longest
            - 0.119 * f_max
            - 0.118 * f_non_ap_tight_d2
            - 0.115 * f_most_isolated
        )

    valid_keys = tuple(k for k in MCQ_LETTERS if k in options)
    norm_valid = softmax_scores(raw_scores, keys=valid_keys)
    scores = {k: norm_valid.get(k, 0.0) for k in MCQ_LETTERS}
    return {"features": features, "scores": scores}


def _is_oracle_shortcut_safe(statement: str, oracle: dict[str, Any]) -> bool:
    """Avoid shortcutting to x when a linear/quadratic equation problem asks for a compound expression like 3x."""
    if oracle.get("kind") not in {"linear", "quadratic"}:
        return True
    for target in oracle.get("forbidden_targets") or ():
        if "=" in target:
            var_part = target.split("=", 1)[0].strip()
            if len(var_part) == 1 and _asks_compound_target(statement, var_part):
                return False
    return True


def score_mcq_options(
    statement: str,
    options: Optional[dict[str, str]],
    problem_id: str = "EXAM-01",
    level: Optional[int] = None,
    qnum: Optional[int] = None,
    raw_logprobs: Optional[dict[str, float]] = None,
    baseline_logprobs: Optional[dict[str, float]] = None,
    priors: Optional[dict[Any, dict[str, float]]] = None,
    knn_votes: Optional[dict[str, float]] = None,
    oracle: Optional[dict[str, Any]] = None,
    use_calibration: bool = True,
    use_prior: bool = True,
    language: Optional[str] = None,
    calibrated_probs_override: Optional[dict[str, float]] = None,
    is_visual_problem: Optional[bool] = None,
) -> dict[str, Any]:
    """Combine logprob calibration, (level, qnum) prior fallback, and option-value features."""
    opts = _normalize_options_for_language(options, language)
    prior_probs = get_level_qnum_prior(
        problem_id, level=level, qnum=qnum, priors=priors, has_options=bool(opts)
    )
    if not opts:
        best = max(MCQ_LETTERS, key=lambda k: (prior_probs.get(k, 0.0), k))
        return {
            "predicted_option": best,
            "combined_probs": dict(prior_probs),
            "prior_probs": prior_probs,
            "calibrated_probs": None,
            "feature_scores": {k: 0.0 for k in MCQ_LETTERS},
            "features": {},
            "source": "level_qnum_prior",
        }

    norm_statement = normalize_catalan_math_keywords(statement, language=language) if language else statement
    active_oracle = oracle if oracle is not None else extract_math_oracle(norm_statement)
    oracle_safe = _is_oracle_shortcut_safe(norm_statement, active_oracle)
    eff_oracle = active_oracle if oracle_safe else {"allowed_givens": set(), "forbidden_targets": set(), "solutions": [], "kind": None}
    feat_out = score_option_features(norm_statement, opts, oracle=eff_oracle, knn_votes=knn_votes)
    feat_scores = feat_out["scores"]
    oracle_opt = _match_oracle_to_option(eff_oracle, opts)

    if calibrated_probs_override is not None:
        cal_probs = dict(calibrated_probs_override)
    else:
        raw_probs = softmax_scores(raw_logprobs) if raw_logprobs else None
        cal_probs = (
            calibrate_logprobs(raw_logprobs, baseline_logprobs=baseline_logprobs)
            if raw_logprobs and use_calibration
            else raw_probs
        )

    is_visual = bool(is_visual_problem) if is_visual_problem is not None else _has_figure_cues(norm_statement)
    w_cal, w_prior, w_feat = 0.0, 0.45, 0.55
    if cal_probs is not None:
        sorted_cal = sorted(cal_probs.values(), reverse=True)
        cal_margin = (sorted_cal[0] - sorted_cal[1]) if len(sorted_cal) >= 2 else 0.0
        if is_visual:
            w_cal, w_prior, w_feat = 0.35, 0.25, 0.40
        elif cal_margin < 0.15:
            w_cal, w_prior, w_feat = 0.50, 0.20, 0.30
        else:
            w_cal, w_prior, w_feat = 0.75, 0.10, 0.15

    valid_keys = [k for k in MCQ_LETTERS if k in opts] or list(MCQ_LETTERS)
    combined: dict[str, float] = {}
    for k in MCQ_LETTERS:
        if k not in valid_keys:
            combined[k] = 0.0
            continue
        score = 0.0
        if cal_probs is not None:
            score += w_cal * cal_probs.get(k, 0.0)
        if use_prior:
            score += w_prior * prior_probs.get(k, 0.0)
        score += w_feat * feat_scores.get(k, 0.0)
        if knn_votes:
            score += 0.25 * float(knn_votes.get(k, 0.0))
        if oracle_opt == k:
            score += 6.0
        combined[k] = score

    total = sum(combined.values()) or 1.0
    combined_probs = {k: round(v / total, 6) for k, v in combined.items()}
    best = max(valid_keys, key=lambda k: (combined_probs[k], prior_probs.get(k, 0.0)))
    source = (
        "math_oracle"
        if oracle_opt == best
        else (
            "calibrated_pride_fusion"
            if calibrated_probs_override is not None
            else ("calibrated_fusion" if raw_logprobs else "prior_feature_fusion")
        )
    )
    return {
        "predicted_option": best,
        "combined_probs": combined_probs,
        "prior_probs": prior_probs,
        "calibrated_probs": cal_probs,
        "feature_scores": feat_scores,
        "features": feat_out["features"],
        "source": source,
    }


def build_mcq_logprob_messages(
    statement: str,
    options: dict[str, str],
    examples: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    """Build multi-turn question-first, options-last prompt so llama-server reuses question KV cache."""
    system_prompt = (
        "You are solving a multiple-choice Kangaroo math problem. "
        "Respond with ONLY the single uppercase option letter A, B, C, D, or E."
    )
    q_parts: list[str] = []
    if examples:
        for ex in examples:
            ex_text = str(ex.get("text") or ex.get("statement") or "").strip()
            ex_opts = ex.get("options") or {}
            ex_label = str(ex.get("label") or ex.get("correct_option") or "").strip().upper()
            if ex_text and ex_opts and ex_label in MCQ_LETTERS:
                ex_opts_block = "\n".join(f"{k}) {ex_opts[k]}" for k in MCQ_LETTERS if k in ex_opts)
                q_parts.append(f"Example Question:\n{ex_text}\nOptions:\n{ex_opts_block}\nAnswer letter: {ex_label}")
    q_parts.append(f"Question:\n{statement.strip()}")
    opts_block = "\n".join(f"{k}) {options[k]}" for k in MCQ_LETTERS if k in options)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n\n".join(q_parts)},
        {"role": "assistant", "content": "Options:"},
        {"role": "user", "content": f"{opts_block}\n\nAnswer:"},
    ]


SYS_POT = (
    "You are a math competition solver. Write short, exact Python 3 code "
    "(using only pure Python, math, fractions, or itertools) that computes the numeric answer "
    "and assigns it to `ans`. Output ONLY Python code, NO prose, NO comments, NO options A-E.\n\n"
    "Example:\n"
    "Problem: Find the sum of all integers from 1 to 10 divisible by 3.\n"
    "Code:\n"
    "ans = sum(x for x in range(1, 11) if x % 3 == 0)"
)

SYS_COT = (
    "Solve the math problem in at most 3 short calculation steps. "
    "End with `Final Answer: <number>`."
)


def build_pot_messages(statement: str) -> list[dict[str, str]]:
    """Build an option-free Program-of-Thought prompt with a static cached 1-shot Python example."""
    return [
        {"role": "system", "content": SYS_POT},
        {"role": "user", "content": f"Problem: {statement.strip()}\nCode:"},
    ]


def build_cot_messages(statement: str) -> list[dict[str, str]]:
    """Build a concise 3-step option-free Chain-of-Thought prompt ending with Final Answer."""
    user_prompt = f"Problem:\n{statement.strip()}\n\nShow at most 3 short steps and end with Final Answer:"
    return [
        {"role": "system", "content": SYS_COT},
        {"role": "user", "content": user_prompt},
    ]


_POT_FORBIDDEN_NAMES = {
    "eval",
    "exec",
    "compile",
    "open",
    "input",
    "__import__",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "dir",
    "breakpoint",
    "exit",
    "quit",
    "help",
    "memoryview",
    "type",
    "object",
}
_POT_ALLOWED_IMPORTS = {"math", "fractions", "itertools", "collections"}


def _extract_pot_code(raw_text: str) -> str:
    text = (raw_text or "").strip()
    fence_m = re.search(r"```(?:python|py)?\s*\n?(.*?)```", text, flags=re.S | re.I)
    if fence_m:
        text = fence_m.group(1).strip()
    else:
        open_m = re.search(r"```(?:python|py)?\s*\n?(.*)", text, flags=re.S | re.I)
        if open_m:
            text = open_m.group(1).strip()
        text = re.sub(r"```\s*$", "", text).strip()
    text = re.sub(r"^Code:\s*\n?", "", text, flags=re.I).strip()
    return text


def _is_trivial_pot_ast(tree: ast.AST) -> bool:
    """Return True if PoT AST performs no real computation (e.g. just `ans = 12`)."""
    compute_nodes = (
        ast.BinOp,
        ast.AugAssign,
        ast.Call,
        ast.For,
        ast.While,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.If,
    )
    return not any(isinstance(node, compute_nodes) for node in ast.walk(tree))


def _validate_pot_ast(tree: ast.AST) -> Optional[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"Forbidden dunder attribute: {node.attr}"
        if isinstance(node, ast.Name) and node.id in _POT_FORBIDDEN_NAMES:
            return f"Forbidden name: {node.id}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_mod = alias.name.split(".", 1)[0]
                if root_mod not in _POT_ALLOWED_IMPORTS:
                    return f"Forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            root_mod = (node.module or "").split(".", 1)[0]
            if root_mod not in _POT_ALLOWED_IMPORTS:
                return f"Forbidden import: {node.module}"
    return None


def _run_pot_sandbox(code: str, timeout_s: float = 1.0) -> tuple[bool, Any, str]:
    """Execute restricted Python PoT code in an AST-checked, step-capped sandbox."""
    cleaned = _extract_pot_code(code)
    if not cleaned:
        return False, None, "Empty PoT code"
    try:
        tree = ast.parse(cleaned, mode="exec")
    except SyntaxError as err:
        return False, None, f"SyntaxError: {err.msg}"

    err_msg = _validate_pot_ast(tree)
    if err_msg:
        return False, None, err_msg
    if _is_trivial_pot_ast(tree):
        return False, None, "Trivial no-op PoT code"

    printed: list[str] = []

    def _safe_range(*args: int) -> range:
        r = range(*args)
        if len(r) > 200_000:
            raise ValueError("Range limit exceeded")
        return r

    def _safe_factorial(n: int) -> int:
        if not isinstance(n, int) or n < 0 or n > 1000:
            raise ValueError("Factorial limit exceeded")
        return math.factorial(n)

    def _safe_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        root_mod = str(name).split(".", 1)[0]
        if root_mod not in _POT_ALLOWED_IMPORTS:
            raise ImportError(f"Blocked import: {name}")
        return {
            "math": math,
            "fractions": fractions,
            "itertools": itertools,
            "collections": collections,
        }[root_mod]

    def _safe_print(*args: Any, **kwargs: Any) -> None:
        if len(printed) < 64:
            printed.append(" ".join(str(a) for a in args))

    safe_builtins = {
        "__import__": _safe_import,
        "abs": abs,
        "all": all,
        "any": any,
        "bin": bin,
        "bool": bool,
        "chr": chr,
        "dict": dict,
        "divmod": divmod,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "format": format,
        "frozenset": frozenset,
        "hex": hex,
        "int": int,
        "isinstance": isinstance,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "oct": oct,
        "ord": ord,
        "pow": pow,
        "print": _safe_print,
        "range": _safe_range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }
    env: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "math": math,
        "fractions": fractions,
        "itertools": itertools,
        "collections": collections,
        "Counter": collections.Counter,
        "defaultdict": collections.defaultdict,
        "Fraction": fractions.Fraction,
        "sqrt": math.sqrt,
        "isqrt": math.isqrt,
        "gcd": math.gcd,
        "lcm": math.lcm,
        "comb": math.comb,
        "perm": math.perm,
        "factorial": _safe_factorial,
        "floor": math.floor,
        "ceil": math.ceil,
    }

    result_box: dict[str, Any] = {"ok": False, "val": None, "err": ""}
    compiled = compile(tree, "<pot>", "exec")

    def _worker() -> None:
        steps = 0
        deadline = time.monotonic() + max(0.05, float(timeout_s))

        def _tracer(frame: Any, event: str, arg: Any) -> Any:
            nonlocal steps
            steps += 1
            if steps > 100_000 or time.monotonic() > deadline:
                raise RuntimeError("PoT execution step/time limit exceeded")
            return _tracer

        sys.settrace(_tracer)
        try:
            exec(compiled, env, env)
            val = None
            for var in ("ans", "answer", "result", "res", "total"):
                if var in env and env[var] is not None:
                    val = env[var]
                    break
            if val is None and printed:
                val = printed[-1].strip()
            result_box["ok"] = val is not None
            result_box["val"] = val
            if val is None:
                result_box["err"] = "No `ans` value produced"
        except Exception as exc:
            result_box["err"] = f"{type(exc).__name__}: {exc}"
        finally:
            sys.settrace(None)

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout=max(0.05, float(timeout_s)) + 0.05)
    if worker.is_alive():
        return False, None, "PoT sandbox timeout"
    return bool(result_box["ok"]), result_box["val"], str(result_box["err"])


def _parse_option_numeric(text: str) -> Optional[float]:
    """Parse an option string into a numeric value (handles units like '19 cm' when pure)."""
    direct = _parse_constant_math(text)
    if direct is not None:
        return direct
    cleaned = str(text or "").strip()
    unit_m = re.fullmatch(
        r"([-+]?\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?)\s*(?:cm|mm|m|km|kg|g|l|ml|h|min|s|deg|degrees?|°|€|\$)?",
        cleaned,
        flags=re.I,
    )
    if unit_m:
        return _parse_constant_math(unit_m.group(1))
    return None


def _match_value_to_unique_option(val: Any, options: dict[str, str]) -> Optional[str]:
    """Match a computed scalar value against options A..E; return letter iff uniquely 1 matches."""
    if val is None or not options or isinstance(val, bool):
        return None
    if isinstance(val, str):
        num_val = _parse_option_numeric(val)
    elif isinstance(val, fractions.Fraction):
        num_val = float(val)
    elif isinstance(val, (int, float)):
        num_val = float(val)
    else:
        return None

    if num_val is None or not math.isfinite(num_val):
        return None

    matches: list[str] = []
    for letter in MCQ_LETTERS:
        if letter not in options:
            continue
        opt_num = _parse_option_numeric(str(options[letter]))
        if opt_num is None:
            continue
        tol = 1e-6 * max(1.0, abs(num_val), abs(opt_num))
        if abs(opt_num - num_val) <= tol:
            matches.append(letter)
    return matches[0] if len(matches) == 1 else None


_FIGURE_CUE_RE = re.compile(
    r"(?i)\b(?:figure|diagram|picture|image|drawing|illustration|graph|shaded|unshaded|"
    r"shown\s+(?:in|below|above|on\s+the\s+right)|figura|dibuix|imatge|ombrejat|ombrejada|"
    r"ombrejats|ombrejades|es\s+mostra|mostra\s+la\s+figura)\b"
)


def _has_figure_cues(statement: str) -> bool:
    return bool(_FIGURE_CUE_RE.search(statement or ""))


def _extract_cot_value_and_match(
    cot_text: str,
    options: dict[str, str],
    statement: str,
) -> Optional[str]:
    """Extract an explicitly concluded numeric/fraction value from CoT text and match uniquely."""
    text = (cot_text or "").strip()
    if len(text) < 12 or not options:
        return None
    has_calc = bool(re.search(r"(=|[+\-*/×÷]|\\times|\\frac)", text))
    if not has_calc:
        return None

    explicit_candidates: list[str] = []
    boxed = re.findall(r"\\boxed\{([^{}]+)\}", text)
    if boxed:
        explicit_candidates.append(boxed[-1].strip())
    final_m = re.findall(
        r"(?im)\b(?:final\s*(?:answer|result|value)?|answer|resultat|resposta)\s*(?:is|és|:|=)\s*([^\n.;,]+)",
        text,
    )
    if final_m:
        explicit_candidates.append(final_m[-1].strip())

    # If an explicit conclusion marker exists, NEVER fall back to earlier intermediate equation lines.
    if explicit_candidates:
        for cand in explicit_candidates:
            cand_clean = cand.strip().strip("$").rstrip(".")
            if "=" in cand_clean:
                cand_clean = cand_clean.rsplit("=", 1)[-1].strip()
            matched = _match_value_to_unique_option(cand_clean, options)
            if matched is not None:
                return matched
            num_unit_m = re.fullmatch(
                r"([-+]?\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?%?)\s+[A-Za-zÀ-ÿ]+",
                cand_clean,
            )
            if num_unit_m:
                matched = _match_value_to_unique_option(num_unit_m.group(1), options)
                if matched is not None:
                    return matched
        return None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    last_line = lines[-1]
    if re.search(r"[+\-*/×÷=<>(,]\s*$", last_line):
        return None

    end_eq = re.search(
        r"=\s*([-+]?\d+(?:[.,]\d+)?(?:\s*/\s*\d+)?%?)\s*(?:cm|mm|m|km|kg|g|l|ml|h|min|s|deg|degrees?|°|€|\$)?\s*[.$]*$",
        last_line,
        flags=re.I,
    )
    if not end_eq:
        return None
    cand_clean = end_eq.group(1).strip()
    stmt_nums = set(re.findall(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?:/\d+)?", statement or ""))
    if "=" not in text and cand_clean in stmt_nums:
        return None
    return _match_value_to_unique_option(cand_clean, options)


def solve_mcq_calibrated(
    problem: dict[str, Any] | str,
    llm_url: Optional[str] = None,
    time_limit_s: float = 45.0,
    priors: Optional[dict[Any, dict[str, float]]] = None,
    train_jsonl: Optional[str | os.PathLike[str]] = None,
    baseline_logprobs: Optional[dict[str, float]] = None,
    examples: Optional[list[dict[str, Any]]] = None,
    knn_votes: Optional[dict[str, float]] = None,
    use_permutations: bool = True,
    max_shifts: int = 2,
    model_id: Optional[str] = None,
    enable_pot_cot: bool = False,
) -> dict[str, Any]:
    """Calibrated offline MCQ exam solver for M3Kang/Qualcomm dicts (cat/eng, <1ms empty-options prior)."""
    try:
        from engine.mcq_optimizer import solve_mcq_calibrated as _opt_solve_mcq_calibrated

        return _opt_solve_mcq_calibrated(
            problem=problem,
            llm_url=llm_url,
            time_limit_s=time_limit_s,
            priors=priors,
            train_jsonl=train_jsonl,
            baseline_logprobs=baseline_logprobs,
            examples=examples,
            knn_votes=knn_votes,
            use_permutations=use_permutations,
            max_shifts=max_shifts,
            model_id=model_id,
            enable_pot_cot=enable_pot_cot,
        )
    except ImportError:
        pass

    t0 = time.perf_counter()
    budget_s = min(45.0, max(0.0, float(time_limit_s)))
    deadline = t0 + budget_s
    # Keep headroom before deadline so worst_s stays strictly <= 45s (and never hits 60s).
    headroom_s = 4.0 if budget_s >= 30.0 else (2.5 if budget_s >= 15.0 else min(0.15, budget_s * 0.1))
    safe_deadline = max(t0 + 0.05, deadline - headroom_s)

    if isinstance(problem, str):
        prob_dict: dict[str, Any] = dict(get_problem(problem) or {"id": problem, "text": problem, "options": {}})
    else:
        prob_dict = dict(problem)

    pid = str(prob_dict.get("id") or prob_dict.get("problem_id") or "EXAM-01")
    language = str(prob_dict.get("language") or prob_dict.get("source_language") or "eng").strip().lower()
    raw_text = str(prob_dict.get("text") or prob_dict.get("statement") or "").strip()
    raw_opts = dict(prob_dict.get("options") or {})

    lvl = _normalize_row_level(prob_dict)
    if lvl is None:
        lvl, _ = parse_level_qnum(pid)
    qn = _extract_row_qnum(prob_dict)
    if qn is None:
        _, qn = parse_level_qnum(pid)

    valid_opts = {k: str(v).strip() for k, v in raw_opts.items() if k in MCQ_LETTERS and str(v).strip()}
    active_priors = priors
    if active_priors is None:
        active_priors = fit_priors_from_jsonl(train_jsonl) if train_jsonl else compute_level_qnum_priors()
    prior_probs = get_level_qnum_prior(
        pid, level=lvl, qnum=qn, priors=active_priors, has_options=bool(valid_opts)
    )
    prior_best = max(MCQ_LETTERS, key=lambda k: (prior_probs.get(k, 0.0), k))

    if not valid_opts:
        return {
            "problem_id": pid,
            "language": language,
            "answer": prior_best,
            "predicted_option": prior_best,
            "status": "ok_prior_empty_options",
            "source": "level_qnum_prior",
            "combined_probs": dict(prior_probs),
            "prior_probs": prior_probs,
            "calibrated_probs": None,
            "raw_logprobs": {},
            "feature_scores": {k: 0.0 for k in MCQ_LETTERS},
            "prompt_tokens": 0,
            "timings": {},
            "permutations_used": 0,
            "normalized_text": raw_text,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "seconds": round(time.perf_counter() - t0, 4),
            "offline_mode": True,
        }

    is_cat = language in {"cat", "ca", "catalan"}
    norm_text = normalize_catalan_math_keywords(raw_text, language=language) if is_cat else raw_text
    norm_opts = _normalize_options_for_language(valid_opts, language if is_cat else None)
    oracle = extract_math_oracle(norm_text)
    oracle_opt: Optional[str] = None
    if _is_oracle_shortcut_safe(norm_text, oracle):
        oracle_opt = _match_oracle_to_option(oracle, norm_opts) or _match_oracle_to_option(oracle, valid_opts)

    eq_verified = False
    if oracle_opt is None:
        oracle_opt = verify_equation_options(raw_text, valid_opts) or verify_equation_options(norm_text, norm_opts)
        eq_verified = oracle_opt is not None

    if oracle_opt is not None:
        oracle_scoring = score_mcq_options(
            statement=norm_text,
            options=norm_opts,
            problem_id=pid,
            level=lvl,
            qnum=qn,
            priors=active_priors,
            knn_votes=knn_votes,
            oracle=oracle if not eq_verified else {"allowed_givens": set(), "forbidden_targets": set(), "solutions": [str(valid_opts[oracle_opt])], "kind": "equation_verifier"},
            use_calibration=True,
            use_prior=True,
        )
        final_oracle_ans = oracle_opt if eq_verified else oracle_scoring["predicted_option"]
        return {
            "problem_id": pid,
            "language": language,
            "answer": final_oracle_ans,
            "predicted_option": final_oracle_ans,
            "status": "ok_oracle",
            "source": "equation_verifier" if eq_verified else oracle_scoring["source"],
            "combined_probs": oracle_scoring["combined_probs"],
            "prior_probs": oracle_scoring["prior_probs"],
            "calibrated_probs": None,
            "raw_logprobs": {},
            "feature_scores": oracle_scoring["feature_scores"],
            "prompt_tokens": 0,
            "timings": {},
            "permutations_used": 0,
            "normalized_text": norm_text,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "seconds": round(time.perf_counter() - t0, 4),
            "offline_mode": True,
        }

    active_model = model_id or os.environ.get("SLM_MODEL_ID", DEFAULT_MODEL_ID)
    raw_logprobs: dict[str, float] = {}
    timings: dict[str, Any] = {}
    prompt_tokens = 0
    perm_count = 0
    cal_override: Optional[dict[str, float]] = None
    status = "ok_fallback"

    remaining = safe_deadline - time.perf_counter()
    if remaining <= 0.15:
        return {
            "problem_id": pid,
            "language": language,
            "answer": prior_best,
            "predicted_option": prior_best,
            "status": "timeout",
            "source": "level_qnum_prior",
            "combined_probs": dict(prior_probs),
            "prior_probs": prior_probs,
            "calibrated_probs": None,
            "raw_logprobs": {},
            "feature_scores": {k: 0.0 for k in MCQ_LETTERS},
            "prompt_tokens": 0,
            "timings": {},
            "permutations_used": 0,
            "normalized_text": norm_text,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "seconds": round(time.perf_counter() - t0, 4),
            "offline_mode": True,
        }

    pass1_start = time.perf_counter()
    # Send native language statement/options to :8081 (no Catanglish substitution).
    pass1_msgs = build_mcq_logprob_messages(raw_text, valid_opts, examples=examples)
    data, call_status = _call_llama_completion_with_status(
        messages=pass1_msgs,
        model_id=active_model,
        max_tokens=1,
        stop=[],
        temperature=DEFAULT_TEMPERATURE,
        seed=DEFAULT_SEED,
        cache_prompt=True,
        logprobs=True,
        top_logprobs=8,
        grammar="root ::= [A-E]",
        url=llm_url,
        timeout_sec=max(0.1, remaining),
    )
    pass1_elapsed = time.perf_counter() - pass1_start

    if call_status == "timeout":
        return {
            "problem_id": pid,
            "language": language,
            "answer": prior_best,
            "predicted_option": prior_best,
            "status": "timeout",
            "source": "level_qnum_prior",
            "combined_probs": dict(prior_probs),
            "prior_probs": prior_probs,
            "calibrated_probs": None,
            "raw_logprobs": {},
            "feature_scores": {k: 0.0 for k in MCQ_LETTERS},
            "prompt_tokens": 0,
            "timings": {},
            "permutations_used": 0,
            "normalized_text": norm_text,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "seconds": round(time.perf_counter() - t0, 4),
            "offline_mode": True,
        }

    if data:
        status = "ok"
        timings = dict(data.get("timings") or {})
        prompt_tokens = int(timings.get("prompt_n") or 0)
        raw_logprobs = extract_letter_logprobs(data)
        if not raw_logprobs:
            try:
                chosen = str(data["choices"][0]["message"]["content"]).strip().upper()
                if chosen in valid_opts:
                    raw_logprobs = {chosen: 0.0}
            except (KeyError, IndexError, TypeError):
                raw_logprobs = {}
        if raw_logprobs:
            perm_count = 1

    text_only_flag = prob_dict.get("text_only")
    is_visual_problem = (text_only_flag is False) or (
        text_only_flag is None and _has_figure_cues(raw_text)
    )
    allow_pot_cot = not is_visual_problem

    # Cascade Step 2 (optional, off by default on 0.8B): Run PoT Python sandbox and Concise CoT.
    if data and allow_pot_cot and enable_pot_cot:
        numeric_opt_count = sum(1 for v in valid_opts.values() if _parse_option_numeric(v) is not None)
        rem_pot = safe_deadline - time.perf_counter()
        pot_matched: Optional[str] = None
        if numeric_opt_count >= 3 and rem_pot > 18.0:
            pot_data, pot_status = _call_llama_completion_with_status(
                messages=build_pot_messages(raw_text),
                model_id=active_model,
                max_tokens=75,
                stop=["\n\n\n", "# Done", "Output:", "Problem:", "Question:"],
                temperature=DEFAULT_TEMPERATURE,
                seed=DEFAULT_SEED,
                cache_prompt=True,
                url=llm_url,
                timeout_sec=min(24.0, max(1.0, rem_pot)),
            )
            if pot_data and pot_status == "ok":
                try:
                    raw_code = str(pot_data["choices"][0]["message"]["content"])
                except (KeyError, IndexError, TypeError):
                    raw_code = ""
                exec_ok, pot_val, _ = _run_pot_sandbox(raw_code, timeout_s=1.0)
                if exec_ok:
                    pot_matched = _match_value_to_unique_option(pot_val, valid_opts)
                    if pot_matched is not None:
                        pot_scoring = score_mcq_options(
                            statement=norm_text,
                            options=norm_opts,
                            problem_id=pid,
                            level=lvl,
                            qnum=qn,
                            raw_logprobs=raw_logprobs or None,
                            baseline_logprobs=baseline_logprobs,
                            priors=active_priors,
                            knn_votes=knn_votes,
                            oracle=oracle,
                            use_calibration=True,
                            use_prior=True,
                        )
                        return {
                            "problem_id": pid,
                            "language": language,
                            "answer": pot_matched,
                            "predicted_option": pot_matched,
                            "status": "ok_pot",
                            "source": "pot_sandbox",
                            "combined_probs": pot_scoring["combined_probs"],
                            "prior_probs": pot_scoring["prior_probs"],
                            "calibrated_probs": pot_scoring["calibrated_probs"],
                            "raw_logprobs": raw_logprobs,
                            "feature_scores": pot_scoring["feature_scores"],
                            "prompt_tokens": prompt_tokens,
                            "timings": timings,
                            "permutations_used": perm_count,
                            "normalized_text": norm_text,
                            "latency_ms": int((time.perf_counter() - t0) * 1000),
                            "seconds": round(time.perf_counter() - t0, 4),
                            "offline_mode": True,
                        }

        rem_cot = safe_deadline - time.perf_counter()
        if pot_matched is None and rem_cot > 19.0:
            cot_data, cot_status = _call_llama_completion_with_status(
                messages=build_cot_messages(raw_text),
                model_id=active_model,
                max_tokens=85,
                stop=["Problem:", "Question:"],
                temperature=DEFAULT_TEMPERATURE,
                seed=DEFAULT_SEED,
                cache_prompt=True,
                url=llm_url,
                timeout_sec=min(24.0, max(1.0, rem_cot)),
            )
            if cot_data and cot_status == "ok":
                try:
                    cot_text = str(cot_data["choices"][0]["message"]["content"])
                except (KeyError, IndexError, TypeError):
                    cot_text = ""
                cot_matched = _extract_cot_value_and_match(cot_text, valid_opts, raw_text)
                if cot_matched is not None:
                    cot_scoring = score_mcq_options(
                        statement=norm_text,
                        options=norm_opts,
                        problem_id=pid,
                        level=lvl,
                        qnum=qn,
                        raw_logprobs=raw_logprobs or None,
                        baseline_logprobs=baseline_logprobs,
                        priors=active_priors,
                        knn_votes=knn_votes,
                        oracle=oracle,
                        use_calibration=True,
                        use_prior=True,
                    )
                    return {
                        "problem_id": pid,
                        "language": language,
                        "answer": cot_matched,
                        "predicted_option": cot_matched,
                        "status": "ok_cot",
                        "source": "cot_value_match",
                        "combined_probs": cot_scoring["combined_probs"],
                        "prior_probs": cot_scoring["prior_probs"],
                        "calibrated_probs": cot_scoring["calibrated_probs"],
                        "raw_logprobs": raw_logprobs,
                        "feature_scores": cot_scoring["feature_scores"],
                        "prompt_tokens": prompt_tokens,
                        "timings": timings,
                        "permutations_used": perm_count,
                        "normalized_text": norm_text,
                        "latency_ms": int((time.perf_counter() - t0) * 1000),
                        "seconds": round(time.perf_counter() - t0, 4),
                        "offline_mode": True,
                    }

    # Cascade Step 3: Uncertainty-gated cached cyclic option shifts (only when Pass 1 is uncertain
    # and > 12s remains, keeping mean latency <= 16-18s).
    if raw_logprobs:
        permuted_runs: list[tuple[dict[str, str], dict[str, float]]] = [(valid_opts, raw_logprobs)]
        distinct_vals = len({valid_opts[k].strip() for k in MCQ_LETTERS if k in valid_opts}) == 5
        pass1_cal = calibrate_logprobs(raw_logprobs, baseline_logprobs=baseline_logprobs)
        sorted_p1 = sorted(pass1_cal.values(), reverse=True)
        pass1_uncertain = len(sorted_p1) >= 2 and (sorted_p1[0] - sorted_p1[1]) < 0.40
        if use_permutations and distinct_vals and max_shifts > 0 and pass1_uncertain:
            last_shift_elapsed: Optional[float] = None
            min_board_shift_s = 5.0 if pass1_elapsed >= 0.5 else 0.3
            shift_indices = [1, 3] if max_shifts == 2 else list(range(1, max_shifts + 1))
            for idx_pos, shift in enumerate(shift_indices):
                rem = safe_deadline - time.perf_counter()
                if idx_pos == 0 and rem <= 12.0:
                    break
                min_shift_budget = (
                    max(min_board_shift_s, last_shift_elapsed * 1.2 + 0.25)
                    if last_shift_elapsed is not None
                    else max(min_board_shift_s, min(pass1_elapsed * 0.6, 6.0) + 0.25)
                )
                if rem <= min_shift_budget:
                    break
                shifted_opts = {
                    MCQ_LETTERS[i]: valid_opts[MCQ_LETTERS[(i + shift) % 5]]
                    for i in range(5)
                }
                s_start = time.perf_counter()
                s_data, s_status = _call_llama_completion_with_status(
                    messages=build_mcq_logprob_messages(raw_text, shifted_opts, examples=examples),
                    model_id=active_model,
                    max_tokens=1,
                    stop=[],
                    temperature=DEFAULT_TEMPERATURE,
                    seed=DEFAULT_SEED,
                    cache_prompt=True,
                    logprobs=True,
                    top_logprobs=8,
                    grammar="root ::= [A-E]",
                    url=llm_url,
                    timeout_sec=max(0.1, rem),
                )
                last_shift_elapsed = time.perf_counter() - s_start
                if s_status == "timeout" or not s_data:
                    break
                s_lp = extract_letter_logprobs(s_data)
                if s_lp:
                    permuted_runs.append((shifted_opts, s_lp))
                    perm_count += 1

        if len(permuted_runs) >= 2:
            val_probs = debias_cyclic_permutations(permuted_runs, baseline_logprobs=baseline_logprobs)
            mapped = {k: float(val_probs.get(valid_opts[k].strip(), 0.0)) for k in MCQ_LETTERS}
            m_sum = sum(mapped.values())
            if m_sum > 0.0:
                cal_override = {k: round(mapped[k] / m_sum, 6) for k in MCQ_LETTERS}

    scoring = score_mcq_options(
        statement=norm_text,
        options=norm_opts,
        problem_id=pid,
        level=lvl,
        qnum=qn,
        raw_logprobs=raw_logprobs or None,
        baseline_logprobs=baseline_logprobs,
        priors=active_priors,
        knn_votes=knn_votes,
        oracle=oracle,
        use_calibration=True,
        use_prior=True,
        calibrated_probs_override=cal_override,
        is_visual_problem=is_visual_problem,
    )
    ans = scoring["predicted_option"]
    source = scoring["source"]

    return {
        "problem_id": pid,
        "language": language,
        "answer": ans,
        "predicted_option": ans,
        "status": status,
        "source": source,
        "combined_probs": scoring["combined_probs"],
        "prior_probs": scoring["prior_probs"],
        "calibrated_probs": scoring["calibrated_probs"],
        "raw_logprobs": raw_logprobs,
        "feature_scores": scoring["feature_scores"],
        "prompt_tokens": prompt_tokens,
        "timings": timings,
        "permutations_used": perm_count,
        "normalized_text": norm_text,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "seconds": round(time.perf_counter() - t0, 4),
        "offline_mode": True,
    }


def warm_prompt_cache(
    statement: str,
    options: Optional[dict[str, str]] = None,
    model_id: Optional[str] = None,
) -> dict[str, Any]:
    """Pre-warm llama-server :8081 KV cache with cache_prompt=true (deterministic, offline-safe)."""
    msgs = build_mcq_logprob_messages(statement, options or {})
    data = _call_llama_completion(
        messages=msgs,
        model_id=model_id or os.environ.get("SLM_MODEL_ID", DEFAULT_MODEL_ID),
        max_tokens=1,
        stop=[],
        temperature=DEFAULT_TEMPERATURE,
        seed=DEFAULT_SEED,
        cache_prompt=True,
        logprobs=True,
        top_logprobs=5,
        grammar="root ::= [A-E]",
    )
    if not data:
        return {"warmed": False, "cache_prompt": True, "logprobs": {}, "timings": {}}
    timings = data.get("timings") or {}
    return {
        "warmed": True,
        "cache_prompt": True,
        "logprobs": extract_letter_logprobs(data),
        "timings": {
            "prompt_n": int(timings.get("prompt_n") or 0),
            "predicted_per_second": float(timings.get("predicted_per_second") or 0.0),
        },
    }


def _resolve_problem_for_workbench(
    problem: str | dict[str, Any],
    language: str = "eng",
) -> dict[str, Any]:
    if isinstance(problem, str):
        base = get_problem(problem)
        if base is not None:
            resolved = bridge_cat_to_eng(base) or dict(base)
        else:
            resolved = match_concept_guide(problem, None)
    else:
        resolved = bridge_cat_to_eng(problem) or dict(problem)
    resolved.setdefault("problem_id", "CUSTOM-01")
    resolved.setdefault("category", "Algebra")
    resolved.setdefault("difficulty_level", 2)
    resolved.setdefault("options", {})
    resolved.setdefault("statement", "")
    resolved.setdefault("intuition_hook", "Isolate the core relationship or invariant first.")
    resolved.setdefault("socratic_seed", "What small case or fixed quantity can you test first?")
    resolved.setdefault("default_move", "DECOMPOSE_PROBLEM")
    resolved.setdefault("default_lcd_summary", "Break into smaller steps")
    resolved.setdefault("option_misconceptions", {})
    resolved["requested_language"] = language
    return resolved


def get_hint_rung(
    problem: str | dict[str, Any],
    rung: int,
    confirm_solution: bool = False,
    selected_option: Optional[str] = None,
    confidence_pct: int = 50,
    attempt_num: int = 1,
    student_note: Optional[str] = None,
    allow_model: bool = False,
) -> dict[str, Any]:
    """Evaluate a single rung (1..4) of the M3Kang 4-Rung Hint Ladder."""
    t0 = time.perf_counter()
    resolved = _resolve_problem_for_workbench(problem)
    pid = str(resolved["problem_id"])
    oracle = extract_math_oracle(resolved.get("statement", ""))
    rung_num = max(1, min(4, int(rung)))

    if rung_num == 1:
        neighbor = get_easier_neighbor(resolved)
        if neighbor:
            stmt_preview = neighbor.get("statement_cat") or neighbor["statement"]
            hook_preview = neighbor["intuition_hook"].rstrip(".!?")
            hint_text = clamp_text(
                f"Try this easier level-{neighbor['difficulty_level']} problem ({neighbor['problem_id']}): "
                f"{stmt_preview} How does '{hook_preview}' carry over here?",
                240,
            )
        else:
            hint_text = clamp_text(
                f"This is already a Tier-{resolved['difficulty_level']} entry problem—what smallest concrete case can you test first?",
                240,
            )
        return {
            "rung": 1,
            "title": "Easier Solved Neighbor",
            "neighbor": neighbor,
            "hint_text": hint_text,
            "is_safe": is_safe_hint(hint_text, resolved, False, oracle=oracle),
            "gated": False,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }

    if rung_num == 2:
        topic_info = infer_topic_from_neighbors(resolved, k=5)
        hint_text = clamp_text(
            f"Top-{topic_info['k']} neighbor vote: {topic_info['topic']} ({topic_info['strategy_label']}). "
            f"What {topic_info['topic'].lower()} invariant or constraint applies here?",
            240,
        )
        return {
            "rung": 2,
            "title": "Top-k Topic & Strategy Vote",
            "topic_inference": topic_info,
            "hint_text": hint_text,
            "is_safe": is_safe_hint(hint_text, resolved, False, oracle=oracle),
            "gated": False,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }

    if rung_num == 3:
        if allow_model and get_problem(pid) is not None:
            turn = generate_socratic_turn(
                problem_id=pid,
                event_type="ANSWER_SUBMIT" if selected_option else "HINT_REQUEST",
                attempt_num=attempt_num,
                confidence_pct=confidence_pct,
                selected_option=selected_option,
                student_note=student_note,
                mode="teach",
            )
            hint_text = turn["hint_text"]
            move = turn["pedagogical_move"]
            dim = turn["socratic_dimension"]
            model_id = turn["model_id"]
        else:
            hint_text = _fallback_hint(
                resolved,
                "HINT_REQUEST",
                attempt_num,
                confidence_pct,
                None,
                selected_option,
                None,
                oracle=oracle,
            )
            move = resolved.get("default_move", "DECOMPOSE_PROBLEM")
            dim = SOCRATIC_DIMENSIONS[move]
            model_id = f"{DEFAULT_MODEL_ID}-offline" if not allow_model else f"{DEFAULT_MODEL_ID}-fallback"

        safe = is_safe_hint(hint_text, resolved, False, oracle=oracle)
        return {
            "rung": 3,
            "title": "Socratic Model Hint (Zero-Leak)",
            "hint_text": hint_text,
            "pedagogical_move": move,
            "socratic_dimension": dim,
            "model_id": model_id,
            "is_safe": safe,
            "gated": False,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }

    # Rung 4: Gated full solution
    if not confirm_solution:
        return {
            "rung": 4,
            "title": "Full Solution (Gated)",
            "gated": True,
            "requires_confirmation": True,
            "hint_text": "Explicit confirmation required before revealing the full solution.",
            "solution_text": None,
            "final_answer": None,
            "is_safe": True,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }

    if allow_model and get_problem(pid) is not None:
        solve_turn = generate_socratic_turn(
            problem_id=pid,
            event_type="HINT_REQUEST",
            attempt_num=attempt_num,
            confidence_pct=confidence_pct,
            selected_option=selected_option,
            student_note=student_note,
            mode="solve",
        )
        sol_text = solve_turn.get("solution_text")
        final_ans = solve_turn.get("final_answer")
        model_id = solve_turn["model_id"]
    else:
        rag = retrieve_similar_problems(resolved.get("statement", ""), exclude_id=pid, top_k=1)
        sol_text, final_ans, _ = _build_deterministic_solution(resolved, oracle, rag)
        model_id = f"{DEFAULT_MODEL_ID}-oracle" if final_ans else f"{DEFAULT_MODEL_ID}-offline"

    return {
        "rung": 4,
        "title": "Full Solution (Unlocked)",
        "gated": False,
        "requires_confirmation": False,
        "hint_text": clamp_text(f"Final Answer: {final_ans}" if final_ans else (sol_text or ""), 240),
        "solution_text": sol_text,
        "final_answer": final_ans,
        "model_id": model_id,
        "is_safe": True,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


def inspect_problem(
    problem: str | dict[str, Any],
    use_calibration: bool = True,
    use_prior: bool = True,
    language: str = "eng",
    raw_logprobs: Optional[dict[str, float]] = None,
    baseline_logprobs: Optional[dict[str, float]] = None,
    confirm_solution: bool = False,
    allow_model: bool = False,
) -> dict[str, Any]:
    """Return full Explorer/Workbench diagnostics: prompt, raw vs calibrated A-E, priors, features, Rungs 1-4, leak status."""
    t0 = time.perf_counter()
    resolved = _resolve_problem_for_workbench(problem, language=language)
    pid = str(resolved["problem_id"])
    statement = str(resolved.get("statement") or "")
    options = resolved.get("options") or {}
    lvl, qn = parse_level_qnum(
        pid,
        level=(
            int(resolved["difficulty_level"])
            if not resolved.get("is_open_prompt") and resolved.get("difficulty_level") is not None
            else None
        ),
    )
    prompt_messages = build_mcq_logprob_messages(statement, options)
    prompt_text = "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in prompt_messages)

    timings: dict[str, Any] = {"prompt_n": 0, "predicted_per_second": 0.0}
    logprobs_source = "provided"
    active_raw_logprobs = dict(raw_logprobs) if raw_logprobs is not None else {}

    if raw_logprobs is None and options:
        if allow_model and os.environ.get("SOCRATIC_MOCK", "0") != "1":
            live_data = _call_llama_completion(
                messages=prompt_messages,
                model_id=os.environ.get("SLM_MODEL_ID", DEFAULT_MODEL_ID),
                max_tokens=1,
                stop=[],
                temperature=DEFAULT_TEMPERATURE,
                seed=DEFAULT_SEED,
                cache_prompt=True,
                logprobs=True,
                top_logprobs=5,
                grammar="root ::= [A-E]",
            )
            if live_data:
                extracted = extract_letter_logprobs(live_data)
                if extracted:
                    active_raw_logprobs = extracted
                    logprobs_source = "llama-server"
                raw_timings = live_data.get("timings") or {}
                timings = {
                    "prompt_n": int(raw_timings.get("prompt_n") or 0),
                    "predicted_per_second": round(float(raw_timings.get("predicted_per_second") or 0.0), 2),
                }
        if not active_raw_logprobs:
            active_raw_logprobs = dict(DEFAULT_BASELINE_LOGPROBS)
            logprobs_source = "offline-baseline"
    elif not options:
        active_raw_logprobs = {}
        logprobs_source = "none-empty-options"

    oracle = extract_math_oracle(statement)
    scoring = score_mcq_options(
        statement=statement,
        options=options,
        problem_id=pid,
        level=lvl,
        qnum=qn,
        raw_logprobs=active_raw_logprobs or None,
        baseline_logprobs=baseline_logprobs,
        oracle=oracle,
        use_calibration=use_calibration,
        use_prior=use_prior,
    )
    raw_scores = softmax_scores(active_raw_logprobs) if active_raw_logprobs else {k: 0.0 for k in MCQ_LETTERS}
    calibrated_scores = scoring["calibrated_probs"] or raw_scores

    rungs = {
        r: get_hint_rung(resolved, r, confirm_solution=confirm_solution, allow_model=allow_model)
        for r in (1, 2, 3, 4)
    }
    rung3_safe = bool(rungs[3]["is_safe"])

    return {
        "problem_id": pid,
        "category": resolved["category"],
        "difficulty_level": int(resolved.get("difficulty_level", lvl)),
        "level": int(resolved.get("raw_level", resolved.get("level", lvl))),
        "qnum": qn,
        "language": language,
        "bridged": bool(resolved.get("bridged", False)),
        "statement": statement,
        "statement_cat": resolved.get("statement_cat"),
        "options": options,
        "options_cat": resolved.get("options_cat"),
        "correct_option": resolved.get("correct_option"),
        "prompt": prompt_text,
        "prompt_messages": prompt_messages,
        "use_calibration": use_calibration,
        "use_prior": use_prior,
        "logprobs_source": logprobs_source,
        "raw_logprobs": active_raw_logprobs,
        "raw_scores": raw_scores,
        "calibrated_scores": calibrated_scores,
        "prior_scores": scoring["prior_probs"],
        "combined_scores": scoring["combined_probs"],
        "predicted_option": scoring["predicted_option"],
        "prediction_source": scoring["source"],
        "feature_breakdown": scoring["features"],
        "feature_scores": scoring["feature_scores"],
        "rungs": rungs,
        "is_safe_hint": rung3_safe,
        "leak_status": "SAFE" if rung3_safe else "LEAK_DETECTED",
        "timings": timings,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "offline_mode": True,
    }


def solve_exam_problem(
    statement: str,
    options: Optional[dict[str, str]] = None,
    problem_id: str = "EXAM-01",
) -> dict[str, Any]:
    """Objective Reasoning Mode entrypoint (predict.py-compatible) for the 50% exam benchmark."""
    t0 = time.perf_counter()
    bank_prob = get_problem(problem_id)
    if bank_prob is not None:
        problem = bank_prob
    else:
        problem = match_concept_guide(problem_id, statement)
        problem["statement"] = statement.strip()
        problem["options"] = options or {}

    rag_examples = retrieve_similar_problems(statement, exclude_id=problem_id, top_k=1)
    rag_example_id = rag_examples[0]["problem_id"] if rag_examples else None
    oracle = extract_math_oracle(statement)

    solution_text, final_answer, predicted_option = _build_deterministic_solution(
        problem, oracle, rag_examples
    )

    use_mock = os.environ.get("SOCRATIC_MOCK", "0") == "1"
    model_id = os.environ.get("SLM_MODEL_ID", DEFAULT_MODEL_ID)
    used_slm = False
    raw_logprobs: dict[str, float] = {}
    active_opts = problem.get("options") or {}

    # Do not invoke LLM when options are explicitly empty (save ~9s, route to (level, qnum) prior).
    if not use_mock and active_opts:
        solve_msgs = build_solve_messages(problem, student_note=None, rag_examples=rag_examples)
        slm_sol = _call_llama_server(
            solve_msgs,
            model_id,
            max_tokens=160,
            extract_question=False,
            stop=["Student:"],
            temperature=DEFAULT_TEMPERATURE,
            seed=DEFAULT_SEED,
            cache_prompt=True,
        )
        if slm_sol:
            solution_text = slm_sol
            used_slm = True
            ext_ans, ext_opt = _extract_final_answer_from_text(slm_sol, active_opts)
            if final_answer is None:
                final_answer = ext_ans
            if predicted_option is None:
                predicted_option = ext_opt

    mcq_scoring: Optional[dict[str, Any]] = None
    if predicted_option is None and (active_opts or options is not None):
        mcq_scoring = score_mcq_options(
            statement=problem.get("statement", statement),
            options=active_opts,
            problem_id=problem["problem_id"],
            level=bank_prob.get("difficulty_level") if bank_prob is not None else None,
            raw_logprobs=raw_logprobs or None,
            oracle=oracle,
        )
        predicted_option = mcq_scoring["predicted_option"]

    if not used_slm:
        model_id = "mock-solve-v1" if use_mock else (f"{model_id}-oracle" if final_answer else f"{model_id}-fallback")

    return {
        "problem_id": problem["problem_id"],
        "category": problem["category"],
        "predicted_option": predicted_option,
        "final_answer": final_answer,
        "solution_text": solution_text,
        "rag_example_id": rag_example_id,
        "oracle_kind": oracle.get("kind"),
        "model_id": model_id,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "offline_mode": True,
    }


def _run_eval() -> None:
    pids = list_problem_ids()
    latencies: list[int] = []
    leaks = 0
    total = 0
    slm_hits = 0

    for pid in pids:
        problem = get_problem(pid)
        assert problem is not None
        wrong_opt = next(k for k in ("A", "B", "C", "D", "E") if k != problem["correct_option"])
        cases = [
            ("PROBLEM_SELECT", 1, 50, None, None),
            ("HINT_REQUEST", 1, 35, None, None),
            ("ANSWER_SUBMIT", 2, 80, wrong_opt, None),
            ("ANSWER_SUBMIT", 2, 35, wrong_opt, "Try checking parity or a tiny case?"),
            ("ANSWER_SUBMIT", 3, 90, problem["correct_option"], None),
        ]
        for ev, att, conf, opt, nudge in cases:
            resp = generate_socratic_hint(pid, ev, att, conf, selected_option=opt, agent_override_nudge=nudge)
            validate_socratic_response(resp)
            latencies.append(resp["latency_ms"])
            total += 1
            if not resp["model_id"].endswith("-fallback") and resp["model_id"] != "mock-socratic-v1":
                slm_hits += 1
            if resp["is_correct"] is not True and not is_safe_hint(resp["hint_text"], problem, resp["is_correct"]):
                leaks += 1

    # Also verify open/custom concept prompt fallback.
    open_resp = generate_socratic_hint(
        "CUSTOM-PARITY-01", "HINT_REQUEST", 1, 40, student_note="Why does chessboard parity work on grids?"
    )
    validate_socratic_response(open_resp)
    total += 1

    p50 = int(statistics.median(latencies))
    p95 = sorted(latencies)[max(0, int(0.95 * len(latencies)) - 1)]
    print(f"EVAL OK: cases={total} slm_hits={slm_hits} leaks={leaks} p50={p50}ms p95={p95}ms")
    if leaks > 0:
        raise SystemExit(1)


def _run_cli(initial_problem: str) -> None:
    problem_id = initial_problem
    attempt_num = 1
    confidence = 50
    last_turn_context = ""

    print(f"SocraticQ CLI | Problem: {problem_id} | Commands: list, problem <id>, conf <0-100>, hint, submit <A-E>, quit")
    prob = get_problem(problem_id)
    if prob:
        print(f"\n[{prob['category']} | Tier {prob['difficulty_level']}] {prob['statement']}")
        for k, v in prob["options"].items():
            print(f"  {k}) {v}")

    while True:
        try:
            raw = input("\nstudent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not raw:
            continue
        if raw in {"quit", "exit"}:
            break
        if raw == "list":
            print("Problems:", ", ".join(list_problem_ids()))
            continue
        if raw.startswith("problem "):
            problem_id = raw.split(" ", 1)[1].strip()
            attempt_num = 1
            last_turn_context = ""
            resp = generate_socratic_hint(problem_id, "PROBLEM_SELECT", attempt_num, confidence)
            prob = get_problem(problem_id)
            if prob:
                print(f"\n[{prob['category']}] {prob['statement']}")
                for k, v in prob["options"].items():
                    print(f"  {k}) {v}")
            print(f"tutor [{resp['pedagogical_move']}]: {resp['hint_text']}")
            continue
        if raw.startswith("conf "):
            confidence = max(0, min(100, int(raw.split(" ", 1)[1])))
            print(f"Confidence dial set to {confidence}%")
            continue
        if raw == "hint":
            resp = generate_socratic_hint(problem_id, "HINT_REQUEST", attempt_num, confidence, student_note=last_turn_context or None)
            attempt_num += 1
        elif raw.startswith("submit "):
            opt = raw.split(" ", 1)[1].strip().upper()
            resp = generate_socratic_hint(
                problem_id, "ANSWER_SUBMIT", attempt_num, confidence, selected_option=opt, student_note=last_turn_context or None
            )
            attempt_num += 1
        else:
            note = f"{last_turn_context} | {raw}".strip(" |") if last_turn_context else raw
            resp = generate_socratic_hint(problem_id, "HINT_REQUEST", attempt_num, confidence, student_note=note[:200])
            attempt_num += 1

        last_turn_context = f"Prev tutor: {resp['hint_text'][:90]}"
        print(f"tutor [{resp['pedagogical_move']} | {resp['latency_ms']}ms]: {resp['hint_text']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="SocraticQ offline engine CLI & evaluator")
    parser.add_argument("--mock", action="store_true", help="Force deterministic fallback without llama-server")
    parser.add_argument("--eval", action="store_true", help="Run contract and zero-leak evaluation across all problems")
    parser.add_argument("--problem", default="CAM-2023-N4-17", help="Starting Cangur problem ID")
    args = parser.parse_args()

    if args.mock:
        os.environ["SOCRATIC_MOCK"] = "1"
    if args.eval:
        _run_eval()
        return
    _run_cli(args.problem)


if __name__ == "__main__":
    main()
