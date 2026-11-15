"""Curated Proves Cangur problem bank, optional M3Kang JSONL loader, and concept guides."""

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

_ENGINE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _ENGINE_DIR.parent
DATA_PATH = _ENGINE_DIR / "data" / "cangur_problems.json"
M3KANG_SEARCH_PATHS: tuple[Path, ...] = (
    Path("/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/train_recuperat.jsonl"),
    Path("/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/train.jsonl"),
    _REPO_ROOT / "datathon-UPC-maths-qualcomm-arduino-unoq-main" / "data" / "train_recuperat.jsonl",
    _REPO_ROOT / "datathon-UPC-maths-qualcomm-arduino-unoq-main" / "data" / "train.jsonl",
    _REPO_ROOT / "datathon-UPC-maths-qualcomm-arduino-unoq" / "data" / "train_recuperat.jsonl",
    _REPO_ROOT / "datathon-UPC-maths-qualcomm-arduino-unoq" / "data" / "train.jsonl",
    _ENGINE_DIR / "data" / "m3kang_sample.jsonl",
)
DEFAULT_M3KANG_PATH = M3KANG_SEARCH_PATHS[0]
DEFAULT_EMBED_URL = "http://127.0.0.1:8082/v1/embeddings"
EMBED_TIMEOUT_SEC = float(os.environ.get("EMBED_TIMEOUT_SEC", "2.0"))
EMBED_RETRY_COOLDOWN_SEC = float(os.environ.get("EMBED_RETRY_COOLDOWN_SEC", "30.0"))
_GUIDE_VECS: dict[str, list[float]] = {}
_PROBLEM_VECS: dict[str, list[float]] = {}
_QUERY_VECS: dict[str, list[float]] = {}
_embed_retry_after: float = 0.0  # timed cooldown after :8082 failure so offline calls stay fast without permanent latch

CATEGORY_DEFAULTS = {
    "Combinatorics": ("PARITY_CHECK", "Check counting invariant", "Look for symmetry, parity, or worst-case pigeonhole limits."),
    "Geometry": ("DECOMPOSE_PROBLEM", "Check angle/side totals", "Use fixed geometric totals (180 triangle, 360 exterior turns) before parts."),
    "NumberTheory": ("BOUNDARY_TEST", "Test small cases mod n", "Try small integers to expose divisibility or repeating remainder cycles."),
    "Algebra": ("DECOMPOSE_PROBLEM", "Break into step totals", "Track each quantity change step by step before combining totals."),
    "Logic": ("COUNTEREXAMPLE_CHECK", "Test one assumption", "Assume one candidate is true and trace whether any statement contradicts it."),
}

EXTRA_KEYWORDS = {
    "Algebra": (
        "apples", "bags", "cost", "total", "left", "remaining", "each", "cromos", "queden",
        "xifres", "euros", "derivative", "integral", "limit", "slope", "equació", "equacio",
        "fraction", "fractions", "fracció", "fraccions", "denominator", "denominators", "denominador",
        "numerator", "numerador", "sequence", "successió", "successio", "function", "funció", "funcio",
        "inequality", "desigualtat", "system", "sistema", "variable", "simplify", "expressió"
    ),
    "Geometry": (
        "square", "triangle", "perimeter", "area", "cm", "angle", "quadrat", "costat", "àrea",
        "perímetre", "perimetre", "pentàgon", "pentagon", "hexàgon", "hexagon", "polígon", "poligon",
        "cercle", "circle", "decagon", "decàgon", "decagons", "decàgons", "heptagon", "heptàgon",
        "octagon", "octàgon", "nonagon", "enneàgon", "dodecagon", "dodecàgon", "diagonal", "diagonals",
        "vertex", "vertices", "vèrtex", "interior", "exterior", "regular", "convex", "radius", "radi",
        "diameter", "diàmetre", "cube", "cub", "cuboid", "volume", "volum", "side", "sides", "costats"
    ),
    "Combinatorics": (
        "ways", "arrange", "draw", "color", "grid", "domino", "maneres", "combinatòria", "combinatoria",
        "probabilitat", "probability", "handshake", "handshakes", "encaixada", "encaixades", "choose",
        "subset", "subsets", "subconjunt", "subconjunts", "committee", "coin", "coins", "moneda", "monedes",
        "dice", "dau", "daus", "card", "cards", "carta", "cartes", "knight", "liar", "cavaller", "mentider",
        "tournament", "torneig", "toss", "heads", "cara", "creu", "walk", "permutations", "combinations"
    ),
    "NumberTheory": (
        "digit", "divisible", "remainder", "integer", "multiple", "nombres", "divisibilitat", "primer",
        "nombre", "residu", "odd", "even", "parell", "senar", "parells", "senars", "gcd", "lcm", "mcd", "mcm",
        "factorial", "divisor", "divisors", "square", "quadrat perfecte", "prime", "primes", "power", "potència",
        "potencia", "modulo", "mod"
    ),
}

