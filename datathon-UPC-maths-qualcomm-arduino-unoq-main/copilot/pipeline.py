"""Calibrated SocraticQ pipeline: one problem in, one deterministic A-E letter out.

Wires predict.py, eval.py, and explorer/server.py to engine.socratic_engine.solve_mcq_calibrated
while preserving a safe fallback to legacy copilot.llm if the parent engine package is absent.
"""
import json
import os
import socket
import sys
import time
import urllib.error

from .config import CONFIG
from .llm import URL, answer_letter

TIME_LIMIT = float(CONFIG["TIME_LIMIT_SECONDS"])
FALLBACK = str(CONFIG.get("FALLBACK_ANSWER") or "D").strip().upper()
if FALLBACK not in {"A", "B", "C", "D", "E"}:
    FALLBACK = "D"
MARGIN = 3.0  # seconds kept free for Python and for writing the answer
VALID_LETTERS = ("A", "B", "C", "D", "E")
TEMPERATURE = 0.0
SEED = 42

_COPILOT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_COPILOT_DIR)
_REPO_CANDIDATES = (
    os.path.dirname(_PROJECT_DIR),
    os.getcwd(),
    os.path.dirname(os.getcwd()),
    "/home/arduino/Datathon-FME-2026",
    os.path.expanduser("~/Datathon-FME-2026"),
)


