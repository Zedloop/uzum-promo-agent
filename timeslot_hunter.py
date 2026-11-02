"""/slot buyrug'i: tanlangan nakladnoylar uchun tanlangan kunlarda bo'sh
time slot paydo bo'lishini kutib, topilishi bilan band qiladi.

Telegram qismi (webapp.py / bot_listener.py chaqiradi):
    start_picker()          - /slot: nakladnoylar ro'yxati tugmalar bilan
    handle_callback(...)    - "ts:..." tugmalari (nakladnoy/sana tanlash, boshlash, to'xtatish)

Qidiruvchi alohida systemd xizmati sifatida ishlaydi (gunicorn worker'lari
bir nechta - har birida qidiruv takrorlanmasligi uchun):
    venv/bin/python timeslot_hunter.py
"""
import datetime as dt
import fcntl
import json
import os
import time
import traceback
import uuid
from contextlib import contextmanager

import requests

from config import Config
from promo_client import PromoClient
from telegram_notify import _answer_callback, _api_base

TZ = dt.timezone(dt.timedelta(hours=5))  # Toshkent
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STATE_FILE = os.path.join(DATA_DIR, "slot_hunts.json")
LOCK_FILE = STATE_FILE + ".lock"

POLL_SECONDS = float(os.getenv("SLOT_POLL_SECONDS", "5"))
MAX_BACKOFF_SECONDS = 300
DATE_DAYS = 21           # sana tanlashda bugundan necha kun ko'rsatiladi
HEARTBEAT_STALE = 60     # qidiruvchi shuncha soniya ovoz bermasa - ogohlantiramiz
MAX_SET_FAILS = 10       # band qilish shuncha marta rad etilsa - qidiruv to'xtatiladi
WEEKDAYS = ["Dush", "Sesh", "Chor", "Pay", "Jum", "Shan", "Yak"]
MONTHS = ["yan", "fev", "mar", "apr", "may", "iyun", "iyul", "avg", "sen", "okt", "noy", "dek"]


# ---------- holat (webapp va qidiruvchi jarayonlari o'rtasida umumiy) ----------

@contextmanager
def _state():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}
        state.setdefault("drafts", {})
        state.setdefault("hunts", {})
        yield state
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE_FILE)


def _fmt(ms, pattern="%d.%m %H:%M"):
    return dt.datetime.fromtimestamp(ms / 1000, TZ).strftime(pattern)


def _day(ms):
    return dt.datetime.fromtimestamp(ms / 1000, TZ).strftime("%Y-%m-%d")


def _day_label(day):
    d = dt.date.fromisoformat(day)
    return f"{d.day} {MONTHS[d.month - 1]} ({WEEKDAYS[d.weekday()]})"


def _today():
    return dt.datetime.now(TZ).date()


# ---------- Telegram ----------

def _tg(method, payload):
    r = requests.post(f"{_api_base()}/{method}", json=payload, timeout=30)
    return r.json().get("result") if r.ok else None


def _send(text, keyboard=None):
    payload = {"chat_id": Config.TELEGRAM_CHAT_ID, "text": text}
    if keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    msg = _tg("sendMessage", payload)
    return msg["message_id"] if msg else None


def _edit(message_id, text, keyboard=None):
    payload = {"chat_id": Config.TELEGRAM_CHAT_ID, "message_id": message_id, "text": text,
               "reply_markup": {"inline_keyboard": keyboard or []}}
    _tg("editMessageText", payload)


def _shop_titles():
    try:
        return PromoClient(Config.UZUM_SHOP_IDS[0]).list_shops()
    except Exception:
        return {}


# ---------- 1-qadam: nakladnoy tanlash ----------

def _load_invoices():
    """Barcha do'konlardagi 'Yaratilgan' nakladnoylar (slotni hali
    o'zgartirish mumkin bo'lganlari)."""
    titles = _shop_titles()
    result = []
    for shop_id in Config.UZUM_SHOP_IDS:
        for inv in PromoClient(shop_id).list_invoices():
            if (inv.get("invoiceStatus") or {}).get("value") != "CREATED":
                continue
            if (inv.get("remainingAmountOfUpdates") or 0) <= 0:
                continue
            stock = inv.get("stock") or {}
            res = inv.get("timeSlotReservation") or {}
            # Sloti o'tib ketgan eski nakladnoylar (Uzum ularni bekor qilishga
            # ham ruxsat bermaydi) - ro'yxatni chalkashtirmasin
            if res.get("timeFrom") and res["timeFrom"] < time.time() * 1000:
                continue
            result.append({
                "shop": shop_id,
                "shop_title": titles.get(shop_id, shop_id),
                "id": inv["id"],
                "number": str(inv.get("invoiceNumber")),
                "qty": inv.get("totalToStock") or 0,
                "stock_id": stock.get("id"),
                "pool": stock.get("poolSource"),
                "cur_from": res.get("timeFrom"),
                "remaining": inv.get("remainingAmountOfUpdates"),
            })
    return result