_CAT_GUIDE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("àrea", "area"),
    ("perímetre", "perimeter"),
    ("perimetre", "perimeter"),
    ("pentàgon", "pentagon polygon"),
    ("pentagon", "pentagon polygon"),
    ("hexàgon", "hexagon polygon"),
    ("hexagon", "hexagon polygon"),
    ("polígon", "polygon"),
    ("poligon", "polygon"),
    ("triangle", "triangle"),
    ("quadrat", "square"),
    ("cercle", "circle"),
    ("angle", "angle"),
    ("combinatòria", "combinatorics"),
    ("combinatoria", "combinatorics"),
    ("probabilitat", "probability"),
    ("equació", "equation"),
    ("equacio", "equation"),
    ("divisibilitat", "divisibility"),
    ("primer", "prime"),
    ("nombre", "number"),
)


def _normalize_cat_for_guide(text: str) -> str:
    out = (text or "").lower()
    for src, dst in _CAT_GUIDE_REPLACEMENTS:
        out = re.sub(rf"(?<![A-Za-zÀ-ÿ0-9_]){re.escape(src)}(?![A-Za-zÀ-ÿ0-9_])", dst, out)
    return out

FALLBACK_CONCEPT_GUIDES: dict[str, dict[str, Any]] = {
    "calculus": {
        "category": "Algebra",
        "keywords": ["derivative", "differentiate", "integral", "integrate", "limit", "tangent", "d/dx", "dy/dx", "rate of change"],
        "default_move": "DECOMPOSE_PROBLEM",
        "default_lcd_summary": "Check rate & local change",
        "intuition_hook": "Connect derivatives to local slope/rate of change and integrals to accumulated area over small intervals.",
        "socratic_seed": "If you nudge the input by a tiny step, how does the output change—what local rate or accumulation rule applies here?",
    },
    "word_problems": {
        "category": "Algebra",
        "keywords": ["cost", "euros", "price", "age", "speed", "distance", "train", "together", "percent", "ratio", "apples", "bags"],
        "default_move": "DECOMPOSE_PROBLEM",
        "default_lcd_summary": "Translate words to relation",
        "intuition_hook": "Name the unknown quantity first and translate each sentence into one clear balance equation.",
        "socratic_seed": "Which single unknown quantity would unlock the rest if you named it, and what condition links it to the given numbers?",
    },
    "algebra": {
        "category": "Algebra",
        "keywords": ["equation", "algebra", "solve", "variable", "polynomial", "quadratic", "linear", "factor", "=", "sequence", "function", "inequality"],
        "default_move": "INVARIANT_NUDGE",
        "default_lcd_summary": "Keep both sides balanced",
        "intuition_hook": "Preserve equality across every step and test your algebraic simplification on a small concrete number.",
        "socratic_seed": "What operation keeps both sides balanced while isolating the variable or factoring the expression?",
    },
    "geometry": {
        "category": "Geometry",
        "keywords": [
            "triangle", "angle", "polygon", "perimeter", "area", "rectangle", "circle",
            "decagon", "decàgon", "heptagon", "heptàgon", "octagon", "octàgon", "nonagon",
            "dodecagon", "dodecàgon", "diagonal", "diagonals", "vertex", "vertices", "vèrtex",
            "interior", "exterior", "regular", "convex", "radius", "diameter", "cube", "cub",
            "cuboid", "volume", "volum", "side", "sides", "costat", "costats"
        ],
        "default_move": "DECOMPOSE_PROBLEM",
        "default_lcd_summary": "Check angle/side invariants",
        "intuition_hook": "Use constant totals like 180 triangle sum or 360 exterior turns before solving parts.",
        "socratic_seed": "Which geometric total stays fixed regardless of the shape's size—perimeter, interior sum, or exterior turn?",
    },
    "parity": {
        "category": "Combinatorics",
        "keywords": [
            "parity", "odd", "even", "chessboard", "domino", "grid", "color", "tile",
            "handshake", "handshakes", "encaixada", "encaixades", "choose", "subset", "subsets",
            "coin", "coins", "card", "cards", "dice", "knight", "liar", "walk", "tournament",
            "probabilitat", "probability", "ways", "maneres"
        ],
        "default_move": "PARITY_CHECK",
        "default_lcd_summary": "Try chessboard parity",
        "intuition_hook": "Track an odd/even or black/white quantity that flips cleanly on every move.",
        "socratic_seed": "If each step changes a square's color, what must hold after an even vs. odd number of steps?",
    },
    "number_theory": {
        "category": "NumberTheory",
        "keywords": [
            "remainder", "mod", "modulo", "divisible", "prime", "lcm", "gcd", "factor", "power",
            "residu", "parell", "senar", "divisor", "divisors", "factorial", "square", "quadrat perfecte",
            "mcd", "mcm", "divisibilitat"
        ],
        "default_move": "BOUNDARY_TEST",
        "default_lcd_summary": "Test small cases mod n",
        "intuition_hook": "Compute the first few small cases to expose repeating remainder cycles or divisibility.",
        "socratic_seed": "What remainders appear if you test the first three or four small integer cases?",
    },
    "algebra_logic": {
        "category": "Logic",
        "keywords": ["truth", "lie", "lying", "logic", "contradiction", "statement", "always", "never", "why"],
        "default_move": "COUNTEREXAMPLE_CHECK",
        "default_lcd_summary": "Test a counterexample",
        "intuition_hook": "Plug a tiny concrete number or truth assignment back into every given condition.",
        "socratic_seed": "If you test your rule on the smallest concrete example (like n=2 or one assumption), does every condition still hold?",
    },
}


