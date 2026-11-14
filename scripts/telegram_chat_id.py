"""Find your Telegram chat id: python scripts/telegram_chat_id.py

1. Put TELEGRAM_BOT_TOKEN in .env.
2. In Telegram, open your bot and send it any message (e.g. "γεια").
3. Run this script. It prints the chat id of whoever wrote to the bot,
   to copy into TELEGRAM_OWNER_CHAT_ID. The token itself is never printed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from xangx.master.telegram import TelegramApi  # noqa: E402
from xangx.shared.env import load_env  # noqa: E402


def main():
    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Λείπει το TELEGRAM_BOT_TOKEN στο .env.")
        return 1
    try:
        updates = TelegramApi(token).call("getUpdates", timeout=0)
    except Exception as exc:  # noqa: BLE001 - show a short, token-free message
        print("Δεν μπόρεσα να μιλήσω με το Telegram:", str(exc).replace(token, "***")[:200])
        return 1
    chats = {}
    for update in updates:
        chat = (update.get("message") or {}).get("chat") or {}
        if chat.get("id") is not None:
            chats[chat["id"]] = chat.get("first_name") or chat.get("title") or ""
    if not chats:
        print("Δεν βρήκα μήνυμα. Στείλε κάτι στο bot σου στο Telegram και ξανατρέξε το.")
        return 1
    for chat_id, name in chats.items():
        print(f"TELEGRAM_OWNER_CHAT_ID={chat_id}   ({name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
