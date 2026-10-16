import sqlite3

from config import Config

KOMISSIYA_FOIZ = 0.25
SOLIQ_FOIZ = 0.011


def get_prices_by_sku():
    """Mahalliy ERP bazasidan uzum_sku -> mahsulot ma'lumotlari (narx, tannarx va
    marja hisoblash uchun kerakli xarajatlar) lug'atini qaytaradi. Faqat uzum_sku
    to'ldirilgan mahsulotlar qaytariladi."""
    conn = sqlite3.connect(Config.ERP_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT uzum_sku, sotish_narxi, tannarx, logistika, boshqa_harajat, nomi, ikpu "
            "FROM mahsulotlar WHERE uzum_sku IS NOT NULL AND TRIM(uzum_sku) != ''"
        ).fetchall()
    finally:
        conn.close()

    prices = {}
    for row in rows:
        sku = row["uzum_sku"].strip()
        prices[sku] = {
            "sale_price": row["sotish_narxi"],
            "tannarx": row["tannarx"],
            "logistika": row["logistika"],
            "boshqa_harajat": row["boshqa_harajat"],
            "nomi": row["nomi"],
            "ikpu": (row["ikpu"] or "").strip() or None,
        }
    return prices


def hisobla_marja(narx, tannarx, logistika, boshqa_harajat):
    """ERP sahifasidagi bilan bir xil formula: komissiya+soliq+logistika+boshqa
    xarajatlarni ayirib, sof foyda foizini hisoblaydi."""
    komissiya = narx * KOMISSIYA_FOIZ
    soliq = narx * SOLIQ_FOIZ
    jami = komissiya + (logistika or 0) + soliq + (boshqa_harajat or 0)
    tushum = narx - jami
    foyda = tushum - (tannarx or 0)
    foyda_foiz = (foyda / narx * 100) if narx else 0
    return foyda, foyda_foiz
