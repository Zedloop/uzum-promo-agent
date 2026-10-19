"""/vozvrat buyrug'i: "Berishga tayyor" holatidagi qaytarishlar (akt raqamlari)."""
import time
from datetime import datetime, timedelta, timezone

from config import Config
from promo_client import PromoClient

TASHKENT = timezone(timedelta(hours=5))

READY_STATUS = "ASSEMBLED"  # Uzum sahifasida "Berishga tayyor" / "Готов к выдаче"
TYPE_NAMES = {"DEFECTED": "Yaroqsiz", "RETURN": "Qaytarilgan"}


def _dt(ms):
    return datetime.fromtimestamp(ms / 1000, TASHKENT)


def _format_item(item):
    turi = TYPE_NAMES.get(item.get("type"), item.get("type") or "?")
    line = f"• {item['id']} — {turi}"

    slot = item.get("timeSlotReservation") or {}
    if slot.get("timeFrom") and slot.get("timeTo"):
        line += f", berish: {_dt(slot['timeFrom']):%d.%m.%Y %H:%M}–{_dt(slot['timeTo']):%H:%M}"
    else:
        line += ", berish vaqti tanlanmagan"

    storage = item.get("paidStorage") or {}
    if storage.get("status") == "ACTIVE" and storage.get("startDate"):
        kun = int((time.time() * 1000 - storage["startDate"]) // 86_400_000) + 1
        line += f", ⏳ pulli saqlash: {kun} kun"
    return line


def build_returns_report():
    lines = ["📦 Berishga tayyor vozvratlar"]
    jami = 0
    for shop_id in Config.UZUM_SHOP_IDS:
        try:
            items = PromoClient(shop_id).list_returns()
        except Exception as e:
            lines.append(f"\n🏪 Do'kon {shop_id}: ❌ ma'lumot olib bo'lmadi ({e})")
            continue
        ready = [x for x in items if x.get("status") == READY_STATUS]
        if not ready:
            continue
        jami += len(ready)
        lines.append(f"\n🏪 Do'kon {shop_id} ({len(ready)} ta):")
        lines.extend(_format_item(x) for x in ready)

    if jami == 0 and len(lines) == 1:
        return "📦 Berishga tayyor vozvrat yo'q."
    lines.append(f"\nJami: {jami} ta")
    return "\n".join(lines)
