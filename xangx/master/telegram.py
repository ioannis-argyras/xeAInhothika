"""Telegram link for the owner: long polling in, notifications out.

Runs only if TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID are set. Messages from any
other chat are ignored without looking at them. The token is never printed or logged:
errors are reported by type only, because HTTP error texts can contain the URL.
"""

import os
import threading
import time

try:
    import httpx2 as httpx  # anthropic 2.x ships httpx2
except ImportError:  # pragma: no cover
    import httpx

from xangx.master import agent
from xangx.shared import connect
from xangx.shared import views
from xangx.shared.cursors import consume, get_cursor, set_cursor

POLL_TIMEOUT = 30
RETRY_SECONDS = 5
NOTIFIER_ACTOR = "telegram-notifier"
NOTIFY_TYPES = ["approval.requested", "notification.sent"]
MAX_TEXT = 4000


class TelegramApi:
    """Minimal Bot API client. call(method, **params) returns the `result` field."""

    def __init__(self, token):
        self._base = f"https://api.telegram.org/bot{token}/"
        self._client = httpx.Client(timeout=POLL_TIMEOUT + 10)

    def call(self, method, **params):
        response = self._client.post(self._base + method, json=params)
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description', '')[:100]}")
        return data["result"]


def _owner_chat_id():
    return str(os.environ.get("TELEGRAM_OWNER_CHAT_ID", "")).strip()


def send_text(api, chat_id, text, buttons=None):
    params = {"chat_id": chat_id, "text": text[:MAX_TEXT]}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": [buttons]}
    return api.call("sendMessage", **params)


def process_update(conn, api, update, owner_chat_id):
    """Handle one Telegram update. Anything not from the owner is ignored."""
    callback = update.get("callback_query")
    if callback:
        chat = (callback.get("message") or {}).get("chat") or {}
        if str(chat.get("id")) != owner_chat_id:
            return
        parts = str(callback.get("data", "")).split(":")
        if len(parts) == 3 and parts[0] == "ap" and parts[2] in ("yes", "no"):
            result = agent.decide_approval(conn, parts[1], parts[2], "telegram")
            api.call("answerCallbackQuery", callback_query_id=callback["id"])
            send_text(api, owner_chat_id, result["message"])
        return
    message = update.get("message")
    if not message:
        return
    if str((message.get("chat") or {}).get("id")) != owner_chat_id:
        return
    text = message.get("text")
    if not text:
        return
    result = agent.handle_owner_message(conn, text, "telegram")
    send_text(api, owner_chat_id, result["reply"])


def poll_forever(api, owner_chat_id, db_path=None):
    conn = connect(db_path)
    offset = 0
    while True:
        try:
            updates = api.call("getUpdates", offset=offset, timeout=POLL_TIMEOUT)
            for update in updates:
                offset = update["update_id"] + 1
                process_update(conn, api, update, owner_chat_id)
        except Exception as exc:
            print(f"telegram poll error: {type(exc).__name__}", flush=True)
            time.sleep(RETRY_SECONDS)


def make_notify_handler(conn, api, owner_chat_id):
    def handle(event):
        if event.type == "approval.requested":
            approval_id = event.payload.get("approval_id")
            still_pending = any(
                e.payload.get("approval_id") == approval_id
                for e in views.pending_approvals(conn)
            )
            if not still_pending:
                return
            text = (
                f"Χρειάζομαι την έγκρισή σου\n{event.payload.get('summary', '')}\n"
                f"{event.payload.get('exact', '')}\n"
                f"Γιατί: {event.payload.get('why', '')}"
            )
            buttons = [
                {"text": "Ναι", "callback_data": f"ap:{approval_id}:yes"},
                {"text": "Όχι", "callback_data": f"ap:{approval_id}:no"},
            ]
            send_text(api, owner_chat_id, text, buttons)
        else:
            send_text(api, owner_chat_id, str(event.payload.get("text", "")))

    return handle


def notify_once(conn, api, owner_chat_id):
    """Send the new approval requests and notifications. Returns how many were sent."""
    return consume(conn, NOTIFIER_ACTOR, NOTIFY_TYPES, make_notify_handler(conn, api, owner_chat_id))


def notify_forever(api, owner_chat_id, db_path=None):
    conn = connect(db_path)
    started = False
    while True:
        try:
            if not started:
                # First start: do not replay the whole history to the owner's phone.
                if get_cursor(conn, NOTIFIER_ACTOR) == 0:
                    last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
                    set_cursor(conn, NOTIFIER_ACTOR, last)
                started = True
            notify_once(conn, api, owner_chat_id)
            time.sleep(1)
        except Exception as exc:
            print(f"telegram notifier error: {type(exc).__name__}", flush=True)
            time.sleep(RETRY_SECONDS)


def start(db_path=None):
    """Start the two daemon threads (polling and notifier), each with its own connection."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    owner = _owner_chat_id()
    if not token or not owner:
        return
    api = TelegramApi(token)
    for target in (poll_forever, notify_forever):
        threading.Thread(
            target=target, args=(api, owner, db_path), daemon=True, name=target.__name__
        ).start()
