"""/promocode buyrug'i: bugun amal qiladigan, bir martalik 50% promokod yaratish
(Uzum sahifasidagi "Создание промокода" formasi bilan bir xil sozlamalar)."""
import secrets
import string
import time
from datetime import datetime, timedelta, timezone

from config import Config
from promo_client import PromoClient

TASHKENT = timezone(timedelta(hours=5))
DEFAULT_SHOP_ID = "74301"  # DM SHOP
DISCOUNT_PERCENT = 50  # foiz yozilmasa
MIN_PERCENT = 2  # Uzum 1% ni "Validation failed" deb rad etadi
# Uzum: chegirma mahsulot narxining yarmidan oshsa promokod ishlamaydi
MAX_PERCENT = 50
QUANTITY_USE = 1
CODE_LENGTH = 8
ALPHABET = string.ascii_uppercase + string.digits


def _end_of_today_ms():
    now = datetime.now(TASHKENT)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    return int(end.timestamp() * 1000)


def parse_percent(text):
    """'/promocode 30' yoki '/promocode 30%' dan foizni oladi. Berilmasa -
    DISCOUNT_PERCENT; noto'g'ri bo'lsa ValueError."""
    if not text:
        return DISCOUNT_PERCENT
    value = text.strip().rstrip("%").strip()
    if not value.isdigit() or not (MIN_PERCENT <= int(value) <= MAX_PERCENT):
        raise ValueError(f"Foiz {MIN_PERCENT} dan {MAX_PERCENT} gacha butun son bo'lishi kerak, masalan: /promocode 30")
    return int(value)


def create_promocode(shop_id=DEFAULT_SHOP_ID, shop_title=None, discount_percent=DISCOUNT_PERCENT):
    if str(shop_id) not in Config.UZUM_SHOP_IDS:
        raise ValueError(f"Do'kon {shop_id} ro'yxatda yo'q ({', '.join(Config.UZUM_SHOP_IDS)})")

    client = PromoClient(shop_id)
    prefix = client.get_promocode_prefix()

    # Sayt "Сгенерировать" tugmasi kabi tasodifiy kod - band bo'lmaganini
    # tekshirib olamiz
    for _ in range(5):
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if not client.promocode_exists(f"{prefix}-{code}"):
            break
    else:
        raise RuntimeError("Bo'sh promokod nomini topib bo'lmadi")

    end_ms = _end_of_today_ms()
    client.create_promocode(code, int(time.time() * 1000), end_ms, discount_percent, QUANTITY_USE)

    full_code = f"{prefix}-{code}"
    return (
        f"🎟 Yangi promokod: {full_code}\n"
        f"🏪 {shop_title or shop_id} ({shop_id}) · butun do'kon uchun\n"
        f"💸 Chegirma: {discount_percent}%\n"
        f"📅 Amal qiladi: {datetime.fromtimestamp(end_ms / 1000, TASHKENT):%d.%m.%Y} 23:59 gacha\n"
        f"🔢 {QUANTITY_USE} marta ishlatiladi"
    ), full_code
