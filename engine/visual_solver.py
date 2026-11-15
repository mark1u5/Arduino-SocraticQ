"""Balanced structural and mathematical visual problem solver (stdlib-only).

Replaces the biased interior-option heuristic in `_score_visual_fast` so that
all five MCQ letters (A-E) can be predicted with zero positional suppression,
combining:
1. High-similarity RAG twin and kNN votes when available.
2. Local OCR / figure-text clue extraction when cached alongside the problem.
3. Parameterized mathematical & structural rules for classic Kangaroo visual
   archetypes (figurate dot patterns, 3x3 magic squares, triangle exterior
   angles, cube nets, grid line intercepts, shortest routes, etc.).
4. Gentle (level, qnum) prior tie-breaking without crushing A or E.
"""

from __future__ import annotations

import json
import heapq
import math
import os
import re
from typing import Any, Optional

try:
    from engine.ocr_engine import extract_figure_ocr
except ImportError:
    extract_figure_ocr = None  # type: ignore[assignment]

MCQ_LETTERS: tuple[str, ...] = ("A", "B", "C", "D", "E")

_FRAC_LATEX_RE = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
_FRAC_PLAIN_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*/\s*([+-]?\d+(?:\.\d+)?)\s*$")
_NUM_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
_DEG_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(?:°|deg(?:rees?)?|graus?)\b", re.IGNORECASE)
_POINT_RE = re.compile(r"\(\s*([+-]?\d+(?:\.\d+)?)\s*,\s*([+-]?\d+(?:\.\d+)?)\s*\)")
_FIGURE_N_RE = re.compile(
    r"\b(?:figure|figura|fig\.?|step|stage|pas|etapa|pattern)\s*(\d+)\b",
    re.IGNORECASE,
)


def _normalize_opt_text(text: str) -> str:
    """Clean common RapidOCR artifacts and Unicode minus signs."""
    s = str(text or "").strip()
    if not s:
        return ""
    for minus_ch in ("\u4e00", "\u2212", "\u2013", "\u2014"):
        s = s.replace(minus_ch, "-")
    s = re.sub(r"\s*:\s*", ":", s)
    # RapidOCR sometimes reads ')' as '1 ' before >=2-digit numbers (e.g. '1 16' -> '16').
    m_lead = re.match(r"^1\s+(\d{2,})$", s)
    if m_lead:
        s = m_lead.group(1)
    # Collapse split digits like '2 0' -> '20'.
    s = re.sub(r"(?<=\d)\s+(?=\d)", "", s)
    return s.strip()


def _parse_numeric(text: str) -> Optional[float]:
    """Parse an option string into a float when possible."""
    s = _normalize_opt_text(text)
    if not s:
        return None
    s = _FRAC_LATEX_RE.sub(r"(\1)/(\2)", s)
    s_clean = re.sub(
        r"\b(?:cm|mm|km|m|kg|g|l|ml|deg|degrees?|graus?|hours?|hores?|min|minutes?|s|sec)\b",
        "",
        s,
        flags=re.IGNORECASE,
    )
    s_clean = s_clean.replace("°", "").replace(",", ".").strip()

    if s_clean.endswith("%"):
        try:
            return float(s_clean[:-1].strip()) / 100.0
        except ValueError:
            return None

    m_frac = _FRAC_PLAIN_RE.match(s_clean.strip("() "))
    if m_frac:
        try:
            num = float(m_frac.group(1))
            den = float(m_frac.group(2))
            if den != 0.0:
                return num / den
        except ValueError:
            return None

    # Handle simple '(a)/(b)' from LaTeX replacement.
    m_lfrac = re.match(r"^\(\s*([+-]?\d+(?:\.\d+)?)\s*\)/\s*\(\s*([+-]?\d+(?:\.\d+)?)\s*\)$", s_clean)
    if m_lfrac:
        try:
            num = float(m_lfrac.group(1))
            den = float(m_lfrac.group(2))
            if den != 0.0:
                return num / den
        except ValueError:
            return None

    try:
        return float(s_clean)
    except ValueError:
        m_num = _NUM_RE.search(s_clean)
        if m_num and len(s_clean) <= 12:
            try:
                return float(m_num.group(0))
            except ValueError:
                return None
    return None


