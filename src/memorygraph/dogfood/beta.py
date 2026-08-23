"""Approved live-session instrumentation and evaluation for Dogfood Beta."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

LIVE_EVENT_SCHEMA = "memorygraph.dogfood.live-event/v1"
LIVE_REPORT_SCHEMA = "memorygraph.dogfood.live-report/v1"
EventType = Literal["recall", "attempt", "task"]


@dataclass(frozen=True, slots=True)
class LiveSessionEvent:
    session_id: str
    task_key: str
    event_type: EventType
    approved: bool
    created_at: str
    query: str | None = None
    recalled_ids: tuple[str, ...] = ()
    useful_ids: tuple[str, ...] = ()
    forbidden_ids: tuple[str, ...] = ()
    outcome: str | None = None
    mistake_key: str | None = None
    latency_ms: float = 0.0
    token_estimate: int = 0
    tool_calls: int = 0
    retries: int = 0
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> LiveSessionEvent:
        payload = value.get("event", value)
        if not isinstance(payload, dict):
            raise ValueError("event must be an object")
        event_type = _text(payload, "event_type")
        if event_type not in {"recall", "attempt", "task"}:
            raise ValueError(f"unsupported event_type: {event_type}")
        approved = payload.get("approved")
        if not isinstance(approved, bool):
            raise ValueError("approved must be a boolean")
        return cls(
            session_id=_text(payload, "session_id"),
            task_key=_text(payload, "task_key"),
            event_type=event_type,
            approved=approved,
            created_at=_text(payload, "created_at"),
            query=_optional_text(payload.get("query")),
            recalled_ids=_strings(payload.get("recalled_ids", []), "recalled_ids"),
            useful_ids=_strings(payload.get("useful_ids", []), "useful_ids"),
            forbidden_ids=_strings(payload.get("forbidden_ids", []), "forbidden_ids"),
            outcome=_optional_text(payload.get("outcome")),
            mistake_key=_optional_text(payload.get("mistake_key")),
            latency_ms=_number(payload.get("latency_ms", 0.0), "latency_ms"),
            token_estimate=_integer(payload.get("token_estimate", 0), "token_estimate"),
            tool_calls=_integer(payload.get("tool_calls", 0), "tool_calls"),
            retries=_integer(payload.get("retries", 0), "retries"),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("recalled_ids", "useful_ids", "forbidden_ids"):
            payload[key] = list(payload[key])
        return payload


class LiveSessionLedger:
    """Append-only, explicitly approved telemetry owned by the repository."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: LiveSessionEvent) -> None:
        if not event.approved:
            raise ValueError("live session events require explicit approval")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"schema": LIVE_EVENT_SCHEMA, "event": event.to_dict()}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")

    def read(self) -> tuple[LiveSessionEvent, ...]:
        if not self.path.exists():
            return ()
        events: list[LiveSessionEvent] = []
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid live ledger JSON on line {line_number}") from error
            if record.get("schema") != LIVE_EVENT_SCHEMA:
                raise ValueError(f"invalid live ledger schema on line {line_number}")
            events.append(LiveSessionEvent.from_mapping(record))
        return tuple(events)


def evaluate_live_sessions(events: tuple[LiveSessionEvent, ...]) -> dict[str, Any]:
    approved = tuple(event for event in events if event.approved)
    recalls = tuple(event for event in approved if event.event_type == "recall")
    attempts = tuple(event for event in approved if event.event_type == "attempt")
    retrieved = sum(len(event.recalled_ids) for event in recalls)
    useful = sum(len(set(event.recalled_ids) & set(event.useful_ids)) for event in recalls)
    forbidden = sum(len(set(event.recalled_ids) & set(event.forbidden_ids)) for event in recalls)
    mistake_counts: dict[str, int] = {}
    repeated_mistakes = 0
    for event in attempts:
        if event.outcome != "failure" or not event.mistake_key:
            continue
        if mistake_counts.get(event.mistake_key, 0):
            repeated_mistakes += 1
        mistake_counts[event.mistake_key] = mistake_counts.get(event.mistake_key, 0) + 1
    successful_tasks = {
        event.task_key
        for event in approved
        if event.event_type == "task" and event.outcome == "success"
    }
    task_keys = {event.task_key for event in approved if event.event_type == "task"}
    return {
        "schema": LIVE_REPORT_SCHEMA,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sessions": len({event.session_id for event in approved}),
        "tasks": len(task_keys),
        "successful_tasks": len(successful_tasks),
        "recall_events": len(recalls),
        "useful_recall_precision": useful / retrieved if retrieved else 0.0,
        "forbidden_recall_hits": forbidden,
        "repeated_mistakes": repeated_mistakes,
        "total_latency_ms": sum(event.latency_ms for event in approved),
        "total_tokens": sum(event.token_estimate for event in approved),
        "total_tool_calls": sum(event.tool_calls for event in approved),
        "total_retries": sum(event.retries for event in approved),
    }


def _text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text fields must be strings")
    return value.strip() or None


def _strings(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be an array of strings")
    return tuple(value)


def _number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"{key} must be a non-negative number")
    return float(value)


def _integer(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value
