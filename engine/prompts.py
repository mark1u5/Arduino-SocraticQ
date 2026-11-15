"""Jane Street-inspired Socratic prompt builder, Socratic Judo guardrails, and silent math oracle."""

import ast
import math
import re
from fractions import Fraction
from typing import Any, Literal, Optional

from contracts.schemas import PedagogicalMove

BANNED_PRAISE = (
    "great job",
    "great try",
    "good question",
    "awesome",
    "brilliant",
    "spot-on",
    "perfect",
)
_BANNED_PRAISE_RE = re.compile(
    r"(?i)\b(?:" + "|".join(re.escape(p) for p in BANNED_PRAISE) + r")\b"
)

EXPLICIT_LEAK_RE = re.compile(
    r"(?i)\b(?:answer|correct\s+(?:option|choice|letter)|solution)\s*(?:is|:|=)\s*[A-E]\b"
    r"|\boption\s+[A-E]\s+is\s+(?:right|correct)\b"
)

EXPLICIT_VALUE_LEAK_RE = re.compile(
    r"(?i)\b(?:answer|final\s+(?:answer|number|value)|solution|result|derivative)\s*"
    r"(?:is|:|=|equals?)\s*[-+]?(?:\d+(?:\.\d+)?(?:/\d+)?)\b"
)

SOCRATIC_DIMENSIONS: dict[PedagogicalMove, str] = {
    "DECOMPOSE_PROBLEM": "Clarification",
    "COUNTEREXAMPLE_CHECK": "Probing Assumptions",
    "INVARIANT_NUDGE": "Probing Reasons & Evidence",
    "PARITY_CHECK": "Alternative Perspectives",
    "BOUNDARY_TEST": "Implications & Consequences",
    "AFFIRM_CORRECT": "Meta-Questioning",
}

MOVE_INSTRUCTIONS: dict[PedagogicalMove, str] = {
    "DECOMPOSE_PROBLEM": "Ask the student to isolate one smaller piece or relationship in the problem.",
    "COUNTEREXAMPLE_CHECK": "Challenge their assumption by asking what happens in a tiny concrete case.",
    "INVARIANT_NUDGE": "Point them toward what quantity or total stays unchanged across steps.",
    "PARITY_CHECK": "Ask how coloring, odd/even, or alternating states constrains the possibilities.",
    "BOUNDARY_TEST": "Ask them to test the worst-case, smallest, or boundary value.",
    "AFFIRM_CORRECT": "Confirm their answer is right without empty praise, then ask one short 'why' question about the general rule.",
}

_JAILBREAK_RE = re.compile(
    r"(?i)\b(?:"
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:(?:previous|prior|above|your|system)\s+)*(?:instructions|prompts?|rules?|guardrails?)"
    r"|system\s+prompt"
    r"|jailbreak"
    r"|dan\b"
    r"|developer\s+mode"
    r"|reveal\s+(?:your\s+)?(?:instructions|prompt|rules)"
    r"|override\s+(?:your\s+)?(?:instructions|rules|safety)"
    r"|bypass\s+(?:safety|guardrails?|rules?)"
    r"|act\s+as\s+(?:an?\s+)?unfiltered"
    r")\b"
)
_HEX_PAYLOAD_RE = re.compile(r"(?<![a-zA-Z0-9])[0-9a-fA-F]{24,}(?![a-zA-Z0-9])")
_B64_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")

_UNSAFE_OFFTOPIC_RE = re.compile(
    r"(?i)\b(?:"
    r"fuck(?:ing|ed|er)?|shit(?:ty)?|bitch(?:es)?|asshole|bastard|cunt|dickhead"
    r"|sql\s+injection|ransomware|malware|keylogger|ddos|phishing|hack\s+into|exploit\s+payload"
    r"|make\s+a\s+bomb|kill\s+yourself|suicide"
    r")\b"
)

_OFFLOADING_RE = re.compile(
    r"(?i)\b(?:"
    r"(?:just\s+)?(?:give|tell|show)\s+me\s+(?:the\s+)?(?:answer|option|solution|final\s+(?:number|answer|value)|what\s+[a-z]\s+(?:is|equals)|[a-z]\b(?:\s+for|\s*=|\s*$))"
    r"|what\s+is\s+(?:the\s+(?:final\s+(?:number|answer|result)|correct\s+option|answer)|[a-z]\s+(?:equal(?:\s+to)?\s*(?:\?|$)|in\s+(?:this|the|it)\b))"
    r"|which\s+option\s+is\s+(?:right|correct)"
    r"|(?:solve|do)\s+(?:it|this)\s+for\s+me"
    r"|no\s+hints?\s*,?\s*just\s+(?:the\s+)?answer"
    r"|skip\s+(?:the\s+)?hints?"
    r"|stop\s+asking\s+questions"
    r")\b"
)

_BINARY_SEARCH_RE = re.compile(
    r"(?i)(?:"
    r"\bis\s+(?:it|that|the\s+(?:answer|option|correct\s+option))\s+(?:option\s+)?[A-E]\b"
    r"|\bis\s+(?:option\s+)?[A-E]\s+(?:correct|right|the\s+answer)\b"
    r"|\bis\s+[a-z]\s*(?:>=|<=|!=|>|<|=)\s*-?\d+(?:\.\d+)?"
    r"|\bis\s+(?:the\s+)?(?:answer|result|value|it)\s*(?:=|equals?|greater\s+than|less\s+than|more\s+than|bigger\s+than|smaller\s+than|at\s+least|at\s+most|between)?\s*-?\d+(?:\.\d+)?"
    r"|\bis\s+it\s+(?:greater|less|more|bigger|smaller|higher|lower)\s+than\b"
    r")"
)


def clamp_text(text: str, max_len: int, append_question_mark: bool = False) -> str:
    """Collapse whitespace and trim to max_len at a word boundary."""
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= max_len:
        return cleaned
    clipped = cleaned[: max_len - 1].rsplit(" ", 1)[0].rstrip(",;:")
    base = clipped or cleaned[: max_len - 1]
    if append_question_mark:
        return base + "?"
    return base.rstrip(".!?") + "." if max_len > 32 else base


def _extract_student_utterance(student_note: Optional[str]) -> str:
    if not student_note:
        return ""
    text = student_note.strip()
    if text.startswith("Prev tutor:"):
        if "|" not in text:
            return ""
        text = text.split("|", 1)[1].strip()
    return text


def _looks_like_encoded_payload(text: str) -> bool:
    if _HEX_PAYLOAD_RE.search(text):
        return True
    for match in _B64_TOKEN_RE.finditer(text):
        tok = match.group(0)
        has_digit_or_sym = any(c.isdigit() or c in "+/=" for c in tok)
        has_mixed_case = any(c.islower() for c in tok) and any(c.isupper() for c in tok)
        if has_digit_or_sym or has_mixed_case:
            return True
    return False


def inspect_student_input(
    student_note: Optional[str],
    problem: dict[str, Any],
    mode: Literal["teach", "solve"] = "teach",
) -> dict[str, Any] | None:
    """Pre-SLM Socratic Judo guardrail detecting jailbreaks, offloading, and binary-search probes."""
    utterance = _extract_student_utterance(student_note)
    if not utterance:
        return None

    category = problem.get("category", "math")

    if _JAILBREAK_RE.search(utterance) or _looks_like_encoded_payload(utterance):
        return {
            "hint_text": clamp_text(
                f"Let's keep our focus on this {category} problem—what is the very first quantity or relationship you can identify from the statement?",
                240,
            ),
            "pedagogical_move": "DECOMPOSE_PROBLEM",
            "misconception_tag": "JAILBREAK_ATTEMPT",
            "lcd_summary": clamp_text("Refocus on first step", 32),
            "guardrail_reason": "Prompt injection or encoded payload intercepted.",
        }

    if _UNSAFE_OFFTOPIC_RE.search(utterance):
        return {
            "hint_text": clamp_text(
                "Let's stay focused on mathematical reasoning here—which given number or condition in the problem should we inspect first?",
                240,
            ),
            "pedagogical_move": "DECOMPOSE_PROBLEM",
            "misconception_tag": "UNSAFE_OFFTOPIC",
            "lcd_summary": clamp_text("Stay on math task", 32),
            "guardrail_reason": "Unsafe or off-topic input redirected to math reasoning.",
        }

    if mode == "solve":
        return None

    has_custom_eq = bool(re.search(r"[0-9a-zA-Z_]\s*=\s*[-+]?[0-9a-zA-Z_]", utterance))
    has_math_expr = bool(re.search(r"(?:\d\s*[a-zA-Z]|[+\-*/^])", utterance))
    if _OFFLOADING_RE.search(utterance) and not has_custom_eq:
        return {
            "hint_text": clamp_text(
                "If I gave you the final result without your own steps, how would you check if it works—what small first step can you test right now?",
                240,
            ),
            "pedagogical_move": "COUNTEREXAMPLE_CHECK",
            "misconception_tag": "OFFLOADING_ATTEMPT",
            "lcd_summary": clamp_text("Test your own step", 32),
            "guardrail_reason": "Cognitive offloading redirected to active problem solving.",
        }

    # Allow legitimate math attempts that include an expression/equation (e.g. "2x + 5 = 17, is x = 6?" or "2x + 5 > 17")
    if _BINARY_SEARCH_RE.search(utterance) and not (has_math_expr and "=" in utterance):
        return {
            "hint_text": clamp_text(
                "Instead of narrowing guesses by yes/no checks, what happens when you substitute that candidate back into the problem's main condition?",
                240,
            ),
            "pedagogical_move": "COUNTEREXAMPLE_CHECK",
            "misconception_tag": "BINARY_SEARCH_PROBE",
            "lcd_summary": clamp_text("Test guess vs condition", 32),
            "guardrail_reason": "Binary-search answer probing redirected to condition verification.",
        }

    return None


def _normalize_unicode_math(text: str) -> str:
    """Normalize common Unicode math operators/superscripts to ASCII equivalents."""
    return (
        text.replace("×", "*")
        .replace("÷", "/")
        .replace("·", "*")
        .replace("∙", "*")
        .replace("⋅", "*")
        .replace("−", "-")
        .replace("–", "-")
        .replace("²", "^2")
        .replace("³", "^3")
    )


def _normalize_math_expr(expr: str) -> str:
    s = _normalize_unicode_math(expr).replace("^", "**")
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    s = re.sub(r"(\d)\s*(?![eE][+-]?\d+\b)([a-zA-Z(])", r"\1*\2", s)
    s = re.sub(r"\)\s*(\d|[a-zA-Z(])", r")*\1", s)
    s = re.sub(
        r"([a-zA-Z]+)\s*\(",
        lambda m: f"{m.group(1)}(" if m.group(1).lower() in {"abs", "sqrt"} else f"{m.group(1)}*(",
        s,
    )
    return s.strip()


def _poly_add(a: dict[int, Fraction], b: dict[int, Fraction], sign: int = 1) -> dict[int, Fraction]:
    out = dict(a)
    for deg, coeff in b.items():
        val = out.get(deg, Fraction(0)) + sign * coeff
        if val == 0:
            out.pop(deg, None)
        else:
            out[deg] = val
    return out