def _invoice_label(inv, selected):
    slot = f"hozir {_fmt(inv['cur_from'])}" if inv.get("cur_from") else "slot yo'q"
    return f"{'✅' if selected else '▫️'} {inv['shop_title']} · {inv['number']} · {inv['qty']} dona · {slot}"


def _invoice_screen(draft):
    sel = set(draft["sel"])
    keyboard = [[{"text": _invoice_label(inv, i in sel), "callback_data": f"ts:i:{i}"}]
                for i, inv in enumerate(draft["invoices"])]
    keyboard.append([
        {"text": f"➡️ Keyingisi ({len(sel)})", "callback_data": "ts:n"},
        {"text": "❌ Bekor qilish", "callback_data": "ts:x"},
    ])
    text = ("📦 Time slot qidiruvi\n\n1️⃣ Nakladnoyni tanlang (bir nechtasini tanlash mumkin), "
            "keyin «Keyingisi» ni bosing.\n\nℹ️ Uzum har bir nakladnoyda slotni o'zgartirish sonini "
            "cheklaydi - qolgan soni yonida ko'rsatilgan.")
    lines = [f"• {inv['number']}: o'zgartirish {inv['remaining']} ta qoldi" for inv in draft["invoices"]]
    return text + "\n" + "\n".join(lines), keyboard


def start_picker():
    """/slot buyrug'i."""
    try:
        invoices = _load_invoices()
    except Exception as e:
        _send(f"❌ Nakladnoylarni olishda xato: {e}")
        return
    if not invoices:
        _send("📦 Slot qo'yish mumkin bo'lgan «Yaratilgan» nakladnoy yo'q.\n"
              "Avval Uzum'da nakladnoy yarating, keyin /slot ni qayta yozing.")
        return
    draft = {"invoices": invoices, "sel": [], "dates": [], "created": time.time()}
    text, keyboard = _invoice_screen(draft)
    msg_id = _send(text, keyboard)
    if msg_id:
        with _state() as st:
            # eski (1 kundan oshgan) qoralamalarni tozalaymiz
            st["drafts"] = {k: v for k, v in st["drafts"].items() if time.time() - v.get("created", 0) < 86400}
            st["drafts"][str(msg_id)] = draft


# ---------- 2-qadam: sana tanlash ----------

def _date_screen(draft):
    sel = set(draft["dates"])
    days = [(_today() + dt.timedelta(days=i)).isoformat() for i in range(DATE_DAYS)]
    keyboard, row = [], []
    for day in days:
        row.append({"text": ("✅ " if day in sel else "") + _day_label(day), "callback_data": f"ts:d:{day}"})
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([
        {"text": "⬅️ Orqaga", "callback_data": "ts:b"},
        {"text": f"🚀 Qidiruvni boshlash ({len(sel)})", "callback_data": "ts:g"},
    ])
    keyboard.append([{"text": "❌ Bekor qilish", "callback_data": "ts:x"}])
    chosen = [draft["invoices"][i] for i in draft["sel"]]
    text = ("📦 Time slot qidiruvi\n\nTanlangan nakladnoylar:\n"
            + "\n".join(f"• {inv['shop_title']} · {inv['number']} · {inv['qty']} dona" for inv in chosen)
            + "\n\n2️⃣ Qaysi kunlarga slot kerak? Kunlarni belgilang va «Qidiruvni boshlash» ni bosing.")
    if sel:
        text += "\n\nTanlangan kunlar: " + ", ".join(_day_label(d) for d in sorted(sel))
    return text, keyboard


# ---------- qidiruvni boshlash / to'xtatish ----------

