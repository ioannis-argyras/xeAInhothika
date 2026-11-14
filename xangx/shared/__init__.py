"""Shared code: the append-only event log that all agents talk through."""

from xangx.shared.db import connect, init_db
from xangx.shared.events import Event, EventError, append_event, read_events

__all__ = [
    "connect",
    "init_db",
    "append_event",
    "read_events",
    "Event",
    "EventError",
]
