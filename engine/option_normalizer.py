"""Utilities for normalizing RapidOCR option artifacts, parsing values, and matching answers."""

from __future__ import annotations

from fractions import Fraction
import math
import re
from typing import Any, Optional

__all__ = [
    "normalize_options",
    "parse_numeric_value",
    "parse_time_minutes",
    "match_value_to_unique_option",
    "translate_cat_to_eng_math",
]

_UNICODE_MINUS_TRANS = str.maketrans({
    "\u4e00": "-",  # CJK '一' misread for minus
    "\u2212": "-",  # Unicode minus '−'
    "\u2013": "-",  # En dash '–'
    "\u2014": "-",  # Em dash '—'
})

_LEADING_ONE_PAREN_RE = re.compile(r"^1\s+(\d{2,})(.*)$")
_CLOCK_SPACE_RE = re.compile(r"(\d)\s*:\s*(\d)")
_SPLIT_DIGITS_RE = re.compile(r"(?<=\d)\s+(?=\d+\b(?!/))")
_LEADING_MINUS_SPACE_RE = re.compile(r"^-\s+")
_LATEX_TEXT_WRAP_RE = re.compile(r"\\(?:text|mathrm|textbf|mathbf)\s*\{([^{}]*)\}")
_LATEX_SPACE_RE = re.compile(r"\\[,;! ]")

_UNIT_SUFFIX_RE = re.compile(
    r"\s*(?:°|deg(?:rees?)?|cèntims|cents|euros?|eur|€|\$|km/h|m/s|cm\^2|cm²|cm\^3|cm³|m\^2|m²|m\^3|m³|km|cm|mm|dm|m|kg|mg|g|ml|l|hrs?|hours?|min|sec|s)\s*$",
    re.IGNORECASE,
)

_LATEX_FRAC_RE = re.compile(
    r"^([+-]?)\s*\\[dt]?frac\s*\{\s*([+-]?\d+(?:\.\d+)?)\s*\}\s*\{\s*([+-]?\d+(?:\.\d+)?)\s*\}$"
)
_MIXED_FRAC_RE = re.compile(r"^([+-]?\d+)\s+(\d+)\s*/\s*(\d+)$")
_PLAIN_FRAC_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)$")
_TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2})|\.(\d{2})\s*h)$", re.IGNORECASE)

_CATALAN_ARTICLES_RE = re.compile(r"^(?:en\s+|la\s+|el\s+|els\s+|les\s+|l['’])", re.IGNORECASE)
_CATALAN_CLITICS_RE = re.compile(r"\b[ldns]['’]", re.IGNORECASE)

_CAT_TO_ENG_TERMS: tuple[tuple[str, str], ...] = (
    ("parelles ordenades", "ordered pairs"),
    ("moneda equilibrada", "fair coin"),
    ("recta numèrica", "number line"),
    ("última xifra", "last digit"),
    ("conjunt buit", "empty set"),
    ("ordre invers", "reverse order"),
    ("després que", "behind"),
    ("abans que", "ahead of"),
    ("subconjunts", "subsets"),
    ("successió", "sequence"),
    ("diumenges", "Sundays"),
    ("cavallers", "knights"),
    ("mentiders", "liars"),
    ("endavant", "forward"),
    ("rellisca", "slides"),
    ("múltiple", "multiple"),
    ("granota", "frog"),
    ("cèntims", "cents"),
    ("darrere", "behind"),
    ("parells", "even"),
    ("parella", "even"),
    ("divisor", "divisor"),
    ("enrere", "backward"),
    ("davant", "ahead"),
    ("residu", "remainder"),
    ("senars", "odd"),
    ("aresta", "edge"),
    ("veïns", "neighbours"),
    ("salts", "jumps"),
    ("salta", "jumps"),
    ("cares", "heads"),
)

_CAT_TERM_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(src) for src, _ in _CAT_TO_ENG_TERMS) + r")(?!\w)",
    re.IGNORECASE,
)
_CAT_TERM_MAP: dict[str, str] = {src.lower(): dst for src, dst in _CAT_TO_ENG_TERMS}


