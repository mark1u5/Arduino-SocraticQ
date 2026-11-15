"""Keyboard/CLI stand-in for the SocraticQ MCU.

It emits the same newline-delimited UART event JSON as the physical firmware, so
the rest of the hardware stack can be developed without a board attached.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from contracts.schemas import validate_uart_event


ACTION_TO_EVENT = {
    "hint": "HINT_REQUEST",
    "wrong": "ANSWER_SUBMIT",
    "correct": "ANSWER_SUBMIT",
    "reset": "SESSION_RESET",
}


@dataclass
class MockMcu:
    """Small state machine mirroring the controls present on the MCU."""

    device_id: str = "unoq-01"
    problem_id: str = "CAM-2023-N4-17"
    category_idx: int = 2
    confidence: int = 35
    attempt_num: int = 0
    _seq: int = 0
    _started_at: float = field(default_factory=time.monotonic)

    def event(self, action: str) -> dict[str, Any]:
        if action not in ACTION_TO_EVENT:
            raise ValueError(f"Unknown mock action: {action}")

        self._seq += 1
        event_type = ACTION_TO_EVENT[action]
        selected_option: str | None = None
        if action == "wrong":
            self.attempt_num += 1
            selected_option = "C"
        elif action == "correct":
            self.attempt_num += 1
            selected_option = "D"
        elif action == "reset":
            self.attempt_num = 0

        payload: dict[str, Any] = {
            "schema_v": 1,
            "device_id": self.device_id,
            "seq": self._seq,
            "ts_ms": int((time.monotonic() - self._started_at) * 1000),
            "event_type": event_type,
            "problem_id": self.problem_id,
            "dial_category_idx": self.category_idx,
            "confidence_dial": self.confidence,
            "selected_option": selected_option,
            "attempt_num": self.attempt_num,
        }
        validate_uart_event(payload)
        return payload

    def set_category(self, category_idx: int) -> dict[str, Any]:
        if not 0 <= category_idx <= 4:
            raise ValueError("category must be between 0 and 4")
        self.category_idx = category_idx
        self._seq += 1
        payload = {
            "schema_v": 1,
            "device_id": self.device_id,
            "seq": self._seq,
            "ts_ms": int((time.monotonic() - self._started_at) * 1000),
            "event_type": "PROBLEM_SELECT",
            "problem_id": self.problem_id,
            "dial_category_idx": self.category_idx,
            "confidence_dial": self.confidence,
            "selected_option": None,
            "attempt_num": self.attempt_num,
        }
        validate_uart_event(payload)
        return payload

    def set_confidence(self, confidence: int) -> dict[str, Any]:
        if not 0 <= confidence <= 100:
            raise ValueError("confidence must be between 0 and 100")
        self.confidence = confidence
        self._seq += 1
        payload = {
            "schema_v": 1,
            "device_id": self.device_id,
            "seq": self._seq,
            "ts_ms": int((time.monotonic() - self._started_at) * 1000),
            "event_type": "CONFIDENCE_UPDATE",
            "problem_id": self.problem_id,
            "dial_category_idx": self.category_idx,
            "confidence_dial": self.confidence,
            "selected_option": None,
            "attempt_num": self.attempt_num,
        }
        validate_uart_event(payload)
        return payload


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def interactive(mcu: MockMcu) -> None:
    print(
        "Mock MCU: [1] hint, [2] wrong answer, [3] correct answer, "
        "c <0-4> category, f <0-100> confidence, r reset, q quit",
        file=sys.stderr,
    )
    while True:
        try:
            command = input("mock-mcu> ").strip().lower()
        except EOFError:
            return
        if command in {"q", "quit", "exit"}:
            return
        try:
            if command in {"1", "hint"}:
                emit(mcu.event("hint"))
            elif command in {"2", "wrong"}:
                emit(mcu.event("wrong"))
            elif command in {"3", "correct"}:
                emit(mcu.event("correct"))
            elif command in {"r", "reset"}:
                emit(mcu.event("reset"))
            elif command.startswith("c "):
                emit(mcu.set_category(int(command.split(maxsplit=1)[1])))
            elif command.startswith("f "):
                emit(mcu.set_confidence(int(command.split(maxsplit=1)[1])))
            elif command:
                print("Unknown command", file=sys.stderr)
        except ValueError as exc:
            print(f"Invalid command: {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", choices=sorted(ACTION_TO_EVENT))
    parser.add_argument("--device-id", default="unoq-01")
    parser.add_argument("--problem-id", default="CAM-2023-N4-17")
    parser.add_argument("--category", type=int, default=2)
    parser.add_argument("--confidence", type=int, default=35)
    args = parser.parse_args()
    mcu = MockMcu(
        device_id=args.device_id,
        problem_id=args.problem_id,
        category_idx=args.category,
        confidence=args.confidence,
    )
    if args.once:
        emit(mcu.event(args.once))
    else:
        interactive(mcu)


if __name__ == "__main__":
    main()
