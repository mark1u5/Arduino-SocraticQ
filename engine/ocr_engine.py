"""Offline RapidOCR figure & problem extractor with persistent JSONL caching.

Safely falls back to empty results when ``rapidocr_onnxruntime`` is not
installed, while still serving any pre-populated ``.ocr_figure_cache.jsonl``
entries in sub-millisecond time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from engine.option_normalizer import normalize_options

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:  # pragma: no cover - optional on minimal environments
    RapidOCR = None  # type: ignore[assignment,misc]

_REPO_ROOT = Path(__file__).resolve().parent.parent

_OPTION_BOX_RE = re.compile(r"^\s*([A-E])\s*[)）\.\:]\s*(.*)$")
_ANGLE_RE = re.compile(r"([+-]?\d+(?:[.,]\d+)?)\s*°")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])([+-]?\d+(?:[.,]\d+)?)(?![A-Za-z0-9_])")
_UNICODE_MINUS_TRANS = str.maketrans({
    "\u4e00": "-",  # CJK '一' misread for minus
    "\u2212": "-",
    "\u2013": "-",
    "\u2014": "-",
})

_OCR_INSTANCE: Any = None
_CACHE_LOADED: bool = False
_MEMORY_CACHE: dict[str, dict[str, Any]] = {}


def _get_ocr() -> Any:
    """Lazily instantiate RapidOCR once per process."""
    global _OCR_INSTANCE
    if RapidOCR is None:
        return None
    if _OCR_INSTANCE is None:
        _OCR_INSTANCE = RapidOCR()
    return _OCR_INSTANCE


def _candidate_image_paths(problem: dict[str, Any]) -> list[Path]:
    """Return ordered candidate PNG paths for ``problem``."""
    paths: list[Path] = []
    explicit = problem.get("image_path")
    if explicit:
        p = Path(str(explicit))
        paths.append(p)
        if not p.is_absolute():
            paths.append(_REPO_ROOT / p)

    pid = str(problem.get("id") or problem.get("problem_id") or "").strip()
    lang = str(problem.get("language") or "eng").strip() or "eng"
    if pid:
        rel_candidates = [
            f"data/demo/images/{pid}_{lang}.png",
            f"data/images/{pid}_{lang}.png",
            f"datathon-UPC-maths-qualcomm-arduino-unoq-main/data/demo/images/{pid}_{lang}.png",
            f"/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/demo/images/{pid}_{lang}.png",
            f"/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/images/{pid}_{lang}.png",
        ]
        for rel in rel_candidates:
            p = Path(rel)
            paths.append(p)
            if not p.is_absolute():
                paths.append(_REPO_ROOT / p)
    return paths


def _locate_image(problem: dict[str, Any]) -> Optional[Path]:
    """Return the first existing image path for ``problem``, or None."""
    seen: set[str] = set()
    for p in _candidate_image_paths(problem):
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return p
    return None


def _candidate_cache_files(image_path: Optional[Path] = None) -> list[Path]:
    """Return candidate ``.ocr_figure_cache.jsonl`` paths."""
    files: list[Path] = []
    if image_path is not None:
        parent = image_path.parent
        # If inside .../images/<file>.png, place cache next to images/ folder.
        cache_dir = parent.parent if parent.name == "images" else parent
        files.append(cache_dir / ".ocr_figure_cache.jsonl")
    files.extend([
        Path("data/demo/.ocr_figure_cache.jsonl"),
        _REPO_ROOT / "data/demo/.ocr_figure_cache.jsonl",
        _REPO_ROOT / "datathon-UPC-maths-qualcomm-arduino-unoq-main/data/demo/.ocr_figure_cache.jsonl",
        Path("/home/arduino/datathon-UPC-maths-qualcomm-arduino-unoq/data/demo/.ocr_figure_cache.jsonl"),
    ])
    # Deduplicate preserving order
    unique: list[Path] = []
    seen: set[str] = set()
    for f in files:
        k = str(f)
        if k not in seen:
            seen.add(k)
            unique.append(f)
    return unique


def _ensure_cache_loaded(image_path: Optional[Path] = None) -> None:
    """Load any existing ``.ocr_figure_cache.jsonl`` files into memory once."""
    global _CACHE_LOADED
    if _CACHE_LOADED and image_path is None:
        return
    for cache_file in _candidate_cache_files(image_path):
        if not cache_file.is_file():
            continue
        try:
            with cache_file.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = rec.get("key")
                    if key and key not in _MEMORY_CACHE:
                        _MEMORY_CACHE[str(key)] = rec
        except OSError:
            continue
    _CACHE_LOADED = True


def _append_cache(cache_file: Path, record: dict[str, Any]) -> None:
    """Persist a single OCR record to ``cache_file`` and memory."""
    key = str(record.get("key") or "")
    if key:
        _MEMORY_CACHE[key] = record
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with cache_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _box_coords(quad: Any) -> tuple[float, float]:
    """Return top-left ``(x, y)`` for a 4-point RapidOCR bounding box."""
    xs = [float(pt[0]) for pt in quad]
    ys = [float(pt[1]) for pt in quad]
    return round(min(xs), 1), round(min(ys), 1)


def _split_zones(raw_boxes: list[Any]) -> dict[str, Any]:
    """Sort RapidOCR boxes in reading order and split into 3 vertical zones."""
    items: list[dict[str, Any]] = []
    for entry in raw_boxes or []:
        if not entry or len(entry) < 2:
            continue
        quad = entry[0]
        text = str(entry[1] or "").strip()
        if not text:
            continue
        x, y = _box_coords(quad)
        items.append({"text": text, "x": x, "y": y})

    # Reading order: cluster roughly by row (20px tolerance) then x, or y then x.
    items.sort(key=lambda b: (round(b["y"] / 15.0), b["x"], b["y"]))

    # 1. Detect bottom A..E option boxes.
    option_candidates: list[tuple[int, str, str, dict[str, Any]]] = []
    for idx, box in enumerate(items):
        m = _OPTION_BOX_RE.match(box["text"])
        if m:
            option_candidates.append((idx, m.group(1), m.group(2).strip(), box))

    option_indices: set[int] = set()
    option_boxes: list[dict[str, Any]] = []
    raw_options: dict[str, str] = {}

    if option_candidates:
        max_opt_y = max(c[3]["y"] for c in option_candidates)
        bottom_row = [c for c in option_candidates if c[3]["y"] >= max_opt_y - 100.0]
        # Keep the last occurrence per letter in the bottom row.
        by_letter: dict[str, tuple[int, str, dict[str, Any]]] = {}
        for idx, letter, val, box in bottom_row:
            by_letter[letter] = (idx, val, box)
        if len(by_letter) >= 3:
            for letter in ("A", "B", "C", "D", "E"):
                if letter in by_letter:
                    idx, val, box = by_letter[letter]
                    option_indices.add(idx)
                    option_boxes.append(box)
                    raw_options[letter] = val

    opt_min_y = min((b["y"] for b in option_boxes), default=float("inf"))
    parsed_options = normalize_options(raw_options) if raw_options else {}

    # 2. Detect top statement boxes (above options, before vertical gap to figure).
    statement_boxes: list[dict[str, Any]] = []
    statement_indices: set[int] = set()
    above_opts = [(idx, box) for idx, box in enumerate(items) if idx not in option_indices and box["y"] < opt_min_y]
    if above_opts:
        first_idx, first_box = above_opts[0]
        statement_boxes.append(first_box)
        statement_indices.add(first_idx)
        min_stmt_x = first_box["x"]
        prev_y = first_box["y"]
        for idx, box in above_opts[1:]:
            same_line = abs(box["y"] - prev_y) <= 25.0
            next_line = (0.0 < box["y"] - prev_y <= 78.0) and (box["x"] <= min_stmt_x + 120.0)
            if same_line or next_line:
                statement_boxes.append(box)
                statement_indices.add(idx)
                prev_y = box["y"]
                min_stmt_x = min(min_stmt_x, box["x"])
            else:
                break

    # 3. Everything strictly between statement and A..E options is figure_tokens.
    figure_tokens: list[dict[str, Any]] = [
        box
        for idx, box in enumerate(items)
        if idx not in option_indices and idx not in statement_indices and box["y"] < opt_min_y
    ]

    numbers: list[float] = []
    angles: list[float] = []
    for tok in figure_tokens:
        norm_text = tok["text"].translate(_UNICODE_MINUS_TRANS)
        for m_ang in _ANGLE_RE.finditer(norm_text):
            try:
                angles.append(float(m_ang.group(1).replace(",", ".")))
            except ValueError:
                pass
        for m_num in _NUMBER_RE.finditer(norm_text):
            try:
                numbers.append(float(m_num.group(1).replace(",", ".")))
            except ValueError:
                pass

    statement_text = " ".join(b["text"] for b in statement_boxes).strip()
    return {
        "statement": statement_text,
        "statement_boxes": statement_boxes,
        "figure_tokens": figure_tokens,
        "numbers": numbers,
        "angles": angles,
        "option_boxes": option_boxes,
        "parsed_options": parsed_options,
    }


def _empty_result(ocr_available: bool, found: bool = False) -> dict[str, Any]:
    return {
        "found": found,
        "ocr_available": ocr_available,
        "statement": "",
        "statement_boxes": [],
        "figure_tokens": [],
        "numbers": [],
        "angles": [],
        "option_boxes": [],
        "parsed_options": {},
    }


def extract_figure_ocr(problem: dict[str, Any]) -> dict[str, Any]:
    """Extract 3-zone OCR data for a benchmark problem dict, with JSONL caching."""
    pid = str(problem.get("id") or problem.get("problem_id") or "").strip()
    lang = str(problem.get("language") or "eng").strip() or "eng"
    cache_key = f"{pid}|{lang}" if pid else ""

    _ensure_cache_loaded()
    if cache_key and cache_key in _MEMORY_CACHE:
        cached = dict(_MEMORY_CACHE[cache_key])
        cached.setdefault("found", True)
        cached["cached"] = True
        return cached

    image_path = _locate_image(problem)
    if image_path is not None:
        _ensure_cache_loaded(image_path)
        if cache_key and cache_key in _MEMORY_CACHE:
            cached = dict(_MEMORY_CACHE[cache_key])
            cached.setdefault("found", True)
            cached["cached"] = True
            return cached

    if image_path is None:
        return _empty_result(ocr_available=RapidOCR is not None, found=False)

    ocr = _get_ocr()
    if ocr is None:
        return _empty_result(ocr_available=False, found=True)

    raw_boxes, _ = ocr(str(image_path))
    zones = _split_zones(raw_boxes or [])
    result: dict[str, Any] = {
        "key": cache_key or str(image_path),
        "found": True,
        "ocr_available": True,
        "cached": False,
        "image_path": str(image_path),
        **zones,
    }

    cache_target = _candidate_cache_files(image_path)[0]
    _append_cache(cache_target, result)
    return result


def ocr_problem_from_image(image_bytes_or_path: Any) -> dict[str, Any]:
    """Run offline RapidOCR on raw image bytes or a file path.

    Returns ``{"statement": str, "figure_tokens": list, "options": dict[str, str]}``.
    """
    ocr = _get_ocr()
    if ocr is None:
        return {
            "statement": "",
            "figure_tokens": [],
            "options": {},
            "numbers": [],
            "angles": [],
            "ocr_available": False,
        }

    if isinstance(image_bytes_or_path, Path):
        inp: Any = str(image_bytes_or_path)
    elif isinstance(image_bytes_or_path, (bytearray, memoryview)):
        inp = bytes(image_bytes_or_path)
    else:
        inp = image_bytes_or_path

    raw_boxes, _ = ocr(inp)
    zones = _split_zones(raw_boxes or [])
    return {
        "statement": zones["statement"],
        "figure_tokens": zones["figure_tokens"],
        "options": zones["parsed_options"],
        "numbers": zones["numbers"],
        "angles": zones["angles"],
        "ocr_available": True,
    }