def _normalize_option_text(text: str) -> str:
    """Clean RapidOCR artifacts from a single option string."""
    s = text.strip().translate(_UNICODE_MINUS_TRANS)
    s = s.replace("{,}", ".")
    s = _LATEX_TEXT_WRAP_RE.sub(r" \1 ", s)
    s = _LATEX_SPACE_RE.sub(" ", s)
    s = _LEADING_MINUS_SPACE_RE.sub("-", s)

    # Fix RapidOCR reading ')' as leading '1 ' before a 2+ digit integer (>= 10).
    m = _LEADING_ONE_PAREN_RE.match(s)
    if m and int(m.group(1)) >= 10:
        s = m.group(1) + m.group(2)

    s = _CLOCK_SPACE_RE.sub(r"\1:\2", s)
    s = _SPLIT_DIGITS_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_options(options: dict[str, str]) -> dict[str, str]:
    """Normalize A-E option strings by cleaning RapidOCR artifacts."""
    return {
        key: _normalize_option_text("" if val is None else str(val))
        for key, val in options.items()
    }


def _parse_exact_fraction(text: str) -> Optional[Fraction]:
    """Parse an integer, decimal, percentage, fraction, or LaTeX fraction into Fraction."""
    s = _normalize_option_text(text)
    if not s:
        return None

    is_percent = False
    if s.endswith("%"):
        is_percent = True
        s = s[:-1].strip()
    else:
        s = _UNIT_SUFFIX_RE.sub("", s).strip()
        if s.endswith("%"):
            is_percent = True
            s = s[:-1].strip()

    if not s:
        return None

    # Convert European decimal comma (e.g. '22,5' or '0,80') when unambiguous
    if re.fullmatch(r"[+-]?\d+,\d+", s):
        s = s.replace(",", ".")

    frac: Optional[Fraction] = None

    m_latex = _LATEX_FRAC_RE.match(s)
    if m_latex:
        sign_str, num_str, den_str = m_latex.groups()
        den = Fraction(den_str)
        if den == 0:
            return None
        frac = Fraction(num_str) / den
        if sign_str == "-":
            frac = -frac

    if frac is None:
        m_mixed = _MIXED_FRAC_RE.match(s)
        if m_mixed:
            whole_str, num_str, den_str = m_mixed.groups()
            den = int(den_str)
            if den == 0:
                return None
            whole = int(whole_str)
            part = Fraction(int(num_str), den)
            frac = whole - part if whole_str.startswith("-") else whole + part

    if frac is None:
        m_plain = _PLAIN_FRAC_RE.match(s)
        if m_plain:
            num_str, den_str = m_plain.groups()
            den = Fraction(den_str)
            if den == 0:
                return None
            frac = Fraction(num_str) / den

    if frac is None:
        # Reject non-numeric tokens like "inf", "nan", or comma-separated lists.
        if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", s):
            try:
                frac = Fraction(s)
            except (ValueError, ZeroDivisionError):
                return None

    if frac is None:
        return None

    return frac / 100 if is_percent else frac


def parse_numeric_value(text: str) -> Optional[float]:
    """Parse an option or answer string into a finite float, or None if not numeric."""
    if not isinstance(text, str):
        text = str(text)
    frac = _parse_exact_fraction(text)
    if frac is None:
        return None
    val = float(frac)
    return val if math.isfinite(val) else None


def parse_time_minutes(text: str) -> Optional[int]:
    """Parse 'HH:MM', 'H:MM', or 'HH.MM h' into minutes since 00:00."""
    s = _CLOCK_SPACE_RE.sub(r"\1:\2", str(text).strip())
    m = _TIME_RE.match(s)
    if not m:
        return None
    hours = int(m.group(1))
    minutes = int(m.group(2) if m.group(2) is not None else m.group(3))
    if hours > 24 or minutes > 59 or (hours == 24 and minutes != 0):
        return None
    return hours * 60 + minutes


def _strip_catalan_articles(text: str) -> str:
    s = " ".join(text.strip().split())
    while True:
        stripped = _CATALAN_ARTICLES_RE.sub("", s, count=1).strip()
        if stripped == s:
            return s
        s = stripped


def _normalize_word_exact(text: str) -> str:
    return _strip_catalan_articles(text).lower()


def _normalize_word_ocr(text: str) -> str:
    cleaned = _strip_catalan_articles(text)
    return re.sub(r"[Il]", "l", cleaned).lower()


def _coerce_numeric(val: Any) -> tuple[Optional[Fraction], Optional[float]]:
    if isinstance(val, bool):
        return None, None
    if isinstance(val, Fraction):
        return val, float(val)
    if isinstance(val, int):
        return Fraction(val), float(val)
    if isinstance(val, float):
        if not math.isfinite(val):
            return None, None
        return None, val
    if isinstance(val, str):
        frac = _parse_exact_fraction(val)
        if frac is not None:
            fval = float(frac)
            if math.isfinite(fval):
                return frac, fval
    return None, None