_CAT_REGEX_CACHE: dict[tuple[str, ...], re.Pattern[str]] = {}


def _compile_kw_regex(keywords: tuple[str, ...] | list[str]) -> re.Pattern[str] | None:
    cleaned = tuple(sorted({kw.strip().lower() for kw in keywords if kw and kw.strip()}, key=len, reverse=True))
    if not cleaned:
        return None
    pat = _CAT_REGEX_CACHE.get(cleaned)
    if pat is None:
        pat = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in cleaned) + r")\b")
        _CAT_REGEX_CACHE[cleaned] = pat
    return pat


def _infer_category(text: str, guides: dict[str, dict[str, Any]]) -> str:
    lower = text.lower()
    scores: dict[str, int] = {}
    for guide in guides.values():
        cat = guide["category"]
        pat = _compile_kw_regex(guide["keywords"])
        if pat:
            scores[cat] = scores.get(cat, 0) + len(set(pat.findall(lower)))
    for cat, kws in EXTRA_KEYWORDS.items():
        pat = _compile_kw_regex(kws)
        if pat:
            scores[cat] = scores.get(cat, 0) + len(set(pat.findall(lower)))
    best_cat = max(scores, key=lambda c: scores[c], default="Algebra")
    return best_cat if scores.get(best_cat, 0) > 0 else "Algebra"


