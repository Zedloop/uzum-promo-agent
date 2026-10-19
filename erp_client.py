import requests

from config import Config

KOMISSIYA_FOIZ = 0.25
SOLIQ_FOIZ = 0.011


def get_prices_by_sku():
    """ERP (boshqa serverda, PythonAnywhere'da ishlaydi) narxlarini uning
    /api/uzum-prices manzilidan oladi - ERP shu serverning o'zida emas,
    shuning uchun lokal faylni to'g'ridan-to'g'ri o'qib bo'lmaydi."""
    response = requests.get(
        Config.ERP_API_URL,
        params={"token": Config.ERP_API_TOKEN},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


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