def _poly_mul(a: dict[int, Fraction], b: dict[int, Fraction]) -> dict[int, Fraction]:
    out: dict[int, Fraction] = {}
    for d1, c1 in a.items():
        for d2, c2 in b.items():
            deg = d1 + d2
            if deg > 12:
                raise ValueError("Degree too high")
            val = out.get(deg, Fraction(0)) + c1 * c2
            if val == 0:
                out.pop(deg, None)
            else:
                out[deg] = val
    return out


def _eval_poly_ast(node: ast.AST, var_name: Optional[str]) -> dict[int, Fraction]:
    if isinstance(node, ast.Expression):
        return _eval_poly_ast(node.body, var_name)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        val = Fraction(str(node.value))
        return {} if val == 0 else {0: val}
    if isinstance(node, ast.Name):
        if var_name and node.id == var_name:
            return {1: Fraction(1)}
        raise ValueError(f"Unsupported symbol: {node.id}")
    if isinstance(node, ast.UnaryOp):
        inner = _eval_poly_ast(node.operand, var_name)
        if isinstance(node.op, ast.UAdd):
            return inner
        if isinstance(node.op, ast.USub):
            return {d: -c for d, c in inner.items()}
        raise ValueError("Unsupported unary op")
    if isinstance(node, ast.BinOp):
        left = _eval_poly_ast(node.left, var_name)
        right = _eval_poly_ast(node.right, var_name)
        if isinstance(node.op, ast.Add):
            return _poly_add(left, right, 1)
        if isinstance(node.op, ast.Sub):
            return _poly_add(left, right, -1)
        if isinstance(node.op, ast.Mult):
            return _poly_mul(left, right)
        if isinstance(node.op, ast.Div):
            if set(right.keys()) - {0} or 0 not in right or right[0] == 0:
                raise ValueError("Non-constant or zero divisor")
            denom = right[0]
            return {d: c / denom for d, c in left.items()}
        if isinstance(node.op, ast.Pow):
            if set(right.keys()) - {0}:
                raise ValueError("Non-constant exponent")
            exp_frac = right.get(0, Fraction(0))
            if exp_frac.denominator != 1 or abs(exp_frac.numerator) > 12:
                raise ValueError("Unsupported exponent")
            exp = exp_frac.numerator
            if exp < 0:
                if set(left.keys()) - {0} or 0 not in left or left[0] == 0:
                    raise ValueError("Negative exponent on variable or zero")
                return {0: left[0] ** exp}
            res: dict[int, Fraction] = {0: Fraction(1)}
            for _ in range(exp):
                res = _poly_mul(res, left)
            return res
    raise ValueError("Unsupported AST node")


def _parse_poly(expr: str, var_name: Optional[str]) -> dict[int, Fraction]:
    tree = ast.parse(_normalize_math_expr(expr), mode="eval")
    return _eval_poly_ast(tree, var_name)


def _format_fraction_variants(val: Fraction) -> set[str]:
    out: set[str] = set()
    if val.denominator == 1:
        out.add(str(val.numerator))
        return out
    out.add(f"{val.numerator}/{val.denominator}")
    flt = val.numerator / val.denominator
    out.add(f"{flt:g}")
    if abs(flt - round(flt, 4)) < 1e-9:
        out.add(f"{flt:.4f}".rstrip("0").rstrip("."))
    return out


def _exact_sqrt_fraction(val: Fraction) -> Fraction | None:
    if val < 0:
        return None
    if val == 0:
        return Fraction(0)
    sn = math.isqrt(val.numerator)
    sd = math.isqrt(val.denominator)
    if sn * sn == val.numerator and sd * sd == val.denominator:
        return Fraction(sn, sd)
    return None


def _format_poly_variants(poly: dict[int, Fraction], var_name: str) -> set[str]:
    if not poly:
        return {"0"}
    if set(poly.keys()) == {0}:
        return _format_fraction_variants(poly[0])

    def _render(mul_sym: str, pow_sym: str) -> str:
        parts: list[str] = []
        for deg in sorted(poly.keys(), reverse=True):
            coeff = poly[deg]
            if coeff == 0:
                continue
            sign = "-" if coeff < 0 else "+"
            abs_c = abs(coeff)
            c_str = str(abs_c.numerator) if abs_c.denominator == 1 else f"({abs_c.numerator}/{abs_c.denominator})"
            if deg == 0:
                term = c_str
            elif deg == 1:
                term = var_name if abs_c == 1 else f"{c_str}{mul_sym}{var_name}"
            else:
                v_part = f"{var_name}{pow_sym}{deg}"
                term = v_part if abs_c == 1 else f"{c_str}{mul_sym}{v_part}"
            if not parts:
                parts.append(f"-{term}" if sign == "-" else term)
            else:
                parts.append(f" {sign} {term}")
        return "".join(parts)

    variants = {
        _render("", "^"),
        _render("*", "^"),
        _render("", "**"),
        _render("*", "**"),
    }
    variants.update(v.replace(" ", "") for v in list(variants))
    return variants


def _extract_allowed_givens(statement: str) -> set[str]:
    givens: set[str] = set()
    for tok in re.findall(r"(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?(?:/\d+)?", statement):
        givens.add(tok)
        if "/" in tok:
            num, den = tok.split("/", 1)
            givens.add(num)
            givens.add(den)
        elif "." in tok:
            givens.add(tok.rstrip("0").rstrip(".") or "0")
    return givens


def _parse_constant_math(text: str) -> Optional[float]:
    """Parse a constant numeric/fraction/percentage/sqrt option value into float."""
    cleaned = _normalize_unicode_math(str(text or "")).strip().replace(",", ".")
    if not cleaned:
        return None
    # Strip surrounding $...$ if present
    if cleaned.startswith("$") and cleaned.endswith("$") and len(cleaned) >= 2:
        cleaned = cleaned[1:-1].strip()
    # Convert LaTeX \frac{a}{b}
    cleaned = re.sub(r"\\d?frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}", r"((\1)/(\2))", cleaned)
    cleaned = cleaned.replace("\\sqrt", "sqrt")
    pct_m = re.fullmatch(r"([-+]?\d+(?:\.\d+)?)\s*%", cleaned)
    if pct_m:
        return float(pct_m.group(1)) / 100.0
    # Reject anything with unknown letters other than sqrt
    names = set(re.findall(r"[a-zA-Z_]+", cleaned))
    if names - {"sqrt"}:
        return None
    try:
        tree = ast.parse(_normalize_math_expr(cleaned), mode="eval")
        val = _eval_scalar_ast(tree, var_name=None, var_value=0.0)
        if math.isfinite(val):
            return val
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return None


def _eval_scalar_ast(node: ast.AST, var_name: Optional[str], var_value: float) -> float:
    """Safely evaluate a scalar arithmetic/algebra AST node with one optional variable."""
    if isinstance(node, ast.Expression):
        return _eval_scalar_ast(node.body, var_name, var_value)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name):
        if var_name and node.id == var_name:
            return float(var_value)
        raise ValueError(f"Unsupported symbol: {node.id}")
    if isinstance(node, ast.UnaryOp):
        inner = _eval_scalar_ast(node.operand, var_name, var_value)
        if isinstance(node.op, ast.UAdd):
            return +inner
        if isinstance(node.op, ast.USub):
            return -inner
        raise ValueError("Unsupported unary op")
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in {"abs", "sqrt"} and len(node.args) == 1 and not node.keywords:
            arg_val = _eval_scalar_ast(node.args[0], var_name, var_value)
            if node.func.id == "abs":
                return abs(arg_val)
            if arg_val < 0:
                raise ValueError("Negative sqrt domain")
            return math.sqrt(arg_val)
        raise ValueError("Unsupported call")
    if isinstance(node, ast.BinOp):
        left = _eval_scalar_ast(node.left, var_name, var_value)
        right = _eval_scalar_ast(node.right, var_name, var_value)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left / right
        if isinstance(node.op, ast.Pow):
            if abs(right) > 20 or (left == 0 and right < 0):
                raise ValueError("Unsupported exponent")
            res = left ** right
            if isinstance(res, complex) or not math.isfinite(res):
                raise ValueError("Non-real or non-finite power")
            return float(res)
    raise ValueError("Unsupported AST node")


def _asks_compound_target(statement: str, var_name: str) -> bool:
    """Return True if statement asks for a compound expression of var_name (e.g., 'what is 3x?')."""
    if not var_name:
        return False
    m = re.search(
        r"(?i)\b(?:what\s+is|find|calculate|determine|compute|value\s+of|"
        r"quin\s+és|quina\s+és|calcula|troba|valor\s+de)\s+([^?.!;]+)",
        statement,
    )
    if not m:
        return False
    asked = m.group(1).strip()
    if "=" in asked:
        return False
    # If the asked phrase contains an arithmetic operator or coefficient attached to var_name (e.g., "3x", "x + 1", "x^2")
    if re.search(rf"(?:\d\s*{re.escape(var_name)}\b|{re.escape(var_name)}\s*[+\-*/^×÷·−]|[+\-*/^×÷·−]\s*{re.escape(var_name)}\b)", asked):
        return True
    return False


