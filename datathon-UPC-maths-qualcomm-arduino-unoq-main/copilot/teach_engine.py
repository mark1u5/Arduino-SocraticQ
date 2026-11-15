"""2-Rung Socratic Hint Ladder for the Uno Q Copilot ("Teach Mode").

Rung 1 (<0.2s, 0 LLM tokens):
    Infers the mathematical topic from a RAG neighbor or bilingual keyword clusters
    and returns a concise theorem / formula / heuristic.
Rung 2 (~3-5s, max_tokens=30 on :8081):
    Prompts Qwen3.5-0.8B for ONE short Socratic guiding question without revealing
    the answer, guarded by a strict Python answer-leak filter.
"""
import re
import time

from .client import post
from .config import CONFIG
from .llm import URL
from .rag import clean_stem

_TOPIC_KEYWORDS = {
    "Geometry": (
        "triangle", "triangles", "square", "squares", "rectangle", "rectangles", "circle", "circles",
        "polygon", "polygons", "pentagon", "hexagon", "heptagon", "octagon", "nonagon", "decagon", "dodecagon",
        "diagonal", "diagonals", "angle", "angles", "perimeter", "area", "radius", "diameter", "side", "sides",
        "corner", "corners", "vertex", "vertices", "cube", "cuboid", "cubes", "volume", "interior", "exterior",
        "quadrat", "quadrats", "cercle", "cercles", "polígon", "polígon", "pentàgon", "hexàgon", "heptàgon",
        "octàgon", "decàgon", "perímetre", "perimetre", "àrea", "area", "costat", "costats", "radi", "diàmetre",
        "vèrtex", "vèrtexs", "vertexs", "cub", "cubs", "volum", "paral·lel",
    ),
    "Combinatorics": (
        "ways", "arrange", "arrangements", "permutation", "combination", "probability", "grid",
        "domino", "chessboard", "pigeonhole", "pair", "pairs", "balls", "bag", "handshake", "handshakes",
        "coin", "coins", "dice", "card", "cards", "subset", "subsets", "tournament",
        "maneres", "combinatòria", "combinatoria", "probabilitat", "tauler", "casella", "caselles",
        "parella", "parelles", "boles", "bossa", "encaixada", "encaixades", "moneda", "monedes",
        "dau", "daus", "carta", "cartes", "partit", "torneig",
    ),
    "NumberTheory": (
        "digit", "digits", "divisible", "divisibility", "remainder", "remainders", "modulo", "prime", "primes",
        "multiple", "multiples", "factor", "factors", "gcd", "lcm", "even", "odd", "integer", "integers",
        "xifra", "xifres", "divisible", "divisibilitat", "residu", "residus", "primer", "primers",
        "múltiple", "múltiples", "parell", "senar", "parells", "senars", "nombre", "nombres", "enter", "enters",
    ),
    "Algebra": (
        "equation", "equations", "solve", "variable", "variables", "sum", "average", "mean", "percent",
        "percentage", "ratio", "age", "ages", "speed", "distance", "cost", "price", "euros",
        "fraction", "fractions", "sequence", "sequences", "inequality",
        "equació", "equacions", "suma", "mitjana", "percentatge", "edat", "edats", "preu", "preus",
        "fracció", "fraccions", "successió", "successions", "desigualtat",
    ),
    "Logic": (
        "truth", "lie", "lies", "liar", "liars", "lying", "contradiction", "always", "never", "must be",
        "knight", "knights", "statement", "statements",
        "veritat", "mentida", "mentides", "mentider", "mentiders", "contradicció", "sempre", "mai", "cavaller",
    ),
}

_RUNG1_THEOREMS = {
    "Geometry": (
        "Polygon & Area Invariants: Split any n-gon into (n - 2) triangles (angle sum = (n - 2) * 180 deg). "
        "For regular polygons, Area = (1/2) * perimeter * apothem; for composite shapes, decompose into rectangles and right triangles.",
        "Which simpler triangles or rectangles can you split this figure into first?",
    ),
    "Combinatorics": (
        "Counting & Invariants: Check parity (even/odd coloring), symmetry, or the Pigeonhole Principle "
        "(with k buckets, k + 1 items force a repeat) before listing cases systematically.",
        "What invariant or worst-case grouping limits the number of possibilities here?",
    ),
    "NumberTheory": (
        "Divisibility & Modular Arithmetic: Track remainders mod 2, 3, 9, or 10 (last digit), "
        "and factor numbers into primes to constrain possible digits or divisors.",
        "What does divisibility or the remainder tell you about the last digit or factors?",
    ),
    "Algebra": (
        "Balance & Rate Equations: Name the unknown quantity x, translate each sentence into one linear relation, "
        "and keep both sides balanced while isolating x.",
        "Which unknown quantity would unlock the rest if you wrote one equation for it?",
    ),
    "Logic": (
        "Case Split & Contradiction: Assume one candidate statement is true and trace its consequences "
        "step by step until either every condition holds or a contradiction appears.",
        "If you assume the first statement is true, which condition immediately breaks or holds?",
    ),
}


