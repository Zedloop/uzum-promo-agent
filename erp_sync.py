"""Haftalik tekshiruv: ERP'dagi komissiya, "Буст заказов" va logistikani Uzum
haqiqatda olayotgan qiymatlarga tenglashtiradi va o'zgarishlarni Telegram'ga yozadi.

Hetzner'da systemd timer orqali har dushanba ertalab ishga tushadi:
    venv/bin/python erp_sync.py
"""
import time
from collections import Counter, defaultdict

import requests

from config import Config
from erp_client import get_prices_by_sku
from promo_client import PromoClient
from telegram_notify import send_text

LOGISTICS_DAYS = 90


def _uzum_commissions():
    """Kartochkadagi joriy komissiya va boost foizi, SKU bo'yicha. Boost
    mahsulot darajasida - faqat ACTIVE holatda hisoblanadi, aks holda 0."""
    result, groups, boosts = {}, {}, {}
    for shop_id in Config.UZUM_SHOP_IDS:
        for product in PromoClient(shop_id).list_products():
            cpo = product.get("cpoAdvertisementsDetails") or {}
            boost = float(cpo.get("commissionPercentage") or 0) if cpo.get("status") == "ACTIVE" else 0.0
            for sku in product.get("skuList") or []:
                code = sku.get("skuFullTitle")
                boosts[code] = boost
                if sku.get("commission") is not None:
                    result[code] = float(sku["commission"])
                groups[code] = sku.get("dimensionalGroup")
    return result, groups, boosts


def _uzum_logistics(groups):
    """Oxirgi 90 kunlik yetkazilgan buyurtmalarda 1 dona uchun eng ko'p
    olingan logistika. Logistika 0 bo'lgan buyurtmalar hisobga olinmaydi
    (hali yechilmagan yoki Uzum o'zi qoplagan). Sotilmagan SKU uchun - shu
    o'lcham guruhidagi boshqa SKU'larning qiymati."""
    now = int(time.time())
    orders = PromoClient(Config.UZUM_SHOP_IDS[0]).list_finance_orders(
        Config.UZUM_SHOP_IDS, now - LOGISTICS_DAYS * 86400, now
    )
    by_sku = defaultdict(Counter)
    for order in orders:
        qty = int(order.get("quantity") or 0)
        fee = order.get("logisticsFee") or 0
        if order["status"] == "TO_WITHDRAW" and qty and fee:
            by_sku[order["product"]["skuTitle"]][round(fee / qty)] += 1

    by_group = defaultdict(Counter)
    for sku, fees in by_sku.items():
        by_group[groups.get(sku)][fees.most_common(1)[0][0]] += sum(fees.values())

    result = {sku: fees.most_common(1)[0][0] for sku, fees in by_sku.items()}
    for sku, group in groups.items():
        if sku not in result and by_group.get(group):
            result[sku] = by_group[group].most_common(1)[0][0]
    return result


def _fmt(value):
    return f"{value:g}"


def run():
    erp = get_prices_by_sku()
    commissions, groups, boosts = _uzum_commissions()
    logistics = _uzum_logistics(groups)

    updates, lines = {}, []
    for sku, info in sorted(erp.items()):
        new_k, new_l = commissions.get(sku), logistics.get(sku)
        old_k, old_l = info.get("komissiya_foiz"), info.get("logistika")
        change = {}
        if new_k is not None and (old_k is None or abs(float(old_k) - new_k) > 0.01):
            change["komissiya_foiz"] = new_k
            lines.append(f"• {sku}: komissiya {_fmt(float(old_k or 0))}% → {_fmt(new_k)}%")
        if new_l is not None and (old_l is None or abs(float(old_l) - new_l) > 0.5):
            change["logistika"] = new_l
            lines.append(f"• {sku}: logistika {int(old_l or 0):,} → {int(new_l):,} so'm".replace(",", " "))
        new_b, old_b = boosts.get(sku), info.get("boost_foiz")
        if new_b is not None and (old_b is None or abs(float(old_b) - new_b) > 0.01):
            change["boost_foiz"] = new_b
            lines.append(f"• {sku}: boost {_fmt(float(old_b or 0))}% → {_fmt(new_b)}%")
        if change:
            updates[sku] = change

    if not updates:
        print("ERP komissiya, boost va logistikasi Uzum bilan bir xil - o'zgarish yo'q.")
        return

    url = Config.ERP_API_URL.replace("/api/uzum-prices", "/api/uzum-costs")
    resp = requests.post(url, params={"token": Config.ERP_API_TOKEN}, json=updates, timeout=60)
    resp.raise_for_status()
    print(f"ERP yangilandi: {resp.json()}")
    send_text(
        "🔄 Uzum komissiya/boost/logistikasi o'zgargan - ERP yangilandi:\n"
        + "\n".join(lines)
        + "\n\nERP'dagi foyda va bot marjasi endi shu qiymatlar bilan hisoblanadi."
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        send_text(f"❌ Haftalik komissiya/logistika tekshiruvida xato: {e}")
        raise
