"""Translate MCU events into Socratic engine calls and feedback frames.

Run with ``--stdin --engine mock`` during laptop development.  The physical
serial transport is optional and requires ``pyserial`` from hardware/requirements.txt.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from contracts.schemas import (
    validate_socratic_response,
    validate_telemetry_event,
    validate_uart_event,
    validate_uart_feedback,
)


Engine = Callable[..., dict[str, Any]]
CATEGORY_BY_DIAL = {
    0: "Logic",
    1: "Geometry",
    2: "Combinatorics",
    3: "NumberTheory",
    4: "Algebra",
}
ENGINE_EVENTS = {"PROBLEM_SELECT", "HINT_REQUEST", "ANSWER_SUBMIT"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def lcd_lines(summary: str) -> tuple[str, str]:
    compact = " ".join(summary.split())[:32]
    return compact[:16], compact[16:32]


def thinking_feedback(event: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_v": 1,
        "ack_seq": event["seq"],
        "led_state": "THINKING",
        "rgb": [0, 200, 255],
        "buzz_pattern": "NONE",
        "lcd_line1": "Thinking...",
        "lcd_line2": "",
    }
    validate_uart_feedback(payload)
    return payload


def idle_feedback(event: dict[str, Any], message: str) -> dict[str, Any]:
    line1, line2 = lcd_lines(message)
    payload = {
        "schema_v": 1,
        "ack_seq": event["seq"],
        "led_state": "IDLE",
        "rgb": [0, 80, 255],
        "buzz_pattern": "NONE",
        "lcd_line1": line1,
        "lcd_line2": line2,
    }
    validate_uart_feedback(payload)
    return payload


def error_feedback(event: dict[str, Any], message: str) -> dict[str, Any]:
    line1, line2 = lcd_lines(message)
    payload = {
        "schema_v": 1,
        "ack_seq": int(event.get("seq", 0)),
        "led_state": "SOCRATIC_NUDGE",
        "rgb": [255, 80, 0],
        "buzz_pattern": "SHORT_DOUBLE",
        "lcd_line1": line1,
        "lcd_line2": line2,
    }
    validate_uart_feedback(payload)
    return payload


def feedback_from_response(
    event: dict[str, Any], response: dict[str, Any], is_bottleneck: bool = False
) -> dict[str, Any]:
    is_correct = response["is_correct"] is True
    if is_bottleneck:
        state = "BOTTLENECK_ALERT"
        rgb = [180, 0, 255]
        buzz = "SHORT_DOUBLE"
    elif is_correct:
        state = "SOLVED"
        rgb = [0, 200, 40]
        buzz = "SUCCESS"
    else:
        state = "SOCRATIC_NUDGE"
        rgb = [255, 180, 0]
        buzz = "SHORT_DOUBLE"

    line1, line2 = lcd_lines(response["lcd_summary"])
    payload = {
        "schema_v": 1,
        "ack_seq": event["seq"],
        "led_state": state,
        "rgb": rgb,
        "buzz_pattern": buzz,
        "lcd_line1": line1,
        "lcd_line2": line2,
    }
    validate_uart_feedback(payload)
    return payload


def mock_engine(**kwargs: Any) -> dict[str, Any]:
    """Deterministic stand-in until Dev 2 publishes the live engine."""
    event_type = kwargs["event_type"]
    is_correct: bool | None = None
    if event_type == "ANSWER_SUBMIT":
        is_correct = kwargs.get("selected_option") == "D"
    category = CATEGORY_BY_DIAL.get(kwargs.get("dial_category_idx", 2), "Logic")

    agent_override_nudge = kwargs.get("agent_override_nudge")
    if agent_override_nudge:
        hint_text = agent_override_nudge[:240]
        lcd_summary = "Bottleneck nudge"
        move = "INVARIANT_NUDGE"
    else:
        hint_text = (
            "Before choosing an option, can you color adjacent squares black and white "
            "and check what changes after every move?"
        )
        lcd_summary = "Try chessboard parity"
        move = "PARITY_CHECK"

    response = {
        "problem_id": kwargs["problem_id"],
        "category": category,
        "difficulty_level": 2,
        "hint_text": hint_text,
        "lcd_summary": lcd_summary,
        "pedagogical_move": move,
        "is_correct": is_correct,
        "misconception_tag": "ignored_parity_invariant" if not is_correct else "none",
        "latency_ms": 0,
        "model_id": "hardware-mock",
        "offline_mode": True,
    }
    validate_socratic_response(response)
    return response


def live_engine(**kwargs: Any) -> dict[str, Any]:
    try:
        from engine import generate_socratic_hint
    except ImportError:
        from engine.socratic_engine import generate_socratic_hint

    return generate_socratic_hint(
        problem_id=kwargs["problem_id"],
        event_type=kwargs["event_type"],
        attempt_num=kwargs["attempt_num"],
        confidence_pct=kwargs.get("confidence_pct", kwargs.get("confidence_dial", 50)),
        selected_option=kwargs.get("selected_option"),
        student_note=kwargs.get("student_note"),
        agent_override_nudge=kwargs.get("agent_override_nudge"),
    )


def telemetry_from(
    event: dict[str, Any], response: dict[str, Any], session_id: str, elapsed_ms: int
) -> dict[str, Any]:
    match = re.search(r"CAM-(\d{4})-", event["problem_id"])
    cangur_year = int(match.group(1)) if match else datetime.now().year
    if event["event_type"] == "ANSWER_SUBMIT" and response["is_correct"] is True:
        telemetry_type = "SOLVED"
    elif event["event_type"] == "ANSWER_SUBMIT":
        telemetry_type = "ANSWER_SUBMIT"
    else:
        telemetry_type = "HINT_REQUEST"
    payload = {
        "event_id": f"evt-{uuid.uuid4().hex}",
        "session_id": session_id,
        "device_id": event["device_id"],
        "ts_utc": utc_now(),
        "problem_id": event["problem_id"],
        "cangur_year": cangur_year,
        "category": response["category"],
        "difficulty_level": response["difficulty_level"],
        "event_type": telemetry_type,
        "attempt_num": event["attempt_num"],
        "confidence_pct": event["confidence_dial"],
        "is_correct": response["is_correct"],
        "misconception_tag": response["misconception_tag"],
        "pedagogical_move": response["pedagogical_move"],
        "solve_elapsed_ms": max(0, elapsed_ms),
        "slm_latency_ms": response["latency_ms"],
        "offline_cached": False,
    }
    validate_telemetry_event(payload)
    return payload


class TelemetrySpool:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS telemetry_spool "
                "(event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def enqueue(self, payload: dict[str, Any]) -> None:
        cached = dict(payload)
        cached["offline_cached"] = True
        validate_telemetry_event(cached)
        connection = self._connect()
        try:
            connection.execute(
                "INSERT OR REPLACE INTO telemetry_spool (event_id, payload_json) VALUES (?, ?)",
                (cached["event_id"], json.dumps(cached, separators=(",", ":"))),
            )
            connection.commit()
        finally:
            connection.close()

    def queued(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT payload_json FROM telemetry_spool ORDER BY rowid"
            ).fetchall()
        finally:
            connection.close()
        return [json.loads(row[0]) for row in rows]

    def remove(self, event_id: str) -> None:
        connection = self._connect()
        try:
            connection.execute("DELETE FROM telemetry_spool WHERE event_id = ?", (event_id,))
            connection.commit()
        finally:
            connection.close()


class IngestClient:
    def __init__(self, url: str, timeout_s: float = 3.0) -> None:
        self.url = url
        self.base_url = url.rsplit("/ingest", 1)[0] if "/ingest" in url else url.rstrip("/")
        self.timeout_s = timeout_s

    def send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_s) as response:
            if not 200 <= response.status < 300:
                raise URLError(f"ingest returned HTTP {response.status}")

    def get_active_interventions(
        self, problem_id: str | None = None, category: str | None = None
    ) -> list[dict[str, Any]]:
        query_params: dict[str, str] = {}
        if problem_id:
            query_params["problem_id"] = problem_id
        if category:
            query_params["category"] = category
        query_str = f"?{urlencode(query_params)}" if query_params else ""
        url = f"{self.base_url}/interventions/active{query_str}"
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                if 200 <= response.status < 300:
                    data = json.loads(response.read().decode("utf-8"))
                    if isinstance(data, list):
                        return data
        except (OSError, URLError, TimeoutError):
            pass
        return []

    def mark_intervention_applied(self, insight_id: str) -> None:
        url = f"{self.base_url}/interventions/{insight_id}/apply"
        request = Request(url, data=b"", headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout_s) as response:
                pass
        except (OSError, URLError, TimeoutError):
            pass


@dataclass
class SocraticDaemon:
    engine: Engine
    ingester: IngestClient | None
    spool: TelemetrySpool | None
    session_id: str
    session_started: float
    auto_interventions: bool = True

    @classmethod
    def create(
        cls,
        engine: Engine,
        ingester: IngestClient | None,
        spool: TelemetrySpool | None,
        auto_interventions: bool = True,
    ) -> "SocraticDaemon":
        return cls(
            engine=engine,
            ingester=ingester,
            spool=spool,
            session_id=f"sess-{uuid.uuid4().hex[:12]}",
            session_started=time.monotonic(),
            auto_interventions=auto_interventions,
        )

    def process_event(
        self,
        event: dict[str, Any],
        on_frame: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        validate_uart_event(event)
        if event["event_type"] not in ENGINE_EVENTS:
            return [idle_feedback(event, "Control updated")], None

        thinking = thinking_feedback(event)
        if on_frame is not None:
            on_frame(thinking)
        frames = [thinking]

        # Check for active intervention from RawTree Agentic Cortex
        agent_override_nudge: str | None = None
        applied_insight_id: str | None = None
        if self.auto_interventions and self.ingester:
            dial_idx = event.get("dial_category_idx", 2)
            category = CATEGORY_BY_DIAL.get(dial_idx)
            active_list = self.ingester.get_active_interventions(
                problem_id=event.get("problem_id"),
                category=category,
            )
            if active_list:
                top_insight = active_list[0]
                agent_override_nudge = top_insight.get("recommended_nudge")
                applied_insight_id = top_insight.get("insight_id")

        response = self.engine(
            problem_id=event["problem_id"],
            event_type=event["event_type"],
            attempt_num=event["attempt_num"],
            confidence_pct=event["confidence_dial"],
            selected_option=event["selected_option"],
            dial_category_idx=event["dial_category_idx"],
            agent_override_nudge=agent_override_nudge,
        )
        validate_socratic_response(response)

        is_bottleneck = bool(agent_override_nudge) and response["is_correct"] is not True
        frames.append(feedback_from_response(event, response, is_bottleneck=is_bottleneck))

        if is_bottleneck and applied_insight_id and self.ingester:
            self.ingester.mark_intervention_applied(applied_insight_id)

        telemetry = telemetry_from(
            event,
            response,
            self.session_id,
            int((time.monotonic() - self.session_started) * 1000),
        )
        return frames, telemetry

    def deliver_telemetry(self, payload: dict[str, Any]) -> None:
        if not self.ingester:
            return
        try:
            self.ingester.send(payload)
        except (OSError, URLError, TimeoutError) as exc:
            print(f"Telemetry queued offline: {exc}", file=sys.stderr)
            if self.spool:
                self.spool.enqueue(payload)

    def flush_spool(self) -> int:
        if not self.ingester or not self.spool:
            return 0
        sent = 0
        for payload in self.spool.queued():
            try:
                self.ingester.send(payload)
            except (OSError, URLError, TimeoutError):
                break
            self.spool.remove(payload["event_id"])
            sent += 1
        return sent


def write_json_line(stream: Any, payload: dict[str, Any]) -> None:
    stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
    stream.flush()


def run_json_lines(
    daemon: SocraticDaemon, lines: Iterable[str], output: Any = sys.stdout
) -> None:
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            emitted_thinking = False

            def _on_frame(frame: dict[str, Any]) -> None:
                nonlocal emitted_thinking
                write_json_line(output, frame)
                emitted_thinking = True

            frames, telemetry = daemon.process_event(event, on_frame=_on_frame)
            for idx, frame in enumerate(frames):
                if idx == 0 and emitted_thinking and len(frames) > 1:
                    continue
                write_json_line(output, frame)
            if telemetry:
                daemon.deliver_telemetry(telemetry)
                daemon.flush_spool()
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"Ignoring malformed MCU event: {exc}", file=sys.stderr)
            fallback_event = {"seq": 0}
            write_json_line(output, error_feedback(fallback_event, "Invalid MCU event"))


def serial_lines(port: str, baud: int) -> tuple[Iterable[str], Any]:
    try:
        import serial
    except ImportError as exc:  # pragma: no cover - depends on local hardware setup
        raise SystemExit("Install hardware requirements first: python -m pip install -r hardware/requirements.txt") from exc
    connection = serial.Serial(port, baudrate=baud, timeout=1)

    class SerialOutput:
        def write(self, text: str) -> None:
            connection.write(text.encode("utf-8"))

        def flush(self) -> None:
            connection.flush()

    def read_lines() -> Iterable[str]:
        while True:
            raw = connection.readline()
            if raw:
                yield raw.decode("utf-8", errors="replace")

    return read_lines(), SerialOutput()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--stdin", action="store_true", help="Read JSON lines from stdin")
    transport.add_argument("--port", help="Physical serial port, for example COM5 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--engine", choices=("mock", "live"), default="mock")
    parser.add_argument("--ingest-url", default=os.getenv("INGEST_URL", "http://127.0.0.1:8000/ingest"))
    parser.add_argument("--spool", type=Path, default=Path("hardware/spool.db"))
    parser.add_argument("--no-ingest", action="store_true")
    parser.add_argument("--flush-only", action="store_true")
    args = parser.parse_args()

    engine = mock_engine if args.engine == "mock" else live_engine
    ingester = None if args.no_ingest else IngestClient(args.ingest_url)
    daemon = SocraticDaemon.create(engine, ingester, TelemetrySpool(args.spool))
    if args.flush_only:
        print(f"Flushed {daemon.flush_spool()} cached telemetry event(s).")
        return
    if args.stdin:
        run_json_lines(daemon, sys.stdin)
    else:
        lines, output = serial_lines(args.port, args.baud)
        run_json_lines(daemon, lines, output)


if __name__ == "__main__":
    main()
