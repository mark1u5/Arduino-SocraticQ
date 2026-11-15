"""Validate all golden fixtures in contracts/fixtures/ against frozen schemas."""

import json
from pathlib import Path
from typing import Any, Callable

from contracts.schemas import (
    validate_agent_insight,
    validate_socratic_response,
    validate_telemetry_event,
    validate_uart_event,
    validate_uart_feedback,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

CHECKS: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
    ("uart_event.json", validate_uart_event),
    ("uart_feedback.json", validate_uart_feedback),
    ("socratic_response.json", validate_socratic_response),
    ("telemetry_event.json", validate_telemetry_event),
    ("agent_insight.json", validate_agent_insight),
]


def main() -> None:
    for filename, validator in CHECKS:
        path = FIXTURES_DIR / filename
        payload = json.loads(path.read_text(encoding="utf-8"))
        validator(payload)
        print(f"OK  {filename}")
    print("All frozen contracts and fixtures verified.")


if __name__ == "__main__":
    main()
