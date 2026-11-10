"""/slot buyrug'i: tanlangan nakladnoylar uchun tanlangan kunlarda bo'sh
time slot paydo bo'lishini kutib, topilishi bilan band qiladi.

Telegram qismi (alohida slot boti - slot_bot.py chaqiradi):
    start_picker(chat)      - /slot: nakladnoylar ro'yxati tugmalar bilan
    handle_callback(...)    - "ts:..." tugmalari (nakladnoy/sana tanlash, boshlash, to'xtatish)
Har bir qidiruv o'z chati va egasini eslab qoladi - keyinchalik bot boshqa
sotuvchilar uchun ham ochilishi uchun.

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
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import requests

from config import Config
from promo_client import PromoClient

TZ = dt.timezone(dt.timedelta(hours=5))  # Toshkent
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
STATE_FILE = os.path.join(DATA_DIR, "slot_hunts.json")
LOCK_FILE = STATE_FILE + ".lock"

POLL_SECONDS = float(os.getenv("SLOT_POLL_SECONDS", "2"))
# Uzum xato qaytarsa (ko'p so'rov va h.k.) - shu oraliqqa o'tamiz, SLOW_MODE_HOURS
# dan keyin yana POLL_SECONDS ni sinab ko'ramiz
SLOW_POLL_SECONDS = float(os.getenv("SLOT_SLOW_POLL_SECONDS", "3"))
SLOW_MODE_HOURS = 24
SLOW_AFTER_ERRORS = 3    # ketma-ket shuncha xatodan (yoki bitta 429 dan) keyin sekin rejim
SYNC_SECONDS = 30        # nakladnoylarning Uzum'dagi joriy sloti shuncha soniyada qayta o'qiladi
MAX_BACKOFF_SECONDS = 300
DATE_DAYS = 21           # sana tanlashda bugundan necha kun ko'rsatiladi
HEARTBEAT_STALE = 60     # qidiruvchi shuncha soniya ovoz bermasa - ogohlantiramiz
MAX_SET_FAILS = 10       # band qilish shuncha marta rad etilsa - qidiruv to'xtatiladi
ALERT_AFTER_ERRORS = 6   # Uzum ketma-ket shuncha marta xato bersa - guruhga yoziladi
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

# Alohida slot boti; sozlanmagan bo'lsa - promo bot guruhi (eski holat)
SLOT_BOT_TOKEN = os.getenv("SLOT_BOT_TOKEN") or Config.TELEGRAM_BOT_TOKEN
DEFAULT_CHAT = os.getenv("SLOT_CHAT_ID") or Config.TELEGRAM_CHAT_ID


def _tg(method, payload):
    r = requests.post(f"https://api.telegram.org/bot{SLOT_BOT_TOKEN}/{method}", json=payload, timeout=30)
    return r.json().get("result") if r.ok else None


def _send(text, keyboard=None, chat=None):
    payload = {"chat_id": chat or DEFAULT_CHAT, "text": text}
    if keyboard is not None:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    msg = _tg("sendMessage", payload)
    return msg["message_id"] if msg else None


def _edit(chat, message_id, text, keyboard=None):
    payload = {"chat_id": chat or DEFAULT_CHAT, "message_id": message_id, "text": text,
               "reply_markup": {"inline_keyboard": keyboard or []}}
    _tg("editMessageText", payload)


def _answer(callback_id, text=""):
    _tg("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})


_titles_cache = {"at": 0, "titles": {}}


def _shop_titles():
    # Do'kon nomlari deyarli o'zgarmaydi - har /slot da qayta so'ramaymiz
    if time.time() - _titles_cache["at"] > 3600:
        try:
            _titles_cache["titles"] = PromoClient(Config.UZUM_SHOP_IDS[0]).list_shops()
            _titles_cache["at"] = time.time()
        except Exception:
            pass
    return _titles_cache["titles"]


# ---------- 1-qadam: nakladnoy tanlash ----------

def _load_invoices():
    """Barcha do'konlardagi 'Yaratilgan' nakladnoylar (slotni hali
    o'zgartirish mumkin bo'lganlari)."""
    # Do'konlar bir-birini kutmasin - parallel so'raymiz
    with ThreadPoolExecutor(len(Config.UZUM_SHOP_IDS) + 1) as ex:
        titles_job = ex.submit(_shop_titles)
        per_shop = list(ex.map(lambda s: (s, PromoClient(s).list_invoices()), Config.UZUM_SHOP_IDS))
        titles = titles_job.result()
    result = []
    for shop_id, invoices in per_shop:
        for inv in invoices:
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


def start_picker(chat=None):
    """/slot buyrug'i. Darhol "yuklanmoqda" deb javob beramiz, ro'yxat
    tayyor bo'lgach shu xabarni o'zgartiramiz."""
    chat = chat or DEFAULT_CHAT
    msg_id = _send("⏳ Nakladnoylar yuklanmoqda...", chat=chat)
    if not msg_id:
        return
    try:
        invoices = _load_invoices()
    except Exception as e:
        _edit(chat, msg_id, f"❌ Nakladnoylarni olishda xato: {e}")
        return
    if not invoices:
        _edit(chat, msg_id, "📦 Slot qo'yish mumkin bo'lgan «Yaratilgan» nakladnoy yo'q.\n"
                            "Avval Uzum'da nakladnoy yarating, keyin /slot ni qayta yozing.")
        return
    draft = {"invoices": invoices, "sel": [], "dates": [], "created": time.time(), "chat": chat}
    with _state() as st:
        # eski (1 kundan oshgan) qoralamalarni tozalaymiz
        st["drafts"] = {k: v for k, v in st["drafts"].items() if time.time() - v.get("created", 0) < 86400}
        st["drafts"][f"{chat}:{msg_id}"] = draft
    _edit(chat, msg_id, *_invoice_screen(draft))


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
        if it.get("booked") and it.get("external"):
            holat = f"✅ slot boshqa joyda qo'yilgan: {_fmt(it['booked'], '%d.%m.%Y %H:%M')}"
        elif it.get("booked"):
            holat = f"✅ band qilindi: {_fmt(it['booked'], '%d.%m.%Y %H:%M')}"
        elif it.get("gone"):
            holat = "⚠️ endi o'zgartirib bo'lmaydi (bekor qilingan yoki o'zgartirish limiti tugagan)"
        elif hunt["status"] == "active":
            holat = "⏳ qidirilmoqda"
        else:
            holat = "❌ topilmadi"
        lines.append(f"• {it['shop_title']} · {it['number']} · {it['qty']} dona — {holat}")
    lines.append("")
    # O'tgan kunlar qidiruvdan chiqarib boriladi - xabarda asl tanlovni ko'rsatamiz
    lines.append("📅 Kunlar: " + ", ".join(_day_label(d) for d in hunt.get("dates_all") or hunt["dates"]))
    if hunt["status"] == "active":
        lines.append(f"🔁 Har {POLL_SECONDS:g} soniyada tekshiriladi (Uzum xato bersa {SLOW_POLL_SECONDS:g} s), "
                     f"tekshiruvlar: {hunt.get('checks', 0)}")
    seen = sorted((hunt.get("seen") or {}).values(), key=lambda s: s["first"])
    if seen:
        lines.append(f"👀 Tanlangan kunlarda {len(seen)} ta bo'sh slot ko'rindi:")
        for s in seen[-5:]:
            lines.append(f"   • {_fmt(s['slot'], '%d.%m %H:%M')} ({s['number'][-4:]}) - {_fmt(s['first'] * 1000, '%d.%m %H:%M:%S')} da, "
                         f"{s['times']} marta: {s['result'] or '?'}")
    elif hunt["status"] != "active":
        lines.append(f"👀 Tekshiruvlarimizda tanlangan kunlarda bo'sh slot ko'rinmadi. Bo'shab, {POLL_SECONDS:g} soniyadan "
                     "tez boshqa bot olib ketgan slotlarni ko'ra olmaymiz - ular bo'lgan bo'lishi mumkin.")
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
    hunt = {"msg_id": msg_id, "chat": draft.get("chat") or DEFAULT_CHAT, "items": chosen, "dates": dates,
            "dates_all": list(dates), "status": "active", "created": time.time(), "checks": 0}
    if all(it.get("booked") for it in chosen):
        hunt["status"] = "done"
    return uuid.uuid4().hex[:10], hunt


def handle_callback(callback, who):
    data = callback.get("data", "")
    msg_id = callback["message"]["message_id"]
    chat = callback["message"]["chat"]["id"]
    key = f"{chat}:{msg_id}"
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    if action == "s":  # qidiruvni to'xtatish
        with _state() as st:
            hunt = st["hunts"].get(arg)
            if hunt and hunt["status"] == "active":
                hunt["status"] = "stopped"
                hunt["stopped_by"] = who
        _answer(callback["id"], "⏹ To'xtatildi" if hunt else "Qidiruv topilmadi")
        if hunt:
            _edit(hunt.get("chat") or chat, hunt["msg_id"], _hunt_text(hunt) + f"\n\n({who})", [])
        return

    with _state() as st:
        draft = st["drafts"].get(key)
        if not draft:
            _answer(callback["id"], "Bu xabar eskirgan - /slot ni qayta yozing.")
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
            del st["drafts"][key]
            screen = ("📦 Time slot qidiruvi bekor qilindi.", [])
        elif action == "g":
            # Bitta nakladnoyni ikki qidiruv poylasa Uzum'ga so'rov ikki baravar
            # ketadi (429 "Too Many Requests" xavfi) va ular bir-biriga xalaqit beradi
            busy = {it["id"] for h in st["hunts"].values() if h["status"] == "active" for it in h["items"]}
            dup = [draft["invoices"][i]["number"] for i in draft["sel"] if draft["invoices"][i]["id"] in busy]
            if not draft["dates"]:
                note = "Avval kamida bitta kunni tanlang."
            elif dup:
                note = f"Bu nakladnoy allaqachon qidirilmoqda: {', '.join(dup)}. Avval o'sha qidiruvni to'xtating (/holat)."
            else:
                hunt_id, hunt = _start_hunt(msg_id, draft)
                hunt["started_by"] = who
                hunt["owner"] = (callback.get("from") or {}).get("id")
                st["hunts"][hunt_id] = hunt
                del st["drafts"][key]
                screen = (_hunt_text(hunt), _hunt_keyboard(hunt_id, hunt))
                hb = st.get("heartbeat", 0)
                if time.time() - hb > HEARTBEAT_STALE:
                    note = "⚠️ Qidiruvchi xizmati javob bermayapti!"
    _answer(callback["id"], note or "")
    if screen:
        _edit(chat, msg_id, *screen)


# ---------- qidiruvchi (alohida jarayon) ----------

_clients = {}


def _client(shop_id):
    # Har tekshiruvda yangi ulanish ochmaymiz - bir xil sessiya (keep-alive)
    # so'rovni bir necha yuz ms tezlashtiradi, poygada bu muhim
    if shop_id not in _clients:
        _clients[shop_id] = PromoClient(shop_id)
    return _clients[shop_id]


def _sync_with_uzum(hunt):
    """Nakladnoylarning Uzum'dagi joriy holatini qayta o'qiydi. Slot boshqa
    joyda (qo'lda yoki boshqa bot orqali) tanlangan kunlardan biriga qo'yilgan
    bo'lsa - o'sha nakladnoy uchun qidiruv tugaydi: yaxshi slotni qayta
    ko'chirib, cheklangan o'zgartirish sonini sarflamaymiz."""
    by_shop = {}
    for it in hunt["items"]:
        if not it.get("booked") and not it.get("gone"):
            by_shop.setdefault(it["shop"], []).append(it)
    external = []
    for shop, items in by_shop.items():
        invoices = {inv["id"]: inv for inv in _client(shop).list_invoices()}
        for it in items:
            inv = invoices.get(it["id"]) or {}
            res = inv.get("timeSlotReservation") or {}
            if (inv.get("invoiceStatus") or {}).get("value") != "CREATED" or (inv.get("remainingAmountOfUpdates") or 0) <= 0:
                it["gone"] = True
            elif res.get("timeFrom") and _day(res["timeFrom"]) in hunt["dates"]:
                it["booked"], it["booked_to"], it["external"] = res["timeFrom"], res.get("timeTo"), True
                external.append(it)
    return external


def _check_item(hunt, it):
    """Bitta nakladnoy uchun bo'sh slotlarni tekshiradi va mos kelsa band
    qiladi. True - band qilindi."""
    client = _client(it["shop"])
    slots = client.get_free_timeslots(it["id"], it["pool"])
    wanted = set(hunt["dates"])
    candidates = sorted((s for s in slots if _day(s["timeFrom"]) in wanted), key=lambda s: s["timeFrom"])
    for slot in candidates[:3]:
        seen = _note_seen(hunt, it, slot)
        try:
            client.set_timeslot(it["id"], slot["timeFrom"], it["stock_id"], it["pool"])
        except requests.HTTPError as e:
            # Odatda slotni bizdan oldin boshqa sotuvchi olib qo'ygan bo'ladi.
            # Lekin ketma-ket ko'p marta rad etilsa - so'rovning o'zida muammo,
            # Uzum'ga so'rov yog'dirmaslik uchun qidiruvni to'xtatamiz.
            hunt["last_error"] = f"{it['number']} {_fmt(slot['timeFrom'])}: {e}"
            seen["result"] = f"olishga ulgurmadik: {str(e)[:120]}"
            print(f"SLOT KO'RINDI, olinmadi: {it['number']} {_fmt(slot['timeFrom'])} - {e}", flush=True)
            it["set_fail"] = it.get("set_fail", 0) + 1
            if it["set_fail"] >= MAX_SET_FAILS:
                hunt["status"] = "failed"
                return False
            continue
        seen["result"] = "olindi ✅"
        print(f"SLOT KO'RINDI va olindi: {it['number']} {_fmt(slot['timeFrom'])}", flush=True)
        it["booked"] = slot["timeFrom"]
        it["booked_to"] = slot.get("timeTo")
        return True
    return False


def _note_seen(hunt, it, slot):
    """Tanlangan kunda ko'ringan har bir bo'sh slotni yozib boramiz - qidiruv
    natija bermasa, "slot umuman chiqmadimi yoki boshqa bot oldin oldimi"
    degan savolga javob shu yerdan olinadi."""
    key = f"{it['number']}:{slot['timeFrom']}"
    now = time.time()
    seen = hunt.setdefault("seen", {}).setdefault(key, {"number": it["number"], "slot": slot["timeFrom"],
                                                         "first": now, "times": 0, "result": None})
    seen["last"] = now
    seen["times"] += 1
    return seen


def _run_checks(active):
    """Barcha faol qidiruvlardagi nakladnoylarni parallel tekshiradi.
    Qaytaradi: oxirgi xato (yo'q bo'lsa None)."""
    jobs = [(hunt, it) for hunt in active.values() for it in hunt["items"]
            if not it.get("booked") and not it.get("gone")]
    if not jobs:
        return None
    errors = []

    def work(job):
        hunt, it = job
        try:
            if _check_item(hunt, it):
                hunt["_booked_now"].append(it)
        except Exception as e:
            if not isinstance(e, requests.HTTPError):
                traceback.print_exc()
            hunt["last_error"] = str(e)
            errors.append(e)

    with ThreadPoolExecutor(min(8, len(jobs))) as ex:
        list(ex.map(work, jobs))
    return errors[-1] if errors else None


def run_forever():
    interval, error_streak, alerted = POLL_SECONDS, 0, False
    last_sync = {}
    print(f"Time slot qidiruvchi ishga tushdi (har {POLL_SECONDS:g} s, muammoda {SLOW_POLL_SECONDS:g} s)", flush=True)
    while True:
        started = time.time()
        with _state() as st:
            st["heartbeat"] = started
            slow_until = st.get("slow_until", 0)
            active = {k: v for k, v in st["hunts"].items() if v["status"] == "active"}
        base = SLOW_POLL_SECONDS if started < slow_until else POLL_SECONDS
        if not active:
            time.sleep(base)
            continue

        today = _today().isoformat()
        external = {}
        for hunt_id, hunt in active.items():
            hunt["_booked_now"] = []
            hunt["dates"] = [d for d in hunt["dates"] if d >= today]
            if not hunt["dates"]:
                hunt["status"] = "expired"
                continue
            if started - last_sync.get(hunt_id, 0) >= SYNC_SECONDS:
                last_sync[hunt_id] = started
                try:
                    external[hunt_id] = _sync_with_uzum(hunt)
                except Exception as e:
                    hunt["last_error"] = f"holatni o'qib bo'lmadi: {e}"

        error = _run_checks({k: h for k, h in active.items() if h["status"] == "active"})

        for hunt in active.values():
            hunt["checks"] = hunt.get("checks", 0) + 1
            if hunt["status"] == "active" and all(it.get("booked") or it.get("gone") for it in hunt["items"]):
                hunt["status"] = "done"

        # Natijani saqlaymiz - shu orada foydalanuvchi "To'xtatish" ni bosgan
        # bo'lsa, uning qarori ustun (statusni qayta "active" qilmaymiz)
        with _state() as st:
            for hunt_id, hunt in active.items():
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
                          + (f"–{_fmt(it['booked_to'], '%H:%M')}" if it.get("booked_to") else ""), chat=hunt.get("chat"))
                for it in external.get(hunt_id, []):
                    _send(f"ℹ️ Nakladnoy {it['number']} ({it['shop_title']}) sloti boshqa joyda tanlangan kunga "
                          f"qo'yilgan: {_fmt(it['booked'], '%d.%m.%Y %H:%M')}.\nBu nakladnoy uchun qidiruv to'xtatildi - "
                          "slotni qayta ko'chirib, o'zgartirish sonini sarflamaymiz.", chat=hunt.get("chat"))
                if hunt["status"] == "failed" and saved["status"] == "active":
                    _send(f"❌ Time slot qidiruvi to'xtadi: Uzum band qilishni qayta-qayta rad etdi.\n"
                          f"Oxirgi javob: {hunt.get('last_error', '')[:300]}", chat=hunt.get("chat"))
                if booked_now or external.get(hunt_id) or hunt["status"] != "active" or hunt["checks"] % 150 == 1:
                    _edit(hunt.get("chat"), hunt["msg_id"], _hunt_text(hunt), _hunt_keyboard(hunt_id, hunt))

            # Uzum xato qaytarsa (ko'p so'rov, server xatosi) - akkauntni xavf
            # ostiga qo'ymaslik uchun avval 3 s rejimiga o'tamiz, xato davom
            # etsa yanada sekinlashamiz
            if error is not None:
                status = getattr(getattr(error, "response", None), "status_code", None)
                error_streak += 1
                if (status == 429 or error_streak >= SLOW_AFTER_ERRORS) and started >= st.get("slow_until", 0):
                    st["slow_until"] = started + SLOW_MODE_HOURS * 3600
                    _send(f"⚠️ Time slot qidiruvi: Uzum xato qaytardi - tekshiruv {POLL_SECONDS:g} s dan "
                          f"{SLOW_POLL_SECONDS:g} s ga o'tkazildi ({SLOW_MODE_HOURS} soatga).\nXato: {str(error)[:300]}")
                interval = min(MAX_BACKOFF_SECONDS, max(interval * 2, 30 if status == 429 else SLOW_POLL_SECONDS))
                print(f"Xato ({status}): {error} - {interval:g} s kutamiz", flush=True)
                if error_streak == ALERT_AFTER_ERRORS:
                    _send(f"⚠️ Time slot qidiruvi: Uzum {error_streak} marta ketma-ket xato qaytardi, "
                          f"tekshiruv sekinlashtirildi (har {interval:g} s).\nXato: {str(error)[:300]}")
                    alerted = True
            else:
                if alerted:
                    _send("✅ Time slot qidiruvi: Uzum yana javob beryapti, tekshiruv odatdagidek davom etmoqda.")
                interval = SLOW_POLL_SECONDS if started < st.get("slow_until", 0) else POLL_SECONDS
                error_streak, alerted = 0, False

        # Oraliq - tekshiruv boshidan hisoblanadi (so'rov vaqti qo'shilib ketmasin)
        time.sleep(max(0.2, interval - (time.time() - started)))


if __name__ == "__main__":
    run_forever()
