import requests

from config import Config

STANDART_KOMISSIYA_FOIZ = 25  # ERP'da mahsulotga komissiya kiritilmagan bo'lsa
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


def hisobla_marja(narx, tannarx, logistika, boshqa_harajat, komissiya_foiz=None):
    """ERP sahifasidagi bilan bir xil formula: komissiya+soliq+logistika+boshqa
    xarajatlarni ayirib, sof foyda foizini hisoblaydi. komissiya_foiz - ERP'da
    shu mahsulotga kiritilgan foiz (masalan 25)."""
    if komissiya_foiz is None:
        komissiya_foiz = STANDART_KOMISSIYA_FOIZ
    komissiya = narx * komissiya_foiz / 100
    soliq = narx * SOLIQ_FOIZ
    jami = komissiya + (logistika or 0) + soliq + (boshqa_harajat or 0)
    tushum = narx - jami
    foyda = tushum - (tannarx or 0)
    foyda_foiz = (foyda / narx * 100) if narx else 0
    return foyda, foyda_foiz
