"""Offline MCQ Optimizer for Kangaroo Math on QRB2210 (Qwen3.5-0.8B + bge-small-en-v1.5).

Implements a strict <=55s neuro-symbolic + KV-cached PoT + CoT + clean-logprob cascade:
1. Empty-options fast prior (<1 ms)
2. Offline Catalan -> English mathematical normalization
3. Sound neuro-symbolic solvers verified against A-E options (<5 ms)
4. Visual problems (text_only=False): clean 1-pass logprobs + (level, qnum) priors (~10 s)
5. Text-only problems: static-prefix KV-cached 2-shot Python PoT with option verification
   -> concise CoT fallback -> clean 1-pass logprob + prior fallback
"""

from __future__ import annotations

import ast
import collections
import fractions
import itertools
import json
import math
import os
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

MCQ_LETTERS: tuple[str, ...] = ("A", "B", "C", "D", "E")
DEFAULT_LLM_URL = os.environ.get("SLM_LLM_URL", "http://127.0.0.1:8081/v1/chat/completions")
DEFAULT_MODEL_ID = os.environ.get("SLM_MODEL_ID", "qwen3.5-0.8b")

# ---------------------------------------------------------------------------
# Offline Catalan -> English mathematical normalizer
# ---------------------------------------------------------------------------

_CAT_TO_ENG_PHRASES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pat, re.IGNORECASE), repl)
    for pat, repl in (
        (r"\btots els .*? excepte\b", "all except"),
        (r"\bexcepte\b", "except"),
        (r"\bde quantes maneres\b", "in how many ways"),
        (r"\bquants nombres de dues xifres\b", "how many 2-digit numbers"),
        (r"\bquants nombres de tres xifres\b", "how many 3-digit numbers"),
        (r"\bnombre de dues xifres\b", "2-digit number"),
        (r"\bnombre de tres xifres\b", "3-digit number"),
        (r"\bsuma de les xifres\b", "sum of the digits"),
        (r"\bproducte de les xifres\b", "product of the digits"),
        (r"\búltima xifra\b|\bdarrera xifra\b", "last digit"),
        (r"\bla mateixa quantitat\b", "the same amount"),
        (r"\bha de donar\b|\bha d'entregar\b", "must give"),
        (r"\bmés gran que\b", "greater than"),
        (r"\bmés petit que\b|\bmenor que\b", "less than"),
        (r"\bmés gran\b", "largest"),
        (r"\bmés petit\b", "smallest"),
        (r"\bigual a\b", "equal to"),
        (r"\bmés que\b", "more than"),
        (r"\bmenys que\b", "less than"),
        (r"\bel doble de\b|\bdoble\b", "twice"),
        (r"\bel triple de\b|\btriple\b", "three times"),
        (r"\bla meitat de\b|\bmeitat\b", "half"),
        (r"\bal quadrat\b", "squared"),
        (r"\bal cub\b", "cubed"),
        (r"\bdivisible per\b", "divisible by"),
        (r"\bdividit per\b|\bentre\b", "divided by"),
        (r"\bmúltiple de\b", "multiple of"),
        (r"\bnombre primer\b|\bnombres primers\b", "prime number"),
        (r"\bnombre parell\b|\bnombres parells\b", "even number"),
        (r"\bnombre senar\b|\bnombres senars\b", "odd number"),
        (r"\bquants\b|\bquantes\b", "how many"),
        (r"\bquant val\b|\bquina és el valor\b|\bquin és el valor\b", "what is the value of"),
        (r"\bquina és la suma\b|\bquin és el\b|\bquina és\b|\bquin és\b", "what is"),
        (r"\bquants anys\b", "how many years"),
        (r"\bedat\b", "age"),
        (r"\bperímetre\b", "perimeter"),
        (r"\bàrea\b|\bsuperfície\b", "area"),
        (r"\bvolum\b", "volume"),
        (r"\btriangle equilàter\b", "equilateral triangle"),
        (r"\btriangle rectangle\b", "right triangle"),
        (r"\btriangle isòsceles\b", "isosceles triangle"),
        (r"\btriangle\b", "triangle"),
        (r"\bquadrat\b", "square"),
        (r"\brectangle\b", "rectangle"),
        (r"\bcercle\b", "circle"),
        (r"\bcostat\b|\bcostats\b", "side"),
        (r"\bangle\b|\bangles\b", "angle"),
        (r"\bdiagonal\b|\bdiagonals\b", "diagonal"),
        (r"\bvèrtex\b|\bvèrtexs\b", "vertex"),
        (r"\bmitjana\b", "average"),
        (r"\bresidu\b|\bresta\b", "remainder"),
        (r"\bquocient\b", "quotient"),
        (r"\bproducte\b", "product"),
        (r"\bsuma\b", "sum"),
        (r"\bdiferència\b", "difference"),
        (r"\bfracció\b", "fraction"),
        (r"\bpercentatge\b", "percentage"),
        (r"\bcada dia\b", "each day"),
        (r"\bcada hora\b", "each hour"),
        (r"\bcada\b", "each"),
        (r"\bhavien\b|\bté\b|\btenen\b", "has"),
        (r"\bqueden\b|\bquedaran\b", "remain"),
        (r"\bcromos\b", "stickers"),
        (r"\bcaramels\b", "candies"),
        (r"\bpomes\b", "apples"),
        (r"\bmonedes\b", "coins"),
        (r"\bdaus\b", "dice"),
        (r"\bboles\b|\bpilotes\b", "balls"),
        (r"\bcartes\b", "cards"),
        (r"\bfitxes\b", "tiles"),
        (r"\balumnes\b|\bestudiants\b", "students"),
        (r"\bnens\b", "boys"),
        (r"\bnoies\b", "girls"),
        (r"\bgats\b", "cats"),
        (r"\bgossos\b", "dogs"),
        (r"\bhàmsters\b", "hamsters"),
        (r"\bjoguines\b", "toys"),
        (r"\bprestatge\b", "shelf"),
        (r"\btauler\b", "board"),
        (r"\bfila\b|\bfiles\b", "row"),
        (r"\bcolumna\b|\bcolumnes\b", "column"),
        (r"\bcangur\b", "kangaroo"),
        (r"\bxifra\b|\bxifres\b", "digit"),
        (r"\bnombre\b|\bnombres\b", "number"),
        (r"\bhores\b", "hours"),
        (r"\bminuts\b", "minutes"),
        (r"\bsegons\b", "seconds"),
        (r"\bdies\b", "days"),
        (r"\bsetmanes\b", "weeks"),
        (r"\bmesos\b", "months"),
        (r"\banys\b", "years"),
        (r"\bavui\b", "today"),
        (r"\bdemà\b", "tomorrow"),
        (r"\bahir\b", "yesterday"),
        (r"\bprimer\b|\bprimera\b", "first"),
        (r"\bsegon\b|\bsegona\b", "second"),
        (r"\btercer\b|\btercera\b", "third"),
        (r"\búltim\b|\búltima\b|\bdarrer\b|\bdarrera\b", "last"),
        (r"\bsi\b", "if"),
        (r"\bllavors\b", "then"),
        (r"\btots\b|\btotes\b", "all"),
        (r"\bamb\b", "with"),
        (r"\bsense\b", "without"),
        (r"\bperquè\b", "so that"),
        (r"\bquant\b", "how much"),
        (r"\bdues\b|\bdos\b", "two"),
        (r"\btres\b", "three"),
        (r"\bquatre\b", "four"),
        (r"\bcinc\b", "five"),
        (r"\bsis\b", "six"),
        (r"\bset\b", "seven"),
        (r"\bvuit\b", "eight"),
        (r"\bnou\b", "nine"),
        (r"\bdeu\b", "ten"),
        (r"\bconsecutius\b|\bconsecutives\b", "consecutive"),
        (r"\bpotència\b", "power"),
        (r"\bproporció\b|\braó\b", "ratio"),
        (r"\bsetmana\b", "week"),
        (r"\brellotge\b", "clock"),
    )
)


