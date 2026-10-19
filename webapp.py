import threading

from flask import Flask, abort, request

from bot_listener import _handle_callback
from config import Config
from lock_store import acquire_lock, release_lock
from main import run
from telegram_notify import send_text

app = Flask(__name__)


def _run_cycle():
    if not acquire_lock(timeout=10):
        return
    try:
        run()
    except Exception as e:
        send_text(f"❌ Promo-agent avtomatik tekshiruvda xato: {e}")
    finally:
        release_lock()


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
