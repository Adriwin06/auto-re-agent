"""Structured runtime events emitted by the reversing pipeline."""
from __future__ import annotations

import contextvars
import json
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol


@dataclass
class RuntimeEvent:
    """A single event emitted while a re-agent run is executing."""

    id: str
    run_id: str | None
    type: str
    timestamp: float
    payload: dict[str, Any]


class EventSink(Protocol):
    """Receives structured runtime events."""

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        """Emit an event and return the normalized event object."""
        ...


class NoopEventSink:
    """Default sink used when no live observer is attached."""

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        return RuntimeEvent(
            id=uuid.uuid4().hex,
            run_id=None,
            type=event_type,
            timestamp=time.time(),
            payload=_jsonable(payload or {}),
        )


class JsonlEventSink:
    """Persist events as newline-delimited JSON."""

    def __init__(self, path: str | Path, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        event = RuntimeEvent(
            id=uuid.uuid4().hex,
            run_id=self.run_id,
            type=event_type,
            timestamp=time.time(),
            payload=_jsonable(payload or {}),
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        return event


class TeeEventSink:
    """Forward each event to multiple sinks."""

    def __init__(self, *sinks: EventSink) -> None:
        self.sinks = sinks

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
        event: RuntimeEvent | None = None
        for sink in self.sinks:
            event = sink.emit(event_type, payload)
        if event is None:
            event = NoopEventSink().emit(event_type, payload)
        return event


_current_sink: contextvars.ContextVar[EventSink | None] = contextvars.ContextVar(
    "re_agent_event_sink",
    default=None,
)


def get_event_sink() -> EventSink:
    """Return the currently active event sink."""
    sink = _current_sink.get()
    return sink if sink is not None else NoopEventSink()


def set_event_sink(sink: EventSink) -> contextvars.Token[EventSink | None]:
    """Install an event sink for the current execution context."""
    return _current_sink.set(sink)


def reset_event_sink(token: contextvars.Token[EventSink | None]) -> None:
    """Restore the previous event sink."""
    _current_sink.reset(token)


def emit_event(event_type: str, payload: dict[str, Any] | None = None) -> RuntimeEvent:
    """Emit an event on the active sink."""
    return get_event_sink().emit(event_type, payload)


def make_jsonable(value: Any) -> Any:
    """Convert dataclasses, enums, paths, and containers into JSON-safe values."""
    return _jsonable(value)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
