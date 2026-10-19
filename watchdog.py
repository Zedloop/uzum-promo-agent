"""Kuzatuvchi: agentlar to'xtab qolsa yoki xato bersa Telegram'ga xabar beradi.

Hetzner'da systemd timer orqali har 10 daqiqada ishga tushadi va tekshiradi:
- promo-agent: oxirgi muvaffaqiyatli sikl qachon bo'lgani (data/heartbeat.json)
- review-agent: PythonAnywhere'dagi /review/health
- ERP: narxlar API'si javob beryaptimi

Xabar faqat holat o'zgarganda (ishlayotgan -> to'xtagan, yoki aksincha)
yuboriladi, muammo davom etsa - har REMIND_SECONDS'da bir marta eslatiladi.
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

import requests

from config import Config
from heartbeat import load_heartbeat
from telegram_notify import send_text

STATE_FILE = os.path.join(os.path.dirname(__file__), "data", "watchdog_state.json")

REVIEW_HEALTH_URL = os.getenv("REVIEW_HEALTH_URL", "https://zedloop.pythonanywhere.com/review/health")
PROMO_MAX_AGE = int(os.getenv("PROMO_MAX_AGE_MINUTES", "75")) * 60    # cron har 15 daqiqada
REVIEW_MAX_AGE = int(os.getenv("REVIEW_MAX_AGE_MINUTES", "35")) * 60  # cron har 10 daqiqada
REMIND_SECONDS = 6 * 3600
# Tarmoq tekshiruvlari bir martalik uzilishda "to'xtadi" demasligi uchun -
# ketma-ket shuncha marta muvaffaqiyatsiz bo'lsagina xabar beriladi.
NETWORK_FAIL_THRESHOLD = 2

TASHKENT = timezone(timedelta(hours=5))


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _fmt(ts):
    if not ts:
        return "hech qachon"
    return datetime.fromtimestamp(ts, TASHKENT).strftime("%d.%m %H:%M")


def _age_problem(name, last_ok, max_age, first_seen, now):
    """last_ok juda eski bo'lsa muammo matnini qaytaradi. Heartbeat hali umuman
    yozilmagan bo'lsa (yangi o'rnatilgan), kuzatuvchi birinchi ishga tushgan
    vaqtdan boshlab hisoblaydi."""
    reference = last_ok or first_seen
    if now - reference <= max_age:
        return None
    return f"{name} {int((now - reference) // 60)} daqiqadan beri ishlamadi (oxirgi muvaffaqiyatli sikl: {_fmt(last_ok)})."


def check_promo(state, now):
    hb = load_heartbeat()
    last_ok = hb.get("last_ok")
    last_error_at = hb.get("last_error_at")
    if last_error_at and (not last_ok or last_error_at > last_ok):
        return f"Promo-agent oxirgi siklda xato berdi ({_fmt(last_error_at)}): {hb.get('last_error')}"
    return _age_problem("Promo-agent", last_ok, PROMO_MAX_AGE, state["_first_seen"], now)


def check_review(state, now):
    try:
        resp = requests.get(REVIEW_HEALTH_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Review-agent serveri (PythonAnywhere) javob bermayapti: {e}", True
    return _age_problem("Review-agent", data.get("last_ok"), REVIEW_MAX_AGE, state["_first_seen"], now), False


def check_erp():
    try:
        resp = requests.get(Config.ERP_API_URL, params={"token": Config.ERP_API_TOKEN}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return "ERP narxlar ro'yxati bo'sh qaytdi."
    except Exception as e:
        return f"ERP (PythonAnywhere) narxlarni bermayapti: {e}"
    return None


def _update(state, key, problem, now, network=False):
    entry = state.setdefault(key, {"status": "ok", "fails": 0})

    if problem is None:
        if entry["status"] == "fail":
            send_text(f"✅ Tiklandi: {key} yana ishlayapti.")
        state[key] = {"status": "ok", "fails": 0}
        return

    entry["fails"] = entry.get("fails", 0) + 1
    if network and entry["fails"] < NETWORK_FAIL_THRESHOLD:
        return

    if entry["status"] != "fail":
        send_text(f"🚨 Diqqat: {problem}")
        entry.update(status="fail", since=now, last_alert=now)
    elif now - entry.get("last_alert", 0) >= REMIND_SECONDS:
        send_text(f"⏰ Hali ham tuzalmadi ({_fmt(entry.get('since'))} dan beri): {problem}")
        entry["last_alert"] = now


def main():
    now = int(time.time())
    state = _load_state()
    state.setdefault("_first_seen", now)

    _update(state, "promo-agent", check_promo(state, now), now)
    review_problem, review_network = check_review(state, now)
    _update(state, "review-agent", review_problem, now, network=review_network)
    _update(state, "ERP", check_erp(), now, network=True)

    _save_state(state)


if __name__ == "__main__":
    main()