def _hunt_text(hunt):
    lines = ["🔎 Time slot qidiruvi" + {"active": " ishlayapti", "done": " tugadi ✅",
                                       "stopped": " to'xtatildi", "expired": " tugadi (kunlar o'tib ketdi)",
                                       "failed": " to'xtadi ❌ (Uzum band qilishni qayta-qayta rad etdi)"}[hunt["status"]], ""]
    for it in hunt["items"]:
        if it.get("booked"):
            holat = f"✅ band qilindi: {_fmt(it['booked'], '%d.%m.%Y %H:%M')}"
        elif hunt["status"] == "active":
            holat = "⏳ qidirilmoqda"
        else:
            holat = "❌ topilmadi"
        lines.append(f"• {it['shop_title']} · {it['number']} · {it['qty']} dona — {holat}")
    lines.append("")
    lines.append("📅 Kunlar: " + ", ".join(_day_label(d) for d in hunt["dates"]))
    if hunt["status"] == "active":
        lines.append(f"🔁 Har {POLL_SECONDS:g} soniyada tekshiriladi (tekshiruvlar: {hunt.get('checks', 0)})")
    if hunt.get("last_error") and hunt["status"] in ("active", "failed"):
        lines.append(f"⚠️ Oxirgi xato: {hunt['last_error'][:200]}")
    return "\n".join(lines)


def _hunt_keyboard(hunt_id, hunt):
    if hunt["status"] != "active":
        return []
    return [[{"text": "⏹ Qidiruvni to'xtatish", "callback_data": f"ts:s:{hunt_id}"}]]


def _start_hunt(msg_id, draft):
    chosen = [dict(draft["invoices"][i]) for i in draft["sel"]]
    dates = sorted(draft["dates"])
    for it in chosen:
        # Joriy sloti allaqachon tanlangan kunlardan birida bo'lsa - qayta
        # band qilib o'zgartirish sonini behuda sarflamaymiz
        if it.get("cur_from") and _day(it["cur_from"]) in dates:
            it["booked"] = it["cur_from"]
    hunt = {"msg_id": msg_id, "items": chosen, "dates": dates, "status": "active",
            "created": time.time(), "checks": 0}
    if all(it.get("booked") for it in chosen):
        hunt["status"] = "done"
    return uuid.uuid4().hex[:10], hunt


def handle_callback(callback, who):
    data = callback.get("data", "")
    msg_id = callback["message"]["message_id"]
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if action == "s":  # qidiruvni to'xtatish
        with _state() as st:
            hunt = st["hunts"].get(arg)
            if hunt and hunt["status"] == "active":
                hunt["status"] = "stopped"
                hunt["stopped_by"] = who
        _answer_callback(callback["id"], "⏹ To'xtatildi" if hunt else "Qidiruv topilmadi")
        if hunt:
            _edit(hunt["msg_id"], _hunt_text(hunt) + f"\n\n({who})", [])
        return

    with _state() as st:
        draft = st["drafts"].get(str(msg_id))
        if not draft:
            _answer_callback(callback["id"], "Bu xabar eskirgan - /slot ni qayta yozing.")
            return
        screen, note = None, None
        if action == "i":
            i = int(arg)
            draft["sel"] = sorted(set(draft["sel"]) ^ {i})
            screen = _invoice_screen(draft)
        elif action == "n":
            if not draft["sel"]:
                note = "Avval kamida bitta nakladnoy tanlang."
            else:
                screen = _date_screen(draft)
        elif action == "b":
            screen = _invoice_screen(draft)
        elif action == "d":
            draft["dates"] = sorted(set(draft["dates"]) ^ {arg})
            screen = _date_screen(draft)
        elif action == "x":
            del st["drafts"][str(msg_id)]
            screen = ("📦 Time slot qidiruvi bekor qilindi.", [])
        elif action == "g":
            if not draft["dates"]:
                note = "Avval kamida bitta kunni tanlang."
            else:
                hunt_id, hunt = _start_hunt(msg_id, draft)
                hunt["started_by"] = who
                st["hunts"][hunt_id] = hunt
                del st["drafts"][str(msg_id)]
                screen = (_hunt_text(hunt), _hunt_keyboard(hunt_id, hunt))
                hb = st.get("heartbeat", 0)
                if time.time() - hb > HEARTBEAT_STALE:
                    note = "⚠️ Qidiruvchi xizmati javob bermayapti!"
    _answer_callback(callback["id"], note or "")
    if screen:
        _edit(msg_id, *screen)


# ---------- qidiruvchi (alohida jarayon) ----------