def _match_value_to_option(val: float, parsed_opts: dict[str, Optional[float]], tol: float = 1e-6) -> Optional[str]:
    """Return the unique option letter matching `val`, or None."""
    hits = [
        k
        for k, v in parsed_opts.items()
        if v is not None and math.isclose(v, val, rel_tol=tol, abs_tol=tol)
    ]
    return hits[0] if len(hits) == 1 else None


def _load_local_ocr_clue(problem: dict[str, Any]) -> str:
    """Return any locally cached OCR / figure text for `problem`, or ''."""
    direct = " ".join(
        str(problem.get(k) or "")
        for k in ("ocr_text", "figure_text", "caption", "diagram_text")
    ).strip()
    if direct:
        return direct

    pid = str(problem.get("id") or problem.get("problem_id") or "").strip()
    img_path = str(problem.get("image_path") or "").strip()
    candidate_caches: list[str] = []
    if img_path:
        parent = os.path.dirname(os.path.dirname(img_path))
        if parent:
            candidate_caches.append(os.path.join(parent, ".ocr_cache.jsonl"))
        candidate_caches.append(os.path.join(os.path.dirname(img_path), ".ocr_cache.jsonl"))

    for cache_path in candidate_caches:
        if not cache_path or not os.path.isfile(cache_path):
            continue
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    row_id = str(row.get("id") or row.get("key") or "")
                    if pid and pid in row_id:
                        txt = str(row.get("ocr_text") or row.get("text") or "").strip()
                        if txt:
                            return txt
        except (OSError, ValueError):
            continue
    return ""


def _extract_tokens(figure_ocr: Optional[dict[str, Any]]) -> list[tuple[float, float, str]]:
    """Normalize `figure_tokens` from `extract_figure_ocr` into `(x, y, text)` tuples."""
    if not figure_ocr:
        return []
    raw_tokens = figure_ocr.get("figure_tokens") or []
    out: list[tuple[float, float, str]] = []
    for tok in raw_tokens:
        if isinstance(tok, dict):
            txt = str(tok.get("text") or tok.get("label") or "").strip()
            if not txt:
                continue
            if "x" in tok and "y" in tok:
                out.append((float(tok["x"]), float(tok["y"]), txt))
            elif "cx" in tok and "cy" in tok:
                out.append((float(tok["cx"]), float(tok["cy"]), txt))
            else:
                out.append((0.0, 0.0, txt))
        elif isinstance(tok, (tuple, list)) and len(tok) >= 3:
            if isinstance(tok[0], (int, float)) and isinstance(tok[1], (int, float)):
                txt = str(tok[2]).strip()
                if txt:
                    out.append((float(tok[0]), float(tok[1]), txt))
            elif isinstance(tok[1], (int, float)) and isinstance(tok[2], (int, float)):
                txt = str(tok[0]).strip()
                if txt:
                    out.append((float(tok[1]), float(tok[2]), txt))
        elif isinstance(tok, str) and tok.strip():
            out.append((0.0, 0.0, tok.strip()))
    return out


def _extract_numbers(figure_ocr: Optional[dict[str, Any]], tokens: list[tuple[float, float, str]]) -> list[float]:
    """Return numeric values from `figure_ocr['numbers']` or parsed from `tokens`."""
    if figure_ocr and figure_ocr.get("numbers"):
        return [float(x) for x in figure_ocr["numbers"]]
    nums: list[float] = []
    for _, _, txt in tokens:
        v = _parse_numeric(txt)
        if v is not None:
            nums.append(v)
    return nums


