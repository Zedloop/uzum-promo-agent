import threading

from flask import Flask, abort, request

from bot_listener import _handle_callback
from akt_report import send_created_acts
from config import Config
from heartbeat import record_run
from lock_store import acquire_lock, release_lock
from main import run
from promo_client import PromoClient
from promocode_create import DISCOUNT_PERCENT
from returns_report import build_returns_reports
from telegram_notify import send_text, send_with_buttons
from timeslot_report import build_timeslot_report

app = Flask(__name__)


def _run_cycle():
    if not acquire_lock(timeout=10):
        return
    try:
        run()
        record_run()
    except Exception as e:
        # Telegram'ga bu yerda emas, kuzatuvchi (watchdog.py) orqali yoziladi -
        # u bir xil xatoni har 15 daqiqada takrorlamay, eslatib turadi.
        record_run(error=e)
    finally:
        release_lock()


def _send_returns():
    try:
        detailed, short = build_returns_reports()
        send_text(detailed)
        if short:
            send_text(short)
    except Exception as e:
        send_text(f"❌ Vozvratlarni olishda xato: {e}")


def _send_timeslot():
    try:
        send_text(build_timeslot_report())
    except Exception as e:
        send_text(f"❌ Time slot tahlilida xato: {e}")


def _send_acts():
    try:
        send_created_acts()
    except Exception as e:
        send_text(f"❌ Aktlarni yuborishda xato: {e}")


def _send_promocode_picker():
    # Do'kon tugmasi bosilgach promokod bot_listener._handle_callback'da
    # ("promo:<shop_id>") yaratiladi
    try:
        titles = PromoClient(Config.UZUM_SHOP_IDS[0]).list_shops()
    except Exception:
        titles = {}
    buttons = [(f"🏪 {titles.get(s, s)}", f"promo:{s}") for s in Config.UZUM_SHOP_IDS]
    send_with_buttons(f"🎟 Qaysi do'kon uchun promokod yaratilsin? ({DISCOUNT_PERCENT}%, bugun, 1 marta)", buttons)


@app.route("/run-check")
def run_check_endpoint():
    token = request.args.get("token", "")
    if not Config.RUN_TOKEN or token != Config.RUN_TOKEN:
        abort(403)
    # Fon oqimida emas, shu so'rovning o'zida ishga tushiramiz - PythonAnywhere
    # kabi muhitlarda so'rov tugagach orqa fondagi thread to'xtatilib qo'yilishi
    # mumkin, shuning uchun natijani kutib shu yerda bajarib tugatamiz.
    _run_cycle()
    return "OK", 200


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    # Telegram'ning o'zidan kelayotganini tasdiqlash - setWebhook'da
    # ro'yxatdan o'tkazilgan maxfiy qiymat shu headerda qaytarilishi shart.
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not Config.TELEGRAM_WEBHOOK_SECRET or secret != Config.TELEGRAM_WEBHOOK_SECRET:
        abort(403)

    update = request.get_json(silent=True) or {}

    msg = update.get("message")
    if msg and str(msg["chat"]["id"]) == str(Config.TELEGRAM_CHAT_ID):
        # Guruhda "/vozvrat" yoki "/vozvrat@bot_nomi" ko'rinishida keladi
        command = (msg.get("text") or "").strip().split()[:1]
        name = command[0].split("@")[0] if command else ""
        if name == "/vozvrat":
            threading.Thread(target=_send_returns, daemon=True).start()
        elif name == "/timeslot":
            threading.Thread(target=_send_timeslot, daemon=True).start()
        elif name == "/akt":
            threading.Thread(target=_send_acts, daemon=True).start()
        elif name == "/promocode":
            # Haqiqiy chegirma yaratadi - shuning uchun faqat ruxsat
            # berilgan foydalanuvchi(lar), tugmalar kabi
            user_id = str((msg.get("from") or {}).get("id", ""))
            if Config.ALLOWED_USER_IDS and user_id not in Config.ALLOWED_USER_IDS:
                send_text("⛔ Promokod yaratishga ruxsatingiz yo'q.")
            else:
                threading.Thread(target=_send_promocode_picker, daemon=True).start()

    callback = update.get("callback_query")
    if callback:
        # Telegram'ga darhol javob qaytaramiz - aks holda Uzum so'rovlari
        # sekinlashsa Telegram javob kutmay, tugma bosilishini qayta-qayta
        # yuboradi. Ish o'zi orqa fonda bajariladi (oddiy VPS'da gunicorn
        # worker'i so'rov tugagach ham thread'ni o'ldirmaydi).
        threading.Thread(target=_handle_callback, args=(callback,), daemon=True).start()
    return "OK", 200


if __name__ == "__main__":
    app.run()
