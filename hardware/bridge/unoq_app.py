"""Arduino App Lab entry point for the UNO Q's Linux (QRB2210) side.

Runs on Debian Linux on the QRB2210.
Translates MCU events from the STM32 via Arduino RouterBridge,
invokes the Socratic engine (offline Qwen / Cangur bank),
drives the 8x13 LED matrix and RGB states,
and spools telemetry locally in SQLite when offline before flushing to RawTree.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

try:
    from arduino.app_utils import App, Bridge
except ImportError:
    # Graceful fallback for local development or non-AppLab execution
    App = None  # type: ignore
    Bridge = None  # type: ignore

try:
    from arduino.app_utils import Frame
except ImportError:
    Frame = None  # type: ignore

from hardware.bridge.serial_daemon import (
    IngestClient,
    SocraticDaemon,
    TelemetrySpool,
    live_engine,
    mock_engine,
)

MATRIX_ROWS = 8
MATRIX_COLS = 13
PIXEL_ON = 7
PROGRESS_ROW = 7
HINT_RUNG_COL = 12
HINT_RUNG_ROWS = (6, 4, 2, 0)  # Bottom-to-top dots for hint rungs 1..4
VALID_OPTIONS = ("A", "B", "C", "D", "E")

# 8x13 Matrix Bitmaps (values 0..7 grayscale)
BITMAPS = {
    "IDLE": [  # Heart
        "..##...##....",
        ".####.####...",
        ".#########...",
        ".#########...",
        "..#######....",
        "...#####.....",
        "....###......",
        ".....#.......",
    ],
    "THINKING": [  # Wave dots
        ".............",
        ".............",
        "..##...##....",
        ".#..#.#..#...",
        ".............",
        "..##...##....",
        ".............",
        ".............",
    ],
    "SOCRATIC_NUDGE": [  # Question mark
        "....###......",
        "...#...#.....",
        ".......#.....",
        "......#......",
        ".....#.......",
        ".............",
        ".....#.......",
        ".............",
    ],
    "SOLVED": [  # Checkmark
        "..........#..",
        ".........#...",
        "........#....",
        ".......#.....",
        ".#....#......",
        "..#..#.......",
        "...##........",
        ".............",
    ],
    "BOTTLENECK_ALERT": [  # Exclamation mark
        "....###......",
        "....###......",
        "....###......",
        "....###......",
        ".....#.......",
        ".............",
        ".....#.......",
        ".....#.......",
    ],
}

# 5x7 Matrix Glyphs (7 rows x 5 columns, leaves row 7 for progress bars & col 12 for rung dots)
_CHECK_5X7 = [
    "....#",
    "...#.",
    "..#..",
    "..#..",
    "#.#..",
    ".#...",
    ".....",
]

_CROSS_5X7 = [
    ".....",
    "#...#",
    ".#.#.",
    "..#..",
    ".#.#.",
    "#...#",
    ".....",
]

GLYPHS_5X7: dict[str, list[str]] = {
    "A": [
        ".###.",
        "#...#",
        "#...#",
        "#####",
        "#...#",
        "#...#",
        "#...#",
    ],
    "B": [
        "####.",
        "#...#",
        "#...#",
        "####.",
        "#...#",
        "#...#",
        "####.",
    ],
    "C": [
        ".###.",
        "#...#",
        "#....",
        "#....",
        "#....",
        "#...#",
        ".###.",
    ],
    "D": [
        "####.",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "#...#",
        "####.",
    ],
    "E": [
        "#####",
        "#....",
        "#....",
        "####.",
        "#....",
        "#....",
        "#####",
    ],
    "CHECK": _CHECK_5X7,
    "✓": _CHECK_5X7,
    "CROSS": _CROSS_5X7,
    "✗": _CROSS_5X7,
    "?": [
        ".###.",
        "#...#",
        "....#",
        "..##.",
        "..#..",
        ".....",
        "..#..",
    ],
    "!": [
        ".###.",
        ".###.",
        ".###.",
        "..#..",
        "..#..",
        ".....",
        "..#..",
    ],
}

_OPTION_RE = re.compile(r"(?:[✓✗]|OPT(?:ION)?\s*:?|ANS(?:WER)?\s*:?|\[)\s*([A-E])\b")
_RUNG_RE = re.compile(r"\brung\s*[:=]?\s*([1-4])\b", re.IGNORECASE)
_LEVEL_RE = re.compile(r"\b(?:lvl|level|lv)\s*[:=]?\s*(\d+)(?:/(\d+))?\b", re.IGNORECASE)
_PCT_RE = re.compile(r"\b(\d{1,3})\s*%")
_FRAC_RE = re.compile(r"\bprefill\s*[:=]?\s*(\d+)\s*/\s*(\d+)\b", re.IGNORECASE)


def _draw_bitmap(grid: Any, pattern: list[str], max_rows: int = MATRIX_ROWS) -> None:
    for r, line in enumerate(pattern[:max_rows]):
        for c, ch in enumerate(line[:MATRIX_COLS]):
            if ch == "#":
                grid[r, c] = PIXEL_ON


def _blit_glyph(
    grid: Any,
    glyph_key: str,
    col_offset: int,
    row_offset: int = 0,
    brightness: int = PIXEL_ON,
) -> None:
    glyph = GLYPHS_5X7.get(glyph_key)
    if glyph is None:
        return
    for r, line in enumerate(glyph):
        rr = row_offset + r
        if not (0 <= rr < MATRIX_ROWS):
            continue
        for c, ch in enumerate(line):
            cc = col_offset + c
            if 0 <= cc < MATRIX_COLS and ch == "#":
                grid[rr, cc] = brightness


def _draw_progress_bar(
    grid: Any,
    filled_cols: int,
    row: int = PROGRESS_ROW,
    brightness: int = PIXEL_ON,
) -> None:
    if not (0 <= row < MATRIX_ROWS):
        return
    grid[row, :] = 0
    clamped = max(0, min(MATRIX_COLS, int(filled_cols)))
    if clamped > 0:
        grid[row, :clamped] = brightness


def _draw_hint_rungs(grid: Any, hint_rung: int, brightness: int = PIXEL_ON) -> None:
    clamped = max(0, min(4, int(hint_rung)))
    for idx in range(clamped):
        grid[HINT_RUNG_ROWS[idx], HINT_RUNG_COL] = brightness


def level_progress_columns(level_progress: int | float | None, max_level: int = MATRIX_COLS) -> int:
    """Map level or solved count to 0..13 columns on the bottom row."""
    if level_progress is None:
        return 0
    if isinstance(level_progress, float) and 0.0 <= level_progress <= 1.0:
        if level_progress <= 0.0:
            return 0
        return max(1, min(MATRIX_COLS, round(level_progress * MATRIX_COLS)))
    denom = max(1, int(max_level))
    value = float(level_progress)
    if value <= 0:
        return 0
    return max(1, min(MATRIX_COLS, round(MATRIX_COLS * value / denom)))


def prefill_progress_columns(prefill_progress: int | float | None) -> int:
    """Map prefill ratio (0.0..1.0) or percentage (0..100) to 0..13 columns."""
    if prefill_progress is None:
        return 0
    value = float(prefill_progress)
    if value <= 0.0:
        return 0
    ratio = value if value <= 1.0 else min(1.0, value / 100.0)
    return max(1, min(MATRIX_COLS, round(ratio * MATRIX_COLS)))


def _parse_feedback_hints(frame: dict[str, Any]) -> dict[str, Any]:
    lcd_text = f"{frame.get('lcd_line1', '')} {frame.get('lcd_line2', '')}".strip()
    hints: dict[str, Any] = {}
    if lcd_text in VALID_OPTIONS:
        hints["selected_option"] = lcd_text
    else:
        opt_match = _OPTION_RE.search(lcd_text)
        if opt_match:
            hints["selected_option"] = opt_match.group(1)

    rung_match = _RUNG_RE.search(lcd_text)
    if rung_match:
        hints["hint_rung"] = int(rung_match.group(1))

    lvl_match = _LEVEL_RE.search(lcd_text)
    if lvl_match:
        hints["level_progress"] = int(lvl_match.group(1))
        if lvl_match.group(2):
            hints["max_level"] = max(1, int(lvl_match.group(2)))

    if frame.get("led_state") == "THINKING":
        frac_match = _FRAC_RE.search(lcd_text)
        if frac_match:
            num, den = int(frac_match.group(1)), max(1, int(frac_match.group(2)))
            hints["prefill_progress"] = min(1.0, max(0.0, num / den))
        else:
            pct_match = _PCT_RE.search(lcd_text)
            if pct_match:
                hints["prefill_progress"] = min(1.0, max(0.0, int(pct_match.group(1)) / 100.0))

    return hints


def state_to_matrix_grid(
    state: str,
    *,
    selected_option: str | None = None,
    is_correct: bool | None = None,
    level_progress: int | float = 0,
    max_level: int = MATRIX_COLS,
    hint_rung: int = 0,
    prefill_progress: int | float | None = None,
) -> Any:
    if np is None:
        return None
    grid = np.zeros((MATRIX_ROWS, MATRIX_COLS), dtype=np.uint8)
    option = selected_option.strip().upper() if selected_option else None
    if option not in VALID_OPTIONS:
        option = None
    rung = max(0, min(4, int(hint_rung)))
    lvl_cols = level_progress_columns(level_progress, max_level=max_level)

    if state == "IDLE":
        if option is not None:
            _blit_glyph(grid, option, col_offset=4)
        else:
            _draw_bitmap(grid, BITMAPS["IDLE"])
        if lvl_cols > 0:
            _draw_progress_bar(grid, lvl_cols)
        if rung > 0:
            _draw_hint_rungs(grid, rung)
    elif state == "THINKING":
        _draw_bitmap(grid, BITMAPS["THINKING"], max_rows=6)
        p_cols = prefill_progress_columns(0.25 if prefill_progress is None else prefill_progress)
        _draw_progress_bar(grid, p_cols)
        if rung > 0:
            _draw_hint_rungs(grid, rung)
    elif state == "SOCRATIC_NUDGE":
        if option is not None:
            status_glyph = "✓" if is_correct is True else "✗"
            _blit_glyph(grid, status_glyph, col_offset=0)
            _blit_glyph(grid, option, col_offset=6)
        else:
            _blit_glyph(grid, "?", col_offset=4)
        _draw_hint_rungs(grid, rung)
        if lvl_cols > 0:
            _draw_progress_bar(grid, lvl_cols)
    elif state == "SOLVED":
        if option is not None:
            _blit_glyph(grid, "✓", col_offset=0)
            _blit_glyph(grid, option, col_offset=6)
        else:
            _blit_glyph(grid, "✓", col_offset=4)
        if lvl_cols > 0:
            _draw_progress_bar(grid, lvl_cols)
    elif state == "BOTTLENECK_ALERT":
        if option is not None:
            _blit_glyph(grid, "!", col_offset=0)
            _blit_glyph(grid, option, col_offset=6)
        else:
            _blit_glyph(grid, "!", col_offset=4)
        _draw_hint_rungs(grid, rung)
        if lvl_cols > 0:
            _draw_progress_bar(grid, lvl_cols)
    else:
        _draw_bitmap(grid, BITMAPS.get(state, BITMAPS["IDLE"]))
    return grid


def feedback_to_matrix_grid(
    frame: dict[str, Any],
    *,
    event: dict[str, Any] | None = None,
    telemetry: dict[str, Any] | None = None,
    selected_option: str | None = None,
    is_correct: bool | None = None,
    level_progress: int | float | None = None,
    max_level: int | None = None,
    hint_rung: int | None = None,
    prefill_progress: int | float | None = None,
) -> Any:
    """Render an 8x13 matrix frame from a frozen contracts UartFeedbackFrame."""
    hints = _parse_feedback_hints(frame)
    resolved_option = (
        selected_option
        if selected_option is not None
        else (event.get("selected_option") if event else None) or hints.get("selected_option")
    )
    resolved_correct = (
        is_correct
        if is_correct is not None
        else (telemetry.get("is_correct") if telemetry else None)
    )
    resolved_rung = (
        hint_rung
        if hint_rung is not None
        else hints.get("hint_rung", 0)
    )
    resolved_level = (
        level_progress
        if level_progress is not None
        else hints.get("level_progress", 0)
    )
    resolved_max_level = (
        max_level
        if max_level is not None
        else hints.get("max_level", MATRIX_COLS)
    )
    resolved_prefill = (
        prefill_progress
        if prefill_progress is not None
        else hints.get("prefill_progress")
    )
    return state_to_matrix_grid(
        frame.get("led_state", "IDLE"),
        selected_option=resolved_option,
        is_correct=resolved_correct,
        level_progress=resolved_level,
        max_level=resolved_max_level,
        hint_rung=resolved_rung,
        prefill_progress=resolved_prefill,
    )


@dataclass
class MatrixSessionState:
    """Tracks session-level matrix overlays (level bar, hint rung, option) across events."""

    solved_count: int = 0
    current_problem_id: str | None = None
    hint_rung: int = 0
    last_option: str | None = None
    last_is_correct: bool | None = None

    def on_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        problem_id = event.get("problem_id")
        if event_type == "SESSION_RESET":
            self.solved_count = 0
            self.current_problem_id = problem_id
            self.hint_rung = 0
            self.last_option = None
            self.last_is_correct = None
            return
        if event_type == "PROBLEM_SELECT" or (problem_id and problem_id != self.current_problem_id):
            self.current_problem_id = problem_id
            self.hint_rung = 0
            self.last_option = None
            self.last_is_correct = None

        self.last_option = event.get("selected_option")
        if event_type == "HINT_REQUEST":
            self.hint_rung = min(4, max(1, self.hint_rung + 1))
        elif event_type == "ANSWER_SUBMIT":
            attempt = int(event.get("attempt_num") or 1)
            self.hint_rung = min(4, max(self.hint_rung, attempt))

    def on_telemetry(self, telemetry: dict[str, Any] | None) -> None:
        if not telemetry:
            return
        if telemetry.get("is_correct") is True:
            self.last_is_correct = True
            self.solved_count = min(MATRIX_COLS, self.solved_count + 1)
            self.hint_rung = 0
        elif telemetry.get("is_correct") is False:
            self.last_is_correct = False
            if self.hint_rung == 0:
                self.hint_rung = 1
        else:
            self.last_is_correct = None

    def render_frame(
        self,
        frame: dict[str, Any],
        event: dict[str, Any] | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> Any:
        return feedback_to_matrix_grid(
            frame,
            event=event,
            telemetry=telemetry,
            selected_option=self.last_option,
            is_correct=self.last_is_correct,
            level_progress=self.solved_count,
            max_level=MATRIX_COLS,
            hint_rung=self.hint_rung,
        )


def get_selected_engine() -> Any:
    choice = os.environ.get("SOCRATIC_ENGINE", "").lower()
    if choice == "mock":
        return mock_engine
    if choice == "live":
        return live_engine
    try:
        import engine  # noqa: F401
        return live_engine
    except ImportError:
        return mock_engine


spool_path = os.environ.get("OFFLINE_SPOOL_DB", "hardware/spool.db")
ingest_url = os.environ.get("INGEST_URL", "http://127.0.0.1:8000/ingest")
spool = TelemetrySpool(Path(spool_path).resolve())
ingester = IngestClient(ingest_url)
engine_fn = get_selected_engine()
daemon = SocraticDaemon.create(engine_fn, ingester, spool)
matrix_state = MatrixSessionState()


def send_feedback(frame: dict[str, Any], grid: Any = None) -> None:
    if Bridge:
        Bridge.notify("socraticq_feedback", json.dumps(frame, separators=(",", ":")))
        resolved_grid = grid if grid is not None else feedback_to_matrix_grid(frame)
        if resolved_grid is not None:
            try:
                Bridge.call("draw", [int(cell) for row in resolved_grid for cell in row])
            except Exception:
                pass


def send_prefill_progress(progress: int | float) -> Any:
    """Render and push a live prefill progress bar grid to the 8x13 matrix."""
    grid = state_to_matrix_grid("THINKING", prefill_progress=progress)
    if Bridge and grid is not None:
        try:
            Bridge.call("draw", [int(cell) for row in grid for cell in row])
        except Exception:
            pass
    return grid


def on_mcu_event(raw_event: str) -> None:
    try:
        event = json.loads(raw_event)
        matrix_state.on_event(event)

        def _on_frame(frame: dict[str, Any]) -> None:
            grid = matrix_state.render_frame(frame, event=event)
            send_feedback(frame, grid=grid)

        frames, telemetry = daemon.process_event(event, on_frame=_on_frame)
        for idx, frame in enumerate(frames):
            if idx == 0 and len(frames) > 1:
                continue
            is_final = idx == len(frames) - 1
            if is_final and telemetry:
                matrix_state.on_telemetry(telemetry)
            grid = matrix_state.render_frame(
                frame,
                event=event,
                telemetry=telemetry if is_final else None,
            )
            send_feedback(frame, grid=grid)
        if telemetry:
            daemon.deliver_telemetry(telemetry)
            daemon.flush_spool()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Ignored malformed MCU Bridge event: {exc}", file=sys.stderr)


def loop() -> None:
    daemon.flush_spool()
    time.sleep(1)


if Bridge and App:
    Bridge.provide("socraticq_event", on_mcu_event)


def run_app() -> None:
    if App:
        App.run(user_loop=loop)
    else:
        print("Running in headless / test loop mode (no Arduino App environment).")
        while True:
            daemon.flush_spool()
            time.sleep(1)


if __name__ == "__main__":
    run_app()
