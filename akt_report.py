"""/akt buyrug'i: "Yaratilgan" holatidagi ta'minlash aktlarini PDF qilib yuborish."""
import time
from datetime import datetime, timedelta, timezone

from config import Config
from promo_client import PromoClient
from telegram_notify import send_document, send_text

TASHKENT = timezone(timedelta(hours=5))
CREATED_STATUS = "CREATED"  # Uzum sahifasida "Yaratilgan"


def _dt(ms):
    return datetime.fromtimestamp(ms / 1000, TASHKENT)


def _display_number(invoice):
    # invoiceNumber oxirida shtrix-kodning nazorat raqami bor
    # (1100040799816) - sahifada esa usiz ko'rsatiladi (110004079981).
    number = str(invoice.get("invoiceNumber") or invoice["id"])
    return number[:-1] if len(number) == 13 else number


def _caption(shop_id, invoice):
    lines = [f"🏪 Do'kon {shop_id} · Akt {_display_number(invoice)}"]
    slot = invoice.get("timeSlotReservation") or {}
    if slot.get("timeFrom") and slot.get("timeTo"):
        lines.append(f"🕒 Ta'minlash vaqti: {_dt(slot['timeFrom']):%d.%m.%Y %H:%M}–{_dt(slot['timeTo']):%H:%M}")
    else:
        lines.append("🕒 Ta'minlash vaqti tanlanmagan")
    lines.append(f"📦 {invoice.get('totalToStock') or 0} dona · {int(invoice.get('fullPrice') or 0):,} so'm".replace(",", " "))
    stock = (invoice.get("stock") or {}).get("title")
    if stock:
        lines.append(f"📍 {stock}")
    return "\n".join(lines)


def send_created_acts():
    yuborildi = 0
    for shop_id in Config.UZUM_SHOP_IDS:
        client = PromoClient(shop_id)
        try:
            invoices = client.list_invoices()
        except Exception as e:
            send_text(f"❌ Do'kon {shop_id}: aktlar ro'yxatini olib bo'lmadi ({e})")
            continue
        for invoice in invoices:
            if (invoice.get("invoiceStatus") or {}).get("value") != CREATED_STATUS:
                continue
            # Ta'minlash vaqti o'tib ketgan aktlar endi kerak emas
            slot_end = (invoice.get("timeSlotReservation") or {}).get("timeTo")
            if slot_end and slot_end < time.time() * 1000:
                continue
            number = _display_number(invoice)
            try:
                pdf = client.get_invoice_pdf(invoice["id"])
                send_document(f"akt_{number}.pdf", pdf, _caption(shop_id, invoice))
                yuborildi += 1
            except Exception as e:
                send_text(f"❌ Do'kon {shop_id}, akt {number}: PDF'ni olib bo'lmadi ({e})")

    if yuborildi == 0:
        send_text("📄 Yaratilgan akt yo'q.")