def verify_equation_options(statement: str, options: dict[str, str]) -> Optional[str]:
    """Substitute numeric candidate options A..E into a 1-var LHS = RHS equation; return unique match."""
    if not statement or "=" not in statement or not options:
        return None
    norm_stmt = _normalize_unicode_math(statement)
    parsed_opts: dict[str, float] = {}
    for letter in ("A", "B", "C", "D", "E"):
        if letter in options:
            val = _parse_constant_math(str(options[letter]))
            if val is not None:
                parsed_opts[letter] = val
    if len(parsed_opts) < 3:
        return None

    clauses = re.split(r"[?;,\n]|(?<!\d)\.(?!\d)", norm_stmt)
    # Collect single-letter lowercase variables across all equation clauses to avoid solving one equation of a system
    all_eq_vars: set[str] = set()
    for cl in clauses:
        if cl.count("=") == 1:
            cl_scan = re.sub(r"\d[eE][+-]?\d+", "", cl)
            for tok in re.findall(r"[a-zA-Z_]+", cl_scan):
                if tok not in {"abs", "sqrt"} and len(tok) == 1 and tok.islower():
                    all_eq_vars.add(tok)

    for clause in clauses:
        if clause.count("=") != 1:
            continue
        lhs_raw, rhs_raw = clause.split("=", 1)
        lhs_clean = re.sub(
            r"^(?:.*?:\s*|.*?\b(?:solve|if|find|given|that|for|where|suppose|let|si|quan|resol|troba)\b\s+)",
            "",
            lhs_raw,
            flags=re.I,
        ).strip()
        rhs_clean = re.split(
            r"[?,;!]|(?:\s+(?:for|where|what|find|and|then|so|quin|quina|quants)\b)",
            rhs_raw,
            maxsplit=1,
            flags=re.I,
        )[0].strip()
        if not lhs_clean or not rhs_clean:
            continue

        lhs_expr = re.sub(r"\|([^|]+)\|", r"abs(\1)", lhs_clean)
        rhs_expr = re.sub(r"\|([^|]+)\|", r"abs(\1)", rhs_clean)
        # Keep only expressions using mathematical characters
        allowed_chars_re = re.compile(r"^[0-9a-zA-Z_+\-*/^().%\s]+$")
        if not allowed_chars_re.match(lhs_expr) or not allowed_chars_re.match(rhs_expr):
            continue

        # Reject ambiguous additive percentages like "x + 20% = 120" (could mean x*1.2 or x+0.2)
        if "%" in f"{lhs_expr}{rhs_expr}" and re.search(r"[+\-]", f"{lhs_expr}{rhs_expr}"):
            continue

        # Exclude scientific notation 'e' from variable scan; require single lowercase math variable [a-z]
        scan_text = re.sub(r"\d[eE][+-]?\d+", "", f"{lhs_expr} {rhs_expr}")
        vars_found = sorted(set(re.findall(r"[a-zA-Z_]+", scan_text)) - {"abs", "sqrt"})
        if len(vars_found) != 1 or not re.fullmatch(r"[a-z]", vars_found[0]):
            continue
        var_name = vars_found[0]
        if var_name in {"m", "g", "l", "h", "s"} and re.search(rf"\d\s+{var_name}\b", f"{lhs_clean} {rhs_clean}"):
            continue
        if len(all_eq_vars) > 1:
            continue

        # Skip if the question asks for a different single-letter variable (e.g. "what is y?")
        asked_m = re.search(
            r"(?i)\b(?:what\s+is|find|calculate|determine|compute|value\s+of|quin\s+és|quina\s+és|calcula|troba)\s+([a-z])\b",
            norm_stmt,
        )
        if asked_m and asked_m.group(1) != var_name:
            continue

        # Skip given-assignment clauses like "x = 3" or "3 = x"
        if (lhs_clean == var_name and re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:/\d+)?", rhs_clean)) or (
            rhs_clean == var_name and re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:/\d+)?", lhs_clean)
        ):
            continue

        if _asks_compound_target(norm_stmt, var_name):
            continue

        try:
            lhs_tree = ast.parse(_normalize_math_expr(lhs_expr), mode="eval")
            rhs_tree = ast.parse(_normalize_math_expr(rhs_expr), mode="eval")
            # Ensure AST has valid structure before testing options
            _eval_scalar_ast(lhs_tree, var_name, 1.234567)
            _eval_scalar_ast(rhs_tree, var_name, 1.234567)
        except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
            # Variable could hit domain at probe value 1.234567; still validate syntax via ast.parse
            try:
                lhs_tree = ast.parse(_normalize_math_expr(lhs_expr), mode="eval")
                rhs_tree = ast.parse(_normalize_math_expr(rhs_expr), mode="eval")
            except SyntaxError:
                continue

        matches: list[str] = []
        evaluated = 0
        for letter, opt_val in parsed_opts.items():
            try:
                l_val = _eval_scalar_ast(lhs_tree, var_name, opt_val)
                r_val = _eval_scalar_ast(rhs_tree, var_name, opt_val)
                evaluated += 1
                tol = 1e-9 * max(1.0, abs(l_val), abs(r_val))
                if abs(l_val - r_val) <= tol:
                    matches.append(letter)
            except (ValueError, ZeroDivisionError, OverflowError):
                continue

        if evaluated >= 2 and len(matches) == 1:
            return matches[0]
    return None


_POLYGON_SIDES: dict[str, int] = {
    "triangle": 3, "triangles": 3, "quadrilateral": 4, "quadrilaterals": 4, "quadrilàter": 4, "quadrilater": 4, "quadrilàters": 4,
    "pentagon": 5, "pentagons": 5, "pentàgon": 5, "pentàgons": 5,
    "hexagon": 6, "hexagons": 6, "hexàgon": 6, "hexàgons": 6,
    "heptagon": 7, "heptagons": 7, "heptàgon": 7, "heptàgons": 7,
    "octagon": 8, "octagons": 8, "octàgon": 8, "octàgons": 8,
    "nonagon": 9, "nonagons": 9, "enneagon": 9, "enneàgon": 9,
    "decagon": 10, "decagons": 10, "decàgon": 10, "decàgons": 10,
    "hendecagon": 11, "undecagon": 11,
    "dodecagon": 12, "dodecagons": 12, "dodecàgon": 12, "dodecàgons": 12,
}


def _extract_polygon_n(sl: str) -> int | None:
    m = re.search(r"(\d+)-(?:gon|sided\s+polygon)|polígon\s+de\s+(\d+)\s+costats|polygon\s+with\s+(\d+)\s+sides", sl)
    if m:
        val = m.group(1) or m.group(2) or m.group(3)
        return int(val)
    for word, n in _POLYGON_SIDES.items():
        if re.search(rf"\b{re.escape(word)}\b", sl):
            return n
    return None


