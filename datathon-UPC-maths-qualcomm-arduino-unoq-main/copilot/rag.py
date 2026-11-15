"""A retrieval index in SQLite: one solved M3Kang problem per row."""
import json
import re
import sqlite3
from array import array

from .embed import embed
from .llm import format_problem

SCHEMA = """
CREATE TABLE IF NOT EXISTS problems (
    key TEXT PRIMARY KEY,   -- id|language
    problem TEXT NOT NULL,  -- the full problem as JSON
    vec BLOB NOT NULL       -- float32 vector
)"""

_CAT_LANGS = {"cat", "ca", "catalan"}
_ENG_LANGS = {"eng", "en", "english"}


def clean_stem(text):
    """Strip leading question number (e.g. '23. ') and trailing A)..E) options block."""
    s = str(text or "").strip()
    s = re.sub(r"^\s*\d{1,2}\.\s+", "", s)
    s = re.split(r"(?:\n|\s{2,})A[\)\.]\s+", s, maxsplit=1)[0]
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _normalize_stem(text, language=""):
    """Clean stem and normalize Catalan math keywords to English when engine helper is available."""
    stem = clean_stem(text)
    if str(language or "").strip().lower() in _CAT_LANGS:
        try:
            from engine.mcq_optimizer import translate_cat_to_eng_math
            return translate_cat_to_eng_math(stem, language="cat")
        except Exception:
            try:
                from engine.socratic_engine import normalize_catalan_math_keywords
                stem = normalize_catalan_math_keywords(stem, language="cat")
            except Exception:
                pass
    return stem


def format_stem_for_embedding(problem):
    """Embed clean question stem only (without A-E options) and normalize cat->eng for bge-small."""
    raw = problem.get("text") or problem.get("statement") or ""
    return _normalize_stem(raw, problem.get("language", ""))


def compute_knn_votes(hits, min_score=0.82):
    """Aggregate neighbour labels (>=min_score similarity) into A-E weights."""
    weights = {letter: 0.0 for letter in "ABCDE"}
    for score, stored in hits or ():
        if score < min_score:
            continue
        label = str(stored.get("label") or "").strip().upper()
        if label in weights:
            diff = max(0.0, float(score) - min_score + 0.02)
            weights[label] += diff * diff
    total = sum(weights.values())
    if total <= 0.0:
        return {}
    return {k: round(v / total, 6) for k, v in weights.items() if v > 0.0}


def build(db_path, problems, batch=16, stem_only=False):
    eng_by_id = {}
    if stem_only:
        for p in problems:
            lang = str(p.get("language") or "").strip().lower()
            pid = p.get("id")
            if pid and lang in _ENG_LANGS:
                eng_by_id[pid] = p

    db = sqlite3.connect(db_path)
    db.execute(SCHEMA)
    for i in range(0, len(problems), batch):
        chunk = problems[i:i + batch]
        if stem_only:
            texts = []
            for p in chunk:
                lang = str(p.get("language") or "").strip().lower()
                pid = p.get("id")
                if lang in _CAT_LANGS and pid in eng_by_id:
                    texts.append(format_stem_for_embedding(eng_by_id[pid]))
                else:
                    texts.append(format_stem_for_embedding(p))
        else:
            texts = [format_problem(p) for p in chunk]
        vectors = embed(texts)
        db.executemany("INSERT OR REPLACE INTO problems VALUES (?, ?, ?)", [
            (f"{p['id']}|{p['language']}", json.dumps(p, ensure_ascii=False), array("f", v).tobytes())
            for p, v in zip(chunk, vectors)])
        db.commit()
        yield min(i + batch, len(problems))
    db.close()


class Index:
    """Loads every vector into memory once, so each search is a fast filtered loop."""

    def __init__(self, db_path):
        db = sqlite3.connect(db_path)
        self.rows = []
        for key, problem, blob in db.execute("SELECT key, problem, vec FROM problems"):
            vec = array("f")
            vec.frombytes(blob)
            self.rows.append((key, json.loads(problem), vec))
        db.close()

    def search(self, problem, k=1, exclude_id=None, min_score=0.0, prefer_same_lang=True, stem_only=False):
        """Return up to k (score, problem) pairs, most similar first.

        exclude_id skips the same problem in other languages, so validation
        never retrieves its own translation.
        """
        eng_override = str(problem.get("eng_text") or "").strip()
        if stem_only:
            q_text = clean_stem(eng_override) if eng_override else format_stem_for_embedding(problem)
        elif eng_override:
            eng_prob = dict(problem)
            eng_prob["text"] = eng_override
            if problem.get("eng_options"):
                eng_prob["options"] = problem["eng_options"]
            q_text = format_problem(eng_prob)
        else:
            q_text = format_problem(problem)
        query = embed([q_text], query=True)[0]
        q_lang = str(problem.get("language") or "").strip().lower()
        preferred_langs = {q_lang} if q_lang else set()
        if eng_override or q_lang in _ENG_LANGS:
            preferred_langs.update(_ENG_LANGS)
        scored = []
        for _key, stored, vec in self.rows:
            if exclude_id not in (None, "") and stored.get("id") == exclude_id:
                continue
            sim = sum(a * b for a, b in zip(query, vec))
            if sim < min_score:
                continue
            s_lang = str(stored.get("language") or "").strip().lower()
            rank_sim = sim + (0.015 if prefer_same_lang and preferred_langs and s_lang in preferred_langs else 0.0)
            scored.append((rank_sim, sim, stored))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        seen_ids = set()
        for _rank_sim, sim, stored in scored:
            sid = stored.get("id")
            if sid:
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
            out.append((sim, stored))
            if len(out) >= k:
                break
        return out