def _unique_key(matches: list[str]) -> Optional[str]:
    return matches[0] if len(matches) == 1 else None


def match_value_to_unique_option(val: Any, options: dict[str, str]) -> Optional[str]:
    """Match a predicted value to a unique A-E option key after normalizing options."""
    if val is None or not options:
        return None

    norm_opts = normalize_options(options)

    # 1. Direct letter 'A'..'E'
    if isinstance(val, str):
        candidate = val.strip().upper()
        if candidate in {"A", "B", "C", "D", "E"} and candidate in norm_opts:
            return candidate

    # 2. Clock time strings or integer minutes against HH:MM options
    target_mins: Optional[int] = None
    if isinstance(val, str):
        target_mins = parse_time_minutes(val)
    elif isinstance(val, int) and not isinstance(val, bool):
        target_mins = val

    if target_mins is not None:
        time_matches = [
            key
            for key, opt_val in norm_opts.items()
            if (opt_mins := parse_time_minutes(opt_val)) is not None and opt_mins == target_mins
        ]
        if time_matches:
            return _unique_key(time_matches)

    # 3. Exact / normalized string match (with Catalan article stripping & OCR I/l folding)
    if isinstance(val, str):
        val_clean = _normalize_option_text(val)
        exact_target = _normalize_word_exact(val_clean)
        if exact_target:
            exact_matches = [
                key
                for key, opt_val in norm_opts.items()
                if _normalize_word_exact(opt_val) == exact_target
            ]
            if exact_matches:
                return _unique_key(exact_matches)

            ocr_target = _normalize_word_ocr(val_clean)
            ocr_matches = [
                key
                for key, opt_val in norm_opts.items()
                if _normalize_word_ocr(opt_val) == ocr_target
            ]
            if ocr_matches:
                return _unique_key(ocr_matches)

            # Unit-stripped & space-collapsed symbolic match (e.g. '10\\pi' vs '10\\pi km', '40:3' vs '40 : 3')
            def _sym_canon(t: str) -> str:
                u = _UNIT_SUFFIX_RE.sub("", t).strip()
                u = u.replace("\\times", "×").replace("*", "×").replace("π", "\\pi")
                return re.sub(r"\s+", "", u).lower()

            sym_target = _sym_canon(val_clean)
            if sym_target:
                sym_matches = [
                    key for key, opt_val in norm_opts.items() if _sym_canon(opt_val) == sym_target
                ]
                if sym_matches:
                    return _unique_key(sym_matches)

    # 4. Numeric / Fraction comparison with relative tolerance 1e-6
    val_frac, val_float = _coerce_numeric(val)
    if val_float is not None:
        num_matches: list[str] = []
        for key, opt_val in norm_opts.items():
            opt_frac, opt_float = _coerce_numeric(opt_val)
            if opt_float is None:
                continue
            if val_frac is not None and opt_frac is not None:
                if val_frac == opt_frac or math.isclose(
                    val_float, opt_float, rel_tol=1e-6, abs_tol=1e-9
                ):
                    num_matches.append(key)
            elif math.isclose(val_float, opt_float, rel_tol=1e-6, abs_tol=1e-9):
                num_matches.append(key)
        if num_matches:
            return _unique_key(num_matches)

        # Fallback for OCR-prefixed '1/NN' or '2/NN' representing integer NN >= 10
        if abs(val_float - round(val_float)) < 1e-9 and round(val_float) >= 10:
            target_int_str = str(int(round(val_float)))
            ocr_frac_hits = [
                key
                for key, opt_val in norm_opts.items()
                if re.fullmatch(rf"[12]/{target_int_str}", opt_val.strip())
            ]
            if len(ocr_frac_hits) == 1:
                return ocr_frac_hits[0]

    return None


def translate_cat_to_eng_math(text: str, language: Optional[str] = "cat") -> str:
    """Normalize Catalan clitics and translate Catalan math competition terms to English."""
    if not text:
        return text
    if language is not None and language.lower() not in {"cat", "ca"}:
        return text

    s = text.replace("\u2019", "'")
    s = _CATALAN_CLITICS_RE.sub("", s)
    s = _CAT_TERM_RE.sub(lambda m: _CAT_TERM_MAP[m.group(1).lower()], s)
    return s
