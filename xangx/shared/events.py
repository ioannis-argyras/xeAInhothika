"""Writing and reading events. append_event / append_events are the only ways to write."""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from xangx.shared.catalog import ACTORS, CATALOG

MAX_PAYLOAD_BYTES = 16 * 1024
_AT_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


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


def _validate(
    conn, *, type, actor, payload, subject=None, caused_by=None,
    idem_key=None, store_id="store-1", v=1, at=None,
):
    """Check one event. Returns the row values (at, payload_json) to insert."""
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
    if actor != "system" and actor not in spec.actors:
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
    if at is None:
        at_value = _now_utc()
    else:
        if actor != "system":
            raise EventError("Only the system actor may set at, for history import.")
        try:
            if not isinstance(at, str) or not _AT_FORMAT.match(at):
                raise ValueError("bad format")
            datetime.strptime(at, "%Y-%m-%dT%H:%M:%S.%fZ")
        except ValueError:
            raise EventError(
                f"Invalid at={at!r}. Use UTC ISO 8601 with milliseconds and Z, "
                f"e.g. 2026-11-14T13:42:08.123Z."
            ) from None
        at_value = at
    return at_value, payload_json


def _insert(
    conn, at_value, payload_json, *, type, actor, subject=None, caused_by=None,
    idem_key=None, store_id="store-1", v=1,
):
    """Insert one validated event without committing. Returns (event, created)."""
    if idem_key is not None:
        existing = _get_by_idem_key(conn, idem_key)
        if existing is not None:
            return existing, False
    cur = conn.execute(
        "INSERT INTO events "
        "(at, store_id, actor, type, subject, caused_by, idem_key, payload, v) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (at_value, store_id, actor, type, subject, caused_by, idem_key,
         payload_json, v),
    )
    row = conn.execute("SELECT * FROM events WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _row_to_event(row), True


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
    at=None,
):
    """Validate and write one event.

    Returns (event, created). created is False when the idem_key already
    existed: nothing is written and the existing event is returned.
    `at` may be set only by the system actor (history import).
    """
    return append_events(
        conn,
        [
            dict(
                type=type, actor=actor, payload=payload, subject=subject,
                caused_by=caused_by, idem_key=idem_key, store_id=store_id,
                v=v, at=at,
            )
        ],
    )[0]


def append_events(conn, items):
    """Write several events in ONE transaction (all or nothing).

    `items` is a list of dicts with the same keyword arguments as
    append_event. All items are validated first; if any is invalid nothing
    is written. Returns a list of (event, created), one per item.
    """
    items = list(items)
    prepared = []
    for item in items:
        at_value, payload_json = _validate(conn, **item)
        prepared.append((at_value, payload_json, item))

    results = []
    try:
        with conn:
            for at_value, payload_json, item in prepared:
                insert_args = {
                    k: item[k]
                    for k in ("type", "actor", "subject", "caused_by",
                              "idem_key", "store_id", "v")
                    if k in item
                }
                results.append(_insert(conn, at_value, payload_json, **insert_args))
    except sqlite3.IntegrityError as exc:
        raise EventError(f"Could not write events, nothing was saved: {exc}.") from exc
    return results


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
