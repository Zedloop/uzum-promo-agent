import json
import os
import time

import requests

from config import Config

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "data", "uzum_token.json")
EXPIRY_SAFETY_MARGIN = 3600  # muddati tugashiga kamida 1 soat qolganda ham yangilaymiz

# seller.uzum.uz frontend'ida ochiq (barcha foydalanuvchilar uchun bir xil)
# statik "mijoz ilovasi" kaliti - foydalanuvchi login/paroliga aloqasi yo'q,
# OAuth2 "password" grant so'rovida shart bo'lgani uchun kerak.
CLIENT_AUTH_HEADER = "Basic YjJiLWZyb250OmNsaWVudFNlY3JldA=="


def _load_cached_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_cached_token(access_token, expires_in):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"access_token": access_token, "obtained_at": time.time(), "expires_in": expires_in},
            f,
        )


def fetch_new_token():
    """Uzum seller login/parol orqali yangi access_token oladi (OAuth2 password grant)."""
    response = requests.post(
        Config.UZUM_OAUTH_URL,
        params={"referer": "https://seller.uzum.uz/"},
        headers={"Authorization": CLIENT_AUTH_HEADER},
        data={
            "grant_type": "password",
            "username": Config.UZUM_USERNAME,
            "password": Config.UZUM_PASSWORD,
        },
    )
    response.raise_for_status()
    data = response.json()
    _save_cached_token(data["access_token"], data["expires_in"])
    return data["access_token"]


def get_valid_token():
    """Amaldagi tokenni qaytaradi; agar muddati tugagan/tugay deb qolgan bo'lsa
    (yoki umuman topilmasa), login/parol orqali avtomatik yangisini oladi.
    Agar login/parol berilmagan bo'lsa, .env'dagi qo'lda kiritilgan tokenga
    (UZUM_API_KEY) qaytib tushadi - bu eski, avtomatik bo'lmagan usul."""
    if not (Config.UZUM_USERNAME and Config.UZUM_PASSWORD):
        return Config.UZUM_API_KEY

    cached = _load_cached_token()
    if cached:
        age = time.time() - cached["obtained_at"]
        if age < cached["expires_in"] - EXPIRY_SAFETY_MARGIN:
            return cached["access_token"]

    return fetch_new_token()