def clean_problem_text(text: str) -> str:
    """Strip leading question number (e.g. '11. ') and collapse extra whitespace."""
    s = str(text or "").strip()
    s = re.sub(r"^\s*\d{1,2}\.\s+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def translate_cat_to_eng_math(text: str, language: Optional[str] = "cat") -> str:
    """Offline Catalan -> English math gloss for embeddings and Qwen3.5-0.8B."""
    cleaned = clean_problem_text(text)
    if not cleaned:
        return cleaned
    lang = str(language or "").strip().lower()
    if lang and lang not in {"cat", "ca", "catalan"}:
        return cleaned
    out = cleaned
    for pat, repl in _CAT_TO_ENG_PHRASES:
        out = pat.sub(repl, out)
    return re.sub(r"\s+", " ", out).strip()


# ---------------------------------------------------------------------------
# Numeric & option matching helpers
# ---------------------------------------------------------------------------

def parse_numeric_value(text: str) -> Optional[float]:
    """Parse integers, decimals, percentages, simple fractions, and LaTeX fractions."""
    s = str(text or "").strip().replace("−", "-").replace(",", ".")
    if not s:
        return None
    s = re.sub(r"^[€$]\s*", "", s).strip()
    if re.search(r"\d", s):
        s = re.sub(
            r"(?i)(?<=\d|\s)(?:cm2|cm²|cm3|cm³|cm|m2|m²|m3|m³|km|mm|dm|kg|mg|ml|hrs|hr|min|euros|eur|deg|°|€|\$|g|m|l|h|s)\s*$",
            "",
            s,
        ).strip()
    latex_frac = re.fullmatch(r"([-+]?)\s*\\d?frac\{\s*([-+]?\d+(?:\.\d+)?)\s*\}\{\s*(\d+(?:\.\d+)?)\s*\}", s)
    if latex_frac:
        den = float(latex_frac.group(3))
        if den != 0.0:
            sign = -1.0 if latex_frac.group(1) == "-" else 1.0
            return sign * float(latex_frac.group(2)) / den
    pct_m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*%", s)
    if pct_m:
        return float(pct_m.group(1)) / 100.0
    frac_m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if frac_m and float(frac_m.group(2)) != 0.0:
        return float(frac_m.group(1)) / float(frac_m.group(2))
    num_m = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", s)
    if num_m:
        return float(s)
    return None


def match_value_to_unique_option(val: Any, options: dict[str, str]) -> Optional[str]:
    """Match a computed value (letter, int, float, Fraction, or string) to a unique A-E option."""
    if val is None or not options:
        return None

    # Direct letter match if val is literally 'A'..'E'
    if isinstance(val, str):
        sval = val.strip()
        if sval.upper() in MCQ_LETTERS and sval.upper() in options:
            return sval.upper()
        # Exact text match (case-insensitive)
        text_hits = [k for k, v in options.items() if str(v).strip().lower() == sval.lower()]
        if len(text_hits) == 1:
            return text_hits[0]

    # Numeric comparison
    if isinstance(val, fractions.Fraction):
        target_num: Optional[float] = float(val)
    elif isinstance(val, (int, float)) and not isinstance(val, bool):
        if math.isnan(val) or math.isinf(val):
            return None
        target_num = float(val)
    else:
        target_num = parse_numeric_value(str(val))

    if target_num is not None:
        num_hits: list[str] = []
        for k, opt_str in options.items():
            ov = parse_numeric_value(opt_str)
            if ov is not None and abs(ov - target_num) <= 1e-6 * max(1.0, abs(target_num)):
                num_hits.append(k)
        if len(num_hits) == 1:
            return num_hits[0]

    return None


def _fallback_normalize_options(options: dict[str, str]) -> dict[str, str]:
    return {k: str(v).strip() for k, v in options.items()}


try:
    from engine.option_normalizer import (
        match_value_to_unique_option,
        normalize_options,
        parse_numeric_value,
        translate_cat_to_eng_math,
    )
except ImportError:
    normalize_options = _fallback_normalize_options


# ---------------------------------------------------------------------------
# Sound Neuro-Symbolic Solvers (general Kangaroo math, zero demo overfitting)
# ---------------------------------------------------------------------------

def _safe_eval_arith(expr: str) -> Optional[float]:
    """Safely evaluate a pure arithmetic expression via AST."""
    cleaned = expr.strip().replace("×", "*").replace("·", "*").replace("÷", "/").replace("^", "**")
    cleaned = cleaned.replace("−", "-").replace(",", ".")
    cleaned = re.sub(r"\\d?frac\{([^{}]+)\}\{([^{}]+)\}", r"((\1)/(\2))", cleaned)
    if not re.fullmatch(r"[0-9+\-*/().\s*]+", cleaned):
        return None
    try:
        node = ast.parse(cleaned, mode="eval")
    except SyntaxError:
        return None

    def _eval_node(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return _eval_node(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = _eval_node(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp):
            l = _eval_node(n.left)
            r = _eval_node(n.right)
            if isinstance(n.op, ast.Add):
                return l + r
            if isinstance(n.op, ast.Sub):
                return l - r
            if isinstance(n.op, ast.Mult):
                return l * r
            if isinstance(n.op, ast.Div):
                if abs(r) < 1e-12:
                    raise ZeroDivisionError
                return l / r
            if isinstance(n.op, ast.Pow):
                if abs(r) > 20:
                    raise ValueError("pow too large")
                return l ** r
        raise ValueError("Unsupported AST node")

    try:
        res = _eval_node(node)
        if math.isnan(res) or math.isinf(res):
            return None
        return res
    except Exception:
        return None


def _fallback_solve_neuro_symbolic(
    statement: str, options: dict[str, str], raw_statement: str = ""
) -> Optional[tuple[str, str]]:
    """Run general, sound math solvers and return (letter, solver_name) if uniquely matched."""
    if not statement or not options:
        return None
    s = clean_problem_text(statement)
    sl = s.lower()

    # 1. Pure arithmetic evaluation: "What is the value of <expr>?" or "<expr> = ?"
    m = re.search(r"(?:what is the value of|what is|calculate|evaluate|compute)\s+([0-9+\-*/×·÷^().\s\\{}frac]+)\??$", sl)
    if m and any(op in m.group(1) for op in "+-*/×·÷^"):
        val = _safe_eval_arith(m.group(1))
        opt = match_value_to_unique_option(val, options)
        if opt:
            return opt, "arith_eval"

    # 2. Equalization transfer: "A has X ... B has Y ... must give ... same amount"
    if ("same amount" in sl or "same number" in sl or "equal number" in sl) and "must give" in sl:
        nums = [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", sl)]
        if len(nums) == 2 and abs(nums[0] - nums[1]) > 0:
            diff = abs(nums[0] - nums[1])
            if diff % 2 == 0:
                opt = match_value_to_unique_option(diff / 2.0, options)
                if opt:
                    return opt, "equalize_transfer"

    # 3. Except-N pets/items: "All ... except A are X, except B are Y, except C are Z"
    except_nums = re.findall(r"except\s+(?:for\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", sl)
    if len(except_nums) >= 3 and ("total" in sl or "how many" in sl):
        word_map = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        vals = [int(x) if x.isdigit() else word_map[x] for x in except_nums]
        k = len(vals)
        total_s = sum(vals)
        if total_s % (k - 1) == 0:
            total = total_s // (k - 1)
            if all(total >= v for v in vals):
                opt = match_value_to_unique_option(total, options)
                if opt:
                    return opt, "except_n_total"

    # 4. Last digit / units digit of power: "last/units digit of a^b"
    m = re.search(r"(?:last|units) digit of\s+(\d+)\s*\^\s*(\d+)", sl)
    if m:
        base, exp = int(m.group(1)), int(m.group(2))
        opt = match_value_to_unique_option(pow(base, exp, 10), options)
        if opt:
            return opt, "last_digit_pow"

    # 5. Sum & difference / Sum & product of two numbers
    m = re.search(r"sum of two numbers is\s+(-?\d+)\s+and\s+(?:their\s+)?difference is\s+(\d+)", sl)
    if m:
        S, D = int(m.group(1)), int(m.group(2))
        if (S + D) % 2 == 0:
            a, b = (S + D) // 2, (S - D) // 2
            if "product" in sl:
                opt = match_value_to_unique_option(a * b, options)
            elif "smaller" in sl or "smallest" in sl:
                opt = match_value_to_unique_option(min(a, b), options)
            elif "larger" in sl or "greater" in sl or "largest" in sl:
                opt = match_value_to_unique_option(max(a, b), options)
            else:
                opt = None
            if opt:
                return opt, "sum_diff"

    m = re.search(r"sum of two numbers is\s+(-?\d+)\s+and\s+(?:their\s+)?product is\s+(-?\d+)", sl)
    if m:
        S, P = int(m.group(1)), int(m.group(2))
        disc = S * S - 4 * P
        if disc >= 0:
            rt = math.isqrt(disc)
            if rt * rt == disc and (S + rt) % 2 == 0:
                a, b = (S + rt) // 2, (S - rt) // 2
                if "difference" in sl:
                    opt = match_value_to_unique_option(abs(a - b), options)
                elif "smaller" in sl or "smallest" in sl:
                    opt = match_value_to_unique_option(min(a, b), options)
                elif "larger" in sl or "greater" in sl or "largest" in sl:
                    opt = match_value_to_unique_option(max(a, b), options)
                else:
                    opt = None
                if opt:
                    return opt, "sum_prod"

    # 6. Square perimeter <-> area
    m = re.search(r"square has (?:a )?perimeter (?:of )?(\d+(?:\.\d+)?)\s*(?:cm|m|mm)?\b.*?\barea\b", sl)
    if m:
        p = float(m.group(1))
        opt = match_value_to_unique_option((p / 4.0) ** 2, options)
        if opt:
            return opt, "square_perim_to_area"

    m = re.search(r"square has (?:an )?area (?:of )?(\d+(?:\.\d+)?)\s*(?:cm|m|mm)?[²2]?\b.*?\bperimeter\b", sl)
    if m:
        a = float(m.group(1))
        if a >= 0:
            opt = match_value_to_unique_option(4.0 * math.sqrt(a), options)
            if opt:
                return opt, "square_area_to_perim"

    # 7. Digit sum counting: "how many 2-digit numbers ... sum of (the) digits (is|equal to) S"
    m = re.search(r"how many (2|two|3|three)-digit numbers.*?sum of (?:the |their )?digits (?:is |equal to )?(\d+)", sl)
    if m:
        nd = 2 if m.group(1) in ("2", "two") else 3
        target_s = int(m.group(2))
        lo, hi = (10, 100) if nd == 2 else (100, 1000)
        cnt = sum(1 for n in range(lo, hi) if sum(int(d) for d in str(n)) == target_s)
        opt = match_value_to_unique_option(cnt, options)
        if opt:
            return opt, "digit_sum_count"

    # 8. Consecutive integers sum: "sum of K consecutive (positive )?integers is S"
    m = re.search(r"sum of (\d+|two|three|four|five|six|seven) consecutive (?:positive )?(?:integers|numbers) is (-?\d+)", sl)
    if m:
        wmap = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
        k = int(m.group(1)) if m.group(1).isdigit() else wmap[m.group(1)]
        S = int(m.group(2))
        # k*x + k*(k-1)/2 = S
        num = S - k * (k - 1) // 2
        if k > 0 and num % k == 0:
            x0 = num // k
            seq = [x0 + i for i in range(k)]
            if "largest" in sl or "greatest" in sl or "biggest" in sl:
                opt = match_value_to_unique_option(seq[-1], options)
            elif "smallest" in sl or "least" in sl or "first" in sl:
                opt = match_value_to_unique_option(seq[0], options)
            elif "middle" in sl and k % 2 == 1:
                opt = match_value_to_unique_option(seq[k // 2], options)
            else:
                opt = None
            if opt:
                return opt, "consecutive_sum"

    # 9. Triangle angles: ratio a:b:c or two angles given
    m = re.search(r"angles of a triangle are in the ratio\s+(\d+)\s*:\s*(\d+)\s*:\s*(\d+)", sl)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        tot = a + b + c
        if tot > 0 and 180 % tot == 0:
            unit = 180 // tot
            angs = [a * unit, b * unit, c * unit]
            if "largest" in sl or "greatest" in sl:
                opt = match_value_to_unique_option(max(angs), options)
            elif "smallest" in sl:
                opt = match_value_to_unique_option(min(angs), options)
            else:
                opt = None
            if opt:
                return opt, "triangle_ratio"

    # 10. Square vertex products: "(a+c)(b+d) = N" -> sum of 4 sides
    if "vertices" in sl and "product" in sl and "square" in sl:
        m = re.search(r"sum of (?:the )?(?:numbers at )?(?:the )?vertices is (\d+)", sl)
        if m:
            N = int(m.group(1))
            sums = set()
            for x in range(2, N + 1):
                if N % x == 0:
                    y = N // x
                    if y >= 2:
                        sums.add(x + y)
            if len(sums) == 1:
                opt = match_value_to_unique_option(next(iter(sums)), options)
                if opt:
                    return opt, "square_vertex_products"

    # 11. LCM cycle: "every A days ... every B days ... after how many days"
    m = re.search(r"every (\d+) days.*?every (\d+) days", sl)
    if m and ("same day" in sl or "together" in sl or "again" in sl):
        a, b = int(m.group(1)), int(m.group(2))
        if a > 0 and b > 0:
            opt = match_value_to_unique_option(math.lcm(a, b), options)
            if opt:
                return opt, "lcm_cycle"

    # 12. Two angles of a triangle given -> third angle
    m = re.search(r"two angles of a triangle are (\d+(?:\.\d+)?)\s*°?\s*and\s*(\d+(?:\.\d+)?)\s*°?", sl)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a + b < 180:
            opt = match_value_to_unique_option(180.0 - a - b, options)
            if opt:
                return opt, "triangle_two_angles"

    # 13. Simple percentage: "what is P% of X"
    m = re.search(r"what is (\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)", sl)
    if m:
        p_val, x_val = float(m.group(1)), float(m.group(2))
        opt = match_value_to_unique_option((p_val / 100.0) * x_val, options)
        if opt:
            return opt, "percent_of"

    # 14. Three digits multiplied give P -> sum of the three digits
    m = re.search(r"three digits.*?(?:multiplied|product).*?(?:give|is)\s+(\d+).*?sum of (?:the )?three digits", sl)
    if m:
        P = int(m.group(1))
        sums = set()
        for a in range(1, 10):
            if P % a == 0:
                for b in range(a, 10):
                    if (P // a) % b == 0:
                        c = (P // a) // b
                        if 1 <= c <= 9:
                            sums.add(a + b + c)
        if len(sums) == 1:
            opt = match_value_to_unique_option(next(iter(sums)), options)
            if opt:
                return opt, "three_digit_product_sum"

    # 15. Triangle with sides a, b, c -> equilateral triangle with equal perimeter -> side length
    m = re.search(r"triangle has sides (?:with lengths )?(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,?\s*and\s*(\d+(?:\.\d+)?).*?equilateral triangle.*?perimeter equal", sl)
    if m:
        perim = float(m.group(1)) + float(m.group(2)) + float(m.group(3))
        opt = match_value_to_unique_option(perim / 3.0, options)
        if opt:
            return opt, "equilateral_equal_perim"

    return None


try:
    from engine.symbolic_solvers import solve_neuro_symbolic as _ext_solve_neuro_symbolic
except ImportError:
    _ext_solve_neuro_symbolic = None


def solve_neuro_symbolic(
    statement: str, options: dict[str, str], raw_statement: str = ""
) -> Optional[tuple[str, str]]:
    """Try external symbolic_solvers first, falling back to built-in solvers if None."""
    if _ext_solve_neuro_symbolic is not None:
        hit = _ext_solve_neuro_symbolic(statement, options, raw_statement=raw_statement)
        if hit is not None:
            return hit
    return _fallback_solve_neuro_symbolic(statement, options, raw_statement=raw_statement)


# ---------------------------------------------------------------------------
# Clean AST-checked Python PoT Sandbox
# ---------------------------------------------------------------------------

_POT_ALLOWED_IMPORTS = {"math", "fractions", "itertools", "collections", "statistics"}
_POT_BLOCKED_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "input", "breakpoint",
    "globals", "locals", "vars", "dir", "getattr", "setattr", "delattr",
}


def _extract_pot_code(text: str) -> str:
    raw = str(text or "").strip()
    fence = re.search(r"```(?:python|py)?\s*\n(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    # Strip trailing non-code explanation lines if any
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("```"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def run_pot_sandbox(
    code: str,
    options: Optional[dict[str, str]] = None,
    timeout_s: float = 1.5,
) -> tuple[bool, Any, str]:
    """Execute restricted Python PoT code safely with dict, collections, itertools, fractions, math."""
    cleaned = _extract_pot_code(code)
    if not cleaned:
        return False, None, "empty"
    try:
        tree = ast.parse(cleaned, mode="exec")
    except SyntaxError as err:
        return False, None, f"SyntaxError: {err.msg}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _POT_BLOCKED_NAMES:
            return False, None, f"blocked_name:{node.id}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return False, None, f"blocked_attr:{node.attr}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in _POT_ALLOWED_IMPORTS:
                    return False, None, f"blocked_import:{alias.name}"
        if isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".", 1)[0]
            if mod not in _POT_ALLOWED_IMPORTS:
                return False, None, f"blocked_import:{node.module}"

    printed: list[str] = []

    def _safe_range(*args: int) -> range:
        r = range(*args)
        if len(r) > 300_000:
            raise ValueError("range_limit")
        return r

    def _safe_factorial(n: int) -> int:
        if not isinstance(n, int) or n < 0 or n > 1000:
            raise ValueError("factorial_limit")
        return math.factorial(n)

    def _safe_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        root = str(name).split(".", 1)[0]
        if root not in _POT_ALLOWED_IMPORTS:
            raise ImportError(f"blocked:{name}")
        return {
            "math": math,
            "fractions": fractions,
            "itertools": itertools,
            "collections": collections,
            "statistics": statistics,
        }[root]

    def _safe_print(*args: Any, **kwargs: Any) -> None:
        if len(printed) < 32:
            printed.append(" ".join(str(a) for a in args))

    safe_builtins = {
        "__import__": _safe_import,
        "abs": abs, "all": all, "any": any, "bin": bin, "bool": bool,
        "chr": chr, "dict": dict, "divmod": divmod, "enumerate": enumerate,
        "filter": filter, "float": float, "format": format, "frozenset": frozenset,
        "hex": hex, "int": int, "isinstance": isinstance, "iter": iter,
        "len": len, "list": list, "map": map, "max": max, "min": min,
        "next": next, "oct": oct, "ord": ord, "pow": pow, "print": _safe_print,
        "range": _safe_range, "reversed": reversed, "round": round, "set": set,
        "slice": slice, "sorted": sorted, "str": str, "sum": sum,
        "tuple": tuple, "zip": zip,
    }
    env: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "math": math,
        "fractions": fractions,
        "itertools": itertools,
        "collections": collections,
        "statistics": statistics,
        "Fraction": fractions.Fraction,
        "Counter": collections.Counter,
        "defaultdict": collections.defaultdict,
        "sqrt": math.sqrt,
        "isqrt": math.isqrt,
        "gcd": math.gcd,
        "lcm": math.lcm,
        "comb": math.comb,
        "perm": math.perm,
        "factorial": _safe_factorial,
        "floor": math.floor,
        "ceil": math.ceil,
        "choices": dict(options or {}),
    }

    result_box: dict[str, Any] = {"ok": False, "val": None, "err": ""}
    compiled = compile(tree, "<pot>", "exec")

    def _worker() -> None:
        try:
            exec(compiled, env, env)
            val = env.get("ans")
            if val is None and printed:
                last_line = printed[-1].strip()
                parsed = parse_numeric_value(last_line)
                val = parsed if parsed is not None else last_line
            if val is None:
                result_box["err"] = "no_output"
            else:
                result_box["ok"] = True
                result_box["val"] = val
        except Exception as exc:
            result_box["err"] = f"{type(exc).__name__}: {exc}"

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        return False, None, "timeout"
    return bool(result_box["ok"]), result_box["val"], str(result_box["err"])


# ---------------------------------------------------------------------------
# Clean (level, qnum) Priors (alpha=15.0)
# ---------------------------------------------------------------------------

_PRIORS_CACHE: dict[str, dict[Any, dict[str, float]]] = {}


def _qnum_band(qnum: Optional[int]) -> str:
    if qnum is None:
        return "_all"
    if qnum <= 10:
        return "easy"
    if qnum <= 20:
        return "med"
    return "hard"


def _parse_level_qnum(pid: str, level: Any = None, qnum: Any = None) -> tuple[int, Optional[int]]:
    lvl = int(level) if level is not None and str(level).lstrip("-").isdigit() else 0
    qn = int(qnum) if qnum is not None and str(qnum).lstrip("-").isdigit() else None
    m = re.search(r"lvl-(\d+)_\d+_(\d+)", str(pid or ""))
    if m:
        if level is None:
            lvl = int(m.group(1))
        if qn is None:
            qn = int(m.group(2))
    return lvl, qn


def _norm_dist(counts: dict[str, float], alpha: float = 15.0) -> dict[str, float]:
    total = sum(counts.values()) + 5.0 * alpha
    return {k: round((counts.get(k, 0.0) + alpha) / total, 6) for k in MCQ_LETTERS}


def load_priors(train_jsonl: Optional[str | os.PathLike[str]] = None, alpha: float = 6.0) -> dict[Any, dict[str, float]]:
    """Compute clean smoothed A-E priors from train.jsonl, conditioned on is_visual, level, and qnum."""
    key = f"{train_jsonl or ''}:{alpha}:v2"
    if key in _PRIORS_CACHE:
        return _PRIORS_CACHE[key]

    vis_band_counts: dict[tuple[bool, int, str], dict[str, float]] = {}
    vis_lvl_counts: dict[tuple[bool, int, str], dict[str, float]] = {}
    vis_global_counts: dict[tuple[bool, str], dict[str, float]] = {}
    band_counts: dict[tuple[int, str], dict[str, float]] = {}
    lvl_counts: dict[tuple[int, str], dict[str, float]] = {}
    no_opts_counts: dict[str, float] = {k: 0.0 for k in MCQ_LETTERS}
    global_counts: dict[str, float] = {k: 0.0 for k in MCQ_LETTERS}

    if train_jsonl and Path(train_jsonl).is_file():
        for line in Path(train_jsonl).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ans = str(row.get("label") or row.get("correct_option") or "").strip().upper()
            if ans not in MCQ_LETTERS:
                continue
            global_counts[ans] += 1.0
            is_vis = row.get("text_only") is False
            vis_global_counts.setdefault((is_vis, "_global"), {k: 0.0 for k in MCQ_LETTERS})[ans] += 1.0
            opts = row.get("options") or {}
            has_opts = isinstance(opts, dict) and sum(1 for k in MCQ_LETTERS if str(opts.get(k, "")).strip()) >= 5
            if not has_opts:
                no_opts_counts[ans] += 1.0
                continue
            lvl, qn = _parse_level_qnum(str(row.get("id") or ""), row.get("level"), row.get("qnum"))
            band = _qnum_band(qn)
            vis_band_counts.setdefault((is_vis, lvl, band), {k: 0.0 for k in MCQ_LETTERS})[ans] += 1.0
            vis_lvl_counts.setdefault((is_vis, lvl, "_all"), {k: 0.0 for k in MCQ_LETTERS})[ans] += 1.0
            band_counts.setdefault((lvl, band), {k: 0.0 for k in MCQ_LETTERS})[ans] += 1.0
            lvl_counts.setdefault((lvl, "_all"), {k: 0.0 for k in MCQ_LETTERS})[ans] += 1.0

    priors: dict[Any, dict[str, float]] = {k: _norm_dist(v, alpha) for k, v in band_counts.items()}
    for k, v in lvl_counts.items():
        priors[k] = _norm_dist(v, alpha)
    for k, v in vis_band_counts.items():
        priors[k] = _norm_dist(v, alpha)
    for k, v in vis_lvl_counts.items():
        priors[k] = _norm_dist(v, alpha)
    for k, v in vis_global_counts.items():
        priors[k] = _norm_dist(v, alpha)
    if sum(no_opts_counts.values()) > 0:
        priors[("_no_opts", "_global")] = _norm_dist(no_opts_counts, alpha=5.0)
    priors["_global"] = _norm_dist(global_counts, alpha=alpha)
    _PRIORS_CACHE[key] = priors
    return priors


def get_prior(
    priors: dict[Any, dict[str, float]],
    pid: str,
    level: Any,
    qnum: Any,
    has_options: bool,
    is_visual: Optional[bool] = None,
) -> dict[str, float]:
    if not has_options and ("_no_opts", "_global") in priors:
        return dict(priors[("_no_opts", "_global")])
    lvl, qn = _parse_level_qnum(pid, level, qnum)
    band = _qnum_band(qn)
    if is_visual is not None:
        vis_flag = bool(is_visual)
        vis_dist = (
            priors.get((vis_flag, lvl, band))
            or priors.get((vis_flag, lvl, "_all"))
            or priors.get((vis_flag, "_global"))
        )
        if vis_dist:
            return dict(vis_dist)
    dist = priors.get((lvl, band)) or priors.get((lvl, "_all")) or priors.get("_global")
    return dict(dist) if dist else {k: 0.2 for k in MCQ_LETTERS}


# ---------------------------------------------------------------------------
# LLM Client & Prompts (Static KV-cached prefixes, --reasoning off)
# ---------------------------------------------------------------------------

_SYS_POT = (
    "Write concise Python 3 code (math/itertools/fractions) to compute the exact answer. "
    "No prose, no fences. Set `ans = ...` and `print(ans)`.\n"
    "Ex1: How many 2-digit numbers have tens digit 3 more than units digit?\n"
    "ans = sum(1 for n in range(10, 100) if (n // 10) - (n % 10) == 3)\n"
    "print(ans)\n"
    "Ex2: Three numbers sum to 30. Second is twice first, third is 6 more than first. Find first.\n"
    "for x in range(-100, 200):\n"
    "    if x + 2*x + (x + 6) == 30:\n"
    "        ans = x; break\n"
    "print(ans)"
)

_SYS_COT = (
    "Solve the Kangaroo math problem step-by-step in at most 3 short lines. "
    "On the last line write ONLY 'Answer: X' where X is A, B, C, D, or E."
)

_SYS_SHARED = "You are a math competition solver. Read the problem and options carefully."

_SYS_LOGPROB = (
    "Solve the Kangaroo math multiple-choice problem. Reply with ONLY one uppercase letter A, B, C, D, or E."
)


def _call_llm(
    messages: list[dict[str, str]],
    llm_url: str,
    model_id: str,
    max_tokens: int,
    timeout_s: float,
    grammar: Optional[str] = None,
    logprobs: bool = False,
) -> Optional[dict[str, Any]]:
    body: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "seed": 42,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if grammar:
        body["grammar"] = grammar
    if logprobs:
        body["logprobs"] = True
        body["top_logprobs"] = 20
    req = urllib.request.Request(
        llm_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=max(1.0, timeout_s)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _extract_letter_logprobs(payload: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        choice = payload["choices"][0]
        lp_root = choice.get("logprobs") or {}
        content = lp_root.get("content")
        top_lp = content[0].get("top_logprobs") if isinstance(content, list) and content else lp_root.get("top_logprobs")
        items: list[tuple[str, float]] = []
        if isinstance(top_lp, dict):
            items = [(str(k), float(v)) for k, v in top_lp.items()]
        elif isinstance(top_lp, list):
            for entry in top_lp:
                if isinstance(entry, dict) and "token" in entry and "logprob" in entry:
                    items.append((str(entry["token"]), float(entry["logprob"])))
        for tok, val in items:
            t = tok.strip().upper()
            if t in MCQ_LETTERS and (t not in out or val > out[t]):
                out[t] = val
    except Exception:
        pass
    return out


# 0.6x damped empirical mean logprobs on Qwen3.5-0.8B-Q8_0 (clean raw_text, 21 text-only val problems)
# so A/B/C/D/E are debiased without crushing A to 0 predictions.
_EMPIRICAL_LP_BASELINE: dict[str, float] = {
    "A": -0.517,
    "B": -0.921,
    "C": -1.401,
    "D": -2.065,
    "E": -3.949,
}


def _pot_uses_problem_numbers(code: str, statement: str) -> bool:
    """Reject hallucinated PoT code that ignores all non-trivial numbers from the problem."""
    prob_nums = {
        tok for tok in re.findall(r"\b\d+\b", statement)
        if int(tok) >= 3
    }
    if not prob_nums:
        return True
    code_nums = set(re.findall(r"\b\d+\b", code))
    return bool(prob_nums & code_nums)


def _softmax(scores: dict[str, float]) -> dict[str, float]:
    vals = [float(scores.get(k, -20.0)) for k in MCQ_LETTERS]
    mx = max(vals)
    exps = [math.exp(max(-40.0, min(40.0, v - mx))) for v in vals]
    tot = sum(exps) or 1.0
    return {k: round(e / tot, 6) for k, e in zip(MCQ_LETTERS, exps)}


def _parse_cot_answer(text: str, options: dict[str, str]) -> Optional[str]:
    if not text:
        return None
    m = re.search(r"(?:Answer|Resposta)\s*[:\-]?\s*\(?([A-E])\)?\b", text, re.IGNORECASE)
    if m and m.group(1).upper() in options:
        return m.group(1).upper()
    # Try matching final number on the last line
    last_lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    for line in reversed(last_lines[-2:]):
        nums = re.findall(r"-?\d+(?:\.\d+)?(?:/\d+)?", line)
        if nums:
            opt = match_value_to_unique_option(nums[-1], options)
            if opt:
                return opt
    return None


def _archetype_hint(eng_text: str) -> str:
    """Return a concise 1-line Python strategy hint for the problem archetype."""
    sl = eng_text.lower()
    if any(w in sl for w in ("digit", "xifra", "divisible", "remainder", "last digit", "prime")):
        return "Hint: iterate integers with % and //."
    if any(w in sl for w in ("how many", "ways", "arrange", "order", "choose", "combinations")):
        return "Hint: count valid cases using range() or itertools."
    if any(w in sl for w in ("sum", "twice", "three times", "half", "age", "years", "total", "more than", "less than")):
        return "Hint: set up an equation or loop over possible integer values."
    if any(w in sl for w in ("triangle", "square", "rectangle", "perimeter", "area", "angle", "side")):
        return "Hint: write geometric formulas using the given lengths/angles."
    return "Hint: compute step-by-step and assign result to ans."


def _extract_rag_twin_votes(
    examples: Optional[list[Any]],
    valid_opts: dict[str, str],
    eng_opts: dict[str, str],
) -> tuple[Optional[str], dict[str, float]]:
    """Match high-similarity RAG hits' correct option text against current options."""
    if not examples:
        return None, {}
    votes: dict[str, float] = {}
    for item in examples:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            score, stored = float(item[0]), item[1]
        elif isinstance(item, dict):
            score, stored = 0.80, item
        else:
            continue
        if score < 0.90 or not isinstance(stored, dict):
            continue
        lbl = str(stored.get("label") or "").strip().upper()
        s_opts = stored.get("options") or {}
        ans_txt = str(s_opts.get(lbl) or "").strip()
        if not ans_txt:
            continue
        matched = match_value_to_unique_option(ans_txt, valid_opts) or match_value_to_unique_option(ans_txt, eng_opts)
        if matched:
            if score >= 0.95:
                return matched, {matched: 1.0}
            votes[matched] = votes.get(matched, 0.0) + (score - 0.85)
    return None, votes


def _format_rag_hint(examples: Optional[list[Any]]) -> str:
    """Return a 1-line compressed RAG hint if any hit has similarity >= 0.78."""
    if not examples:
        return ""
    best_score = 0.0
    best_stored: Optional[dict[str, Any]] = None
    for item in examples:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            score, stored = float(item[0]), item[1]
        elif isinstance(item, dict):
            score, stored = 0.80, item
        else:
            continue
        if score >= 0.78 and score > best_score and isinstance(stored, dict):
            best_score = score
            best_stored = stored
    if not best_stored:
        return ""
    lbl = str(best_stored.get("label") or "").strip().upper()
    s_opts = best_stored.get("options") or {}
    ans_txt = str(s_opts.get(lbl) or "").strip()
    ex_text = clean_problem_text(str(best_stored.get("text") or ""))[:140]
    if ans_txt and ex_text:
        return f"\nSimilar example ({best_score:.2f}): {ex_text} -> {ans_txt}"
    return ""


def _score_visual_fast(
    valid_opts: dict[str, str],
    prior_probs: dict[str, float],
    knn_votes: Optional[dict[str, float]] = None,
    twin_votes: Optional[dict[str, float]] = None,
) -> str:
    """Fast (<1ms) visual problem scorer: (level, qnum) prior + interior + twin/kNN."""
    scores = {k: float(prior_probs.get(k, 0.0)) for k in valid_opts}
    parsed = {k: parse_numeric_value(v) for k, v in valid_opts.items()}
    nums = sorted(v for v in parsed.values() if v is not None)
    if len(nums) == 5 and len(set(nums)) == 5:
        for k in valid_opts:
            if parsed[k] not in (nums[0], nums[-1]):
                scores[k] += 0.04
    if knn_votes:
        for k in valid_opts:
            scores[k] += 0.35 * float(knn_votes.get(k, 0.0))
    if twin_votes:
        for k in valid_opts:
            scores[k] += 0.45 * float(twin_votes.get(k, 0.0))
    return max(valid_opts, key=lambda k: (scores[k], prior_probs.get(k, 0.0), k))


try:
    from engine.visual_solver import solve_visual_problem
except ImportError:
    solve_visual_problem = None  # type: ignore[assignment]

try:
    from engine.ocr_engine import extract_figure_ocr
except ImportError:
    extract_figure_ocr = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Main Cascade Entry Point
# ---------------------------------------------------------------------------

def solve_mcq_optimized(
    problem: dict[str, Any] | str,
    llm_url: Optional[str] = None,
    time_limit_s: float = 52.0,
    priors: Optional[dict[Any, dict[str, float]]] = None,
    train_jsonl: Optional[str | os.PathLike[str]] = None,
    examples: Optional[list[dict[str, Any]]] = None,
    knn_votes: Optional[dict[str, float]] = None,
    model_id: Optional[str] = None,
) -> dict[str, Any]:
    """Optimized offline MCQ solver cascade within <=55s."""
    t0 = time.perf_counter()
    budget_s = min(52.0, max(0.5, float(time_limit_s)))
    safe_deadline = t0 + max(0.2, budget_s - 2.0)

    if isinstance(problem, str):
        prob_dict: dict[str, Any] = {"id": "EXAM-01", "text": problem, "options": {}}
    else:
        prob_dict = dict(problem)

    pid = str(prob_dict.get("id") or prob_dict.get("problem_id") or "EXAM-01")
    lang = str(prob_dict.get("language") or prob_dict.get("lang") or "eng").strip().lower()
    raw_text = str(prob_dict.get("text") or prob_dict.get("statement") or "").strip()
    raw_opts = dict(prob_dict.get("options") or {})
    raw_eng_opts = prob_dict.get("eng_options")
    norm_eng_opts: dict[str, str] = {}
    if isinstance(raw_eng_opts, dict) and raw_eng_opts:
        norm_eng_opts = normalize_options(
            {k: str(v).strip() for k, v in raw_eng_opts.items() if k in MCQ_LETTERS and str(v).strip()}
        )
    valid_opts = normalize_options({k: str(v).strip() for k, v in raw_opts.items() if k in MCQ_LETTERS and str(v).strip()})
    if not valid_opts and norm_eng_opts:
        valid_opts = dict(norm_eng_opts)
    if (not valid_opts or all(v.strip().upper() == k for k, v in valid_opts.items())) and extract_figure_ocr is not None:
        try:
            ocr_res = extract_figure_ocr(prob_dict)
            parsed_opts = ocr_res.get("parsed_options") if isinstance(ocr_res, dict) else None
            if isinstance(parsed_opts, dict) and parsed_opts:
                norm_ocr = normalize_options(
                    {k: str(v).strip() for k, v in parsed_opts.items() if k in MCQ_LETTERS and str(v).strip()}
                )
                if norm_ocr:
                    valid_opts = norm_ocr
        except Exception:
            pass

    is_visual = prob_dict.get("text_only") is False
    active_priors = priors if priors is not None else load_priors(train_jsonl)
    prior_probs = get_prior(
        active_priors,
        pid,
        prob_dict.get("level"),
        prob_dict.get("qnum"),
        has_options=bool(valid_opts),
        is_visual=is_visual,
    )
    prior_best = max(MCQ_LETTERS, key=lambda k: (prior_probs.get(k, 0.0), k))

    def _ret(ans: str, status: str, source: str, ptok: int = 0) -> dict[str, Any]:
        dt = time.perf_counter() - t0
        return {
            "problem_id": pid,
            "language": lang,
            "answer": ans,
            "predicted_option": ans,
            "status": status,
            "source": source,
            "prior_probs": prior_probs,
            "prompt_tokens": ptok,
            "latency_ms": int(dt * 1000),
            "seconds": round(dt, 4),
            "offline_mode": True,
        }

    # 1. Empty options -> fast prior (<1 ms)
    if not valid_opts:
        return _ret(prior_best, "ok_prior_empty_options", "level_qnum_prior")

    # 2. Multilingual bridge + offline Catalan -> English math gloss
    is_cat = lang in {"cat", "ca", "catalan"}
    raw_eng_text = str(prob_dict.get("eng_text") or "").strip()
    has_eng_bridge = bool(raw_eng_text)
    if has_eng_bridge:
        eng_text = clean_problem_text(raw_eng_text)
    else:
        eng_text = translate_cat_to_eng_math(raw_text, language=lang) if is_cat else clean_problem_text(raw_text)

    if len(norm_eng_opts) == len(valid_opts) and norm_eng_opts:
        eng_opts = dict(norm_eng_opts)
    else:
        eng_opts = {
            k: (translate_cat_to_eng_math(v, language="cat") if is_cat else v)
            for k, v in valid_opts.items()
        }

    # 3. Sound neuro-symbolic solvers (<5 ms)
    ns_raw = raw_eng_text if has_eng_bridge else raw_text
    ns_hit = solve_neuro_symbolic(eng_text, eng_opts, raw_statement=ns_raw)
    if ns_hit is None and eng_opts != valid_opts:
        ns_hit = solve_neuro_symbolic(eng_text, valid_opts, raw_statement=ns_raw)
    if ns_hit is None and has_eng_bridge:
        fallback_cat_eng = translate_cat_to_eng_math(raw_text, language=lang) if is_cat else clean_problem_text(raw_text)
        ns_hit = solve_neuro_symbolic(fallback_cat_eng, valid_opts, raw_statement=raw_text)
    if ns_hit is not None:
        return _ret(ns_hit[0], "ok_symbolic", ns_hit[1])

    # 3b. Option-text twin matching from high-similarity RAG hits (>=0.95 shortcut, >=0.90 vote)
    twin_shortcut, twin_votes = _extract_rag_twin_votes(examples, valid_opts, eng_opts)
    if twin_shortcut is not None:
        return _ret(twin_shortcut, "ok_rag_twin", "rag_twin_exact")

    url = llm_url or DEFAULT_LLM_URL
    mid = model_id or DEFAULT_MODEL_ID
    total_ptok = 0

    clean_text = clean_problem_text(raw_eng_text) if has_eng_bridge else clean_problem_text(raw_text)
    prompt_opts = eng_opts if (is_cat and len(norm_eng_opts) == len(valid_opts) and norm_eng_opts) else valid_opts
    opt_lines = "\n".join(f"{k}) {prompt_opts[k]}" for k in MCQ_LETTERS if k in prompt_opts)
    _shared_prefix = f"Problem:\n{clean_text}\n\nOptions:\n{opt_lines}"

    # 4. Visual problems (missing diagram): balanced structural + RAG + prior + SLM logprob solver
    if is_visual:
        if solve_visual_problem is not None:
            vis_ans, vis_src = solve_visual_problem(
                prob_dict, valid_opts, prior_probs, knn_votes=knn_votes, twin_votes=twin_votes
            )
            if vis_src != "visual_balanced_rag_prior":
                return _ret(vis_ans, "ok_visual_prior", vis_src)
        else:
            vis_ans = _score_visual_fast(valid_opts, prior_probs, knn_votes=knn_votes, twin_votes=twin_votes)
            vis_src = "visual_prior_struct"

        rem = safe_deadline - time.perf_counter()
        if rem >= 16.0:
            lp_msgs = [
                {"role": "system", "content": _SYS_SHARED},
                {
                    "role": "user",
                    "content": _shared_prefix + "\n\nReply with ONLY the correct option letter (A, B, C, D, or E).\nAnswer:",
                },
            ]
            d_lp = _call_llm(
                lp_msgs, url, mid, max_tokens=1, timeout_s=min(rem - 0.5, 22.0),
                grammar="root ::= [A-E]", logprobs=True,
            )
            if d_lp:
                total_ptok += int((d_lp.get("usage") or {}).get("prompt_tokens") or 0)
                raw_lp = _extract_letter_logprobs(d_lp)
                if raw_lp:
                    cal_lp = {
                        k: max(-10.0, raw_lp.get(k, -10.0)) - _EMPIRICAL_LP_BASELINE.get(k, 0.0)
                        for k in MCQ_LETTERS
                    }
                    lp_probs = _softmax(cal_lp)
                    combined_vis = {}
                    for k in valid_opts:
                        s = 0.45 * lp_probs.get(k, 0.0) + 0.40 * prior_probs.get(k, 0.0)
                        if knn_votes:
                            s += 0.35 * float(knn_votes.get(k, 0.0))
                        if twin_votes:
                            s += 0.45 * float(twin_votes.get(k, 0.0))
                        combined_vis[k] = s
                    best_vis = max(valid_opts, key=lambda k: (combined_vis[k], prior_probs.get(k, 0.0), k))
                    return _ret(best_vis, "ok_visual_slm", "visual_slm_prior", total_ptok)

        return _ret(vis_ans, "ok_visual_prior", vis_src, total_ptok)

    # 5. Shared-Prefix KV-Cached Logprob -> Micro-PoT/CoT Ensemble
    rem = safe_deadline - time.perf_counter()
    if rem >= 6.0:
        # Step A: 1-Pass Calibrated Logprobs (~11s)
        lp_msgs = [
            {"role": "system", "content": _SYS_SHARED},
            {
                "role": "user",
                "content": _shared_prefix + "\n\nReply with ONLY the correct option letter (A, B, C, D, or E).\nAnswer:",
            },
        ]
        d_lp = _call_llm(
            lp_msgs, url, mid, max_tokens=1, timeout_s=min(rem - 0.5, 24.5),
            grammar="root ::= [A-E]", logprobs=True,
        )
        if d_lp:
            total_ptok += int((d_lp.get("usage") or {}).get("prompt_tokens") or 0)
            raw_lp = _extract_letter_logprobs(d_lp)
            if raw_lp:
                cal_lp = {
                    k: max(-10.0, raw_lp.get(k, -10.0)) - _EMPIRICAL_LP_BASELINE.get(k, 0.0)
                    for k in MCQ_LETTERS
                }
                lp_probs = _softmax(cal_lp)
                sorted_probs = sorted((lp_probs.get(k, 0.0) for k in valid_opts), reverse=True)
                margin = (sorted_probs[0] - sorted_probs[1]) if len(sorted_probs) >= 2 else 1.0

                # Step B: Zero-Prefill KV-Cached Micro-PoT/CoT when uncertain
                pot_vote: dict[str, float] = {}
                rem = safe_deadline - time.perf_counter()
                if margin < 0.22 and rem >= 14.0 and len(eng_text) <= 260:
                    pot_msgs = [
                        {"role": "system", "content": _SYS_SHARED},
                        {
                            "role": "user",
                            "content": _shared_prefix + "\n\nWrite 1-3 lines of Python 3 to compute the answer value `ans`:\nans = ",
                        },
                    ]
                    d_pot = _call_llm(pot_msgs, url, mid, max_tokens=50, timeout_s=min(rem - 0.5, 16.0))
                    if d_pot:
                        total_ptok += int((d_pot.get("usage") or {}).get("prompt_tokens") or 0)
                        gen_txt = (d_pot.get("choices") or [{}])[0].get("message", {}).get("content", "")
                        code_txt = "ans = " + gen_txt.lstrip()
                        matched: Optional[str] = None
                        if _pot_uses_problem_numbers(code_txt, eng_text):
                            ok_pot, val_pot, _err = run_pot_sandbox(code_txt, options=eng_opts, timeout_s=1.5)
                            if ok_pot:
                                matched = match_value_to_unique_option(val_pot, eng_opts) or match_value_to_unique_option(val_pot, valid_opts)
                        if matched is None:
                            matched = _parse_cot_answer(gen_txt, eng_opts) or _parse_cot_answer(gen_txt, valid_opts)
                        if matched and matched in valid_opts:
                            pot_vote[matched] = 0.55

                # Combine logprob + prior + micro-PoT + RAG kNN/twin
                combined = {}
                for k in valid_opts:
                    s = 0.85 * lp_probs.get(k, 0.0) + 0.15 * prior_probs.get(k, 0.0) + pot_vote.get(k, 0.0)
                    if knn_votes:
                        s += 0.50 * float(knn_votes.get(k, 0.0))
                    if twin_votes:
                        s += 0.45 * float(twin_votes.get(k, 0.0))
                    combined[k] = s

                best = max(valid_opts, key=lambda k: (combined[k], prior_probs.get(k, 0.0)))
                src = "kv_logprob_pot" if pot_vote else "cal_logprob_prior"
                return _ret(best, "ok_logprob", src, total_ptok)

    return _ret(prior_best, "ok_prior_budget", "level_qnum_prior", total_ptok)
