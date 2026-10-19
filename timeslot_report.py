"""/timeslot buyrug'i: oxirgi 2 haftalik sotuv bo'yicha qaysi SKU'ning
qoldig'i tugayapti va omborga yetkazib berish (time slot) kerakligi."""
import math
import time
from collections import defaultdict

from config import Config
from promo_client import PromoClient

PERIOD_DAYS = 14
URGENT_DAYS = 7    # shundan kam kunga yetsa - darhol time slot ilish kerak
SOON_DAYS = 14     # shundan kam kunga yetsa - tez orada kerak bo'ladi
TARGET_DAYS = 30   # tavsiya etilgan yetkazish miqdori shuncha kunga yetadigan qilib


def _sold_by_sku(client, now):
    orders = client.list_finance_orders(Config.UZUM_SHOP_IDS, now - PERIOD_DAYS * 86400, now)
    sold = defaultdict(int)
    for order in orders:
        # Bekor qilinganlarda quantity 0 bo'ladi - shuning uchun alohida
        # filtrlash shart emas, haqiqatan sotilgan soni olinadi.
        sold[order["product"]["skuId"]] += int(order.get("quantity") or 0)
    return sold


def _analyze_shop(shop_id, sold):
    rows = []
    for product in PromoClient(shop_id).list_products():
        for sku in product.get("skuList") or []:
            if sku.get("archived"):
                continue
            sold_qty = sold.get(sku.get("skuId"), 0)
            if sold_qty <= 0:
                continue
            stock = int(sku.get("quantityActive") or 0)
            daily = sold_qty / PERIOD_DAYS
            days_left = stock / daily
            if days_left >= SOON_DAYS:
                continue
            rows.append(
                {
                    "sku": sku.get("skuFullTitle") or sku.get("skuTitle"),
                    "sold": sold_qty,
                    "stock": stock,
                    "daily": daily,
                    "days_left": days_left,
                    "need": max(0, math.ceil(daily * TARGET_DAYS - stock)),
                }
            )
    rows.sort(key=lambda r: r["days_left"])
    return rows


def _line(r):
    if r["stock"] == 0:
        holat = "qoldiq 0"
    else:
        holat = f"qoldiq {r['stock']} ta, ~{r['days_left']:.0f} kunga yetadi"
    return (
        f"• {r['sku']} — {PERIOD_DAYS} kunda {r['sold']} ta sotildi (kuniga ~{r['daily']:.1f}), "
        f"{holat} → ~{r['need']} ta olib borish"
    )


def build_timeslot_report():
    now = int(time.time())
    sold = _sold_by_sku(PromoClient(Config.UZUM_SHOP_IDS[0]), now)

    lines = [f"📊 Time slot tahlili (oxirgi {PERIOD_DAYS} kunlik sotuv bo'yicha)"]
    topildi = False
    for shop_id in Config.UZUM_SHOP_IDS:
        try:
            rows = _analyze_shop(shop_id, sold)
        except Exception as e:
            lines.append(f"\n🏪 Do'kon {shop_id}: ❌ ma'lumot olib bo'lmadi ({e})")
            continue
        if not rows:
            continue
        topildi = True
        lines.append(f"\n🏪 Do'kon {shop_id}:")
        groups = (
            ("🔴 Tugagan:", [r for r in rows if r["stock"] == 0]),
            (f"🟠 Time slot ilish kerak ({URGENT_DAYS} kundan kam qoldi):",
             [r for r in rows if r["stock"] > 0 and r["days_left"] < URGENT_DAYS]),
            (f"🟡 Tez orada kerak bo'ladi ({URGENT_DAYS}–{SOON_DAYS} kun):",
             [r for r in rows if r["days_left"] >= URGENT_DAYS]),
        )
        for title, group in groups:
            if group:
                lines.append(title)
                lines.extend(_line(r) for r in group)

    if not topildi and len(lines) == 1:
        return f"✅ Hamma sotilayotgan mahsulotlar qoldig'i {SOON_DAYS} kundan ko'proqqa yetadi - hozircha time slot shart emas."
    lines.append(f"\nℹ️ Olib borish miqdori ~{TARGET_DAYS} kunlik sotuvga yetadigan qilib hisoblangan.")
    return "\n".join(lines)
