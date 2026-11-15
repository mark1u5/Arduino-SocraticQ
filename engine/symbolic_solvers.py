"""Neuro-symbolic math solvers for Kangaroo / Proves Cangur problems.

Uses Python standard library only so it runs 100% offline on edge hardware
(Qualcomm QRB2210 / Arduino Uno Q) with zero external dependencies.
"""

from __future__ import annotations

import ast
import collections
import fractions
import itertools
import math
import re
from typing import Any, Optional

MCQ_LETTERS = ("A", "B", "C", "D", "E")

_WORD_NUMBERS: dict[str, int] = {
    # English
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    # Catalan
    "un": 1, "una": 1, "dos": 2, "dues": 2, "tres": 3, "quatre": 4, "cinc": 5,
    "sis": 6, "set": 7, "vuit": 8, "nou": 9, "deu": 10,
    "onze": 11, "dotze": 12, "tretze": 13, "catorze": 14, "quinze": 15,
    "setze": 16, "disset": 17, "divuit": 18, "dinou": 19, "vint": 20,
}

_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _clean_text(text: str) -> str:
    s = str(text or "").strip()
    s = s.replace("−", "-").replace("–", "-").replace("—", "-")
    s = s.replace("×", "*").replace("·", "*").replace("÷", "/")
    s = re.sub(r"^\s*\d+\.\s+", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _normalize_name(name: str) -> str:
    s = str(name or "").strip()
    s = re.sub(r"^(?:en|na|el|la|l'|d'en|d'na)\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^\w]", "", s, flags=re.UNICODE)
    return s.lower()


def _parse_word_or_int(token: str) -> Optional[int]:
    t = token.strip().lower()
    if re.fullmatch(r"\d+", t):
        return int(t)
    return _WORD_NUMBERS.get(t)


def parse_numeric_value(text: str, collapse_spaces: bool = False) -> Optional[float]:
    """Parse integers, decimals, percentages, simple fractions, and LaTeX fractions."""
    s = str(text or "").strip().replace("−", "-").replace("–", "-").replace("—", "-")
    s = s.replace("{,}", ".")
    s = re.sub(r"\\(?:text|mathrm|textbf|mathbf)\s*\{([^{}]*)\}", r" \1 ", s)
    s = re.sub(r"\\[,;! ]", " ", s)
    s = s.replace(",", ".")
    if not s:
        return None
    if collapse_spaces:
        s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    s = re.sub(r"^[€$]\s*", "", s).strip()
    if re.search(r"\d", s):
        s = re.sub(
            r"(?i)(?<=\d|\s)(?:km/h|m/s|cm\^2|cm2|cm²|cm\^3|cm3|cm³|cm|m\^2|m2|m²|m\^3|m3|m³|km|mm|dm|kg|mg|ml|hrs|hr|hours|hour|min|sec|euros|euro|eur|deg|degrees|°|€|\$|g|m|l|h|s)\s*$",
            "",
            s,
        ).strip()
    latex_frac = re.fullmatch(
        r"([-+]?)\s*\\[dt]?frac\{\s*([-+]?\d+(?:\.\d+)?)\s*\}\{\s*(\d+(?:\.\d+)?)\s*\}",
        s,
    )
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


def _canon_symbolic_str(text: str) -> str:
    s = str(text or "").strip().replace("−", "-").replace("{,}", ".")
    s = re.sub(r"\\(?:text|mathrm|textbf|mathbf)\s*\{([^{}]*)\}", r" \1 ", s)
    s = re.sub(r"\\[,;! ]", " ", s)
    s = re.sub(
        r"(?i)\s*(?:km/h|m/s|cm\^2|cm2|cm²|cm\^3|cm3|cm³|cm|m\^2|m2|m²|m\^3|m3|m³|km|mm|dm|kg|mg|ml|hrs|hr|min|euros|eur|deg|°|€|\$|g|m|l|h|s)\s*$",
        "",
        s,
    ).strip()
    s = s.replace("\\times", "×").replace("*", "×").replace("π", "\\pi")
    return re.sub(r"\s+", "", s).lower()


def match_value_to_unique_option(val: Any, options: dict[str, str]) -> Optional[str]:
    """Match a computed value (letter, int, float, Fraction, or string) to a unique A-E option."""
    if val is None or not options:
        return None

    if isinstance(val, str):
        sval = val.strip()
        if sval.upper() in MCQ_LETTERS and sval.upper() in options:
            return sval.upper()
        # Exact text match (case-insensitive)
        text_hits = [k for k, v in options.items() if str(v).strip().lower() == sval.lower()]
        if len(text_hits) == 1:
            return text_hits[0]
        # Whitespace-collapsed text match (e.g. '8: 45' vs '8:45')
        sval_nospace = re.sub(r"\s+", "", sval).lower()
        nospace_hits = [
            k for k, v in options.items() if re.sub(r"\s+", "", str(v)).lower() == sval_nospace
        ]
        if len(nospace_hits) == 1:
            return nospace_hits[0]
        # Clock time match against 'HH.MM h' or 'HH:MM'
        m_time = re.fullmatch(r"(\d{1,2}):(\d{2})", sval)
        if m_time:
            th, tm = int(m_time.group(1)), int(m_time.group(2))
            time_hits = []
            for k, v in options.items():
                mv = re.fullmatch(r"\s*(\d{1,2})\s*[:.]\s*(\d{2})(?:\s*h)?\s*", str(v), flags=re.I)
                if mv and int(mv.group(1)) == th and int(mv.group(2)) == tm:
                    time_hits.append(k)
            if len(time_hits) == 1:
                return time_hits[0]
        # Unit-stripped & symbolic canonical match (e.g. '10\\pi' vs '10\\pi \\text{ km}', '40:3' vs '40 : 3')
        sym_val = _canon_symbolic_str(sval)
        if sym_val:
            sym_hits = [k for k, v in options.items() if _canon_symbolic_str(v) == sym_val]
            if len(sym_hits) == 1:
                return sym_hits[0]
        # Normalized name match (handles 'En Jan' vs 'Jan', "L'Oriol" vs 'Oriol')
        norm_val = _normalize_name(sval)
        if norm_val:
            name_hits = [k for k, v in options.items() if _normalize_name(v) == norm_val]
            if len(name_hits) == 1:
                return name_hits[0]

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
        for collapse in (False, True):
            num_hits: list[str] = []
            for k, opt_str in options.items():
                ov = parse_numeric_value(opt_str, collapse_spaces=collapse)
                if ov is not None and abs(ov - target_num) <= 1e-6 * max(1.0, abs(target_num)):
                    num_hits.append(k)
            if len(num_hits) == 1:
                return num_hits[0]
            if len(num_hits) > 1:
                return None

        # Fallback for OCR-prefixed tokens like '1 11' or '1/12' / '2/28' representing '11', '12', '28'
        if abs(target_num - round(target_num)) < 1e-9:
            target_int_str = str(int(round(target_num)))
            tok_hits = [
                k for k, opt_str in options.items()
                if target_int_str in str(opt_str).split()
            ]
            if len(tok_hits) == 1:
                return tok_hits[0]
            if round(target_num) >= 10:
                ocr_frac_hits = [
                    k for k, opt_str in options.items()
                    if re.fullmatch(rf"\s*[12]/{target_int_str}\s*", str(opt_str))
                ]
                if len(ocr_frac_hits) == 1:
                    return ocr_frac_hits[0]

    return None


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


# ---------------------------------------------------------------------------
# Individual Parameterized Solvers
# ---------------------------------------------------------------------------

def _solve_arith_eval(sl: str) -> Optional[tuple[Any, str]]:
    m = re.search(
        r"(?:what is the value of|what is|calculate|evaluate|compute|"
        r"quin és el valor de|quant val|calcula)\s+([0-9+\-*/^().\s\\{}frac]+)\??$",
        sl,
    )
    if m and any(op in m.group(1) for op in "+-*/^"):
        val = _safe_eval_arith(m.group(1))
        if val is not None:
            return val, "arith_eval"
    return None


def _solve_equalization(sl: str) -> Optional[tuple[Any, str]]:
    if any(p in sl for p in ("same amount", "same number", "equal number", "mateixa quantitat", "mateix nombre")) and any(
        g in sl for g in ("give", "donar", "doni", "dona")
    ):
        nums = [float(x) for x in re.findall(r"\b\d+(?:\.\d+)?\b", sl)]
        if len(nums) == 2 and abs(nums[0] - nums[1]) > 0:
            return abs(nums[0] - nums[1]) / 2.0, "equalization_transfer"
    return None


def _solve_except_n(sl: str) -> Optional[tuple[Any, str]]:
    matches = re.findall(r"(?:all except|tots menys)\s+(\d+)", sl)
    if len(matches) >= 2:
        vals = [int(x) for x in matches]
        k = len(vals)
        total_num = sum(vals)
        if total_num % (k - 1) == 0:
            total = total_num // (k - 1)
            if total >= max(vals):
                return total, "except_n_total"
    return None


def _solve_alternating_jumps(sl: str) -> Optional[tuple[Any, str]]:
    if ("alternat" in sl) and any(w in sl for w in ("jump", "salt")) and any(
        w in sl for w in ("forward", "endavant")
    ) and any(w in sl for w in ("backward", "enrere")):
        m_start = re.search(r"(?:starts at(?: number)?|comença al(?: nombre)?)\s+(-?\d+)", sl)
        m_fwd = re.search(r"(\d+)\s*(?:(?:units?|unitats?)\s+)?(?:forwards?|endavant)", sl)
        m_bwd = re.search(r"(\d+)\s*(?:(?:units?|unitats?)\s+)?(?:backwards?|enrere)", sl)
        m_jumps = re.search(r"(?:after|després de)\s+(\d+)\s+(?:jumps?|salts?)", sl)
        if m_fwd and m_bwd and m_jumps:
            start = int(m_start.group(1)) if m_start else 0
            fwd = int(m_fwd.group(1))
            bwd = int(m_bwd.group(1))
            jumps = int(m_jumps.group(1))
            pos = start + (jumps // 2) * (fwd - bwd) + (fwd if jumps % 2 == 1 else 0)
            return pos, "alternating_jumps"
    return None


def _solve_jump_with_slip(sl: str) -> Optional[tuple[Any, str]]:
    if any(w in sl for w in ("slides", "slips", "rellisca")) and any(
        w in sl for w in ("first time", "primera vegada")
    ):
        m_up = re.search(r"(\d+)\s*(?:m|cm|km|units?)?\s*(?:forward|up|endavant)", sl)
        m_down = re.search(r"(?:slides|slips|rellisca)\s+(\d+)\s*(?:m|cm|km|units?)?\s*(?:back|down|enrere)", sl)
        m_target = re.search(r"(?:reach|arribar)[^.?!\d]*(\d+)", sl)
        if m_up and m_down and m_target:
            up = int(m_up.group(1))
            down = int(m_down.group(1))
            target = int(m_target.group(1))
            if up > down:
                jumps = 1 + math.ceil(max(0, target - up) / (up - down))
                return jumps, "jump_with_slip"
    return None


def _solve_digital_clock(sl: str) -> Optional[tuple[Any, str]]:
    if ("00:00" in sl and "23:59" in sl) or any(w in sl for w in ("digital clock", "rellotge digital")):
        if any(p in sl for p in ("four equal digits", "quatre xifres iguals", "4 equal digits")):
            cnt = sum(
                1
                for h in range(24)
                for m in range(60)
                if len(set(f"{h:02d}{m:02d}")) == 1
            )
            return cnt, "digital_clock"
        if any(p in sl for p in ("four different digits", "quatre xifres diferents", "all digits different")):
            cnt = sum(
                1
                for h in range(24)
                for m in range(60)
                if len(set(f"{h:02d}{m:02d}")) == 4
            )
            return cnt, "digital_clock"
        m_sum = re.search(r"(?:sum of (?:the |its )?digits|suma de les (?:seves )?xifres)[^0-9]*(\d+)", sl)
        if m_sum:
            target = int(m_sum.group(1))
            cnt = sum(
                1
                for h in range(24)
                for m in range(60)
                if sum(int(c) for c in f"{h:02d}{m:02d}") == target
            )
            return cnt, "digital_clock"
    return None


def _solve_coins(sl: str) -> Optional[tuple[Any, str]]:
    if not any(w in sl for w in ("coin", "moned")):
        return None

    # 7a. Coin constraints: N coins worth V cents, denominations d1, d2, d3, at least one each
    m_ncoins = re.search(r"(\d+)\s+(?:coins?|monedes)\s+(?:worth|que valen)\s+(\d+)\s+(?:cents?|cèntims)", sl)
    if m_ncoins:
        n_coins = int(m_ncoins.group(1))
        total_val = int(m_ncoins.group(2))
        denom_block = re.search(
            r"(?:each coin is worth|cada moneda val|worth|val)\s+([\d,\s]+(?:or|and|o|i)\s+\d+)\s+(?:cents?|cèntims)", sl
        )
        if denom_block:
            denoms = sorted(set(int(x) for x in re.findall(r"\d+", denom_block.group(1))))
            m_target = re.search(r"(?:of|de)\s+(\d+)[-\s]*(?:cents?|cèntims?)", sl)
            if len(denoms) == 3 and m_target:
                target_d = int(m_target.group(1))
                if target_d in denoms:
                    idx = denoms.index(target_d)
                    at_least_one = any(p in sl for p in ("at least one", "almenys una"))
                    lo = 1 if at_least_one else 0
                    matches = []
                    d0, d1, d2 = denoms
                    for c0 in range(lo, n_coins + 1):
                        for c1 in range(lo, n_coins - c0 + 1):
                            c2 = n_coins - c0 - c1
                            if c2 >= lo and c0 * d0 + c1 * d1 + c2 * d2 == total_val:
                                matches.append((c0, c1, c2)[idx])
                    if matches:
                        want_min = bool(
                            re.search(r"\b(?:smallest|fewest|minimum|mínim|més petit)\b|(?<!\bat\s)\bleast\b", sl)
                        )
                        return (min(matches) if want_min else max(matches)), "coin_constraints"

    # 7b. Coin change ways: pay V cents using d1 and d2
    m_ways = re.search(
        r"(?:pay|pagar)[^0-9]*(\d+)\s+(?:cents?|cèntims)[^0-9]*"
        r"(?:using|fent servir)[^0-9]*(\d+)[-\s]*(?:cent|cèntims?)?(?:\s*(?:and|i)\s*(?:de\s*)?)(\d+)[-\s]*(?:cent|cèntims?)",
        sl,
    )
    if m_ways and any(w in sl for w in ("ways", "maneres")):
        total_val = int(m_ways.group(1))
        d1 = int(m_ways.group(2))
        d2 = int(m_ways.group(3))
        if d1 > 0 and d2 > 0:
            need_both = any(p in sl for p in ("at least one of each", "almenys una de cada"))
            lo = 1 if need_both else 0
            ways = 0
            for c1 in range(lo, total_val // d1 + 1):
                rem = total_val - c1 * d1
                if rem % d2 == 0:
                    c2 = rem // d2
                    if c2 >= lo:
                        ways += 1
            return ways, "coin_change_ways"

    return None


def _solve_digit_combinatorics(sl: str) -> Optional[tuple[Any, str]]:
    # 8e. Largest/smallest sum of two 2-digit numbers formed from 4 given digits
    if any(p in sl for p in ("two two-digit numbers", "dos nombres de dues xifres")) and any(
        w in sl for w in ("sum", "suma", "add", "sumen")
    ):
        m_digits = re.search(
            r"(?:digits|xifres)\s+([\d,\s]+(?:and|i)\s+\d+)", sl
        )
        if m_digits:
            digits = [int(x) for x in re.findall(r"\d", m_digits.group(1))]
            if len(digits) == 4:
                sums = [
                    (10 * a + b) + (10 * c + d)
                    for a, b, c, d in itertools.permutations(digits)
                    if a != 0 and c != 0
                ]
                if sums:
                    want_min = any(w in sl for w in ("smallest", "minimum", "més petita", "mínima"))
                    return (min(sums) if want_min else max(sums)), "two_digit_sum_extrema"

    # 8a. Using each of given digits once, how many k-digit numbers
    if any(p in sl for p in ("exactly once", "exactament un cop")) and any(
        w in sl for w in ("digits", "xifres")
    ):
        m_digits = re.search(
            r"(?:digits|xifres)\s+([\d,\s]+(?:and|i)\s+\d+)", sl
        )
        m_k = re.search(
            r"(four|three|two|five|quatre|tres|dues|cinc|\d)[-\s]*(?:digit|xifres)",
            sl,
        )
        if m_digits and m_k:
            digits = [int(x) for x in re.findall(r"\d", m_digits.group(1))]
            k = _parse_word_or_int(m_k.group(1))
            if k is not None and 1 <= k <= len(digits):
                perms = set(itertools.permutations(digits, k))
                cnt = sum(1 for p in perms if p[0] != 0)
                return cnt, "digit_permutations"

    # 8b. Two-digit numbers whose digits are both odd / both even
    if any(p in sl for p in ("two-digit numbers", "nombres de dues xifres")) and any(
        p in sl for p in ("both odd", "dues xifres senars", "both even", "dues xifres parelles")
    ):
        if "odd" in sl or "senars" in sl:
            return 25, "two_digit_parity"
        if "even" in sl or "parell" in sl:
            return 20, "two_digit_parity"

    # 8c. Whole numbers from 1 to N containing digit d at least once
    m_cont = re.search(
        r"(?:from\s+1\s+to|de\s+l'1\s+al)\s+(\d+)[^.?!]*?(?:contain|contenen)[^0-9]*(\d)",
        sl,
    )
    if m_cont:
        n_max = int(m_cont.group(1))
        d_char = m_cont.group(2)
        if 1 <= n_max <= 100000:
            cnt = sum(1 for x in range(1, n_max + 1) if d_char in str(x))
            return cnt, "count_containing_digit"

    # 8d. Three-digit numbers with exactly two equal digits
    if any(p in sl for p in ("three-digit numbers", "nombres de tres xifres")) and any(
        p in sl for p in ("exactly two equal digits", "exactament dues xifres iguals")
    ):
        cnt = sum(1 for x in range(100, 1000) if len(set(str(x))) == 2)
        return cnt, "exact_two_equal_digits"

    # 8f. 3-digit number with no zero digits, reversed, difference = D
    if any(p in sl for p in ("three-digit number", "nombre de tres xifres")) and any(
        p in sl for p in ("reverse", "ordre invers")
    ) and any(p in sl for p in ("subtract", "resta")):
        m_diff = re.search(r"(?:result is|resultat és)\s+(\d+)", sl)
        if m_diff:
            diff = int(m_diff.group(1))
            no_zero = any(p in sl for p in ("no zero", "cap xifra zero"))
            rng_mid = range(1, 10) if no_zero else range(0, 10)
            cnt = sum(
                1
                for a in range(1, 10)
                for b in rng_mid
                for c in range(1, 10)
                if abs((100 * a + 10 * b + c) - (100 * c + 10 * b + a)) == diff
            )
            return cnt, "three_digit_reverse_diff"

    # 8g. 4-digit pattern like a23b divisible by M -> a + b
    m_pat = re.search(
        r"\b((?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]+)\b[^.?!]*?(?:divisible by|divisible per)\s+(\d+)",
        sl,
    )
    if m_pat and any(w in sl for w in ("digit", "xifra", "xifres")):
        pat = m_pat.group(1)
        mod_m = int(m_pat.group(2))
        vars_in_pat = sorted(set(ch for ch in pat if ch.isalpha()))
        if len(pat) in (3, 4, 5) and any(ch.isdigit() for ch in pat) and len(vars_in_pat) == 2:
            v1, v2 = vars_in_pat
            r1 = range(1, 10) if pat[0] == v1 else range(0, 10)
            r2 = range(1, 10) if pat[0] == v2 else range(0, 10)
            results = []
            for d1 in r1:
                for d2 in r2:
                    num = int(pat.replace(v1, str(d1)).replace(v2, str(d2)))
                    if num % mod_m == 0:
                        results.append(d1 + d2)
            if len(set(results)) == 1:
                return results[0], "digit_pattern_divisible"

    # 8h. Smallest positive whole number whose digits multiply to P
    m_prod = re.search(
        r"(?:smallest|més petit)[^.?!]*?(?:digits multiply to|producte de les xifres del qual és)\s+(\d+)",
        sl,
    )
    if m_prod:
        p = int(m_prod.group(1))
        if p == 0:
            return 10, "smallest_digit_product"
        if p == 1:
            return 1, "smallest_digit_product"
        digits_out: list[int] = []
        rem = p
        for d in range(9, 1, -1):
            while rem % d == 0:
                digits_out.append(d)
                rem //= d
        if rem == 1 and digits_out:
            digits_out.sort()
            return int("".join(str(d) for d in digits_out)), "smallest_digit_product"

    # 8i. Positive whole numbers < N equal to K times the sum of their digits
    m_dsum = re.search(
        r"(?:less than|més petits que)\s+(\d+)[^.?!]*?(?:equal to|iguals a)\s+(\d+)\s+(?:times|vegades)[^.?!]*?(?:sum of their digits|suma de les seves xifres)",
        sl,
    )
    if m_dsum:
        n_lim = int(m_dsum.group(1))
        k_mult = int(m_dsum.group(2))
        if n_lim <= 100000:
            cnt = sum(1 for n in range(1, n_lim) if n == k_mult * sum(int(c) for c in str(n)))
            return cnt, "digit_sum_multiple"

    return None


def _solve_race(text: str) -> Optional[tuple[Any, str]]:
    sl = text.lower()

    # 9b. Ratio position: "N runners, k times as many runners ahead as behind"
    m_ratio = re.search(r"(\d+)\s+(?:runners|corredors)", sl)
    if m_ratio and any(w in sl for w in ("ahead", "davant")) and any(w in sl for w in ("behind", "darrere")):
        n_runners = int(m_ratio.group(1))
        mult: Optional[float] = None
        if "twice" in sl or "doble" in sl:
            mult = 2.0
        elif "three times" in sl or "triple" in sl:
            mult = 3.0
        elif "half" in sl or "meitat" in sl:
            mult = 0.5
        else:
            m_k = re.search(r"(\d+)\s+(?:times|vegades)", sl)
            if m_k:
                mult = float(m_k.group(1))
        if mult is not None and n_runners > 1:
            behind_f = (n_runners - 1) / (mult + 1.0)
            if abs(behind_f - round(behind_f)) < 1e-9:
                behind = int(round(behind_f))
                ahead = (n_runners - 1) - behind
                return ahead + 1, "race_ratio_position"

    # 9a. Transitive race / ordering solver
    if not any(
        p in sl
        for p in ("finished ahead of", "finished behind", "arribat abans que", "arribat després que")
    ):
        return None

    # Identify question sentence and rank requested
    sentences = [s.strip() for s in re.split(r"[.?!]+", text) if s.strip()]
    if len(sentences) < 2:
        return None
    q_sent = sentences[-1].lower()
    rank_map = {
        "first": 0, "primer": 0, "primera": 0,
        "second": 1, "segon": 1, "segona": 1,
        "third": 2, "tercer": 2, "tercera": 2,
        "fourth": 3, "quart": 3, "quarta": 3,
        "fifth": 4, "cinquè": 4, "cinquena": 4,
        "last": -1, "últim": -1, "última": -1, "darrer": -1,
    }
    target_idx: Optional[int] = None
    for kw, idx in rank_map.items():
        if re.search(rf"\b{kw}\b", q_sent):
            target_idx = idx
            break
    if target_idx is None:
        return None

    # Parse ordering constraints from preceding sentences
    edges: list[tuple[str, str]] = []
    people: set[str] = set()

    def _clean_person(raw_p: str) -> str:
        p = raw_p.strip(" ,;:")
        p = re.sub(r"^(?:en|na|el|la|l')\s+", "", p, flags=re.IGNORECASE)
        return p.strip()

    for sent in sentences[:-1]:
        # Split compound clauses on "but" / "però" while keeping subject
        parts = re.split(r"\b(?:but|però)\b", sent, flags=re.IGNORECASE)
        subject: Optional[str] = None
        for part in parts:
            m_rel = re.search(
                r"(?:(.+?)\s+)?(?:finished\s+|ha\s+arribat\s+|va\s+arribar\s+)"
                r"(ahead of|behind|abans que|després que|davant de|darrere de)\s+(.+)",
                part.strip(),
                flags=re.IGNORECASE,
            )
            if not m_rel:
                # Try shorthand without repeating verb: "but behind Marc" / "però després que en Marc"
                m_rel = re.search(
                    r"^\s*(ahead of|behind|abans que|després que|davant de|darrere de)\s+(.+)",
                    part.strip(),
                    flags=re.IGNORECASE,
                )
                if m_rel and subject is not None:
                    rel = m_rel.group(1).lower()
                    obj = _clean_person(m_rel.group(2))
                    subj = subject
                else:
                    continue
            else:
                subj_raw = m_rel.group(1)
                subj = _clean_person(subj_raw) if subj_raw else (subject or "")
                rel = m_rel.group(2).lower()
                obj = _clean_person(m_rel.group(3))

            if not subj or not obj:
                continue
            subject = subj
            people.add(subj)
            people.add(obj)
            if rel in ("ahead of", "abans que", "davant de"):
                edges.append((subj, obj))
            else:
                edges.append((obj, subj))

    if len(people) < 2 or len(people) > 8 or not edges:
        return None

    plist = sorted(people)
    valid_orders = []
    for perm in itertools.permutations(plist):
        pos = {name: i for i, name in enumerate(perm)}
        if all(pos[u] < pos[v] for u, v in edges):
            valid_orders.append(perm)
            if len(valid_orders) > 1:
                break

    if len(valid_orders) == 1:
        order = valid_orders[0]
        if -len(order) <= target_idx < len(order):
            return order[target_idx], "race_ordering"
    return None


def _solve_modular(sl: str) -> Optional[tuple[Any, str]]:
    # 10a. Modular age
    if any(w in sl for w in ("age", "edat", "old", "anys")) and any(
        w in sl for w in ("multiple of", "múltiple de")
    ):
        m_now = re.search(r"(?:multiple of|múltiple de)\s+(\d+)", sl)
        m_next = re.search(r"(?:next year|l'any que ve)[^.?!]*?(?:multiple of|múltiple de)\s+(\d+)", sl)
        m_two = re.search(r"(?:in two years|d'aquí a dos anys)[^.?!]*?(?:multiple of|múltiple de)\s+(\d+)", sl)
        m_rng = re.search(r"(?:between|entre)\s+(\d+)\s+(?:and|i)\s+(\d+)", sl)
        if m_now and m_next and m_two and m_rng:
            a = int(m_now.group(1))
            b = int(m_next.group(1))
            c = int(m_two.group(1))
            lo, hi = int(m_rng.group(1)), int(m_rng.group(2))
            hits = [
                age
                for age in range(lo, hi + 1)
                if age % a == 0 and (age + 1) % b == 0 and (age + 2) % c == 0
            ]
            if len(hits) == 1:
                return hits[0], "modular_age"

    # 10b. Smallest integer > 1 leaving remainder R when divided by d1, d2, ...
    m_rem = re.search(
        r"(?:remainder(?: of)?|residu)\s+(\d+)[^.?!]*?(?:divided by|divideix entre)\s+([^?.!]+)",
        sl,
    )
    if m_rem and any(w in sl for w in ("smallest", "més petit")):
        rem = int(m_rem.group(1))
        divs = [int(x) for x in re.findall(r"\d+", m_rem.group(2))]
        if divs:
            lcm_val = math.lcm(*divs)
            ans = lcm_val + rem
            if ans <= 1:
                ans += lcm_val
            return ans, "lcm_remainder"

    return None


def _solve_bus_schedule(sl: str) -> Optional[tuple[Any, str]]:
    m_every = re.search(r"(?:every|cada)\s+(\d+)\s*(?:minutes?|minuts?)", sl)
    times = re.findall(r"\b(\d{1,2}):(\d{2})\b", sl)
    if m_every and len(times) == 2 and any(w in sl for w in ("next", "següent")):
        step = int(m_every.group(1))
        h1, m1 = int(times[0][0]), int(times[0][1])
        h2, m2 = int(times[1][0]), int(times[1][1])
        t1 = h1 * 60 + m1
        t2 = h2 * 60 + m2
        if step > 0 and t2 >= t1:
            k = (t2 - t1) // step + 1
            t_next = t1 + k * step
            return f"{t_next // 60}:{t_next % 60:02d}", "bus_schedule"
    return None


def _solve_class_fraction(sl: str) -> Optional[tuple[Any, str]]:
    m_frac = re.search(r"(\d+)\s*/\s*(\d+)\s+(?:of the students|de l'alumnat)", sl)
    m_diff = re.search(
        r"(?:(\d+)\s+(?:more|més)\s+(?:girls|boys|noies|nois)|(\d+)\s+(?:girls|boys|noies|nois)\s+(?:more|més))",
        sl,
    )
    if m_frac and m_diff:
        p, q = int(m_frac.group(1)), int(m_frac.group(2))
        diff = int(m_diff.group(1) or m_diff.group(2))
        if q != 0:
            frac = fractions.Fraction(p, q)
            gap = abs(2 * frac - 1)
            if gap > 0:
                total = fractions.Fraction(diff) / gap
                if total.denominator == 1:
                    return int(total), "class_fraction_diff"
    return None


def _solve_geometry_3d(sl: str) -> Optional[tuple[Any, str]]:
    # 13a. A x B rectangle cut into largest equal squares
    m_rect = re.search(r"(\d+)\s*(?:cm|m|mm)?\s*\*\s*(\d+)\s*(?:cm|m|mm)?\s*(?:rectangle|rectangle)", sl)
    if not m_rect:
        m_rect = re.search(r"(?:rectangle(?:\s+de)?)\s+(\d+)\s*(?:cm|m|mm)?\s*\*\s*(\d+)", sl)
    if m_rect and any(w in sl for w in ("equal squares", "quadrats iguals")) and any(
        w in sl for w in ("how many squares", "quants quadrats")
    ):
        a, b = int(m_rect.group(1)), int(m_rect.group(2))
        g = math.gcd(a, b)
        if g > 0:
            return (a // g) * (b // g), "rect_to_squares"

    # 13b. A x B x C block to form a cube
    m_block = re.search(r"(\d+)\s*\*\s*(\d+)\s*\*\s*(\d+)", sl)
    if m_block and any(w in sl for w in ("cube", "cub")) and any(w in sl for w in ("added", "afegir")):
        a, b, c = int(m_block.group(1)), int(m_block.group(2)), int(m_block.group(3))
        side = max(a, b, c)
        return side ** 3 - a * b * c, "block_to_cube"

    # 13c. Fill/cut A x B x C box with equal cubes (minimum number of cubes)
    if m_block and any(w in sl for w in ("equal cubes", "cubs iguals")):
        a, b, c = int(m_block.group(1)), int(m_block.group(2)), int(m_block.group(3))
        g = math.gcd(math.gcd(a, b), c)
        if g > 0:
            return (a // g) * (b // g) * (c // g), "box_to_cubes"

    # 13d. Rectangular prism container with square base B cm, submerging solid cube of edge c cm
    if any(w in sl for w in ("submerged", "submergit", "enfonsat")) and any(
        w in sl for w in ("square base", "base quadrada")
    ):
        m_base = re.search(r"(?:square base(?: of)?|base quadrada(?: de)?)\s+(\d+(?:\.\d+)?)", sl)
        m_cube = re.search(r"(?:cube[^.?!]*?(?:edge|side)[^0-9]*|cub[^.?!]*?(?:aresta|costat)[^0-9]*)(\d+(?:\.\d+)?)", sl)
        if m_base and m_cube:
            b_side = float(m_base.group(1))
            c_edge = float(m_cube.group(1))
            if b_side > c_edge > 0:
                return c_edge - (c_edge ** 3) / (b_side ** 2), "submerged_cube_height"

    # 13e. Two cubes with sides a and a + k, pour from larger to smaller leaving D liters
    m_two_cubes = re.search(
        r"two cubes.*?a\s*\+\s*(\d+).*?leaving\s+(\d+)|dos cubs.*?a\s*\+\s*(\d+).*?queden\s+(\d+)",
        sl,
    )
    if m_two_cubes:
        k_diff = int(m_two_cubes.group(1) or m_two_cubes.group(3))
        rem_vol = int(m_two_cubes.group(2) or m_two_cubes.group(4))
        for a in range(1, 200):
            if (a + k_diff) ** 3 - a ** 3 == rem_vol:
                return a ** 3, "two_cubes_volume_diff"

    return None


def _solve_last_digit_power(sl: str) -> Optional[tuple[Any, str]]:
    m = re.search(r"(?:last digit of|última xifra de)\s+(\d+)\s*\^\s*(\d+)", sl)
    if m:
        base = int(m.group(1))
        exp = int(m.group(2))
        return pow(base, exp, 10), "last_digit_power"
    return None


def _solve_calendar_weekday(sl: str) -> Optional[tuple[Any, str]]:
    m = re.search(
        r"(?:largest possible number of|com a màxim[^0-9]*en els)\s+(?:mondays|tuesdays|wednesdays|thursdays|fridays|saturdays|sundays|"
        r"dilluns|dimarts|dimecres|dijous|divendres|dissabtes|diumenges)?[^0-9]*?(?:first|primers)\s+(\d+)\s+(?:days|dies)",
        sl,
    )
    if not m:
        if any(w in sl for w in ("largest possible", "com a màxim")) and any(
            d in sl for d in ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
                              "diumenge", "dilluns", "dimarts", "dimecres", "dijous", "divendres", "dissabte")
        ):
            m = re.search(r"(?:first|primers)\s+(\d+)\s+(?:days|dies)", sl)
    if m:
        n_days = int(m.group(1))
        return math.ceil(n_days / 7), "calendar_max_weekday"
    return None


def _solve_knights_liars(sl: str) -> Optional[tuple[Any, str]]:
    if any(w in sl for w in ("knight", "cavaller")) and any(w in sl for w in ("liar", "mentider")) and any(
        w in sl for w in ("round table", "taula rodona")
    ) and any(p in sl for p in ("both of my neighbours are liars", "els meus dos veïns són mentiders")):
        m = re.search(r"(\w+)\s+(?:inhabitants|people|habitants)", sl)
        if m:
            n = _parse_word_or_int(m.group(1))
            if n is not None and n >= 3:
                return n // 2, "knights_liars_round_table"
    return None


def _solve_combinatorics_walks(sl: str) -> Optional[tuple[Any, str]]:
    # 17a. Cards 1 to N, choose 2 so sum is even/odd
    m_cards = re.search(
        r"(?:1\s+to|de\s+l'1\s+al)\s+(\d+).*?(?:cards?|target[ae]s?).*?(?:two|dues)",
        sl,
    )
    if m_cards and any(w in sl for w in ("sum", "suma")):
        n = int(m_cards.group(1))
        evens = n // 2
        odds = n - evens
        if any(w in sl for w in ("even", "parell", "parella")):
            return math.comb(odds, 2) + math.comb(evens, 2), "cards_even_sum"
        if any(w in sl for w in ("odd", "senar", "senars")):
            return odds * evens, "cards_odd_sum"

    # 17b. Frog on corner of square ABCD, K jumps back to start
    if any(w in sl for w in ("square abcd", "quadrat abcd")) and any(
        w in sl for w in ("neighbouring", "neighboring", "veïns")
    ):
        m_k = re.search(r"(?:after|després d'exactament)\s+(?:exactly\s+)?(\d+)\s+(?:jumps?|salts?)", sl)
        if m_k:
            k = int(m_k.group(1))
            # DP on 4-cycle: 0=A, 1=B, 2=C, 3=D
            dp = [1, 0, 0, 0]
            for _ in range(k):
                dp = [
                    dp[1] + dp[3],
                    dp[0] + dp[2],
                    dp[1] + dp[3],
                    dp[0] + dp[2],
                ]
            return dp[0], "square_random_walk"

    # 17c. Subsets of {1, ..., N} whose sum is a multiple of M
    m_sub = re.search(
        r"\{1,\s*2[^}]*,\s*(\d+)\}[^.?!]*?(?:multiple of|múltiple de)\s+(\d+)",
        sl,
    )
    if m_sub and any(w in sl for w in ("subsets", "subconjunts")):
        n = int(m_sub.group(1))
        mod_m = int(m_sub.group(2))
        if 1 <= n <= 200 and 1 <= mod_m <= 200:
            dp = [1] + [0] * (mod_m - 1)
            for x in range(1, n + 1):
                new_dp = [0] * mod_m
                for r in range(mod_m):
                    new_dp[r] += dp[r]
                    new_dp[(r + x) % mod_m] += dp[r]
                dp = new_dp
            ans = dp[0]
            if any(p in sl for p in ("excluding the empty", "sense el conjunt buit")):
                ans -= 1
            return ans, "subset_sum_divisible"

    return None


def _solve_number_theory_algebra(sl: str) -> Optional[tuple[Any, str]]:
    sl_norm = sl.translate(_SUBSCRIPT_MAP).translate(_SUPERSCRIPT_MAP)

    # 18a. Positive integers n where n^2 is a divisor of M
    m_sqdiv = re.search(r"n(?:\^?2)\s+(?:is\s+)?(?:a\s+divisor\s+of|és\s+un\s+divisor\s+de)\s+(\d+)", sl_norm)
    if m_sqdiv:
        m_val = int(m_sqdiv.group(1))
        if m_val >= 1:
            cnt = sum(1 for n in range(1, math.isqrt(m_val) + 1) if m_val % (n * n) == 0)
            return cnt, "square_divisors"

    # 18b. Three children ages with product P and sum S
    if any(w in sl for w in ("product", "producte")) and any(w in sl for w in ("sum", "suma")):
        m_p = re.search(r"(?:product is|producte és)\s+(\d+)", sl)
        m_s = re.search(r"(?:sum is|suma és)\s+(\d+)", sl)
        if m_p and m_s and any(w in sl for w in ("three", "tres")):
            p = int(m_p.group(1))
            s = int(m_s.group(1))
            triples = []
            for a in range(1, s + 1):
                if p % a != 0:
                    continue
                for b in range(a, s - a + 1):
                    c = s - a - b
                    if c >= b and a * b * c == p:
                        triples.append((a, b, c))
            if triples:
                want_youngest = any(w in sl for w in ("youngest", "més petit"))
                vals = [t[0] if want_youngest else t[2] for t in triples]
                if len(set(vals)) == 1:
                    return vals[0], "product_sum_triples"

    # 18c. Numbers 1..N split into two groups, prod1 divisible by prod2, smallest quotient
    m_part = re.search(
        r"(?:1,\s*2,\s*3,\s*\.\.\.,\s*|1\s+to\s+)(\d+).*?(?:two groups|dos grups).*?(?:quotient|quocient)",
        sl,
    )
    if m_part:
        n = int(m_part.group(1))
        if 2 <= n <= 16:
            total_prod = math.factorial(n)
            # Find largest prod(G2) <= isqrt(total_prod) such that total_prod % (prod(G2)**2) == 0
            limit = math.isqrt(total_prod)
            best_g2 = 1

            def _dfs(idx: int, cur: int) -> None:
                nonlocal best_g2
                if cur > limit:
                    return
                if total_prod % (cur * cur) == 0 and cur > best_g2:
                    best_g2 = cur
                for nxt in range(idx, n + 1):
                    if cur * nxt <= limit:
                        _dfs(nxt + 1, cur * nxt)

            _dfs(2, 1)
            return total_prod // (best_g2 * best_g2), "partition_product_quotient"

    # 18d. Quadratic or factored inequality: n^2 - Bn + C < 0 or (x^2 - A)(x^2 - B) < 0
    m_fact = re.search(
        r"\(\s*[nx](?:\^?2)\s*([+-])\s*(\d+)\s*\)\s*\(\s*[nx](?:\^?2)\s*([+-])\s*(\d+)\s*\)\s*(<=|>=|<|>)\s*0",
        sl_norm,
    )
    if m_fact:
        s1 = 1 if m_fact.group(1) == "+" else -1
        c1 = s1 * int(m_fact.group(2))
        s2 = 1 if m_fact.group(3) == "+" else -1
        c2 = s2 * int(m_fact.group(4))
        op = m_fact.group(5)
        bound = max(abs(c1), abs(c2)) + 2
        sols = []
        for x in range(-bound, bound + 1):
            val = (x * x + c1) * (x * x + c2)
            ok = (val < 0) if op == "<" else (val <= 0) if op == "<=" else (val > 0) if op == ">" else (val >= 0)
            if ok:
                sols.append(x)
        if any(w in sl for w in ("sum", "suma")):
            return sum(sols), "inequality_integers"
        return len(sols), "inequality_integers"

    m_quad = re.search(
        r"[nx](?:\^?2)\s*([+-])\s*(\d+)\s*[nx]\s*([+-])\s*(\d+)\s*(<=|>=|<|>)\s*0",
        sl_norm,
    )
    if m_quad:
        sb = 1 if m_quad.group(1) == "+" else -1
        b = sb * int(m_quad.group(2))
        sc = 1 if m_quad.group(3) == "+" else -1
        c = sc * int(m_quad.group(4))
        op = m_quad.group(5)
        if op in ("<", "<="):
            bound = abs(b) + abs(c) + 5
            sols = []
            for x in range(-bound, bound + 1):
                val = x * x + b * x + c
                if (op == "<" and val < 0) or (op == "<=" and val <= 0):
                    sols.append(x)
            if any(w in sl for w in ("sum", "suma")):
                return sum(sols), "inequality_integers"
            return len(sols), "inequality_integers"

    # 18e. Ordered pairs 1/x + 1/y = 1/k
    m_uf = re.search(r"1\s*/\s*x\s*\+\s*1\s*/\s*y\s*=\s*1\s*/\s*(\d+)", sl)
    if m_uf:
        k = int(m_uf.group(1))
        if k >= 1:
            k2 = k * k
            cnt = 0
            for d in range(1, math.isqrt(k2) + 1):
                if k2 % d == 0:
                    cnt += 1 if d * d == k2 else 2
            return cnt, "unit_fraction_pairs"

    # 18f. Remainder of 1! + 2! + ... + N! divided by M
    m_fac = re.search(
        r"1!\s*\+\s*2!\s*\+(?:\s*3!\s*\+)?[^0-9]*?(?:dots|\.\.\.)\s*\+\s*(\d+)![^0-9]*?(?:divided by|entre)\s+(\d+)",
        sl,
    )
    if m_fac:
        n = int(m_fac.group(1))
        mod_m = int(m_fac.group(2))
        if mod_m >= 1:
            fact = 1
            total = 0
            for i in range(1, n + 1):
                fact = (fact * i) % mod_m
                if fact == 0:
                    break
                total = (total + fact) % mod_m
            return total, "factorial_sum_mod"

    # 18g. Positive integers n <= N for which n^2 + n + 1 is divisible by p
    m_pmod = re.search(
        r"n\s*(?:<=|≤)\s*(\d+)[^.?!]*?n(?:\^?2)\s*\+\s*n\s*\+\s*1[^.?!]*?(?:divisible by|divisible per)\s+(\d+)",
        sl_norm,
    )
    if m_pmod:
        n_lim = int(m_pmod.group(1))
        p = int(m_pmod.group(2))
        if p >= 1 and n_lim >= 1:
            full_cycles, rem = divmod(n_lim, p)
            per_cycle = sum(1 for r in range(p) if (r * r + r + 1) % p == 0)
            extra = sum(1 for r in range(1, rem + 1) if (r * r + r + 1) % p == 0)
            return full_cycles * per_cycle + extra, "poly_mod_count"

    # 18h. Digit-sum sequence a1 = c, a_{i+1} = a_i + sum_digits(a_i), find a_k
    m_seq = re.search(
        r"a1\s*=\s*(\d+).*?(?:sum of its digits|suma de les seves xifres).*?(?:what is|quant val)\s+a(\d+)",
        sl_norm,
    )
    if m_seq:
        val = int(m_seq.group(1))
        k = int(m_seq.group(2))
        if 1 <= k <= 10000:
            for _ in range(k - 1):
                val += sum(int(c) for c in str(val))
            return val, "digit_sum_sequence"

    # 18i. Functional equation a*f(x) + b*f(1/x) = c*x -> f(x0)
    m_feq = re.search(
        r"(?:(\d+)\s*\*?\s*)?f\(x\)\s*([+-])\s*(?:(\d+)\s*\*?\s*)?f\(1/x\)\s*=\s*([+-]?\d+)\s*\*?\s*x.*?f\(\s*([+-]?\d+(?:/\d+)?)\s*\)",
        sl,
    )
    if m_feq:
        a = int(m_feq.group(1) or 1)
        b = int(m_feq.group(3) or 1) * (1 if m_feq.group(2) == "+" else -1)
        c = int(m_feq.group(4))
        x0 = fractions.Fraction(m_feq.group(5))
        denom = a * a - b * b
        if denom != 0 and x0 != 0:
            ans = (a * c * x0 - b * c / x0) / denom
            if ans.denominator == 1:
                return int(ans), "functional_eq_reciprocal"
            return ans, "functional_eq_reciprocal"

    # 18j. Square vertex products: "(a+c)(b+d) = N" -> sum of 4 sides
    if "vertices" in sl and "product" in sl and "square" in sl:
        m_vp = re.search(r"sum of (?:the )?(?:numbers at )?(?:the )?vertices is (\d+)", sl)
        if m_vp:
            n_val = int(m_vp.group(1))
            sums = set()
            for x in range(2, n_val + 1):
                if n_val % x == 0:
                    y = n_val // x
                    if y >= 2:
                        sums.add(x + y)
            if len(sums) == 1:
                return next(iter(sums)), "square_vertex_products"

    return None


def _solve_probability(sl: str) -> Optional[tuple[Any, str]]:
    # 19a. Bag with R red and B blue balls, drawn without replacement until blue, prob of k draws
    if any(w in sl for w in ("without putting them back", "sense tornar-les", "without replacement")):
        m_rb = re.search(
            r"(\d+)(?:\s+(?:balls?|boles))?\s+(?:red|vermell\w*)[^0-9]*(\d+)(?:\s+(?:balls?|boles))?\s+(?:blue|blav\w*)",
            sl,
        )
        m_k = re.search(
            r"(?:exactly|exactament)\s+(\w+)(?:\s+(?:balls?|boles))?",
            sl,
        )
        if m_rb and m_k:
            r_balls = int(m_rb.group(1))
            b_balls = int(m_rb.group(2))
            k = _parse_word_or_int(m_k.group(1))
            if k is not None and k >= 1:
                if k - 1 > r_balls or k > r_balls + b_balls:
                    return fractions.Fraction(0, 1), "balls_until_blue"
                num = math.perm(r_balls, k - 1) * b_balls
                den = math.perm(r_balls + b_balls, k)
                return fractions.Fraction(num, den), "balls_until_blue"

    # 19b. Fair coin tossed N times, probability of k consecutive heads at least once
    if any(w in sl for w in ("coin", "moneda")) and any(w in sl for w in ("consecutive", "seguides")):
        m_n = re.search(r"(?:tossed|llança)\s+(\d+)\s+(?:times|vegades)", sl)
        m_k = re.search(r"(\w+)\s+(?:cares|heads)\s+(?:seguides|in a row)", sl)
        if not m_k:
            m_k = re.search(r"(\w+)\s+(?:consecutive|seguides)", sl)
        if m_n and m_k:
            n_toss = int(m_n.group(1))
            k_streak = _parse_word_or_int(m_k.group(1))
            if k_streak is not None and 1 <= k_streak <= n_toss <= 30:
                # DP counting sequences WITHOUT k consecutive heads
                dp = [1] + [0] * (k_streak - 1)
                for _ in range(n_toss):
                    new_dp = [0] * k_streak
                    new_dp[0] = sum(dp)  # tail resets streak
                    for j in range(1, k_streak):
                        new_dp[j] = dp[j - 1]  # head extends streak
                    dp = new_dp
                total = 1 << n_toss
                favorable = total - sum(dp)
                return fractions.Fraction(favorable, total), "coin_consecutive_heads"

    return None


def solve_neuro_symbolic(
    statement: str,
    options: dict[str, str],
    raw_statement: str = "",
) -> Optional[tuple[str, str]]:
    """Run general, sound math solvers and return (letter, solver_name) if uniquely matched."""
    if not options or (not statement and not raw_statement):
        return None

    candidates: list[str] = []
    for txt in (statement, raw_statement):
        c = _clean_text(txt)
        if c and c not in candidates:
            candidates.append(c)

    solvers = (
        _solve_arith_eval,
        _solve_equalization,
        _solve_except_n,
        _solve_alternating_jumps,
        _solve_jump_with_slip,
        _solve_digital_clock,
        _solve_coins,
        _solve_digit_combinatorics,
        _solve_race,
        _solve_modular,
        _solve_bus_schedule,
        _solve_class_fraction,
        _solve_geometry_3d,
        _solve_last_digit_power,
        _solve_calendar_weekday,
        _solve_knights_liars,
        _solve_combinatorics_walks,
        _solve_number_theory_algebra,
        _solve_probability,
    )

    for text in candidates:
        sl = text.lower()
        for solver in solvers:
            res = solver(text if solver is _solve_race else sl)
            if res is None:
                continue
            val, solver_name = res
            opt = match_value_to_unique_option(val, options)
            if opt is not None:
                return opt, solver_name

    return None