def _ensure_repo_root():
    """Add the Datathon-FME-2026 repo root to sys.path so `engine` is importable from any cwd."""
    for candidate in _REPO_CANDIDATES:
        if candidate and os.path.isfile(os.path.join(candidate, "engine", "socratic_engine.py")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            return candidate
    return None


def _load_optimizer():
    """Import engine.mcq_optimizer lazily, returning None if unavailable."""
    _ensure_repo_root()
    try:
        import engine.mcq_optimizer as mcq_optimizer
        if hasattr(mcq_optimizer, "solve_mcq_optimized"):
            return mcq_optimizer
    except ImportError:
        return None
    return None


_DEFAULT_INDEX = None
_DEFAULT_INDEX_LOADED = False


def _find_train_jsonl():
    """Locate M3Kang training data (never demo/eval sets) for (level, qnum) priors."""
    candidates = (
        os.path.join(_PROJECT_DIR, "data", "train_recuperat.jsonl"),
        os.path.join(_PROJECT_DIR, "data", "train.jsonl"),
        os.path.join(os.getcwd(), "data", "train_recuperat.jsonl"),
        os.path.join(os.getcwd(), "data", "train.jsonl"),
        "/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/train_recuperat.jsonl",
        "/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/train.jsonl",
        "/home/arduino/Datathon-FME-2026/datathon-UPC-maths-qualcomm-arduino-unoq-main/data/train_recuperat.jsonl",
        "/home/arduino/Datathon-FME-2026/datathon-UPC-maths-qualcomm-arduino-unoq-main/data/train.jsonl",
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _get_default_index():
    """Lazy-load data/index.db once so zero-token kNN scoring works even when --rag 0."""
    global _DEFAULT_INDEX, _DEFAULT_INDEX_LOADED
    if _DEFAULT_INDEX_LOADED:
        return _DEFAULT_INDEX
    _DEFAULT_INDEX_LOADED = True
    for candidate in (
        os.path.join(_PROJECT_DIR, "data", "index.db"),
        os.path.join(os.getcwd(), "data", "index.db"),
        "/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/index.db",
    ):
        if os.path.isfile(candidate):
            try:
                from .rag import Index
                _DEFAULT_INDEX = Index(candidate)
                return _DEFAULT_INDEX
            except Exception:
                return None
    return None


_SIBLING_BY_ID: dict[tuple[str, str], dict] = {}
_SIBLING_LOADED_FILES: set[str] = set()


def _get_sibling_cache() -> dict[tuple[str, str], dict]:
    """Lazily build a read-only sibling cache (pid, lang) -> {text, options, text_only, language}.

    CRITICAL FAIRNESS RULE: Never read or store 'label' or 'answer'.
    """
    argv_inputs = []
    for idx, arg in enumerate(sys.argv):
        if arg == "--input" and idx + 1 < len(sys.argv):
            argv_inputs.append(sys.argv[idx + 1])
        elif arg.startswith("--input="):
            argv_inputs.append(arg.split("=", 1)[1])

    candidates = []
    for inp in argv_inputs:
        candidates.append(inp)
        candidates.append(os.path.join(_PROJECT_DIR, inp))
        candidates.append(os.path.join(os.getcwd(), inp))

    candidates.extend((
        os.path.join(_PROJECT_DIR, "data", "val.jsonl"),
        os.path.join(os.getcwd(), "data", "val.jsonl"),
        os.path.join(_PROJECT_DIR, "data", "demo", "train.jsonl"),
        os.path.join(os.getcwd(), "data", "demo", "train.jsonl"),
        "/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/val.jsonl",
        "/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/demo/train.jsonl",
    ))

    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        norm_path = os.path.abspath(path)
        if norm_path in _SIBLING_LOADED_FILES:
            continue
        _SIBLING_LOADED_FILES.add(norm_path)
        try:
            with open(norm_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    pid = str(row.get("id") or row.get("problem_id") or "").strip()
                    lang = str(row.get("language") or row.get("lang") or "").strip().lower()
                    if not pid or not lang:
                        continue
                    raw_opts = row.get("options")
                    opts = dict(raw_opts) if isinstance(raw_opts, dict) and raw_opts else {}
                    sib_entry = {
                        "text": str(row.get("text") or row.get("statement") or ""),
                        "options": opts,
                        "text_only": bool(row.get("text_only", True)),
                        "language": lang,
                    }
                    key = (pid, lang)
                    if key not in _SIBLING_BY_ID:
                        _SIBLING_BY_ID[key] = sib_entry
                    else:
                        if not _SIBLING_BY_ID[key].get("options") and opts:
                            _SIBLING_BY_ID[key]["options"] = opts
                        if not _SIBLING_BY_ID[key].get("text") and sib_entry["text"]:
                            _SIBLING_BY_ID[key]["text"] = sib_entry["text"]
        except Exception:
            pass
    return _SIBLING_BY_ID


def _prior_fallback_letter(problem, opt_mod=None, train_jsonl=None):
    """Return deterministic (level, qnum) prior A-E fallback (never blind D when priors exist)."""
    if opt_mod is not None:
        try:
            pid = str(problem.get("id") or problem.get("problem_id") or "EXAM-01")
            priors = opt_mod.load_priors(train_jsonl)
            dist = opt_mod.get_prior(
                priors, pid, problem.get("level"), problem.get("qnum"), has_options=bool(problem.get("options"))
            )
            best = max(VALID_LETTERS, key=lambda letter: (dist.get(letter, 0.0), letter))
            if best in VALID_LETTERS:
                return best
        except Exception:
            pass
    return FALLBACK


def _normalize_letter(letter, problem, opt_mod=None, train_jsonl=None):
    """Guarantee a single uppercase A-E letter."""
    cleaned = str(letter or "").strip().upper()
    if len(cleaned) >= 1 and cleaned[0] in VALID_LETTERS and (len(cleaned) == 1 or not cleaned[1].isalpha()):
        return cleaned[0]
    if cleaned in VALID_LETTERS:
        return cleaned
    return _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl)


def solve(problem, index=None, k=0, exclude_id=None, image_b64=None, llm_url=URL,
          time_limit=TIME_LIMIT):
    """Return {"answer", "seconds", "status", ...} for one problem."""
    start = time.perf_counter()
    opt_mod = _load_optimizer()
    train_jsonl = _find_train_jsonl()
    pid = str(problem.get("id") or problem.get("problem_id") or "EXAM-01")
    lang = str(problem.get("language") or problem.get("lang") or "eng").strip().lower()
    siblings = _get_sibling_cache()
    if lang == "cat" and (pid, "eng") in siblings:
        eng_sib = siblings[(pid, "eng")]
        problem = dict(problem)
        problem["eng_text"] = eng_sib["text"]
        if eng_sib.get("options"):
            problem["eng_options"] = dict(eng_sib["options"])
        if not problem.get("options") and eng_sib.get("options"):
            problem["options"] = dict(eng_sib["options"])
    elif lang == "eng" and not problem.get("options"):
        cat_sib = siblings.get((pid, "cat"))
        if cat_sib and cat_sib.get("options"):
            problem = dict(problem)
            problem["options"] = dict(cat_sib["options"])

    result = {
        "answer": _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl),
        "status": "ok",
        "prompt_tokens": 0,
        "retrieved": "",
        "examples": [],
    }

    examples = []
    twin_examples = []
    knn_votes = None
    active_index = index if index is not None else _get_default_index()
    if active_index is not None:
        try:
            search_k = max(k, 5)
            eff_exclude = exclude_id if exclude_id is not None else pid
            hits = active_index.search(problem, k=search_k, exclude_id=eff_exclude, stem_only=True)
            from .rag import compute_knn_votes
            knn_votes = compute_knn_votes(hits, min_score=0.82) or None
            twin_examples = [(score, p) for score, p in hits if score >= 0.90]
            if k > 0:
                examples = [(score, p) for score, p in hits[:k] if score >= 0.72]
                result["examples"] = hits[:k]
                result["retrieved"] = ";".join(f"{p['id']}:{s:.2f}" for s, p in result["examples"])
        except Exception:
            examples = []
            twin_examples = []
            knn_votes = None

    # Target latency <= 55s (hard contest limit is 60s)
    budget = min(float(time_limit), 55.0)
    effective_margin = MARGIN if budget > MARGIN + 1.0 else min(1.0, budget * 0.2)
    remaining = max(0.2, budget - (time.perf_counter() - start) - effective_margin)

    # Explicit vision image requests (Explorer --vision) use legacy multimodal copilot.llm.
    if opt_mod is not None and not image_b64:
        try:
            optimized = opt_mod.solve_mcq_optimized(
                problem,
                llm_url=llm_url,
                time_limit_s=remaining,
                train_jsonl=train_jsonl,
                examples=(examples or twin_examples) or None,
                knn_votes=knn_votes,
            )
            result["answer"] = _normalize_letter(
                optimized.get("answer") or optimized.get("predicted_option"),
                problem,
                opt_mod=opt_mod,
                train_jsonl=train_jsonl,
            )
            result["status"] = str(optimized.get("status") or "ok")
            result["prompt_tokens"] = int(optimized.get("prompt_tokens") or 0)
        except (socket.timeout, TimeoutError):
            result["answer"] = _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl)
            result["status"] = "timeout"
        except urllib.error.URLError as error:
            result["answer"] = _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl)
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                result["status"] = "timeout"
            else:
                result["status"] = f"error: {error.reason}"
        except Exception as error:
            result["answer"] = _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl)
            result["status"] = f"error: {error}"
        result["seconds"] = round(time.perf_counter() - start, 2)
        return result

    # Legacy fallback when optimizer is unavailable or vision image_b64 is provided.
    try:
        letter, timings = answer_letter(
            problem,
            examples,
            timeout=max(remaining, 1.0),
            image_b64=image_b64,
            url=llm_url,
        )
        result["answer"] = _normalize_letter(letter, problem, opt_mod=opt_mod, train_jsonl=train_jsonl)
        result["prompt_tokens"] = int(timings.get("prompt_n", 0) or 0)
    except (socket.timeout, TimeoutError):
        result["answer"] = _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl)
        result["status"] = "timeout"
    except urllib.error.URLError as error:
        result["answer"] = _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl)
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            result["status"] = "timeout"
        else:
            result["status"] = f"error: {error.reason}"
    except Exception as error:
        result["answer"] = _prior_fallback_letter(problem, opt_mod=opt_mod, train_jsonl=train_jsonl)
        result["status"] = f"error: {error}"

    result["seconds"] = round(time.perf_counter() - start, 2)
    return result
