"""Alohida time slot Telegram boti (shaxsiy chat).

Hozircha faqat SLOT_ALLOWED_USER_IDS (bo'lmasa ALLOWED_USER_IDS) dagilar
ishlata oladi. Telegram'dan xabarlarni o'zi so'rab oladi (long polling) -
webhook/nginx kerak emas. Qidiruvning o'zi timeslot_hunter.py xizmatida.

    venv/bin/python slot_bot.py
"""
import json
import os
import threading
import time
import traceback

import requests

import timeslot_hunter as hunter
from config import Config

API = f"https://api.telegram.org/bot{hunter.SLOT_BOT_TOKEN}"
OFFSET_FILE = os.path.join(hunter.DATA_DIR, "slot_bot_offset.json")
ALLOWED = {u.strip() for u in os.getenv("SLOT_ALLOWED_USER_IDS", "").split(",") if u.strip()} or Config.ALLOWED_USER_IDS

COMMANDS = [
    {"command": "slot", "description": "Bo'sh time slotni poylab band qilish"},
    {"command": "holat", "description": "Faol qidiruvlar"},
]

WELCOME = (
    "👋 Time slot boti\n\n"
    "Uzum omboriga yetkazib berish uchun bo'sh time slotni poylab, topilishi bilan band qiladi.\n\n"
    "/slot - yangi qidiruv: nakladnoy va kunlarni tanlaysiz\n"
    "/holat - hozir ishlayotgan qidiruvlar"
)


def _allowed(user):
    return not ALLOWED or str((user or {}).get("id", "")) in ALLOWED


def _who(user):
    user = user or {}
    return f"@{user['username']}" if user.get("username") else (user.get("first_name") or str(user.get("id", "")))


def _status_text(chat):
    with hunter._state() as st:
        active = [(k, h) for k, h in st["hunts"].items() if h["status"] == "active" and str(h.get("chat")) == str(chat)]
        hb = st.get("heartbeat", 0)
    lines = [f"🔁 Qidiruvchi: {'ishlayapti ✅' if time.time() - hb < hunter.HEARTBEAT_STALE else 'JAVOB BERMAYAPTI ⚠️'}"]
    if not active:
        lines.append("Faol qidiruv yo'q. Yangisini /slot bilan boshlang.")
    for _, h in active:
        items = ", ".join(f"{it['shop_title']} {it['number'][-6:]}" for it in h["items"] if not it.get("booked"))
        lines.append(f"\n• {items or 'hammasi band qilingan'}\n  kunlar: "
                     + ", ".join(hunter._day_label(d) for d in h["dates"]) + f"\n  tekshiruvlar: {h.get('checks', 0)}")
    return "\n".join(lines)


def _handle(update):
    msg = update.get("message")
    if msg:
        chat, user = msg["chat"]["id"], msg.get("from")
        cmd = (msg.get("text") or "").strip().split()[:1]
        cmd = cmd[0].split("@")[0] if cmd else ""
        if not _allowed(user):
            hunter._send("⛔ Bu bot hozircha yopiq.", chat=chat)
            return
        if cmd in ("/start", "/help"):
            hunter._send(WELCOME, chat=chat)
        elif cmd == "/slot":
            threading.Thread(target=hunter.start_picker, args=(chat,), daemon=True).start()
        elif cmd == "/holat":
            hunter._send(_status_text(chat), chat=chat)
        return
    cb = update.get("callback_query")
    if cb and (cb.get("data") or "").startswith("ts:"):
        if not _allowed(cb.get("from")):
            hunter._answer(cb["id"], "Sizda ruxsat yo'q.")
            return
        threading.Thread(target=_safe_callback, args=(cb,), daemon=True).start()


def _safe_callback(cb):
    try:
        hunter.handle_callback(cb, _who(cb.get("from")))
    except Exception:
        traceback.print_exc()
        hunter._answer(cb["id"], "❌ Xato yuz berdi")


def main():
    requests.post(f"{API}/setMyCommands", json={"commands": COMMANDS}, timeout=30)
    try:
        offset = json.load(open(OFFSET_FILE))["offset"]
    except Exception:
        offset = 0
    print("Slot boti ishga tushdi", flush=True)
    while True:
        try:
            r = requests.get(f"{API}/getUpdates", timeout=60, params={
                "offset": offset, "timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"])})
            updates = r.json().get("result") or []
        except Exception as e:
            print(f"getUpdates xato: {e}", flush=True)
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                _handle(u)
            except Exception:
                traceback.print_exc()
        if updates:
            with open(OFFSET_FILE, "w") as f:
                json.dump({"offset": offset}, f)


if __name__ == "__main__":
    main()
