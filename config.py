import os
from pathlib import Path

from dotenv import load_dotenv

# Aniq shu loyihaning o'z .env fayli, override=True bilan - aks holda bir
# nechta loyiha bitta jarayonda birlashtirilganda (masalan PythonAnywhere'da),
# avval yuklangan boshqa loyihaning o'zgaruvchilari ustidan yozilmay qolib,
# noto'g'ri qiymatlar (masalan boshqa loyihaning Telegram tokeni) ishlatilib
# ketishi mumkin edi.
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)


class Config:
    UZUM_API_BASE_URL = os.getenv(
        "UZUM_API_BASE_URL", "https://api-seller.uzum.uz/api/seller"
    )
    UZUM_OAUTH_URL = os.getenv("UZUM_OAUTH_URL", "https://api-seller.uzum.uz/api/oauth/token")
    UZUM_MARKETING_BASE_URL = os.getenv(
        "UZUM_MARKETING_BASE_URL", "https://api.uzum.uz/api/seller/shop"
    )
    UZUM_API_KEY = os.getenv("UZUM_API_KEY")
    UZUM_USERNAME = os.getenv("UZUM_USERNAME")
    UZUM_PASSWORD = os.getenv("UZUM_PASSWORD")
    UZUM_SHOP_IDS = [
        shop_id.strip()
        for shop_id in os.getenv("UZUM_SHOP_IDS", "").split(",")
        if shop_id.strip()
    ]

    ERP_DB_PATH = os.getenv("ERP_DB_PATH", r"C:\Users\VICTUS\Desktop\templates\erp.db")

    # Narx (Uzum talab qiladigan eng kichik chegirma foizidan past tushmasligi uchun
    # zaxira - masalan agar Uzum 1% talab qilsa, biz shuncha + shu qiymat qilib qo'yamiz)
    MIN_DISCOUNT_BUFFER_PERCENT = float(os.getenv("MIN_DISCOUNT_BUFFER_PERCENT", "0"))

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    # /run-check manzilini tashqaridan (cron-job.org) chaqirish uchun maxfiy token
    RUN_TOKEN = os.getenv("RUN_TOKEN")

    # Telegram webhook'ning haqiqatan Telegram'dan kelganini tekshirish uchun
    # maxfiy qiymat (setWebhook'da secret_token sifatida ro'yxatdan o'tkaziladi)
    TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET")


def validate():
    missing = []
    has_login = Config.UZUM_USERNAME and Config.UZUM_PASSWORD
    if not has_login and not Config.UZUM_API_KEY:
        missing.append("UZUM_USERNAME+UZUM_PASSWORD (yoki UZUM_API_KEY)")
    if not Config.UZUM_SHOP_IDS:
        missing.append("UZUM_SHOP_IDS")
    if missing:
        raise RuntimeError(
            f".env faylida quyidagilar to'ldirilmagan: {', '.join(missing)}"
        )
