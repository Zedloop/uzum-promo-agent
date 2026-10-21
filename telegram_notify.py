import os

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


def _som(value):
    return f"{int(value):,}".replace(",", " ")


def _short_codes(codes):
    """'BSST-MIKRAFONK9-2tamic', 'BSST-MIKRAFONK9-1iphone' -> '2tamic', '1iphone'
    (umumiy boshlanishni olib tashlaymiz - xabar o'qilishi oson bo'lsin)."""
    if len(codes) < 2:
        return {c: c for c in codes}
    prefix = os.path.commonprefix(codes)
    prefix = prefix[: prefix.rfind("-") + 1] if "-" in prefix else ""
    return {c: c[len(prefix):] or c for c in codes}


def _rejected_text(sale_title, skus, others, reason):
    short = _short_codes([s["sku_code"] for s in skus] + [o["sku_code"] for o in others])
    # Uzum tavsiyasi - aksiyada ruxsat etilgan eng yuqori narx. ERP narxi
    # undan baland bo'lgan bitta SKU ham butun mahsulotni rad ettiradi.
    qimmat = [s for s in skus if s["your_price"] > s["recommended_price"]]

    lines = [
        "⚠️ Aksiyaga qo'shilmadi - qaroringiz kerak",
        "",
        f"📦 {skus[0]['nomi']}",
        f"🏷 {sale_title}",
        "",
    ]
    if qimmat:
        nomlar = ", ".join(short[s["sku_code"]] for s in qimmat)
        lines.append(f"❗️ Sabab: {nomlar} - ERP narxi Uzum ruxsat bergan narxdan qimmat.")
    elif others:
        lines.append("❗️ Sabab: ba'zi SKU'lar ERP'da yo'q, ular chegirmasiz qolib ketdi.")
    elif reason:
        lines.append(f"❗️ Sabab: {reason}")

    lines += ["", "ERP narxi → Uzum ruxsat bergan narx:"]
    for s in skus:
        belgi = "❌" if s in qimmat else "✅"
        lines.append(
            f"{belgi} {short[s['sku_code']]}: {_som(s['your_price'])} → {_som(s['recommended_price'])} so'm"
            f" (marja {s['marja_foiz']:.0f}%)"
        )
    for o in others:
        lines.append(f"➖ {short[o['sku_code']]}: ERP'da yo'q → {_som(o['recommended_price'])} so'm")

    lines += [
        "",
        "✅ Qo'shish - ✅ SKU'lar ERP narxida, ❌ va ➖ SKU'lar Uzum narxida aksiyaga qo'shiladi",
        "⚙️ Nastroyka - aksiyaga qo'shilmaydi, Sotish narxi ERP narxida qoladi",
    ]
    return "\n".join(lines)


def notify_product_rejected(shop_id, sale_id, sale_title, product_id, skus, others=None, reason=None):
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

    text = _rejected_text(sale_title, skus, others or [], reason)

    payload = {
        "chat_id": Config.TELEGRAM_CHAT_ID,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Qo'shish", "callback_data": f"add:{decision_id}"},
                    {"text": "⚙️ Nastroyka (ERP narxi)", "callback_data": f"skip:{decision_id}"},
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