def _check_item(hunt, it):
    """Bitta nakladnoy uchun bo'sh slotlarni tekshiradi va mos kelsa band
    qiladi. True - band qilindi."""
    client = PromoClient(it["shop"])
    slots = client.get_free_timeslots(it["id"], it["pool"])
    wanted = set(hunt["dates"])
    candidates = sorted((s for s in slots if _day(s["timeFrom"]) in wanted), key=lambda s: s["timeFrom"])
    for slot in candidates[:3]:
        try:
            client.set_timeslot(it["id"], slot["timeFrom"], it["stock_id"], it["pool"])
        except requests.HTTPError as e:
            # Odatda slotni bizdan oldin boshqa sotuvchi olib qo'ygan bo'ladi.
            # Lekin ketma-ket ko'p marta rad etilsa - so'rovning o'zida muammo,
            # Uzum'ga so'rov yog'dirmaslik uchun qidiruvni to'xtatamiz.
            hunt["last_error"] = f"{it['number']} {_fmt(slot['timeFrom'])}: {e}"
            it["set_fail"] = it.get("set_fail", 0) + 1
            if it["set_fail"] >= MAX_SET_FAILS:
                hunt["status"] = "failed"
                return False
            continue
        it["booked"] = slot["timeFrom"]
        it["booked_to"] = slot.get("timeTo")
        return True
    return False


def run_forever():
    backoff = POLL_SECONDS
    print(f"Time slot qidiruvchi ishga tushdi (har {POLL_SECONDS:g} s)", flush=True)
    while True:
        with _state() as st:
            st["heartbeat"] = time.time()
            active = {k: v for k, v in st["hunts"].items() if v["status"] == "active"}
        if not active:
            time.sleep(POLL_SECONDS)
            continue

        today = _today().isoformat()
        results, error = {}, None
        for hunt_id, hunt in active.items():
            hunt["dates"] = [d for d in hunt["dates"] if d >= today]
            if not hunt["dates"]:
                hunt["status"] = "expired"
                results[hunt_id] = hunt
                continue
            booked_now = []
            for it in hunt["items"]:
                if it.get("booked"):
                    continue
                try:
                    if _check_item(hunt, it):
                        booked_now.append(it)
                except requests.HTTPError as e:
                    error = e
                    hunt["last_error"] = str(e)
                except Exception as e:
                    traceback.print_exc()
                    error = e
                    hunt["last_error"] = str(e)
                if hunt["status"] == "failed":
                    break
            hunt["checks"] = hunt.get("checks", 0) + 1
            if hunt["status"] == "failed":
                pass
            elif all(it.get("booked") for it in hunt["items"]):
                hunt["status"] = "done"
            hunt["_booked_now"] = booked_now
            results[hunt_id] = hunt

        # Natijani saqlaymiz - shu orada foydalanuvchi "To'xtatish" ni bosgan
        # bo'lsa, uning qarori ustun (statusni qayta "active" qilmaymiz)
        with _state() as st:
            for hunt_id, hunt in results.items():
                saved = st["hunts"].get(hunt_id)
                if not saved:
                    continue
                booked_now = hunt.pop("_booked_now", [])
                if saved["status"] != "active":
                    hunt["status"] = saved["status"]
                st["hunts"][hunt_id] = hunt
                for it in booked_now:
                    _send(f"✅ Time slot band qilindi!\n\n🏪 {it['shop_title']}\n📄 Nakladnoy {it['number']} "
                          f"({it['qty']} dona)\n🕒 {_fmt(it['booked'], '%d.%m.%Y %H:%M')}"
                          + (f"–{_fmt(it['booked_to'], '%H:%M')}" if it.get("booked_to") else ""))
                if hunt["status"] == "failed" and saved["status"] == "active":
                    _send(f"❌ Time slot qidiruvi to'xtadi: Uzum band qilishni qayta-qayta rad etdi.\n"
                          f"Oxirgi javob: {hunt.get('last_error', '')[:300]}")
                if booked_now or hunt["status"] != "active" or hunt["checks"] % 60 == 1:
                    _edit(hunt["msg_id"], _hunt_text(hunt), _hunt_keyboard(hunt_id, hunt))

        # Uzum xato qaytarsa (ko'p so'rov, server xatosi) - akkauntni xavf
        # ostiga qo'ymaslik uchun sekinlashamiz
        if error is not None:
            status = getattr(getattr(error, "response", None), "status_code", None)
            backoff = min(MAX_BACKOFF_SECONDS, max(backoff * 2, 30 if status == 429 else POLL_SECONDS * 2))
            print(f"Xato ({status}): {error} - {backoff:g} s kutamiz", flush=True)
        else:
            backoff = POLL_SECONDS
        time.sleep(backoff)


if __name__ == "__main__":
    run_forever()