def _solve_general_word_problem(statement: str) -> tuple[str | None, list[Any]]:
    """Safe, bilingual (ENG/CAT) deterministic solvers for contest math families."""
    s = _normalize_unicode_math(statement or "")
    sl = s.lower()

    # --- GEOMETRY & POLYGONS ---

    # Polygon diagonals: n(n-3)/2 or inverse
    if re.search(r"\bdiagonals?\b", sl):
        if re.search(r"(?:how\s+many|number\s+of|quantes|quants|nombre\s+de)\s+diagonals?\b", sl):
            pn = _extract_polygon_n(sl)
            if pn and pn >= 3:
                return "polygon_diagonals", [Fraction(pn * (pn - 3) // 2)]
        m_dinv = re.search(r"(\d+)\s+diagonals?\b.*?(?:how\s+many\s+(?:sides|vertices)|quants\s+(?:costats|vèrtexs)|quants\s+costats)", sl)
        if m_dinv:
            d_count = int(m_dinv.group(1))
            disc = 9 + 8 * d_count
            sq = math.isqrt(disc)
            if sq * sq == disc and (3 + sq) % 2 == 0:
                return "polygon_diagonals_inv", [Fraction((3 + sq) // 2)]

    # Polygon angles: interior sum, regular interior angle, exterior angle, inverse
    if re.search(r"\b(?:interior\s+angles?|angles?\s+interiors?)\b", sl):
        if re.search(r"\b(?:sum|suma|total)\b", sl):
            pn = _extract_polygon_n(sl)
            if pn and pn >= 3:
                return "polygon_angle_sum", [Fraction((pn - 2) * 180)]
        if re.search(r"\b(?:each|cada|regular)\b", sl):
            pn = _extract_polygon_n(sl)
            if pn and pn >= 3:
                return "polygon_regular_interior_angle", [Fraction((pn - 2) * 180, pn)]
        m_ang_inv = re.search(r"(?:measures?|mesura)\s+(\d+(?:\.\d+)?)\s*(?:degrees|graus|°)?", sl)
        if m_ang_inv:
            ang = float(m_ang_inv.group(1))
            if 0 < ang < 180:
                sides = Fraction(360) / Fraction(180 - ang)
                if sides.denominator == 1 and sides >= 3:
                    return "polygon_interior_to_sides", [sides]

    if re.search(r"\b(?:exterior\s+angles?|angles?\s+exteriors?)\b", sl):
        pn = _extract_polygon_n(sl)
        if pn and pn >= 3:
            return "polygon_exterior_angle", [Fraction(360, pn)]

    # Rectangle tiling into largest equal squares: (W/g)*(H/g)
    if any(k in sl for k in ("equal square", "quadrat igual", "quadrats iguals", "square tiles", "rajoles quadrades", "tiled", "rajoles")):
        m_dims = re.search(r"(\d+)\s*(?:cm|m|mm)?\s*(?:[x×*]|by|per)\s*(\d+)\s*(?:cm|m|mm)?", sl)
        if m_dims and any(w in sl for w in ("rectangle", "rectangular", "rectàngol", "rectangol", "floor", "terra", "board")):
            w_dim, h_dim = int(m_dims.group(1)), int(m_dims.group(2))
            if w_dim > 0 and h_dim > 0:
                g = math.gcd(w_dim, h_dim)
                return "rectangle_equal_squares", [Fraction((w_dim // g) * (h_dim // g))]

    # Cuboid completed into smallest cube: max(a,b,c)^3 - a*b*c
    m_cube = re.search(
        r"(?:block|bloc|cuboid|box|caixa)\s+(?:of\s+)?(\d+)\s*(?:cm|m|mm)?\s*(?:[x×*]|by|per)\s*(\d+)\s*(?:cm|m|mm)?\s*(?:[x×*]|by|per)\s*(\d+)\s*(?:cm|m|mm)?.*?"
        r"(?:small\s+cubes|smallest\s+cube|form\s+a\s+cube|complete\s+a\s+cube|cub\s+més\s+petit|formar\s+un\s+cub)",
        sl,
    )
    if m_cube:
        ca, cb, cc = int(m_cube.group(1)), int(m_cube.group(2)), int(m_cube.group(3))
        if ca > 0 and cb > 0 and cc > 0:
            s_edge = max(ca, cb, cc)
            diff = s_edge**3 - ca * cb * cc
            return "cuboid_to_cube", [Fraction(diff)]

    # --- COMBINATORICS & COUNTING ---

    # Handshakes: n(n-1)/2
    m_hshake = re.search(
        r"(\d+)\s+(?:people|persons|friends|teams|persones|amics|equips)\b.*?"
        r"(?:shake\s+hands|handshakes?|encaixades?\s+de\s+mans|play\s+each\s+other\s+once|juguen\s+tots\s+contra\s+tots)",
        sl,
    )
    if m_hshake:
        hn = int(m_hshake.group(1))
        if hn >= 1:
            return "handshakes", [Fraction(hn * (hn - 1) // 2)]

    # Even-sum card pairs: comb(ceil(N/2), 2) + comb(floor(N/2), 2)
    m_cards = re.search(
        r"(?:numbers?\s+1\s+(?:to|through|fins\s+a)\s+(\d+)|1\s+(?:a|fins\s+al)\s+(\d+)).*?"
        r"(?:two\s+(?:different\s+)?cards|dues\s+cartes).*?(?:sum.*?even|suma.*?parell)",
        sl,
    )
    if m_cards:
        n_c = int(m_cards.group(1) or m_cards.group(2))
        if n_c >= 2:
            even_c = n_c // 2
            odd_c = n_c - even_c
            total_pairs = (even_c * (even_c - 1) // 2) + (odd_c * (odd_c - 1) // 2)
            return "even_sum_card_pairs", [Fraction(total_pairs)]

    # Two-digit numbers with both digits odd/even
    if re.search(r"(?:two-digit\s+numbers|nombres\s+de\s+dues\s+xifres)", sl):
        if re.search(r"\b(?:both|ambdues|totes\s+dues)\b.*?\b(?:odd|senars?)\b", sl):
            return "two_digit_odd_even", [Fraction(25)]
        if re.search(r"\b(?:both|ambdues|totes\s+dues)\b.*?\b(?:even|parells?)\b", sl):
            return "two_digit_odd_even", [Fraction(20)]

    # Digit occurrence count in 1..N
    m_docc = re.search(
        r"(?:whole\s+numbers|integers|nombres)\s+(?:from\s+1\s+to\s+|de\s+1\s+a\s+)(\d+).*?"
        r"(?:contain|tenen).*?(?:digit|xifra)\s+(\d)",
        sl,
    )
    if m_docc:
        n_max, d_target = int(m_docc.group(1)), str(m_docc.group(2))
        if 1 <= n_max <= 10000:
            cnt = sum(1 for i in range(1, n_max + 1) if d_target in str(i))
            return "digit_occurrence_count", [Fraction(cnt)]

    # 3-digit numbers with no zero digits and |abc - cba| = D
    m_rev = re.search(
        r"(?:three-digit|tres\s+xifres).*?(?:no\s+zero\s+digits|cap\s+xifra\s+zero).*?"
        r"(?:reverse\s+order|ordre\s+invers).*?(\d+)",
        sl,
    )
    if m_rev:
        diff_val = int(m_rev.group(1))
        if diff_val % 99 == 0:
            k_diff = diff_val // 99
            if 0 < k_diff < 9:
                return "reversed_diff_three_digit", [Fraction((9 - k_diff) * 18)]

    # 3-digit numbers with exactly two equal digits
    if re.search(
        r"(?:three-digit\s+numbers|nombres\s+de\s+tres\s+xifres).*?"
        r"(?:exactly\s+two\s+equal\s+digits|exactament\s+dues\s+xifres\s+iguals)",
        sl,
    ):
        return "three_digit_two_equal", [Fraction(243)]

    # Coin payment ways: c1*x + c2*y = T
    m_coin2 = re.search(
        r"(?:pay\s+exactly|pagar\s+exactament)\s+(\d+)\s+cents?.*?(\d+)-cent.*?(\d+)-cent",
        sl,
    )
    if m_coin2:
        tot_val, c_a, c_b = int(m_coin2.group(1)), int(m_coin2.group(2)), int(m_coin2.group(3))
        if c_a > 0 and c_b > 0 and tot_val >= 0:
            ways = sum(1 for x in range(0, tot_val // c_a + 1) if (tot_val - x * c_a) % c_b == 0)
            return "coin_payment_ways", [Fraction(ways)]

    # Coin combination optimization: max 5-cent coins
    m_coin_opt = re.search(
        r"(\d+)\s+(?:coins|monedes).*?(?:worth|valen)\s+(\d+)\s+cents?.*?(?:1,\s*2\s*(?:or|o)\s*5|1\s*cèntim|5\s*cèntims)",
        sl,
    )
    if m_coin_opt and re.search(r"largest|maximum|més\s+gran", sl):
        n_c, tot_c = int(m_coin_opt.group(1)), int(m_coin_opt.group(2))
        for z in range(n_c - 2, 0, -1):
            rem_coins = n_c - z
            rem_val = tot_c - 5 * z
            y = rem_val - rem_coins
            x = rem_coins - y
            if x >= 1 and y >= 1:
                return "coin_combination_opt", [Fraction(z)]

    # Cycle walk on square ABCD
    if re.search(r"(?:frog|granota).*?(?:square\s+abcd|quadrat\s+abcd).*?(\d+)\s+(?:jumps|salts)", sl):
        m_j = re.search(r"(\d+)\s+(?:jumps|salts)", sl)
        if m_j and int(m_j.group(1)) == 4:
            return "cycle_walk", [Fraction(8)]

    # Fair coin tosses with consecutive heads
    m_cheads = re.search(
        r"(?:fair\s+coin\s+is\s+tossed|moneda\s+es\s+llança)\s+(\d+)\s+times.*?(?:consecutive\s+heads|cares\s+consecutives)",
        sl,
    )
    if m_cheads:
        t_tosses = int(m_cheads.group(1))
        f_prev, f_curr = 1, 2
        for _ in range(1, t_tosses):
            f_prev, f_curr = f_curr, f_prev + f_curr
        no_hh = f_curr
        total_outcomes = 2**t_tosses
        hh_count = total_outcomes - no_hh
        return "consecutive_heads", [Fraction(hh_count, total_outcomes)]

    # Balls without replacement until blue appears
    m_balls = re.search(
        r"(?:bag\s+contains|bossa\s+conté).*?(\d+)\s+(?:red\s+balls|boles\s+vermelles).*?(\d+)\s+(?:blue\s+balls|boles\s+blaves).*?(?:exactly\s+three\s+balls|exactament\s+tres\s+boles)",
        sl,
    )
    if m_balls:
        r_cnt, b_cnt = int(m_balls.group(1)), int(m_balls.group(2))
        tot_balls = r_cnt + b_cnt
        if r_cnt >= 2 and b_cnt >= 1 and tot_balls >= 3:
            prob_rrb = Fraction(r_cnt, tot_balls) * Fraction(r_cnt - 1, tot_balls - 1) * Fraction(b_cnt, tot_balls - 2)
            return "balls_without_replacement", [prob_rrb]

    # Subsets of {1..N} whose sum is divisible by m
    m_subset = re.search(
        r"(?:subsets?\s+of\s*\{([0-9,\s]+)\}|subconjunts\s+de\s*\{([0-9,\s]+)\}).*?(?:multiple\s+of|divisible\s+by|múltiple\s+de)\s+(\d+)",
        sl,
    )
    if m_subset:
        elems_str = m_subset.group(1) or m_subset.group(2)
        s_div = int(m_subset.group(3))
        elems = [int(x) for x in re.findall(r"\d+", elems_str)]
        if elems and s_div > 0:
            dp = {0: 1}
            for x in elems:
                new_dp = dict(dp)
                for s_mod, count in dp.items():
                    nxt = (s_mod + x) % s_div
                    new_dp[nxt] = new_dp.get(nxt, 0) + count
                dp = new_dp
            return "subset_sum_divisible", [Fraction(dp.get(0, 0))]

    # Knights & liars around round table
    m_knights = re.search(
        r"(\d+)\s+(?:inhabitants|people|persones|habitants).*?(?:round\s+table|taula\s+rodona).*?"
        r"(?:both\s+of\s+my\s+neighbours\s+are\s+liars|tots\s+dos\s+ve[iï]ns\s+s[oó]n\s+mentiders)",
        sl,
    )
    if m_knights:
        n_tbl = int(m_knights.group(1))
        return "knights_and_liars_circle", [Fraction(n_tbl // 2)]

    # --- NUMBER THEORY ---

    # Last digit of a^b
    m_ld = re.search(r"(?:last\s+digit\s+of|darrera\s+xifra\s+de|última\s+xifra\s+de)\s+(\d+)\s*\^\s*(\d+)", sl)
    if m_ld:
        base_n, exp_n = int(m_ld.group(1)), int(m_ld.group(2))
        return "last_digit_pow", [Fraction(pow(base_n, exp_n, 10))]

    # Smallest integer >1 with common remainder r mod d_1..d_k
    m_crem = re.search(
        r"(?:smallest\s+whole\s+number|nombre\s+més\s+petit).*?(?:remainder\s+(?:of\s+)?(\d+)|residu\s+(\d+)).*?"
        r"(?:divided\s+by|dividit\s+entre|dividit\s+per)\s+([0-9,\s\w]+)",
        sl,
    )
    if m_crem:
        r_rem = int(m_crem.group(1) or m_crem.group(2))
        d_vals = [int(x) for x in re.findall(r"\b\d+\b", m_crem.group(3)) if int(x) > 0]
        if d_vals:
            lcm_val = math.lcm(*d_vals)
            return "common_remainder", [Fraction(lcm_val + r_rem)]

    # Grandpa age modular range search
    if re.search(r"(?:grandpa|avi|age|edat).*?(?:multiple\s+of|múltiple\s+de)\s+(\d+).*?(?:next\s+year|l'any\s+que\s+ve).*?(?:multiple\s+of|múltiple\s+de)\s+(\d+).*?(?:in\s+two\s+years|d'aquí\s+a\s+dos\s+anys).*?(?:multiple\s+of|múltiple\s+de)\s+(\d+)", sl):
        m_mods = re.search(r"(?:multiple\s+of|múltiple\s+de)\s+(\d+).*?(?:multiple\s+of|múltiple\s+de)\s+(\d+).*?(?:multiple\s+of|múltiple\s+de)\s+(\d+)", sl)
        if m_mods:
            m1, m2, m3 = int(m_mods.group(1)), int(m_mods.group(2)), int(m_mods.group(3))
            m_rng = re.search(r"(?:between|entre)\s+(\d+)\s+(?:and|i)\s+(\d+)", sl)
            l_rng, h_rng = (int(m_rng.group(1)), int(m_rng.group(2))) if m_rng else (50, 100)
            valid_ages = [x for x in range(l_rng, h_rng + 1) if x % m1 == 0 and (x + 1) % m2 == 0 and (x + 2) % m3 == 0]
            if valid_ages:
                return "modular_age_range", [Fraction(valid_ages[0])]

    # Square divisors of M
    m_sqdiv = re.search(r"(?:integers?|nombres?)\s+n.*?n[²2]\s+(?:is\s+a\s+divisor\s+of|divisor\s+de)\s+(\d+)", sl)
    if m_sqdiv:
        m_val = int(m_sqdiv.group(1))
        sq_cnt = sum(1 for n in range(1, int(math.isqrt(m_val)) + 1) if m_val % (n * n) == 0)
        return "square_divisors", [Fraction(sq_cnt)]

    # Smallest positive integer whose digits multiply to K
    m_dprod = re.search(
        r"(?:smallest\s+positive\s+whole\s+number|nombre\s+més\s+petit).*?"
        r"(?:digits\s+multiply\s+to|xifres\s+multipliquen|producte\s+de\s+les\s+xifres\s+(?:és\s+)?|product\s+of\s+its\s+digits\s+is)\s+(\d+)",
        sl,
    )
    if m_dprod:
        k_prod = int(m_dprod.group(1))
        dig_factors = []
        rem_p = k_prod
        for d in range(9, 1, -1):
            while rem_p % d == 0:
                dig_factors.append(d)
                rem_p //= d
        if rem_p == 1:
            dig_factors.sort()
            prod_ans = int("".join(str(d) for d in dig_factors))
            return "digit_product_min", [Fraction(prod_ans)]

    # 4-digit aXYb divisible by 90
    m_div90 = re.search(
        r"(?:four-digit\s+number|nombre\s+de\s+quatre\s+xifres)\s+a(\d)(\d)b.*?divisible\s+(?:by|per)\s+90.*?(?:what\s+is|quant\s+val)\s+a\s*\+\s*b",
        sl,
    )
    if m_div90:
        d_x, d_y = int(m_div90.group(1)), int(m_div90.group(2))
        b_val = 0
        a_val = (9 - (d_x + d_y) % 9) % 9
        if a_val == 0: a_val = 9
        return "divisible_by_90", [Fraction(a_val + b_val)]

    # Factorial sum modulo m
    m_fsum = re.search(
        r"(?:remainder\s+when|residu\s+de)\s+1!\s*\+\s*2!\s*\+\s*3!\s*\+\s*\.\.\.\s*\+\s*(\d+)!\s+(?:is\s+divided\s+by|dividit\s+per)\s+(\d+)",
        sl,
    )
    if m_fsum:
        f_n, f_mod = int(m_fsum.group(1)), int(m_fsum.group(2))
        f_tot = 0
        cur_fact = 1
        for k in range(1, min(f_n + 1, f_mod + 1)):
            cur_fact = (cur_fact * k) % f_mod
            if cur_fact == 0:
                break
            f_tot = (f_tot + cur_fact) % f_mod
        return "factorial_sum_mod", [Fraction(f_tot)]

    # Ordered pairs 1/x + 1/y = 1/n
    m_recip = re.search(r"(?:ordered\s+pairs|parells\s+ordenats).*?1/x\s*\+\s*1/y\s*=\s*1/(\d+)", sl)
    if m_recip:
        n_rec = int(m_recip.group(1))
        n_sq = n_rec * n_rec
        num_divs = sum(1 for d in range(1, int(math.isqrt(n_sq)) + 1) if n_sq % d == 0)
        tot_pairs = num_divs * 2 - (1 if math.isqrt(n_sq)**2 == n_sq else 0)
        return "reciprocal_sum_pairs", [Fraction(tot_pairs)]

    # Polynomial divisibility count
    m_pdiv = re.search(
        r"(?:integers?|nombres?)\s+n\s*(?:<=|≤)\s*(\d+).*?n[²2]\s*([+\-])\s*(\d*)n\s*([+\-])\s*(\d+).*?divisible\s+(?:by|per)\s+(\d+)",
        sl,
    )
    if m_pdiv:
        p_n = int(m_pdiv.group(1))
        b_sgn = -1 if m_pdiv.group(2) == '-' else 1
        b_cf = int(m_pdiv.group(3)) if m_pdiv.group(3) else 1
        p_b = b_sgn * b_cf
        c_sgn = -1 if m_pdiv.group(4) == '-' else 1
        p_c = c_sgn * int(m_pdiv.group(5))
        p_m = int(m_pdiv.group(6))
        v_cnt = sum(1 for n in range(1, p_n + 1) if (n * n + p_b * n + p_c) % p_m == 0)
        return "poly_divisibility_count", [Fraction(v_cnt)]

    # Numbers < 1000 equal to k * digit_sum
    m_dmult = re.search(
        r"(?:whole\s+numbers|integers|nombres)\s+(?:less\s+than|menors\s+que)\s*1000\s+(?:are\s+equal\s+to|iguals\s+a)\s+(\d+)\s+(?:times\s+the\s+sum\s+of\s+their\s+digits|vegades\s+la\s+suma\s+de\s+les\s+seves\s+xifres)",
        sl,
    )
    if m_dmult:
        k_mult = int(m_dmult.group(1))
        k_cnt = sum(1 for n in range(1, 1000) if n == k_mult * sum(int(d) for d in str(n)))
        return "digit_sum_multiple", [Fraction(k_cnt)]

    # Minimum quotient of two-group partition of {1..N}
    m_part = re.search(
        r"(?:numbers?\s+1,\s*2,\s*3,\s*\.\.\.,\s*(\d+)|1\s+a\s+(\d+)).*?"
        r"(?:split\s+into\s+two\s+groups|dividits\s+en\s+dos\s+grups).*?"
        r"(?:product.*?divisible.*?product|producte.*?divisible.*?producte).*?"
        r"(?:smallest.*?quotient|mínim.*?quocient)",
        sl,
    )
    if m_part:
        n_part = int(m_part.group(1) or m_part.group(2))
        p_cands = [p for p in range(n_part // 2 + 1, n_part + 1) if all(p % d != 0 for d in range(2, int(math.isqrt(p)) + 1))]
        min_q = max(p_cands) if p_cands else 1
        return "partition_quotient_min", [Fraction(min_q)]

    # --- WORD PROBLEMS & ALGEBRA ---

    # Alternating kangaroo
    m_alt = re.search(
        r"(?:kangaroo|cangur).*?(?:jumps\s+alternately|salta\s+alternativament)\s+(\d+)\s+(?:forwards?|endavant)\s+and\s+(\d+)\s+(?:backwards?|enrere).*?(\d+)\s+(?:jumps|salts)",
        sl,
    )
    if not m_alt:
        m_alt = re.search(r"\+(\d+),\s*[-−](\d+).*?(\d+)\s+(?:jumps|salts)", sl)
    if m_alt:
        f_jump, b_jump, k_jump = int(m_alt.group(1)), int(m_alt.group(2)), int(m_alt.group(3))
        land_pos = (k_jump // 2) * (f_jump - b_jump) + (f_jump if (k_jump % 2 == 1) else 0)
        return "alternating_steps", [Fraction(land_pos)]

    # Climbing frog/snail
    m_frog = re.search(
        r"(?:frog|granota|snail|cargol).*?(?:jump|salt|climb).*?(\d+)\s*m\s+(?:forward|up|endavant|amunt).*?"
        r"(\d+)\s*m\s+(?:back|down|enrere|avall).*?(\d+)\s*m\s+(?:from\s+the\s+start|high|de\s+la\s+sortida|d'alçària)",
        sl,
    )
    if m_frog:
        f_u, f_d, f_h = int(m_frog.group(1)), int(m_frog.group(2)), int(m_frog.group(3))
        if f_u >= f_h:
            f_ans = 1
        else:
            net_f = f_u - f_d
            f_ans = math.ceil((f_h - f_u) / net_f) + 1
        return "climbing_frog", [Fraction(f_ans)]

    # Digital clock equal digits
    if re.search(r"(?:digital\s+clock|rellotge\s+digital).*?(?:four\s+equal\s+digits|quatre\s+xifres\s+iguals)", sl):
        return "clock_equal_digits", [Fraction(3)]

    # Runner ahead/behind ratio
    m_run = re.search(
        r"(?:race\s+with|cursa\s+amb)\s+(\d+)\s+runners?.*?(\w+)\s+(?:finished\s+with|ha\s+arribat).*?"
        r"(?:(\d+)\s+times\s+as\s+many\s+runners\s+ahead.*?behind|twice\s+as\s+many\s+runners\s+ahead.*?behind|el\s+doble\s+de\s+corredors\s+davant.*?darrere)",
        sl,
    )
    if m_run:
        tot_run = int(m_run.group(1))
        r_ratio = 2 if re.search(r"twice|doble", sl) else int(m_run.group(3) or 2)
        b_behind = (tot_run - 1) // (r_ratio + 1)
        a_ahead = r_ratio * b_behind
        return "runner_ahead_behind", [Fraction(a_ahead + 1)]

    # Bus schedule
    m_bus = re.search(
        r"(?:bus|autobús).*?(?:every|cada)\s+(\d+)\s+minutes?.*?first.*?(\d{1,2}):(\d{2}).*?arrives?.*?(\d{1,2}):(\d{2})",
        sl,
    )
    if not m_bus:
        m_bus = re.search(
            r"autobús.*?cada\s+(\d+)\s+minuts.*?primer.*?(\d{1,2}):(\d{2}).*?arriba.*?(\d{1,2}):(\d{2})",
            sl,
        )
    if m_bus:
        b_period = int(m_bus.group(1))
        b_h0, b_m0 = int(m_bus.group(2)), int(m_bus.group(3))
        b_ha, b_ma = int(m_bus.group(4)), int(m_bus.group(5))
        b_cur = b_h0 * 60 + b_m0
        b_target = b_ha * 60 + b_ma
        while b_cur < b_target:
            b_cur += b_period
        b_deph = (b_cur // 60) % 24
        b_depm = b_cur % 60
        return "bus_schedule", [f"{b_deph}:{b_depm:02d}"]

    # Fraction class difference
    m_fclass = re.search(
        r"(\d+)/(\d+)\s+(?:of\s+the\s+students|dels\s+alumnes).*?(\d+)\s+more\s+(?:girls|boys|noies|nois).*?than\s+(?:boys|girls|nois|noies)",
        sl,
    )
    if m_fclass:
        f_p, f_q, f_diff = int(m_fclass.group(1)), int(m_fclass.group(2)), int(m_fclass.group(3))
        f_gap = abs(Fraction(2 * f_p, f_q) - 1)
        if f_gap > 0:
            return "fraction_class_difference", [Fraction(f_diff) / f_gap]

    # Largest sum of two 2-digit numbers from 4 digits
    m_4d = re.search(
        r"(?:digits|xifres)\s+([0-9,\s]+).*?(?:two\s+two-digit\s+numbers|dos\s+nombres\s+de\s+dues\s+xifres).*?(?:largest\s+sum|suma\s+més\s+gran)",
        sl,
    )
    if m_4d:
        d_list = sorted([int(x) for x in re.findall(r"\d", m_4d.group(1))], reverse=True)
        if len(d_list) >= 4:
            s_max = 10 * (d_list[0] + d_list[1]) + (d_list[2] + d_list[3])
            return "largest_sum_4_digits", [Fraction(s_max)]

    # Max weekdays in D days
    m_wday = re.search(
        r"(?:largest\s+possible\s+number\s+of|nombre\s+més\s+gran\s+possible\s+de)\s+(?:sundays?|mondays?|diumenges?|dilluns?).*?(\d+)\s+days",
        sl,
    )
    if m_wday:
        d_days = int(m_wday.group(1))
        return "max_weekdays", [Fraction(math.ceil(d_days / 7))]

    # Three ages product P & sum S
    m_3ages = re.search(
        r"(?:ages\s+of\s+three\s+children|edats\s+de\s+tres\s+infants).*?(?:product\s+is|producte\s+és)\s+(\d+).*?(?:sum\s+is|suma\s+és)\s+(\d+)",
        sl,
    )
    if m_3ages:
        a_prod, a_sum = int(m_3ages.group(1)), int(m_3ages.group(2))
        oldest_age = -1
        for a in range(1, a_sum):
            if a_prod % a == 0:
                rem_prod = a_prod // a
                for b in range(a, a_sum - a):
                    if rem_prod % b == 0:
                        c = rem_prod // b
                        if c >= b and a + b + c == a_sum:
                            oldest_age = c
        if oldest_age != -1:
            return "three_ages", [Fraction(oldest_age)]

    # Quadratic / factored integer inequalities
    m_ineq = re.search(r"n[²2]\s*([+\-])\s*(\d+)n\s*([+\-])\s*(\d+)\s*(<|<=|≤)\s*0", sl)
    if m_ineq:
        i_b = -int(m_ineq.group(2)) if m_ineq.group(1) == '-' else int(m_ineq.group(2))
        i_c = -int(m_ineq.group(4)) if m_ineq.group(3) == '-' else int(m_ineq.group(4))
        strict = m_ineq.group(5) == '<'
        i_sols = [n for n in range(-1000, 1000) if (n * n + i_b * n + i_c < 0 if strict else n * n + i_b * n + i_c <= 0)]
        if re.search(r"sum|suma", sl):
            return "integer_inequality", [Fraction(sum(i_sols))]
        return "integer_inequality", [Fraction(len(i_sols))]

    m_ineq2 = re.search(r"\(\s*x[²2]\s*-\s*(\d+)\s*\)\s*\(\s*x[²2]\s*-\s*(\d+)\s*\)\s*<\s*0", sl)
    if m_ineq2:
        val_a, val_b = int(m_ineq2.group(1)), int(m_ineq2.group(2))
        i_sols2 = [x for x in range(-1000, 1000) if (x * x - val_a) * (x * x - val_b) < 0]
        return "integer_inequality", [Fraction(len(i_sols2))]

    # Functional equation
    m_func = re.search(r"f\(x\)\s*\+\s*(\d*)\s*[·*]?\s*f\(1/x\)\s*=\s*(\d*)\s*x.*?f\((\d+)\)", sl)
    if m_func:
        f_b = int(m_func.group(1)) if m_func.group(1) else 1
        f_c = int(m_func.group(2)) if m_func.group(2) else 1
        f_x0 = int(m_func.group(3))
        if f_x0 != 0 and f_b * f_b != 1:
            f_val = (Fraction(f_b * f_c, f_x0) - Fraction(f_c * f_x0)) / Fraction(f_b * f_b - 1)
            return "functional_equation", [f_val]

    # Digit-sum recurrence
    m_drec = re.search(
        r"a_?[₁1]\s*=\s*(\d+).*?(?:sum\s+of\s+its\s+digits|suma\s+de\s+les\s+seves\s+xifres).*?(?:a([₁1₀0]{1,2})|a_\{?(\d+)\}?)",
        sl,
    )
    if m_drec:
        a_init = int(m_drec.group(1))
        sub_raw = (m_drec.group(2) or m_drec.group(3) or "10").translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789"))
        target_k = int(sub_raw) if sub_raw.isdigit() else 10
        if 1 <= target_k <= 1000:
            curr_val = a_init
            for _ in range(1, target_k):
                curr_val += sum(int(d) for d in str(curr_val))
            return "digit_sum_recurrence", [Fraction(curr_val)]

    # 2-variable sum-product / sum-diff systems: "x + y = S, x * y = P" or "x + y = S, x - y = D"
    m_sys_sp = re.search(
        r"\b([a-z])\s*\+\s*([a-z])\s*=\s*(-?\d+)\b.*?\b\1\s*\*\s*\2\s*=\s*(-?\d+)\b",
        sl,
    )
    if m_sys_sp and m_sys_sp.group(1) != m_sys_sp.group(2):
        s_sum = Fraction(int(m_sys_sp.group(3)))
        p_prod = Fraction(int(m_sys_sp.group(4)))
        disc_sp = s_sum * s_sum - 4 * p_prod
        sqrt_sp = _exact_sqrt_fraction(disc_sp)
        if sqrt_sp is not None:
            r1 = (s_sum + sqrt_sp) / 2
            r2 = (s_sum - sqrt_sp) / 2
            sols = sorted({r1, r2})
            return "two_var_system", sols

    m_sys_sd = re.search(
        r"\b([a-z])\s*\+\s*([a-z])\s*=\s*(-?\d+)\b.*?\b\1\s*-\s*\2\s*=\s*(-?\d+)\b",
        sl,
    )
    if m_sys_sd and m_sys_sd.group(1) != m_sys_sd.group(2):
        s_sum = Fraction(int(m_sys_sd.group(3)))
        d_diff = Fraction(int(m_sys_sd.group(4)))
        r1 = (s_sum + d_diff) / 2
        r2 = (s_sum - d_diff) / 2
        return "two_var_system", [r1, r2]

    # Pigeonhole same-color draw: "N1 red, N2 blue, N3 black ... minimum to guarantee K of the same color"
    if re.search(r"(?:same\s+color|mateix\s+color)", sl) and re.search(r"(?:minimum|least|mínim|almenys)", sl):
        m_k = re.search(r"(\d+)\s+(?:balls?|socks?|marbles?|boles?|mitjons|bales)?\s*(?:of\s+the\s+same\s+color|del\s+mateix\s+color)", sl)
        if m_k:
            k_same = int(m_k.group(1))
            # Count distinct color groups mentioned with counts before the question
            pre_q = re.split(r"[.?]", sl)[0]
            color_counts = [int(c) for c in re.findall(r"(\d+)\s+(?:[a-zà-ÿ]+\s+){0,2}(?:balls?|socks?|marbles?|boles?|vermelles|blaves|negres|verdes|grogues|blanques|red|blue|black|green|yellow|white)\b", pre_q)]
            if len(color_counts) >= 2 and k_same >= 1:
                worst = sum(min(c, k_same - 1) for c in color_counts) + 1
                return "pigeonhole_same_color", [Fraction(worst)]

    # Fraction-of-group + offset: "D more A than B. Half/third/quarter of B is K. How many A?"
    m_more = re.search(r"(\d+)\s+(?:more|més)\s+([a-zà-ÿ]+)\s+(?:than|que)\s+([a-zà-ÿ]+)", sl)
    if m_more:
        diff_n = int(m_more.group(1))
        ent_a, ent_b = m_more.group(2), m_more.group(3)
        m_frac = re.search(
            r"(?:(half|a\s+half|meitat|one\s+third|a\s+third|un\s+terç|quarter|a\s+quarter|un\s+quart))\s+(?:the\s+number\s+of\s+|of\s+(?:the\s+)?|del\s+nombre\s+de\s+|de\s+)?([a-zà-ÿ]+)\s+(?:is|are|és|són)\s+(\d+)",
            sl,
        )
        if m_frac:
            frac_word = m_frac.group(1)
            mult = 2 if ("half" in frac_word or "meitat" in frac_word) else (3 if ("third" in frac_word or "terç" in frac_word) else 4)
            target_ent = m_frac.group(2)
            rhs_val = int(m_frac.group(3))
            base_val = rhs_val * mult
            if target_ent.startswith(ent_b[:3]):
                val_b = base_val
                val_a = val_b + diff_n
            elif target_ent.startswith(ent_a[:3]):
                val_a = base_val
                val_b = val_a - diff_n
            else:
                val_a, val_b = base_val + diff_n, base_val
            ask_b = bool(re.search(rf"(?:how\s+many|quants|quantes)\s+{re.escape(ent_b[:3])}", sl))
            return "fraction_offset_pair", [Fraction(val_b if ask_b else val_a)]

    # --- EXISTING REPO SOLVERS ---

    # 1. Queue / rank from both ends: "a-th from front and b-th from back" -> a + b - 1
    m_front = re.search(
        r"(\d+)(?:st|nd|rd|th|è|e|r|t|n)?\s+(?:from\s+(?:the\s+)?(?:front|left|start|top)|pel\s+davant|des\s+del\s+principi|per\s+l'esquerra)",
        sl,
    )
    m_back = re.search(
        r"(\d+)(?:st|nd|rd|th|è|e|r|t|n)?\s+(?:from\s+(?:the\s+)?(?:back|right|end|bottom)|pel\s+darrere|des\s+del\s+final|per\s+la\s+dreta)",
        sl,
    )
    if m_front and m_back:
        a, b = int(m_front.group(1)), int(m_back.group(1))
        if a >= 1 and b >= 1:
            return "queue_rank", [Fraction(a + b - 1)]

    # 2. Doubling every day: "doubles every day ... full on day D ... half/quarter full"
    if re.search(r"\b(?:doubles?|dobla|duplica)\b", sl) and re.search(r"\b(?:day|dia)\b", sl):
        m_day = re.search(r"(?:on\s+day|in|after|al\s+cap\s+de|el\s+dia)\s+(\d+)", sl)
        if m_day:
            d = int(m_day.group(1))
            if re.search(r"\b(?:half|meitat)\b|1/2", sl) and d >= 2:
                return "doubling_days", [Fraction(d - 1)]
            if re.search(r"\b(?:quarter|quart)\b|1/4", sl) and d >= 3:
                return "doubling_days", [Fraction(d - 2)]

    # 3. Age group shift: "N people ... sum of ages is S ... in k years"
    m_age = re.search(
        r"(\d+)\s+(?:people|friends|sisters|brothers|students|children|persones|amics|germanes|germans|alumnes|nens)\b.*?"
        r"(?:sum\s+of\s+(?:their\s+)?ages|total\s+age|suma\s+de\s+les\s+(?:seves\s+)?edats)\s+(?:is|was|és|era)\s+(\d+).*?"
        r"(?:in|after|d'aquí\s+a|després\s+de)\s+(\d+)\s+(?:years|anys)",
        sl,
    )
    if m_age:
        n_ppl, s_now, k_yrs = int(m_age.group(1)), int(m_age.group(2)), int(m_age.group(3))
        if n_ppl >= 1 and s_now >= 0 and k_yrs >= 0:
            return "age_group_shift", [Fraction(s_now + n_ppl * k_yrs)]

    # 4. Weighted average of two groups
    m_wavg = re.search(
        r"(\d+)\s+\w+\s+(?:have\s+(?:an?\s+)?(?:average|mean)|amb\s+(?:una\s+)?mitjana)\s+(?:of\s+|de\s+)?(\d+(?:\.\d+)?)"
        r".*?(\d+)\s+\w+\s+(?:have\s+(?:an?\s+)?(?:average|mean)|amb\s+(?:una\s+)?mitjana)\s+(?:of\s+|de\s+)?(\d+(?:\.\d+)?)",
        sl,
    )
    if m_wavg:
        n1, m1 = int(m_wavg.group(1)), Fraction(m_wavg.group(2))
        n2, m2 = int(m_wavg.group(3)), Fraction(m_wavg.group(4))
        if n1 > 0 and n2 > 0:
            return "weighted_avg", [(n1 * m1 + n2 * m2) / (n1 + n2)]

    # 5. Percentage discount -> original price
    m_pct = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s+(?:discount|off|descompte).*?(?:costs?|pays?|paid|(?<!original\s)(?<!regular\s)(?<!initial\s)price\s+is|costa|paga)\s+(?:€|\$)?(\d+(?:\.\d+)?)",
        sl,
    )
    if m_pct and not re.search(r"\b(?:original|regular|initial|inicial|abans)\b", m_pct.group(0)):
        p = Fraction(m_pct.group(1))
        sale = Fraction(m_pct.group(2))
        if 0 < p < 100 and sale > 0:
            return "pct_diff_total", [sale / (1 - p / 100)]

    # 6. Line segment system: points A, B, C, D in order on a line
    if re.search(r"\b(?:in\s+that\s+order|en\s+aquest\s+ordre|on\s+a\s+line|en\s+una\s+recta)\b", sl):
        segs = dict(re.findall(r"\b(AB|BC|CD|AC|BD|AD)\s*=\s*(\d+(?:\.\d+)?)", s))
        if len(segs) >= 3:
            eq_map = {
                "AB": (1, 0, 0),
                "BC": (0, 1, 0),
                "CD": (0, 0, 1),
                "AC": (1, 1, 0),
                "BD": (0, 1, 1),
                "AD": (1, 1, 1),
            }
            known = {k: Fraction(v) for k, v in segs.items() if k in eq_map}
            if len(known) >= 3:
                rows = [[Fraction(c) for c in eq_map[k]] + [val] for k, val in known.items()]
                sol_vec: list[Fraction] | None = None
                for i in range(len(rows)):
                    for j in range(i + 1, len(rows)):
                        for r_idx in range(j + 1, len(rows)):
                            mat = [rows[i][:], rows[j][:], rows[r_idx][:]]
                            ok = True
                            for col in range(3):
                                piv = next((r for r in range(col, 3) if mat[r][col] != 0), None)
                                if piv is None:
                                    ok = False
                                    break
                                mat[col], mat[piv] = mat[piv], mat[col]
                                div = mat[col][col]
                                mat[col] = [v / div for v in mat[col]]
                                for r in range(3):
                                    if r != col and mat[r][col] != 0:
                                        fac = mat[r][col]
                                        mat[r] = [mat[r][c] - fac * mat[col][c] for c in range(4)]
                            if ok:
                                sol_vec = [mat[0][3], mat[1][3], mat[2][3]]
                                break
                        if sol_vec is not None:
                            break
                    if sol_vec is not None:
                        break
                if sol_vec is not None and all(v > 0 for v in sol_vec):
                    m_ask = re.search(r"(?:what\s+is|find|length\s+of|quant\s+mesura|troba)\s+(AB|BC|CD|AC|BD|AD)\b", s, re.I)
                    if m_ask:
                        target = m_ask.group(1).upper()
                        cx, cy, cz = eq_map[target]
                        return "line_segment_sys", [cx * sol_vec[0] + cy * sol_vec[1] + cz * sol_vec[2]]

    # 7. Two-type linear system (e.g. bicycles/tricycles, chickens/rabbits)
    m_sys = re.search(
        r"(\d+)\s+(?:animals|vehicles|bicycles\s+and\s+tricycles|chickens\s+and\s+rabbits)\b.*?"
        r"(\d+)\s+(?:legs|wheels|potes|rodes)",
        sl,
    )
    if m_sys:
        n_items, n_parts = int(m_sys.group(1)), int(m_sys.group(2))
        if re.search(r"tricycl|tricicl", sl):
            w1, w2 = 2, 3
        elif re.search(r"legs|potes|car|cars|cotxe|cotxes|truck|trucks|cami[oó]", sl):
            w1, w2 = 2, 4
        else:
            w1, w2 = 0, 0
        if w2 > w1:
            y_num = n_parts - w1 * n_items
            denom = w2 - w1
            if denom > 0 and y_num >= 0 and y_num % denom == 0:
                y = y_num // denom
                x = n_items - y
                if x >= 0:
                    ask_second = bool(re.search(r"how\s+many\s+(?:tricycles|rabbits|cows|dogs|cars|conills|vaques|tricicles|cotxes)", sl))
                    return "two_type_sys", [Fraction(y if ask_second else x)]

    # 8. Digit permutations without repetition: "how many N-digit numbers ... using digits d1, d2, ..."
    m_dperm = re.search(
        r"(?:how\s+many|quants)\s+(\d+)-digit\s+(?:numbers|integers|nombres).*?(?:digits|xifres)\s+([0-9,\s]+?)(?:\s+(?:without|once|different|distinct|sense|diferents)|[?.])",
        sl,
    )
    if (
        m_dperm
        and re.search(r"\b(?:without\s+repeating|each\s+digit\s+once|different|distinct|sense\s+repetir|diferents)\b", sl)
        and not re.search(r"\b(?:even|odd|parell|parells|senar|senars|divisible|multiple|greater|less|smaller|larger|més\s+gran|més\s+petit)\b", sl)
    ):
        n_len = int(m_dperm.group(1))
        digs = sorted(set(re.findall(r"\d", m_dperm.group(2))))
        if 1 <= n_len <= len(digs) <= 9:
            has_zero = "0" in digs
            k = len(digs)
            first_choices = (k - 1) if has_zero else k
            ways = first_choices
            for step in range(1, n_len):
                ways *= (k - step)
            return "digit_perms", [Fraction(ways)]

    return None, []

    return None, []


def extract_math_oracle(statement: str) -> dict[str, Any]:
    """Zero-dependency AST + Fraction silent math oracle for anti-leak verification."""
    allowed_givens = _extract_allowed_givens(statement)
    forbidden_targets: set[str] = set()
    solutions: list[str] = []
    kind: str | None = None

    if not statement or not statement.strip():
        return {
            "allowed_givens": allowed_givens,
            "forbidden_targets": forbidden_targets,
            "solutions": solutions,
            "kind": kind,
        }

    norm_stmt = _normalize_unicode_math(statement)

    # 1. Derivative requests (e.g. "derivative of x^3", "d/dx((x+1)^2)", "differentiate 4x^3 - 2x with respect to x")
    deriv_prefix = re.search(r"(?i)d/d([a-z])\s*\(", norm_stmt)
    raw_deriv_expr = ""
    deriv_var = "x"
    if deriv_prefix:
        deriv_var = deriv_prefix.group(1).lower()
        start = deriv_prefix.end()
        depth = 1
        end = start
        while end < len(norm_stmt) and depth > 0:
            if norm_stmt[end] == "(":
                depth += 1
            elif norm_stmt[end] == ")":
                depth -= 1
            end += 1
        if depth == 0:
            raw_deriv_expr = norm_stmt[start : end - 1].strip()
    else:
        deriv_match = re.search(
            r"(?i)(?:derivative\s+of|differentiate)\s+([0-9a-zA-Z_+\-*/^().%\s]+)",
            norm_stmt,
        )
        if deriv_match:
            raw_deriv_expr = re.split(
                r"(?i)\s+(?:with\s+respect\s+to|wrt|at|for|where|please)\b|[?,;!]",
                deriv_match.group(1),
                maxsplit=1,
            )[0].strip().rstrip("?.!")

    if raw_deriv_expr:
        vars_found = sorted(set(re.findall(r"[a-zA-Z]", raw_deriv_expr)))
        if len(vars_found) <= 1:
            if vars_found:
                deriv_var = vars_found[0]
            try:
                poly = _parse_poly(raw_deriv_expr, deriv_var)
                deriv = {d - 1: c * d for d, c in poly.items() if d > 0}
                variants = _format_poly_variants(deriv, deriv_var)
                forbidden_targets.update(variants)
                solutions.append(sorted(variants, key=len)[0])
                kind = "derivative"
            except (SyntaxError, ValueError, ZeroDivisionError):
                pass

    # 2. Single-variable linear / quadratic equation (e.g. "2x + 5 = 17", "x^2 - 5x + 6 = 0")
    eq_stmt = re.sub(
        r"(?i)(?:,|\band\b|\bbut\b)?\s*\b(?:i\s+think|maybe|guess|i\s+got|crec\s+que|potser)\s+[a-z]\s*=\s*-?\d+(?:\.\d+)?(?:/\d+)?",
        "",
        norm_stmt,
    ).strip()
    if kind is None and eq_stmt.count("=") == 1 and "|" not in eq_stmt:
        eq_match = re.search(
            r"([0-9a-zA-Z_+\-*/^().%\s]+=[0-9a-zA-Z_+\-*/^().%\s]+)",
            eq_stmt,
        )
        if eq_match:
            # Strip leading English/Catalan prose words before the equation expression.
            lhs_raw, rhs_raw = eq_match.group(1).split("=", 1)
            lhs_clean = re.sub(
                r"^(?:.*?:\s*|.*?\b(?:solve|if|find|given|that|for|where|si|quan|resol|troba)\b\s+)",
                "",
                lhs_raw,
                flags=re.I,
            ).strip()
            rhs_clean = re.split(
                r"[?,;!]|(?:\s+(?:for|where|what|find|and|quin|quina|quants)\b)",
                rhs_raw,
                maxsplit=1,
                flags=re.I,
            )[0].strip()
            # Reject ambiguous additive percentages (e.g. "x + 20% = 120")
            has_ambig_pct = "%" in f"{lhs_clean}{rhs_clean}" and bool(re.search(r"[+\-]", f"{lhs_clean}{rhs_clean}"))
            scan_eq = re.sub(r"\d[eE][+-]?\d+", "", f"{lhs_clean} {rhs_clean}")
            vars_found = sorted(set(re.findall(r"[a-zA-Z_]+", scan_eq)))
            is_trivial_assign = (
                len(vars_found) == 1
                and (
                    (lhs_clean == vars_found[0] and bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:/\d+)?", rhs_clean)))
                    or (rhs_clean == vars_found[0] and bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?(?:/\d+)?", lhs_clean)))
                )
            )
            is_unit_label = (
                len(vars_found) == 1
                and vars_found[0] in {"m", "g", "l", "h", "s"}
                and bool(re.search(rf"\d\s+{vars_found[0]}\b", f"{lhs_clean} {rhs_clean}"))
            )
            if (
                not has_ambig_pct
                and not is_trivial_assign
                and not is_unit_label
                and len(vars_found) == 1
                and re.fullmatch(r"[a-z]", vars_found[0])
                and lhs_clean
                and rhs_clean
            ):
                var_name = vars_found[0]
                try:
                    lhs_p = _parse_poly(lhs_clean, var_name)
                    rhs_p = _parse_poly(rhs_clean, var_name)
                    diff = _poly_add(lhs_p, rhs_p, -1)
                    max_deg = max(diff.keys(), default=0)
                    if max_deg == 1:
                        a1 = diff[1]
                        a0 = diff.get(0, Fraction(0))
                        sol = -a0 / a1
                        for v in _format_fraction_variants(sol):
                            forbidden_targets.add(v)
                            forbidden_targets.add(f"{var_name} = {v}")
                            forbidden_targets.add(f"{var_name}={v}")
                            solutions.append(v)
                        kind = "linear"
                    elif max_deg == 2:
                        a = diff[2]
                        b = diff.get(1, Fraction(0))
                        c = diff.get(0, Fraction(0))
                        disc = b * b - 4 * a * c
                        sqrt_d = _exact_sqrt_fraction(disc)
                        if sqrt_d is not None:
                            roots = [(-b + sqrt_d) / (2 * a), (-b - sqrt_d) / (2 * a)]
                            for r in roots:
                                for v in _format_fraction_variants(r):
                                    forbidden_targets.add(v)
                                    forbidden_targets.add(f"{var_name} = {v}")
                                    forbidden_targets.add(f"{var_name}={v}")
                                    solutions.append(v)
                            kind = "quadratic"
                        elif disc > 0:
                            r1 = (-float(b) + math.sqrt(float(disc))) / (2.0 * float(a))
                            r2 = (-float(b) - math.sqrt(float(disc))) / (2.0 * float(a))
                            for r in (r1, r2):
                                v = f"{r:.4f}".rstrip("0").rstrip(".")
                                forbidden_targets.add(v)
                                forbidden_targets.add(f"{var_name} = {v}")
                                forbidden_targets.add(f"{var_name}={v}")
                                solutions.append(v)
                            kind = "quadratic"
                except (SyntaxError, ValueError, ZeroDivisionError):
                    pass

    # 3. Basic arithmetic (e.g. "3/4 + 1/2", "15 * 4 - 8", "2 + 3 × 4", "(2 + 3) × 4", "50% + 25%")
    has_arith_trap = bool(
        re.search(
            r"(?i)\b(?:instead\s+of|en\s+comptes\s+de|grid|square|quadrat|tauler|graella)\b"
            r"|\b\d+\s*[x×*]\s*\d+(?:\s*[x×*]\s*\d+)?\s*(?:cm|mm|m|box|boxes|caixa|caixes|block|cuboid|grid|square|quadrat|tauler|rectangle|board)\b"
            r"|\b(?:box|boxes|caixa|caixes|cuboid|block)\s+(?:of\s+)?\d+\s*[x×*]\s*\d+",
            norm_stmt,
        )
    )
    if kind is None and not has_arith_trap:
        stmt_body = re.sub(r"^\s*\d{1,2}\.\s+", "", norm_stmt)
        num_atom = r"-?\d+(?:\.\d+)?(?:\s*%)?(?:\s*\^\s*-?\d+(?:\.\d+)?)?"
        inner_expr = rf"{num_atom}(?:\s*[+\-*/^]\s*{num_atom})+"
        atom = rf"(?:-?\(\s*(?:{inner_expr}|{num_atom})\s*\)(?:\s*\^\s*-?\d+(?:\.\d+)?)?|{num_atom})"
        arith_pattern = re.compile(
            rf"(?<![A-Za-z0-9_.])"
            rf"({atom}(?:\s*[+\-*/^]\s*{atom})+)"
            rf"(?![A-Za-z0-9_.])"
        )
        candidate_matches: list[re.Match[str]] = []
        for match in arith_pattern.finditer(stmt_body):
            expr = match.group(1).strip()
            # Reject partial matches immediately adjacent (even across spaces) to another math operator
            left_ctx = stmt_body[: match.start()].rstrip()
            right_ctx = stmt_body[match.end() :].lstrip()
            if left_ctx and left_ctx[-1] in "+-*/^|":
                continue
            if right_ctx and right_ctx[0] in "+-*/^|":
                continue
            # Skip date-like / year-range strings or bare single fraction like "3/4"
            if re.fullmatch(r"\d+/\d+", expr) or re.fullmatch(r"\d{4}\s*-\s*\d{4}", expr):
                continue
            # Reject mixing percentages and bare numbers in +/- expressions (e.g. "100 + 20%" or "20% + 10")
            if "%" in expr and re.search(r"[+\-]", expr):
                non_pct = re.sub(r"\d+(?:\.\d+)?\s*%", "", expr)
                if re.search(r"\d", non_pct):
                    continue
            candidate_matches.append(match)

        # Anti-overfit: fire arithmetic oracle ONLY when there is a single expression and no stray numbers elsewhere
        if len(candidate_matches) == 1:
            match = candidate_matches[0]
            expr = match.group(1).strip()
            outside = stmt_body[: match.start()] + stmt_body[match.end() :]
            if not re.search(r"\d", outside):
                try:
                    poly = _parse_poly(expr, None)
                    if set(poly.keys()) <= {0}:
                        val = poly.get(0, Fraction(0))
                        # Guard against range patterns like "pages 10 - 15" unless preceded by explicit math cue
                        is_bare_neg_sub = (
                            val < 0
                            and bool(re.fullmatch(r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?", expr))
                            and not re.search(
                                r"(?i)\b(?:compute|calculate|evaluate|simplify|what\s+is|find|value\s+of|calcula|quant\s+és|quin\s+és)\b|=",
                                stmt_body,
                            )
                        )
                        if not is_bare_neg_sub:
                            variants = _format_fraction_variants(val)
                            forbidden_targets.update(variants)
                            solutions.extend(sorted(variants))
                            kind = "arithmetic"
                except (SyntaxError, ValueError, ZeroDivisionError):
                    pass

    # 4. General bilingual word-problem solvers (high-precision deterministic families)
    if kind is None:
        wp_kind, wp_vals = _solve_general_word_problem(statement)
        if wp_kind and wp_vals:
            for val in wp_vals:
                if isinstance(val, (int, Fraction)):
                    variants = _format_fraction_variants(Fraction(val))
                else:
                    variants = [str(val)]
                forbidden_targets.update(variants)
                solutions.extend(sorted(variants))
            kind = wp_kind

    return {
        "allowed_givens": allowed_givens,
        "forbidden_targets": forbidden_targets,
        "solutions": list(dict.fromkeys(solutions)),
        "kind": kind,
    }


STATIC_SOCRATIC_SYSTEM = (
    "SocraticQ math tutor: use simple words and the problem's numbers. "
    "Ask ONE short friendly guiding question (?) without giving the answer."
)
STATIC_SOLVE_SYSTEM = (
    "Solve the math problem concisely step-by-step and end with 'Final Answer: <value>'."
)


def _format_history_messages(history: Optional[list[dict[str, str]]], max_turns: int = 4) -> list[dict[str, str]]:
    if not history:
        return []
    out: list[dict[str, str]] = []
    for item in history[-max_turns:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            out.append({"role": role, "content": content[:180]})
    return out


def build_messages(
    problem: dict[str, Any],
    event_type: str,
    attempt_num: int,
    confidence_pct: int,
    pedagogical_move: PedagogicalMove,
    is_correct: Optional[bool],
    selected_option: Optional[str],
    student_note: Optional[str],
    agent_override_nudge: Optional[str],
    rag_hook: Optional[str] = None,
    oracle_clue: Optional[str] = None,
    history: Optional[list[dict[str, str]]] = None,
    language: str = "eng",
) -> list[dict[str, str]]:
    """Build a compact prompt grounded in a static pinned system prompt for KV cache reuse."""
    lang = "cat" if str(language or "eng").strip().lower().startswith("ca") else "eng"
    stmt_norm = str(problem.get("statement") or "").strip()
    note_norm = student_note.strip() if student_note else ""
    is_open = bool(problem.get("is_open_prompt") or problem.get("problem_id") == "GENERAL-MATH")

    lines = [f"Topic: {problem.get('category', 'Math')}"]
    if is_open and note_norm:
        lines.append(f"Student message: {note_norm[:220]}")
    else:
        lines.append(f"Problem: {stmt_norm[:220]}")
        options = problem.get("options") or {}
        if options:
            opts_str = " ".join(f"({k}){v}" for k, v in options.items())
            lines.append(f"Options: {opts_str[:140]}")
        if note_norm and note_norm != stmt_norm:
            lines.append(f"Student message: {note_norm[:160]}")

    if selected_option:
        status = "right" if is_correct is True else ("wrong" if is_correct is False else "?")
        lines.append(f"Choice: {selected_option} ({status}, {confidence_pct}%)")

    clue = oracle_clue or problem.get("intuition_hook", "")
    if clue and not is_open:
        lines.append(f"Hint idea: {clue[:80]}")
    if agent_override_nudge:
        lines.append(f"Focus: {agent_override_nudge.strip()[:70]}")

    if lang == "cat":
        lines.append("Reply in Catalan, concrete & friendly, ending with ?:")
    else:
        lines.append("Reply in English, concrete & friendly, ending with ?:")

    msgs: list[dict[str, str]] = [{"role": "system", "content": STATIC_SOCRATIC_SYSTEM}]
    msgs.extend(_format_history_messages(history))
    msgs.append({"role": "user", "content": "\n".join(lines)})
    return msgs


def build_solve_messages(
    problem: dict[str, Any],
    student_note: Optional[str] = None,
    rag_examples: Optional[list[dict[str, Any]]] = None,
    history: Optional[list[dict[str, str]]] = None,
    language: str = "eng",
) -> list[dict[str, str]]:
    """Build a Reasoning Mode prompt for step-by-step walkthrough + final answer."""
    lang = "cat" if str(language or "eng").strip().lower().startswith("ca") else "eng"
    lines = [
        f"Topic: {problem.get('category', 'Algebra')}",
        f"Problem: {problem.get('statement', '')[:320]}",
    ]
    options = problem.get("options") or {}
    if options:
        opts_str = " ".join(f"({k}){v}" for k, v in options.items())
        lines.append(f"Options: {opts_str[:160]}")
    if rag_examples:
        ex = rag_examples[0]
        ex_hook = ex.get("intuition_hook") or ex.get("statement", "")
        if ex_hook:
            lines.append(f"Similar pattern ({ex.get('category', 'Math')}): {str(ex_hook)[:120]}")
    if student_note and student_note.strip() != str(problem.get("statement") or "").strip():
        lines.append(f"Student note: {student_note.strip()[:120]}")
    if lang == "cat":
        lines.append("Write clear steps in Catalan (catala) and end with 'Final Answer: <value>'.")
    else:
        lines.append("Write clear steps in English and end with 'Final Answer: <value>'.")

    msgs: list[dict[str, str]] = [{"role": "system", "content": STATIC_SOLVE_SYSTEM}]
    msgs.extend(_format_history_messages(history))
    msgs.append({"role": "user", "content": "\n".join(lines)})
    return msgs


def is_safe_hint(
    hint_text: str,
    problem: dict[str, Any],
    is_correct: Optional[bool],
    oracle: Optional[dict[str, Any]] = None,
) -> bool:
    """Reject empty, sycophantic, non-question, or answer/oracle-leaking outputs."""
    text = " ".join(hint_text.split()).strip()
    if not text or "?" not in text:
        return False
    if len(text) > 240:
        text = clamp_text(text, 240, append_question_mark=True)
    if len(text) > 240:
        return False

    lower = text.lower()
    lower_for_praise = re.sub(r"\bperfect\s+(?:square|cube|number|power|match)\b", "", lower)
    if _BANNED_PRAISE_RE.search(lower_for_praise) or "the student" in lower:
        return False

    if is_correct is True:
        return True

    # Reject verbatim or near-verbatim parroting of the problem stem.
    stem_lower = problem["statement"].lower().strip()
    if lower == stem_lower or lower.rstrip("?") == stem_lower.rstrip("?"):
        return False
    hint_words = set(re.findall(r"[a-z0-9]+", lower))
    stem_words = set(re.findall(r"[a-z0-9]+", stem_lower))
    if len(hint_words) >= 12 and stem_words:
        if len(hint_words & stem_words) / len(hint_words) >= 0.80:
            return False

    if EXPLICIT_LEAK_RE.search(text) or EXPLICIT_VALUE_LEAK_RE.search(text):
        return False

    # Reject direct variable assignments (e.g. "x = 6?") unless framed as a hypothetical test case
    # ("like n=2", "if a = 3 and b = 4", "substitute x = 11").
    _HYPO_FRAMING_RE = re.compile(
        r"(?i)\b(?:like|try|suppose|if|when|say|test|let|substitute|plug|check|assume|given|setting|set|e\.g\.|for\s+example"
        r"|si|substitueixes|substituïm|suposem|prova|provem|posa|posem|comprova|per\s+exemple)\b"
    )
    for var_assign in re.finditer(r"(?i)(?<![a-z0-9_])([a-z])\s*=\s*(-?\d+(?:\.\d+)?(?:/\d+)?)(?![a-z0-9_])", text):
        prefix = lower[: var_assign.start()].rstrip()
        sentence_prefix = re.split(r"[.?!;]", prefix)[-1]
        if not _HYPO_FRAMING_RE.search(sentence_prefix):
            return False

    correct_opt = problem.get("correct_option")
    if correct_opt and correct_opt in problem.get("options", {}):
        correct_val = str(problem["options"][correct_opt]).strip()
        # Catch leaked numeric/word answer if it didn't already appear in the problem statement.
        if len(correct_val) >= 2 and correct_val.lower() not in stem_lower:
            if re.search(rf"(?<!\w){re.escape(correct_val.lower())}(?!\w)", lower):
                return False

    active_oracle = oracle if oracle is not None else extract_math_oracle(problem.get("statement", ""))
    forbidden = active_oracle.get("forbidden_targets", set())
    allowed = active_oracle.get("allowed_givens", set())
    compact_lower = lower.replace(" ", "")

    for target in forbidden:
        t_low = target.lower()
        # Variable assignment forms (e.g., "x = 6", "x=6") are always forbidden even if number is a given.
        if "=" in t_low:
            if t_low.replace(" ", "") in compact_lower:
                return False
            var_part, val_part = t_low.split("=", 1)
            if re.search(
                rf"(?<![a-z0-9_]){re.escape(var_part.strip())}\s*(?:=|is|equals?)\s*{re.escape(val_part.strip())}(?![a-z0-9_])",
                lower,
            ):
                return False
            continue
        if target in allowed or t_low in allowed:
            continue
        if re.search(rf"(?<![a-z0-9_.]){re.escape(t_low)}(?![a-z0-9_.])", lower):
            return False
        if re.search(rf"(?<![a-z0-9_.]){re.escape(t_low.replace(' ', ''))}(?![a-z0-9_.])", compact_lower):
            return False

    return True