def resolve_m3kang_path() -> Path | None:
    """Return explicit M3KANG_JSONL or first existing M3Kang JSONL in priority search order."""
    env_override = os.environ.get("M3KANG_JSONL")
    if env_override:
        p = Path(env_override)
        return p if p.is_file() else None
    for candidate in M3KANG_SEARCH_PATHS:
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=1)
def _load_bank() -> dict[str, Any]:
    bank = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    eng_twins: dict[str, dict[str, Any]] = {}
    cat_twins: dict[str, dict[str, Any]] = {}
    for pid, prob in bank.get("problems", {}).items():
        if prob.get("statement"):
            eng_twins[pid] = {
                "id": pid,
                "language": "eng",
                "text": prob["statement"],
                "options": prob.get("options") or {},
                "label": prob.get("correct_option"),
                "year": prob.get("year"),
                "difficulty_level": prob.get("difficulty_level"),
            }
        if prob.get("statement_cat"):
            cat_twins[pid] = {
                "id": pid,
                "language": "cat",
                "text": prob["statement_cat"],
                "options": prob.get("options_cat") or prob.get("options") or {},
                "label": prob.get("correct_option"),
                "year": prob.get("year"),
                "difficulty_level": prob.get("difficulty_level"),
            }

    extra_path = resolve_m3kang_path()
    if extra_path is not None and extra_path.is_file():
        guides = bank["concept_guides"]
        rows = [json.loads(line) for line in extra_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        # Index same-id language twins so Catalan inputs can reason in English and display back in Catalan.
        for r in rows:
            r_pid = str(r.get("id") or r.get("problem_id") or "")
            if not r_pid or not r.get("text"):
                continue
            lang = str(r.get("language") or "eng").lower()
            if lang == "eng":
                eng_twins[r_pid] = r
            elif lang == "cat":
                cat_twins[r_pid] = r

        for row in rows:
            pid = str(row.get("id") or row.get("problem_id") or "")
            if not pid or not row.get("options"):
                continue
            eng_src = eng_twins.get(pid)
            cat_src = cat_twins.get(pid)
            src = eng_src or row
            src_text = str(src.get("text") or row.get("text") or "").strip()
            if not src_text:
                continue
            cat = _infer_category(src_text, guides)
            move, lcd, hook = CATEGORY_DEFAULTS[cat]
            raw_level = row.get("level")
            diff_level = (
                max(1, min(3, 1 + int(raw_level) // 3))
                if raw_level is not None
                else max(1, min(3, int(row.get("difficulty_level", 2))))
            )
            entry: dict[str, Any] = {
                "problem_id": pid,
                "year": int(row.get("year") or 0),
                "category": cat,
                "difficulty_level": diff_level,
                "statement": src_text,
                "options": src.get("options") or row["options"],
                "correct_option": row.get("label") or src.get("label"),
                "default_move": move,
                "default_lcd_summary": lcd,
                "intuition_hook": hook,
                "socratic_seed": f"Break this {cat.lower()} problem down: what small step or relation can you compute first?",
                "option_misconceptions": {},
                "text_only": bool(row.get("text_only", True)),
            }
            if raw_level is not None:
                entry["raw_level"] = int(raw_level)
            if cat_src and cat_src.get("text"):
                entry["statement_cat"] = str(cat_src["text"]).strip()
                entry["options_cat"] = cat_src.get("options") or row["options"]
            if pid not in bank["problems"] or str(row.get("language", "")).lower() == "eng":
                bank["problems"][pid] = entry

    bank["_eng_by_id"] = eng_twins
    bank["_cat_by_id"] = cat_twins
    return bank


def list_problem_ids() -> list[str]:
    """Return all curated Cangur problem IDs."""
    return list(_load_bank()["problems"].keys())


def get_problem(problem_id: str) -> dict[str, Any] | None:
    """Return a curated Cangur problem dict, or None if not in the bank."""
    return _load_bank()["problems"].get(problem_id)


_PUBLIC_FIELDS = ("problem_id", "year", "category", "difficulty_level", "statement", "options")


def normalize_language(language: str | None) -> str:
    """Normalize language codes ('ca', 'ca-ES', 'catalan', 'cat' -> 'cat', else 'eng')."""
    raw = (language or "eng").strip().lower()
    if raw in {"cat", "ca", "ca-es", "catalan", "català"} or raw.startswith("ca-"):
        return "cat"
    return "eng"


def localize_problem(problem: dict[str, Any], language: str = "eng") -> dict[str, Any]:
    """Return a copy of a problem dict with statement/options switched to `language` when available."""
    lang = normalize_language(language)
    out = dict(problem)
    if "statement_eng" not in out and out.get("statement"):
        out["statement_eng"] = out["statement"]
    if "options_eng" not in out and out.get("options") is not None:
        out["options_eng"] = out["options"]
    if lang == "cat":
        if out.get("statement_cat"):
            out["statement"] = out["statement_cat"]
        if out.get("options_cat"):
            out["options"] = out["options_cat"]
    else:
        if out.get("statement_eng"):
            out["statement"] = out["statement_eng"]
        if out.get("options_eng") is not None:
            out["options"] = out["options_eng"]
    out["language"] = lang
    return out


def get_problem_localized(problem_id: str, language: str = "eng") -> dict[str, Any] | None:
    """Return a curated Cangur problem dict localized to `language`, or None if not in the bank."""
    prob = get_problem(problem_id)
    if prob is None:
        return None
    return localize_problem(prob, language)


def get_public_problems(language: str = "eng") -> list[dict[str, Any]]:
    """Return curated problems without answer keys for safe UI display."""
    lang = normalize_language(language)
    return [
        {k: lp[k] for k in _PUBLIC_FIELDS}
        for p in _load_bank()["problems"].values()
        for lp in (localize_problem(p, lang),)
    ]


def _embed(texts: list[str]) -> list[list[float]] | None:
    global _embed_retry_after
    if os.environ.get("SOCRATIC_MOCK", "0") == "1":
        return None
    now = time.monotonic()
    if now < _embed_retry_after:
        return None
    url = os.environ.get("EMBED_SERVER_URL", DEFAULT_EMBED_URL)
    req = urllib.request.Request(
        url,
        data=json.dumps({"input": texts}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _embed_retry_after = 0.0
        return [row["embedding"] for row in data["data"]]
    except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, ValueError):
        _embed_retry_after = time.monotonic() + EMBED_RETRY_COOLDOWN_SEC
        return None


def _semantic_guide_key(query: str, guides: dict[str, dict[str, Any]]) -> str | None:
    if not _GUIDE_VECS:
        keys = list(guides.keys())
        descs = [f"{guides[k]['category']}: {guides[k]['intuition_hook']}" for k in keys]
        vecs = _embed(descs)
        if not vecs:
            return None
        for k, v in zip(keys, vecs):
            _GUIDE_VECS[k] = v
    qvecs = _embed([f"Represent this sentence for searching relevant passages: {query}"])
    if not qvecs:
        return None
    qv = qvecs[0]
    return max(_GUIDE_VECS, key=lambda k: sum(a * b for a, b in zip(qv, _GUIDE_VECS[k])))


def match_concept_guide(problem_id: str, student_note: str | None) -> dict[str, Any]:
    """Build a synthetic problem entry for open/custom math prompts."""
    bank = _load_bank()
    guides: dict[str, dict[str, Any]] = {**FALLBACK_CONCEPT_GUIDES, **bank.get("concept_guides", {})}
    norm_note = _normalize_cat_for_guide(student_note or "")
    haystack = f"{problem_id} {student_note or ''} {norm_note}".lower()

    best_key = "algebra_logic"
    best_hits = 0
    for key, guide in guides.items():
        hits = sum(1 for kw in guide["keywords"] if re.search(rf"\b{re.escape(kw)}\b", haystack))
        if hits > best_hits:
            best_hits = hits
            best_key = key

    # Direct mapping: check if _infer_category detects a category with keywords
    inferred_cat = _infer_category(haystack, guides)
    cat_hits = sum(1 for kw in EXTRA_KEYWORDS.get(inferred_cat, ()) if re.search(rf"\b{re.escape(kw)}\b", haystack))
    if cat_hits > best_hits:
        for key, guide in guides.items():
            if guide["category"] == inferred_cat:
                best_key = key
                best_hits = cat_hits
                break

    if best_hits == 0 and student_note:
        semantic = _semantic_guide_key(norm_note or student_note, guides)
        if semantic:
            best_key = semantic
        elif re.search(r"\d\s*[+\-*/^=]", haystack) and "algebra" in guides:
            best_key = "algebra"

    chosen = guides[best_key]

    # Parse inline options (A) ... (B) ... (C) ... (D) ... (E) if present
    parsed_options: dict[str, Any] = {}
    if student_note:
        opt_matches = re.findall(r"\(([A-E])\)\s*([^()]+?)(?=\s*\([A-E]\)|$)", student_note)
        if opt_matches:
            parsed_options = {k.strip(): v.strip() for k, v in opt_matches}

    return {
        "problem_id": problem_id,
        "year": 0,
        "category": chosen["category"],
        "difficulty_level": 2,
        "statement": student_note.strip() if student_note else f"Open concept exploration ({chosen['category']}).",
        "options": parsed_options,
        "correct_option": None,
        "default_move": chosen["default_move"],
        "default_lcd_summary": chosen["default_lcd_summary"],
        "intuition_hook": chosen["intuition_hook"],
        "socratic_seed": chosen["socratic_seed"],
        "option_misconceptions": {},
        "is_open_prompt": True,
    }


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0.0 and nb > 0.0 else 0.0


def _truncate_statement(text: str, max_len: int = 160) -> str:
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= max_len:
        return cleaned
    clipped = cleaned[: max_len - 3].rsplit(" ", 1)[0].rstrip(",;:")
    return (clipped or cleaned[: max_len - 3]) + "..."


def bridge_cat_to_eng(problem: str | dict[str, Any]) -> dict[str, Any] | None:
    """Resolve a Catalan (or English) problem ID/dict onto its same-id English twin for reasoning."""
    bank = _load_bank()
    eng_by_id: dict[str, dict[str, Any]] = bank.get("_eng_by_id", {})
    cat_by_id: dict[str, dict[str, Any]] = bank.get("_cat_by_id", {})
    problems: dict[str, dict[str, Any]] = bank.get("problems", {})

    if isinstance(problem, str):
        pid = problem.strip()
        base = problems.get(pid)
        eng_twin = eng_by_id.get(pid)
        cat_twin = cat_by_id.get(pid)
        if not base and not eng_twin and not cat_twin:
            return None
        merged = dict(base or {})
        merged.setdefault("problem_id", pid)
        if eng_twin and eng_twin.get("text"):
            merged["statement"] = str(eng_twin["text"]).strip()
            merged["options"] = eng_twin.get("options") or merged.get("options", {})
            merged["statement_eng"] = merged["statement"]
            merged["options_eng"] = merged["options"]
        if cat_twin and cat_twin.get("text"):
            merged["statement_cat"] = str(cat_twin["text"]).strip()
            merged["options_cat"] = cat_twin.get("options") or merged.get("options", {})
        merged["source_language"] = "cat" if cat_twin else str(merged.get("source_language", "eng"))
        merged["reasoning_language"] = "eng" if (eng_twin or merged.get("statement")) else merged["source_language"]
        merged["bridged"] = bool(cat_twin and eng_twin)
        return merged

    pid = str(problem.get("problem_id") or problem.get("id") or "")
    lang = str(problem.get("language") or problem.get("source_language") or "eng").lower()
    raw_stmt = str(problem.get("statement") or problem.get("text") or "").strip()
    raw_opts = problem.get("options") or {}
    base = problems.get(pid, {}) if pid else {}
    eng_twin = eng_by_id.get(pid) if pid else None
    cat_twin = cat_by_id.get(pid) if pid else None

    merged = {**base, **problem}
    if pid:
        merged["problem_id"] = pid
    if lang == "cat":
        if raw_stmt:
            merged["statement_cat"] = raw_stmt
        if raw_opts:
            merged["options_cat"] = raw_opts
    elif cat_twin and cat_twin.get("text") and "statement_cat" not in merged:
        merged["statement_cat"] = str(cat_twin["text"]).strip()
        merged["options_cat"] = cat_twin.get("options") or raw_opts

    if eng_twin and eng_twin.get("text"):
        merged["statement"] = str(eng_twin["text"]).strip()
        merged["options"] = eng_twin.get("options") or raw_opts
        merged["statement_eng"] = merged["statement"]
        merged["options_eng"] = merged["options"]
        reasoning_lang = "eng"
    elif base.get("statement") and lang == "cat":
        merged["statement"] = str(base["statement"]).strip()
        merged["options"] = base.get("options") or raw_opts
        merged["statement_eng"] = merged["statement"]
        merged["options_eng"] = merged["options"]
        reasoning_lang = "eng"
    else:
        merged["statement"] = raw_stmt
        merged["options"] = raw_opts
        reasoning_lang = lang

    merged["source_language"] = lang
    merged["reasoning_language"] = reasoning_lang
    merged["bridged"] = bool(lang == "cat" and reasoning_lang == "eng" and merged.get("statement"))
    return merged


def _lexical_score_problems(
    query: str,
    candidates: list[dict[str, Any]],
) -> list[tuple[float, dict[str, Any]]]:
    _STOPWORDS = {
        "how", "many", "does", "the", "a", "an", "is", "are", "of", "in", "to", "what", "which", "have",
        "un", "una", "de", "del", "dels", "les", "per", "com", "quin", "quina", "quants", "quantes", "té", "te"
    }
    q_tokens = {w for w in re.findall(r"[a-z0-9à-ÿ]+", query.lower()) if w not in _STOPWORDS}
    scored: list[tuple[float, dict[str, Any]]] = []
    for p in candidates:
        p_tokens = {w for w in re.findall(r"[a-z0-9à-ÿ]+", f"{p['category']} {p['statement']}".lower()) if w not in _STOPWORDS}
        union = q_tokens | p_tokens
        cat_match = 0.2 if p.get("category", "").lower() in query.lower() else 0.0
        score = ((len(q_tokens & p_tokens) / len(union)) if union else 0.0) + cat_match
        scored.append((score, p))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _score_problems(
    query: str,
    candidates: list[dict[str, Any]],
    chat_mode: bool = False,
) -> list[tuple[float, dict[str, Any]]]:
    """Score candidate problems against query using :8082 embeddings with lexical-overlap fallback."""
    if not candidates or not query or not query.strip():
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    # Use :8082 semantic embeddings in both exam and chat modes. When candidate pool > 24,
    # pre-filter via fast lexical overlap (top 6 in chat_mode, top 24 otherwise) so embedding latency stays bounded.
    top_n = 6 if chat_mode else 24
    embed_pool = (
        candidates
        if len(candidates) <= top_n
        else [p for _, p in _lexical_score_problems(query, candidates)[:top_n]]
    )
    missing = [p for p in embed_pool if p["problem_id"] not in _PROBLEM_VECS]
    if missing:
        vecs = _embed([f"{p['category']}: {_truncate_statement(p['statement'], 240)}" for p in missing])
        if vecs:
            for p, v in zip(missing, vecs):
                _PROBLEM_VECS[p["problem_id"]] = v

    if any(p["problem_id"] in _PROBLEM_VECS for p in embed_pool):
        q_key = query.strip()
        qv = _QUERY_VECS.get(q_key)
        if qv is None:
            qvecs = _embed([f"Represent this sentence for searching relevant passages: {q_key}"])
            if qvecs:
                qv = qvecs[0]
                if len(_QUERY_VECS) >= 256:
                    _QUERY_VECS.clear()
                _QUERY_VECS[q_key] = qv
        if qv is not None:
            for p in embed_pool:
                pv = _PROBLEM_VECS.get(p["problem_id"])
                if pv is not None:
                    scored.append((_cosine_sim(qv, pv), p))

    if not scored:
        return _lexical_score_problems(query, candidates)

    scored.sort(key=lambda item: item[0], reverse=True)
    return scored


def _format_neighbor_result(score: float, p: dict[str, Any]) -> dict[str, Any]:
    opt = p.get("correct_option")
    item: dict[str, Any] = {
        "problem_id": p["problem_id"],
        "category": p["category"],
        "difficulty_level": int(p.get("difficulty_level", 2)),
        "statement": _truncate_statement(p["statement"], 160),
        "options": p.get("options", {}),
        "correct_option": opt,
        "correct_value": p.get("options", {}).get(opt) if opt else None,
        "intuition_hook": p.get("intuition_hook", ""),
        "score": round(float(score), 4),
    }
    if "raw_level" in p:
        item["level"] = p["raw_level"]
    if p.get("statement_cat"):
        item["statement_cat"] = _truncate_statement(p["statement_cat"], 160)
        item["options_cat"] = p.get("options_cat", {})
    return item


def retrieve_similar_problems(
    query: str,
    exclude_id: str | None = None,
    top_k: int = 1,
    chat_mode: bool = False,
) -> list[dict[str, Any]]:
    """Retrieve top-k solved Cangur/M3Kang problems via bge-small-en-v1.5 (:8082) or lexical fallback."""
    if top_k <= 0 or not query or not query.strip():
        return []

    problems = _load_bank()["problems"]
    solved = [
        p for pid, p in problems.items()
        if pid != exclude_id and p.get("correct_option") and p.get("statement")
    ]
    scored = _score_problems(query, solved, chat_mode=chat_mode)
    return [_format_neighbor_result(score, p) for score, p in scored[:top_k]]


def _effective_levels(cur: dict[str, Any], cand: dict[str, Any]) -> tuple[int, int]:
    cur_raw = cur.get("raw_level", cur.get("level"))
    cand_raw = cand.get("raw_level", cand.get("level"))
    if cur_raw is not None and cand_raw is not None:
        return int(cur_raw), int(cand_raw)
    cur_diff = int(cur.get("difficulty_level") or cur.get("level") or 2)
    cand_diff = int(cand.get("difficulty_level") or cand.get("level") or 2)
    return cur_diff, cand_diff


def get_easier_neighbor(problem: str | dict[str, Any]) -> dict[str, Any] | None:
    """Rung 1: return the nearest solved neighbor with level < current (supporting cat->eng bridge)."""
    resolved = bridge_cat_to_eng(problem)
    if resolved is None:
        return None
    query = str(resolved.get("statement") or "").strip()
    if not query:
        return None

    cur_id = resolved.get("problem_id")
    problems = _load_bank()["problems"]
    candidates: list[dict[str, Any]] = []
    for pid, cand in problems.items():
        if pid == cur_id or not cand.get("correct_option") or not cand.get("statement"):
            continue
        cur_lvl, cand_lvl = _effective_levels(resolved, cand)
        if cand_lvl < cur_lvl:
            candidates.append(cand)

    if not candidates:
        return None

    scored = _score_problems(query, candidates)
    if not scored:
        return None
    best_score, best_cand = scored[0]
    return _format_neighbor_result(best_score, best_cand)


def infer_topic_from_neighbors(problem: str | dict[str, Any], k: int = 5) -> dict[str, Any]:
    """Rung 2: infer topic and pedagogical strategy by majority vote over top-k neighbors."""
    resolved = bridge_cat_to_eng(problem)
    if resolved is None:
        query = problem if isinstance(problem, str) else ""
        cur_id = None
    else:
        query = str(resolved.get("statement") or "").strip()
        cur_id = resolved.get("problem_id")

    if k <= 0 or not query:
        cat = _infer_category(query, _load_bank()["concept_guides"])
        move, label, _ = CATEGORY_DEFAULTS[cat]
        return {
            "topic": cat,
            "category": cat,
            "strategy": move,
            "strategy_label": label,
            "confidence": 0.0,
            "votes": {},
            "k": 0,
            "neighbors": [],
        }

    problems = _load_bank()["problems"]
    candidates = [
        p for pid, p in problems.items()
        if pid != cur_id and p.get("statement") and p.get("category")
    ]
    scored = _score_problems(query, candidates)[:k]
    if not scored:
        cat = _infer_category(query, _load_bank()["concept_guides"])
        move, label, _ = CATEGORY_DEFAULTS[cat]
        return {
            "topic": cat,
            "category": cat,
            "strategy": move,
            "strategy_label": label,
            "confidence": 0.0,
            "votes": {},
            "k": 0,
            "neighbors": [],
        }

    counts: dict[str, int] = {}
    score_sums: dict[str, float] = {}
    for score, p in scored:
        cat = str(p["category"])
        counts[cat] = counts.get(cat, 0) + 1
        score_sums[cat] = score_sums.get(cat, 0.0) + float(score)

    winning_cat = max(counts.keys(), key=lambda c: (counts[c], score_sums[c], c))
    move_counts: dict[str, int] = {}
    for _, p in scored:
        if p["category"] == winning_cat and p.get("default_move"):
            mv = str(p["default_move"])
            move_counts[mv] = move_counts.get(mv, 0) + 1

    default_move, default_label, _ = CATEGORY_DEFAULTS.get(
        winning_cat, ("DECOMPOSE_PROBLEM", "Break into step totals", "")
    )
    winning_move = max(move_counts.keys(), key=lambda m: (move_counts[m], m)) if move_counts else default_move
    return {
        "topic": winning_cat,
        "category": winning_cat,
        "strategy": winning_move,
        "strategy_label": default_label,
        "confidence": round(counts[winning_cat] / len(scored), 4),
        "votes": counts,
        "k": len(scored),
        "neighbors": [p["problem_id"] for _, p in scored],
    }
