import requests

from config import Config
from decision_store import create_decision

API_BASE_TMPL = "https://api.telegram.org/bot{token}"


def _api_base():
    return API_BASE_TMPL.format(token=Config.TELEGRAM_BOT_TOKEN)


def send_text(text):
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    # Telegram bitta xabarda 4096 belgidan ko'pini qabul qilmaydi - uzun
    # matnni qatorlar bo'yicha bo'laklarga ajratib yuboramiz.
    chunks, current = [], ""
    for line in text.split("\n"):
        if current and len(current) + len(line) + 1 > 4000:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    chunks.append(current)
    for chunk in chunks:
        try:
            requests.post(
                f"{_api_base()}/sendMessage",
                json={"chat_id": Config.TELEGRAM_CHAT_ID, "text": chunk[:4096]}, timeout=30,
            )
        except Exception:
            pass


def send_with_buttons(text, buttons):
    """buttons: [(matn, callback_data), ...] - har biri alohida qatorda."""
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    keyboard = [[{"text": label, "callback_data": data}] for label, data in buttons]
    try:
        requests.post(
            f"{_api_base()}/sendMessage",
            json={"chat_id": Config.TELEGRAM_CHAT_ID, "text": text, "reply_markup": {"inline_keyboard": keyboard}},
            timeout=30,
        )
    except Exception:
        pass


def send_document(filename, content, caption=""):
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    requests.post(
        f"{_api_base()}/sendDocument",
        data={"chat_id": Config.TELEGRAM_CHAT_ID, "caption": caption[:1024]},
        files={"document": (filename, content, "application/pdf")},
        timeout=60,
    ).raise_for_status()


def notify_product_rejected(shop_id, sale_id, sale_title, product_id, skus):
    """Mahsulot (uning barcha mos SKU'lari bilan birga) aksiyadan rad
    etilganda, har bir SKU uchun Uzum tavsiya qilgan narx va marjani
    ko'rsatib, BITTA xabar bilan 'shu narxda qo'shish' / 'nastroykadan
    sozlash kerak' tugmalarini yuboradi - SKU-SKU alohida emas, chunki Uzum
    bitta mahsulotning barcha SKU'larini birgalikda qaror qilishni talab
    qiladi."""
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return

    decision_id = create_decision(
        {
            "shop_id": shop_id,
            "sale_id": sale_id,
            "product_id": product_id,
            "skus": skus,
        }
    )

    nomi = skus[0]["nomi"]
    lines = [f"⚠️ Narx mos kelmadi: {nomi}", f"Aksiya: '{sale_title}'"]
    for s in skus:
        lines.append(
            f"- {s['sku_code']}: sizning narxingiz {s['your_price']:,} so'm - rad etildi, "
            f"Uzum tavsiyasi {s['recommended_price']:,} so'm (marja: {s['marja_foiz']:.1f}%)".replace(",", " ")
        )
    text = "\n".join(lines)

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
        requests.post(f"{_api_base()}/sendMessage", json=payload, timeout=30)
    except Exception:
        pass


def _answer_callback(callback_query_id, text):
    try:
        requests.post(
            f"{_api_base()}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text}, timeout=30,
        )
    except Exception:
        pass


def _edit_message(chat_id, message_id, text, parse_mode=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        requests.post(f"{_api_base()}/editMessageText", json=payload, timeout=30)
    except Exception:
        pass


def send_summary(added, nastroyka_only, bogsiz_unlinked=None):
    """Bir sikl tugagach, qaysi SKU'lar aksiyaga qo'shilgani, qaysilari
    faqat nastroykadan narxi sozlanib aksiyadan rad etilgani, va qaysi
    SKU'lar ERP'da umuman bog'lanmagani haqida bitta umumiy xabar yuboradi."""
    bogsiz_unlinked = bogsiz_unlinked or []
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_CHAT_ID:
        return
    if not added and not nastroyka_only and not bogsiz_unlinked:
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

    if bogsiz_unlinked:
        lines.append(f"\n🔗 ERP'da bog'lanmagan SKU'lar ({len(bogsiz_unlinked)}) - Uzum sozlamalaridan bog'lang:")
        for shop_id, sku_code in bogsiz_unlinked:
            lines.append(f"- {sku_code} (do'kon {shop_id})")

    send_text("\n".join(lines))


# Eslatma: tugma bosilishini qayta ishlash endi bot_listener.py'da - u doimiy
# (uzluksiz) ishlab, tugma bosilgan zahoti javob beradi. Bu yerda (main.py
# davriy sikli ichida) takror tekshirish qilinmaydi, aks holda ikkala jarayon
# bitta Telegram offset'ini bir vaqtda ishlatib, to'qnashib qolishi mumkin edi.