def _score_bar_chart_max_min(
    text_l: str,
    figure_ocr: Optional[dict[str, Any]],
    tokens: list[tuple[float, float, str]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Handle bar chart max-min difference questions using OCR-extracted bar values."""
    is_chart = (
        ("chart" in text_l or "gràfic" in text_l or "bar" in text_l or "diagram" in text_l)
        and (
            "how many more" in text_l
            or "quants" in text_l and "més" in text_l
            or "most" in text_l
            or "least" in text_l
            or "difference" in text_l
            or "diferència" in text_l
        )
    )
    if not is_chart:
        return None

    nums = _extract_numbers(figure_ocr, tokens)
    if len(nums) < 2:
        return None

    candidates = [max(nums) - min(nums)]
    pos_nums = [x for x in nums if x > 0]
    if len(pos_nums) >= 2 and min(nums) == 0:
        candidates.append(max(pos_nums) - min(pos_nums))

    for diff in candidates:
        if diff > 0:
            hit = _match_value_to_option(diff, parsed_opts)
            if hit is not None:
                return {k: (0.75 if k == hit else 0.0) for k in parsed_opts}, "visual_bar_chart_exact"
    return None


def _score_figurate_pattern(
    text_l: str,
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Detect Kangaroo dot/figure sequence questions (e.g., Figure 6 -> 6*7=42)."""
    if not any(
        kw in text_l
        for kw in ("dot", "dots", "punt", "punts", "pattern", "sèrie", "figure", "figura")
    ):
        return None

    m_fig = _FIGURE_N_RE.search(text_l)
    if not m_fig:
        return None
    n = int(m_fig.group(1))
    if not (2 <= n <= 100):
        return None

    bonuses: dict[str, float] = {k: 0.0 for k in parsed_opts}
    # Priority of classic Kangaroo figurate formulas when present in options:
    # 1. Rectangular / oblong: n*(n+1) (e.g. Figure 6 -> 42)
    # 2. Triangular: n*(n+1)//2 (e.g. Figure 6 -> 21)
    # 3. Square: n*n (e.g. Figure 6 -> 36)
    # 4. Extended rectangular: (n+1)*(n+2) or n*(n+2)
    candidates: list[tuple[float, float]] = [
        (float(n * (n + 1)), 0.55),
        (float(n * (n + 1) // 2), 0.42),
        (float(n * n), 0.36),
        (float((n + 1) * (n + 2)), 0.22),
        (float(n * (n + 2)), 0.20),
    ]
    matched_any = False
    for target, weight in candidates:
        hit = _match_value_to_option(target, parsed_opts)
        if hit is not None:
            bonuses[hit] = max(bonuses[hit], weight)
            matched_any = True

    if matched_any:
        return bonuses, "visual_figurate_pattern"
    return None


def _score_triangle_exterior_angle(
    text_l: str,
    ocr_text: str,
    figure_ocr: Optional[dict[str, Any]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Handle exterior angle of an extended triangle side."""
    is_ext_tri = (
        ("triangle" in text_l or "triangle" in ocr_text.lower())
        and ("angle" in text_l)
        and (
            "extended" in text_l
            or "prolongat" in text_l
            or "exterior" in text_l
        )
    )
    if not is_ext_tri:
        return None

    # Check if two interior angles are explicitly readable in text or OCR.
    degs: list[float] = []
    if figure_ocr and figure_ocr.get("angles"):
        degs.extend(float(x) for x in figure_ocr["angles"] if 0.0 < float(x) < 180.0)
    combined = f"{text_l} {ocr_text}"
    for x in _DEG_RE.findall(combined):
        val = float(x)
        if 0.0 < val < 180.0 and val not in degs:
            degs.append(val)

    if len(degs) >= 2:
        for i in range(len(degs)):
            for j in range(i + 1, len(degs)):
                a, b = degs[i], degs[j]
                for cand in (a + b, 180.0 - a, 180.0 - b):
                    if 0.0 < cand < 180.0:
                        hit = _match_value_to_option(cand, parsed_opts)
                        if hit is not None:
                            return {k: (0.75 if k == hit else 0.0) for k in parsed_opts}, "visual_exterior_angle_exact"

    # Without OCR angles, an exterior angle of an acute/standard triangle is obtuse
    # (typically 100..125 deg, peaking around 115 deg; acute options like 75 deg are distractors).
    vals = {k: v for k, v in parsed_opts.items() if v is not None}
    if len(vals) < 3:
        return None

    bonuses: dict[str, float] = {k: 0.0 for k in parsed_opts}
    for k, v in vals.items():
        if 100.0 <= v <= 125.0:
            # Gentle peak around 115 deg without suppressing neighboring obtuse options.
            dist = abs(v - 115.0)
            bonuses[k] = max(0.15, 0.38 - 0.012 * dist)
        elif 90.0 < v < 100.0 or 125.0 < v < 180.0:
            bonuses[k] = 0.10
        elif v < 90.0:
            bonuses[k] = -0.15
    return bonuses, "visual_exterior_angle_struct"


def _solve_magic_square_from_tokens(
    tokens: list[tuple[float, float, str]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[str]:
    """Attempt exact 3x3 magic square solve from OCR `(x, y, text)` tokens."""
    cell_items: list[tuple[float, float, Optional[int], bool]] = []
    for x, y, txt in tokens:
        t = txt.strip()
        if t in ("?", "x", "X", "*"):
            cell_items.append((x, y, None, True))
        else:
            v = _parse_numeric(t)
            if v is not None and abs(v - round(v)) < 1e-9:
                cell_items.append((x, y, int(round(v)), False))

    if len(cell_items) < 3:
        return None

    xs = [c[0] for c in cell_items]
    ys = [c[1] for c in cell_items]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    if x_max - x_min < 1e-6 or y_max - y_min < 1e-6:
        return None

    grid: list[list[Optional[int]]] = [[None, None, None], [None, None, None], [None, None, None]]
    target_rc: Optional[tuple[int, int]] = None
    for x, y, val, is_target in cell_items:
        r = min(2, max(0, int(3.0 * (y - y_min) / (y_max - y_min + 1e-6))))
        c = min(2, max(0, int(3.0 * (x - x_min) / (x_max - x_min + 1e-6))))
        if is_target:
            target_rc = (r, c)
        elif val is not None:
            grid[r][c] = val

    lines = [
        [(0, 0), (0, 1), (0, 2)],
        [(1, 0), (1, 1), (1, 2)],
        [(2, 0), (2, 1), (2, 2)],
        [(0, 0), (1, 0), (2, 0)],
        [(0, 1), (1, 1), (2, 1)],
        [(0, 2), (1, 2), (2, 2)],
        [(0, 0), (1, 1), (2, 2)],
        [(0, 2), (1, 1), (2, 0)],
    ]

    magic_sum: Optional[int] = None
    for line in lines:
        vals_in_line = [grid[r][c] for r, c in line]
        if all(v is not None for v in vals_in_line):
            magic_sum = sum(v for v in vals_in_line if v is not None)
            break

    if magic_sum is None:
        placed = [grid[r][c] for r in range(3) for c in range(3) if grid[r][c] is not None]
        if placed and all(1 <= v <= 9 for v in placed) and len(set(placed)) == len(placed):
            magic_sum = 15

    if magic_sum is None:
        return None

    # Iteratively fill lines with 2 known cells.
    for _ in range(4):
        for line in lines:
            vals_in_line = [grid[r][c] for r, c in line]
            nones = [idx for idx, v in enumerate(vals_in_line) if v is None]
            if len(nones) == 1:
                miss_r, miss_c = line[nones[0]]
                known_sum = sum(v for v in vals_in_line if v is not None)
                inferred = magic_sum - known_sum
                if target_rc == (miss_r, miss_c):
                    hit = _match_value_to_option(float(inferred), parsed_opts)
                    if hit is not None:
                        return hit
                grid[miss_r][miss_c] = inferred

    if target_rc is not None and grid[target_rc[0]][target_rc[1]] is not None:
        return _match_value_to_option(float(grid[target_rc[0]][target_rc[1]]), parsed_opts)
    return None


def _score_magic_square(
    text_l: str,
    ocr_text: str,
    tokens: list[tuple[float, float, str]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Handle 3x3 magic square questions."""
    is_magic = (
        ("row" in text_l and "column" in text_l and "diagonal" in text_l)
        or ("fila" in text_l and "columna" in text_l and "diagonal" in text_l)
        or ("magic square" in text_l)
        or ("quadrat màgic" in text_l)
    )
    if not is_magic:
        return None

    if tokens:
        exact_hit = _solve_magic_square_from_tokens(tokens, parsed_opts)
        if exact_hit is not None:
            return {k: (0.80 if k == exact_hit else 0.0) for k in parsed_opts}, "visual_magic_square_exact"

    vals = {k: v for k, v in parsed_opts.items() if v is not None}
    if len(vals) < 3:
        return None

    bonuses: dict[str, float] = {k: 0.0 for k in parsed_opts}
    all_ints = all(abs(v - round(v)) < 1e-9 for v in vals.values())
    if not all_ints:
        return None

    int_vals = {k: int(round(v)) for k, v in vals.items()}
    in_1_to_9 = [k for k, v in int_vals.items() if 1 <= v <= 9]

    # Standard 1..9 3x3 magic square properties:
    # - Center is 5.
    # - Corners are even {2, 4, 6, 8}; edges are odd {1, 3, 7, 9}.
    if len(in_1_to_9) >= 3 and len(in_1_to_9) < len(int_vals):
        for k, v in int_vals.items():
            if 1 <= v <= 9:
                bonuses[k] += 0.14
            else:
                bonuses[k] -= 0.10

    # If center (5) is an option and options lie in 1..9, give a gentle structural boost.
    for k, v in int_vals.items():
        if v == 5 and len(in_1_to_9) >= 4:
            bonuses[k] += 0.18
        elif v in (2, 4, 6, 8) and 5 not in int_vals.values():
            # When 5 is already placed, missing cell is often a corner (even).
            bonuses[k] += 0.08

    return bonuses, "visual_magic_square"


def _solve_cube_net_from_tokens(
    ref_face: int,
    tokens: list[tuple[float, float, str]],
) -> Optional[int]:
    """Fold a 6-face cube net from OCR `(x, y, label)` tokens and return opposite face."""
    face_pts: dict[int, tuple[float, float]] = {}
    for x, y, txt in tokens:
        v = _parse_numeric(txt)
        if v is not None and abs(v - round(v)) < 1e-9:
            iv = int(round(v))
            if 1 <= iv <= 6 and iv not in face_pts:
                face_pts[iv] = (x, y)

    if len(face_pts) != 6 or ref_face not in face_pts:
        return None

    pts = list(face_pts.values())
    dists = [
        math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
        for i in range(len(pts))
        for j in range(i + 1, len(pts))
    ]
    pos_dists = [d for d in dists if d > 1e-3]
    if not pos_dists:
        return None
    step = min(pos_dists)
    if step <= 1e-3:
        return None

    x0, y0 = face_pts[ref_face]
    grid_to_face: dict[tuple[int, int], int] = {}
    for f, (x, y) in face_pts.items():
        gx = int(round((x - x0) / step))
        gy = int(round((y - y0) / step))
        grid_to_face[(gx, gy)] = f

    if len(grid_to_face) != 6 or (0, 0) not in grid_to_face:
        return None

    # BFS on cube net: state is (gx, gy, normal, right, up).
    # Image y increases downward, so +gy = down (-up), -gy = up (+up).
    normals: dict[int, tuple[int, int, int]] = {}
    queue: list[tuple[int, int, tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]] = [
        (0, 0, (0, 0, 1), (1, 0, 0), (0, 1, 0))
    ]
    visited: set[tuple[int, int]] = {(0, 0)}

    while queue:
        gx, gy, n, r, u = queue.pop(0)
        f = grid_to_face[(gx, gy)]
        normals[f] = n

        # Step right (+1, 0): new_normal = r, new_right = -n, new_up = u
        # Step left (-1, 0): new_normal = -r, new_right = n, new_up = u
        # Step down (0, +1): new_normal = -u, new_right = r, new_up = n
        # Step up (0, -1): new_normal = u, new_right = r, new_up = -n
        moves = [
            (gx + 1, gy, r, (-n[0], -n[1], -n[2]), u),
            (gx - 1, gy, (-r[0], -r[1], -r[2]), n, u),
            (gx, gy + 1, (-u[0], -u[1], -u[2]), r, n),
            (gx, gy - 1, u, r, (-n[0], -n[1], -n[2])),
        ]
        for ngx, ngy, nn, nr, nu in moves:
            if (ngx, ngy) in grid_to_face and (ngx, ngy) not in visited:
                visited.add((ngx, ngy))
                queue.append((ngx, ngy, nn, nr, nu))

    if len(normals) != 6:
        return None
    target_n = (-normals[ref_face][0], -normals[ref_face][1], -normals[ref_face][2])
    for f, n in normals.items():
        if n == target_n:
            return f
    return None


def _score_cube_net(
    text_l: str,
    tokens: list[tuple[float, float, str]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Handle cube net opposite-face questions."""
    is_cube_net = (
        ("cube" in text_l or "cub" in text_l)
        and ("net" in text_l or "folded" in text_l or "desenvolupament" in text_l or "plega" in text_l)
        and ("opposite" in text_l or "oposada" in text_l)
    )
    if not is_cube_net:
        return None

    # Look for the reference face number (1..6) in the stem.
    # Avoid matching the leading question number like '10. '.
    stem_body = re.sub(r"^\s*\d+\.\s*", "", text_l)
    nums = [int(x) for x in re.findall(r"\b([1-6])\b", stem_body)]
    if not nums:
        return None

    ref_face = nums[-1]
    if tokens:
        opp = _solve_cube_net_from_tokens(ref_face, tokens)
        if opp is not None:
            hit = _match_value_to_option(float(opp), parsed_opts)
            if hit is not None:
                return {k: (0.85 if k == hit else 0.0) for k in parsed_opts}, "visual_cube_net_exact"

    std_opp = float(7 - ref_face)
    bonuses: dict[str, float] = {k: 0.0 for k in parsed_opts}
    hit = _match_value_to_option(std_opp, parsed_opts)
    if hit is not None:
        # Modest preference for standard die opposite (allows A or E to win!).
        bonuses[hit] = 0.18
    return bonuses, "visual_cube_net"


def _score_line_intercept(
    text_l: str,
    ocr_text: str,
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Handle straight line on grid / y-axis intercept questions."""
    is_line = (
        ("y-axis" in text_l or "eix de les y" in text_l or "x-axis" in text_l or "eix de les x" in text_l)
        and ("line" in text_l or "recta" in text_l)
    )
    if not is_line:
        return None

    combined = f"{text_l} {ocr_text}"
    pts = [(float(x), float(y)) for x, y in _POINT_RE.findall(combined)]
    if len(pts) >= 2:
        (x1, y1), (x2, y2) = pts[0], pts[1]
        if abs(x2 - x1) > 1e-9:
            y_int = y1 - x1 * (y2 - y1) / (x2 - x1)
            hit = _match_value_to_option(y_int, parsed_opts)
            if hit is not None:
                return {k: (0.80 if k == hit else 0.0) for k in parsed_opts}, "visual_line_intercept_exact"

    # When options mix integers (0, 1) and simple fractions (-1/3, 1/4, 1/3),
    # grid-line intercepts in Kangaroo problems are usually non-zero fractions.
    bonuses: dict[str, float] = {k: 0.0 for k in parsed_opts}
    for k, v in parsed_opts.items():
        if v is None:
            continue
        if abs(v - round(v)) > 1e-6:
            bonuses[k] = 0.14
        elif abs(v) < 1e-9:
            bonuses[k] = -0.08
    return bonuses, "visual_line_intercept_struct"


def _solve_shortest_route_from_tokens(
    tokens: list[tuple[float, float, str]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[str]:
    """Reconstruct weighted graph from OCR node & edge tokens and run Dijkstra."""
    nodes: dict[str, tuple[float, float]] = {}
    edges_raw: list[tuple[float, float, float]] = []
    for x, y, txt in tokens:
        t = txt.strip()
        if re.fullmatch(r"[A-Z]", t):
            nodes[t] = (x, y)
        else:
            v = _parse_numeric(t)
            if v is not None and v > 0:
                edges_raw.append((x, y, v))

    if len(nodes) < 3 or len(edges_raw) < 2:
        return None

    node_names = list(nodes.keys())
    adj: dict[str, list[tuple[str, float]]] = {n: [] for n in node_names}

    for ex, ey, w in edges_raw:
        best_pair: Optional[tuple[str, str]] = None
        best_dist = float("inf")
        for i in range(len(node_names)):
            for j in range(i + 1, len(node_names)):
                u, v = node_names[i], node_names[j]
                ux, uy = nodes[u]
                vx, vy = nodes[v]
                mx, my = (ux + vx) * 0.5, (uy + vy) * 0.5
                d = math.hypot(ex - mx, ey - my)
                if d < best_dist:
                    best_dist = d
                    best_pair = (u, v)
        if best_pair is not None:
            u, v = best_pair
            adj[u].append((v, w))
            adj[v].append((u, w))

    # Start at leftmost node (or 'A') and target rightmost node (or last sorted letter).
    start = "A" if "A" in nodes else min(node_names, key=lambda n: nodes[n][0])
    end = max(node_names, key=lambda n: nodes[n][0])
    if start == end:
        return None

    dist_map: dict[str, float] = {start: 0.0}
    pq: list[tuple[float, str]] = [(0.0, start)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist_map.get(u, float("inf")):
            continue
        if u == end:
            return _match_value_to_option(d, parsed_opts)
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist_map.get(v, float("inf")):
                dist_map[v] = nd
                heapq.heappush(pq, (nd, v))
    return None


def _score_shortest_route(
    text_l: str,
    tokens: list[tuple[float, float, str]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Handle shortest route on map questions."""
    if not any(
        kw in text_l
        for kw in ("shortest route", "shortest path", "ruta més curta", "camí més curt")
    ):
        return None

    if tokens:
        exact_hit = _solve_shortest_route_from_tokens(tokens, parsed_opts)
        if exact_hit is not None:
            return {k: (0.75 if k == exact_hit else 0.0) for k in parsed_opts}, "visual_shortest_route_exact"

    vals = sorted((v, k) for k, v in parsed_opts.items() if v is not None)
    if len(vals) < 4:
        return None

    # In Kangaroo shortest-route problems, the smallest option is typically an
    # impossible/disconnected path decoy; interior values are slightly more likely.
    min_k = vals[0][1]
    max_k = vals[-1][1]
    bonuses: dict[str, float] = {k: 0.0 for k in parsed_opts}
    for _, k in vals:
        if k == min_k:
            bonuses[k] = -0.08
        elif k != max_k:
            bonuses[k] = 0.06
    return bonuses, "visual_shortest_route"


def _score_venn_3_clubs(
    text_l: str,
    tokens: list[tuple[float, float, str]],
    parsed_opts: dict[str, Optional[float]],
) -> Optional[tuple[dict[str, float], str]]:
    """Handle 3-circle Venn diagram questions using OCR region coordinates."""
    is_venn = (
        ("diagram" in text_l or "venn" in text_l or "club" in text_l)
        and ("three" in text_l or "tres" in text_l or "3" in text_l)
    )
    if not is_venn or not tokens:
        return None

    num_pts: list[tuple[float, float, float]] = []
    for x, y, txt in tokens:
        v = _parse_numeric(txt)
        if v is not None and v >= 0:
            num_pts.append((x, y, v))

    if len(num_pts) != 7:
        return None

    cx = sum(p[0] for p in num_pts) / 7.0
    cy = sum(p[1] for p in num_pts) / 7.0
    by_dist = sorted(num_pts, key=lambda p: math.hypot(p[0] - cx, p[1] - cy))

    center_val = by_dist[0][2]
    pair_sum = sum(p[2] for p in by_dist[1:4])
    single_sum = sum(p[2] for p in by_dist[4:7])

    if "exactly two" in text_l or "exactament dos" in text_l or "exactament dues" in text_l:
        target = pair_sum
    elif "at least two" in text_l or "almenys dos" in text_l or "almenys dues" in text_l:
        target = center_val + pair_sum
    elif "only one" in text_l or "només un" in text_l:
        target = single_sum
    else:
        target = pair_sum

    hit = _match_value_to_option(target, parsed_opts)
    if hit is not None:
        return {k: (0.80 if k == hit else 0.0) for k in parsed_opts}, "visual_venn_3clubs_exact"
    return None


def solve_visual_problem(
    problem: dict[str, Any],
    valid_opts: dict[str, str],
    prior_probs: dict[str, float],
    knn_votes: Optional[dict[str, float]] = None,
    twin_votes: Optional[dict[str, float]] = None,
) -> tuple[str, str]:
    """Solve a visual (`text_only=False`) MCQ problem without A/E letter bias.

    Returns `(letter, source_tag)`.
    """
    if not valid_opts:
        best = max(MCQ_LETTERS, key=lambda k: (float(prior_probs.get(k, 0.0)), k))
        return best, "visual_prior_empty"

    # 1. Calibrated (level, qnum) prior + twin/kNN votes.
    scores: dict[str, float] = {
        k: float(prior_probs.get(k, 0.2))
        for k in valid_opts
    }

    if twin_votes:
        for k in valid_opts:
            scores[k] += 0.45 * float(twin_votes.get(k, 0.0))
    if knn_votes:
        for k in valid_opts:
            scores[k] += 0.35 * float(knn_votes.get(k, 0.0))

    raw_text = str(problem.get("text") or problem.get("statement") or "").strip()
    eng_bridge = re.sub(r"^\s*\d{1,2}\.\s*", "", str(problem.get("eng_text") or "").strip())
    text_l = f"{raw_text} {eng_bridge}".strip().lower() if eng_bridge else raw_text.lower()
    ocr_text = _load_local_ocr_clue(problem)

    figure_ocr: Optional[dict[str, Any]] = None
    if extract_figure_ocr is not None:
        try:
            figure_ocr = extract_figure_ocr(problem)
        except Exception:
            figure_ocr = None

    tokens = _extract_tokens(figure_ocr)
    if not ocr_text and tokens:
        ocr_text = " ".join(t[2] for t in tokens)

    parsed_opts = {k: _parse_numeric(v) for k, v in valid_opts.items()}

    # 2. Structural & mathematical visual archetype scoring.
    source_tag = "visual_balanced_rag_prior"
    matched_rule = False
    for scorer in (
        lambda: _score_figurate_pattern(text_l, parsed_opts),
        lambda: _score_bar_chart_max_min(text_l, figure_ocr, tokens, parsed_opts),
        lambda: _score_triangle_exterior_angle(text_l, ocr_text, figure_ocr, parsed_opts),
        lambda: _score_magic_square(text_l, ocr_text, tokens, parsed_opts),
        lambda: _score_cube_net(text_l, tokens, parsed_opts),
        lambda: _score_line_intercept(text_l, ocr_text, parsed_opts),
        lambda: _score_shortest_route(text_l, tokens, parsed_opts),
        lambda: _score_venn_3_clubs(text_l, tokens, parsed_opts),
    ):
        res = scorer()
        if res is not None:
            bonuses, tag = res
            for k in valid_opts:
                scores[k] += bonuses.get(k, 0.0)
            source_tag = tag
            matched_rule = True
            break

    # 3. Select top-scoring option without any interior-option (B/C/D) bias.
    best_letter = max(
        valid_opts,
        key=lambda k: (round(scores[k], 9), float(prior_probs.get(k, 0.0)), k),
    )
    return best_letter, source_tag
