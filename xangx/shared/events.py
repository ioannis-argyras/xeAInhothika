"""Writing and reading events. append_event is the only way to write."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from xangx.shared.catalog import ACTORS, CATALOG

MAX_PAYLOAD_BYTES = 16 * 1024


class EventError(ValueError):
    """Raised when an event does not pass validation."""


@dataclass(frozen=True)
class Event:
    id: int
    at: str
    store_id: str
    actor: str
    type: str
    subject: str | None
    caused_by: int | None
    idem_key: str | None
    payload: dict
    v: int


def _row_to_event(row):
    return Event(
        id=row["id"],
        at=row["at"],
        store_id=row["store_id"],
        actor=row["actor"],
        type=row["type"],
        subject=row["subject"],
        caused_by=row["caused_by"],
        idem_key=row["idem_key"],
        payload=json.loads(row["payload"]),
        v=row["v"],
    )


def _now_utc():
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _get_by_idem_key(conn, idem_key):
    row = conn.execute(
        "SELECT * FROM events WHERE idem_key = ?", (idem_key,)
    ).fetchone()
    return _row_to_event(row) if row else None


def append_event(
    conn,
    *,
    type,
    actor,
    payload,
    subject=None,
    caused_by=None,
    idem_key=None,
    store_id="store-1",
    v=1,
):
    """Validate and write one event.

    Returns (event, created). created is False when the idem_key already
    existed: nothing is written and the existing event is returned.
    """
    spec = CATALOG.get(type)
    if spec is None:
        raise EventError(
            f"Unknown event type {type!r}. Add it to xangx/shared/catalog.py "
            f"(CATALOG) or use one of the existing types."
        )
    if actor not in ACTORS:
        raise EventError(
            f"Unknown actor {actor!r}. Allowed actors: {', '.join(sorted(ACTORS))}."
        )
    if actor not in spec.actors:
        raise EventError(
            f"Actor {actor!r} may not write {type!r}. "
            f"Allowed actors for this type: {', '.join(sorted(spec.actors))}."
        )
    if not isinstance(payload, dict):
        raise EventError(
            f"Payload for {type!r} must be a dict, got {payload.__class__.__name__}."
        )
    missing = [name for name in spec.required if name not in payload]
    if missing:
        raise EventError(
            f"Payload for {type!r} is missing required field(s): "
            f"{', '.join(missing)}. Add them to the payload."
        )
    try:
        payload_json = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise EventError(
            f"Payload for {type!r} is not JSON-serializable: {exc}."
        ) from exc
    size = len(payload_json.encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        raise EventError(
            f"Payload for {type!r} is {size} bytes, over the {MAX_PAYLOAD_BYTES} "
            f"byte limit. Store large data as a file and put only its file_ref "
            f"in the payload."
        )
    if caused_by is not None:
        found = conn.execute(
            "SELECT 1 FROM events WHERE id = ?", (caused_by,)
        ).fetchone()
        if found is None:
            raise EventError(
                f"caused_by={caused_by} does not exist. Use the id of an "
                f"existing event."
            )

    if idem_key is not None:
        existing = _get_by_idem_key(conn, idem_key)
        if existing is not None:
            return existing, False

    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO events "
                "(at, store_id, actor, type, subject, caused_by, idem_key, payload, v) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now_utc(), store_id, actor, type, subject, caused_by,
                    idem_key, payload_json, v,
                ),
            )
            new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        # Another writer inserted the same idem_key between our check and insert.
        if idem_key is not None:
            existing = _get_by_idem_key(conn, idem_key)
            if existing is not None:
                return existing, False
        raise

    row = conn.execute("SELECT * FROM events WHERE id = ?", (new_id,)).fetchone()
    return _row_to_event(row), True


def read_events(
    conn, *, after_id=0, until_id=None, types=None, subject=None, limit=None
):
    """Read events in id order. after_id is exclusive, until_id is inclusive."""
    sql = "SELECT * FROM events WHERE id > ?"
    params = [after_id]
    if until_id is not None:
        sql += " AND id <= ?"
        params.append(until_id)
    if types is not None:
        types = list(types)
        if not types:
            return []
        sql += " AND type IN (" + ",".join("?" * len(types)) + ")"
        params.extend(types)
    if subject is not None:
        sql += " AND subject = ?"
        params.append(subject)
    sql += " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_row_to_event(r) for r in conn.execute(sql, params).fetchall()]