def _infer_topic(problem, index=None):
    """Infer topic from RAG neighbor (if index provided) or bilingual keyword clusters."""
    text = str(problem.get("text") or problem.get("statement") or "").lower()
    if index is not None:
        try:
            hits = index.search(problem, k=1, exclude_id=problem.get("id"), stem_only=True)
            if hits and hits[0][0] >= 0.60:
                neighbor = hits[0][1]
                ncat = str(neighbor.get("category") or "").strip()
                if ncat in _RUNG1_THEOREMS:
                    return ncat, f"rag:{neighbor.get('id', 'neighbor')}"
                text = f"{text} {str(neighbor.get('text') or '').lower()}"
        except Exception:
            pass

    best_topic = "Algebra"
    best_hits = 0
    for topic, kws in _TOPIC_KEYWORDS.items():
        hits = sum(1 for kw in kws if re.search(rf"\b{re.escape(kw)}\b", text))
        if hits > best_hits:
            best_hits = hits
            best_topic = topic
    return best_topic, "keywords"


def contains_answer_leak(hint_text, problem):
    """Return True if hint_text reveals the correct option letter or option value."""
    text = str(hint_text or "").strip()
    if not text:
        return True
    low = text.lower()
    label = str(problem.get("label") or problem.get("correct_option") or "").strip().upper()
    raw_opts = problem.get("options") or {}
    if isinstance(raw_opts, (list, tuple)):
        options = {l: v for l, v in zip("ABCDE", raw_opts)}
    elif isinstance(raw_opts, dict):
        options = raw_opts
    else:
        options = {}

    if label in {"A", "B", "C", "D", "E"}:
        # Explicit letter revelation patterns (ENG + CAT)
        letter_pat = rf"(?i)\b(?:answer|option|choice|letter|choose|select|pick|resposta|opci[oó]|lletra|tria|marca)\s*(?:is|és|:|=)?\s*\(?{label}\)?(?!\w)"
        if re.search(letter_pat, text):
            return True
        if re.match(rf"^\s*\(?{label}\)?[\)\.\:]\s+", text):
            return True

        ans_val = str(options.get(label) or "").strip()
        if ans_val:
            ans_low = ans_val.lower()
            # Check exact option value occurrence (for length >= 2 or standalone numeric match)
            if len(ans_low) >= 2 and re.search(rf"(?<!\w){re.escape(ans_low)}(?!\w)", low):
                return True
            if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", ans_low):
                if re.search(rf"(?<!\d){re.escape(ans_low)}(?!\d)", low):
                    return True
    return False


def _extract_single_question(raw_text):
    """Keep at most one concise Socratic question."""
    cleaned = re.sub(r"\s+", " ", str(raw_text or "")).strip()
    if not cleaned:
        return ""
    q_idx = cleaned.find("?")
    if q_idx != -1:
        return cleaned[: q_idx + 1].strip()
    return cleaned[:180].strip()


def hint_rung1(problem, index=None):
    """Rung 1 (<0.2s, 0 LLM tokens): return topic + mathematical theorem/formula/heuristic."""
    t0 = time.perf_counter()
    topic, source = _infer_topic(problem, index=index)
    theorem, fallback_q = _RUNG1_THEOREMS[topic]
    return {
        "rung": 1,
        "topic": topic,
        "topic_source": source,
        "theorem": theorem,
        "socratic_question": fallback_q,
        "hint_text": f"[{topic}] {theorem}",
        "is_safe": not contains_answer_leak(theorem, problem),
        "used_llm": False,
        "offline_mode": True,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


def hint_rung2(problem, index=None, llm_url=URL, timeout=12.0):
    """Rung 2 (~3-5s, max_tokens=30 on :8081): ONE short Socratic question with leak filter."""
    t0 = time.perf_counter()
    rung1 = hint_rung1(problem, index=index)
    topic = rung1["topic"]
    fallback_q = rung1["socratic_question"]

    stem = clean_stem(problem.get("text") or problem.get("statement") or "")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a Socratic math tutor. Ask ONE short guiding question (under 25 words) "
                "in the student's language. NEVER give the final number, calculation result, or option letter."
            ),
        },
        {
            "role": "user",
            "content": f"Topic: {topic}\nProblem: {stem}\nOne short Socratic question:",
        },
    ]

    candidate = ""
    used_llm = False
    leaked = False
    try:
        resp = post(
            llm_url,
            {
                "messages": messages,
                "max_tokens": 30,
                "temperature": 0.0,
                "seed": int(CONFIG.get("SEED", 42)),
                "cache_prompt": True,
            },
            timeout=timeout,
        )
        raw = str(resp["choices"][0]["message"]["content"] or "")
        cand = _extract_single_question(raw)
        if cand and not contains_answer_leak(cand, problem):
            candidate = cand
            used_llm = True
        elif cand:
            leaked = True
    except Exception:
        candidate = ""

    final_hint = candidate if candidate else fallback_q
    return {
        "rung": 2,
        "topic": topic,
        "topic_source": rung1["topic_source"],
        "hint_text": final_hint,
        "is_safe": not contains_answer_leak(final_hint, problem),
        "leaked_filtered": leaked,
        "used_llm": used_llm,
        "offline_mode": True,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


def get_teach_hint(problem, rung=1, index=None, llm_url=URL, timeout=12.0):
    """Unified entrypoint for the 2-Rung Hint Ladder."""
    if int(rung) <= 1:
        return hint_rung1(problem, index=index)
    return hint_rung2(problem, index=index, llm_url=llm_url, timeout=timeout)
