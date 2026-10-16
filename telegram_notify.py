import requests

from config import Config
from decision_store import create_decision

API_BASE_TMPL = "https://api.telegram.org/bot{token}"


def _api_base():
    return API_BASE_TMPL.format(token=Config.TELEGRAM_BOT_TOKEN)


def send_text(text):
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"{_api_base()}/sendMessage",
            json={"chat_id": Config.TELEGRAM_CHAT_ID, "text": text},
        )
    except Exception:
        pass


def notify_price_rejected(shop_id, sale_id, sale_title, product_id, sku_id, sku_code, nomi, your_price, recommended_price, marja_foiz, ikpu=None):
    """Narx rad etilganda, Uzum tavsiya qilgan narx va shu narxdagi marjani
    ko'rsatib, 'shu narxda qo'shish' / 'nastroykadan sozlash kerak' tugmalari
    bilan xabar yuboradi."""
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return

    decision_id = create_decision(
        {
            "shop_id": shop_id,
            "sale_id": sale_id,
            "product_id": product_id,
            "sku_id": sku_id,
            "sku_code": sku_code,
            "nomi": nomi,
            "your_price": your_price,
            "recommended_price": recommended_price,
            "ikpu": ikpu,
        }
    )

    text = (
        f"⚠️ Narx mos kelmadi: {sku_code} ({nomi})\n"
        f"Aksiya: '{sale_title}'\n"
        f"Sizning narxingiz: {your_price:,} so'm - rad etildi\n"
        f"Uzum tavsiyasi: {recommended_price:,} so'm (marja: {marja_foiz:.1f}%)"
    ).replace(",", " ")

    payload = {
        "chat_id": Config.TELEGRAM_CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Shu narxda qo'shish", "callback_data": f"add:{decision_id}"},
                    {"text": "⚙️ Nastroykadan sozlash kerak", "callback_data": f"skip:{decision_id}"},
                ]
            ]
        },
    }
    try:
        requests.post(f"{_api_base()}/sendMessage", json=payload)
    except Exception:
        pass


def _answer_callback(callback_query_id, text):
    try:
        requests.post(
            f"{_api_base()}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
        )
    except Exception:
        pass


def _edit_message(chat_id, message_id, text):
    try:
        requests.post(
            f"{_api_base()}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text},
        )
    except Exception:
        pass


def send_summary(added, nastroyka_only):
    """Bir sikl tugagach, qaysi SKU'lar aksiyaga qo'shilgani va qaysilari
    faqat nastroykadan narxi sozlanib, aksiyadan rad etilgani haqida bitta
    umumiy xabar yuboradi."""
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    if not added and not nastroyka_only:
        return

    lines = ["📋 Aksiya tekshiruvi yakunlandi"]

    lines.append(f"\n✅ Aksiyaga qo'shildi ({len(added)}):")
    if added:
        for item in added:
            lines.append(
                f"- {item['sku_code']} ({item['nomi']}) -> {item['price']:,} so'm "
                f"['{item['sale_title']}']".replace(",", " ")
            )
    else:
        lines.append("- yo'q")

    lines.append(f"\n⚙️ Faqat nastroykadan narx sozlandi, aksiyadan rad etildi ({len(nastroyka_only)}):")
    if nastroyka_only:
        for item in nastroyka_only:
            lines.append(
                f"- {item['sku_code']} ({item['nomi']}) -> {item['price']:,} so'm "
                f"['{item['sale_title']}']".replace(",", " ")
            )
    else:
        lines.append("- yo'q")

    send_text("\n".join(lines))


# Eslatma: tugma bosilishini qayta ishlash endi bot_listener.py'da - u doimiy
# (uzluksiz) ishlab, tugma bosilgan zahoti javob beradi. Bu yerda (main.py
# davriy sikli ichida) takror tekshirish qilinmaydi, aks holda ikkala jarayon
# bitta Telegram offset'ini bir vaqtda ishlatib, to'qnashib qolishi mumkin edi.
