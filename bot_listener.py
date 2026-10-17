import time
import traceback

import requests

from config import Config, validate
from decision_store import get_offset, set_offset
from telegram_notify import _answer_callback, _edit_message, _api_base
from decision_store import pop_decision
from promo_client import PromoClient

SKIP_MARKUP = 49990  # "Nastroykadan sozlash kerak" tugmasi: Narx = ERP narxi + shuncha, Chegirma = shuncha


def _handle_callback(callback):
    data = callback.get("data", "")
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]
    original_text = callback["message"]["text"]

    if ":" not in data:
        return
    action, decision_id = data.split(":", 1)
    decision = pop_decision(decision_id)
    if not decision:
        _answer_callback(callback["id"], "Bu qaror allaqachon ko'rib chiqilgan.")
        return

    skus = decision["skus"]

    if action == "add":
        try:
            client = PromoClient(decision["shop_id"])
            # Uzum bitta mahsulotning BARCHA SKU'larini birga talab qiladi -
            # shuning uchun joriy to'liq SKU ro'yxatini qayta olib, mos
            # kelganlarini tavsiya narxida, qolganlarini o'zgarishsiz qo'shamiz.
            all_skus = client.get_product_skus(decision["sale_id"], decision["product_id"])
            sku_list_payload = []
            for sku in all_skus:
                sku_id = sku.get("skuId")
                match = next((s for s in skus if s["sku_id"] == sku_id), None)
                if match:
                    sku_list_payload.append({"skuId": sku_id, "newSalePrice": match["recommended_price"]})
                else:
                    sku_list_payload.append({"skuId": sku_id, "newSalePrice": int(sku.get("currentSellPrice") or 0)})

            client.add_products(
                decision["sale_id"],
                [{"productId": decision["product_id"], "skuList": sku_list_payload}],
            )
            _answer_callback(callback["id"], "Qo'shildi ✅")
            _edit_message(chat_id, message_id, f"{original_text}\n\n✅ Uzum tavsiya narxlarida qo'shildi.")
        except Exception as e:
            _answer_callback(callback["id"], "Xato yuz berdi")
            _edit_message(chat_id, message_id, f"{original_text}\n\n❌ Qo'shishda xato: {e}")
    elif action == "skip":
        try:
            client = PromoClient(decision["shop_id"])
            # Narx = ERP narxi + 49990, Chegirma = 49990 - shunda Sotish narxi
            # o'z-o'zidan ERP narxiga teng bo'ladi. Mahsulotning qolgan
            # (ERP'da bog'lanmagan) SKU'lari o'zgarishsiz qoldiriladi.
            sku_updates = {s["sku_id"]: {"new_sell_price": s["your_price"], "ikpu": s.get("ikpu")} for s in skus}
            client.update_product_skus(decision["product_id"], sku_updates, markup=SKIP_MARKUP)
            # Aksiyaga qo'shishga darhol urinmaymiz - narx shunday sozlangan
            # holida qoladi, aksiyaga qo'shish keyingi davriy tekshiruvda
            # (yoki foydalanuvchi o'zi) amalga oshiriladi.
            _answer_callback(callback["id"], "Narx sozlandi ✅")
            _edit_message(
                chat_id,
                message_id,
                f"{original_text}\n\n⚙️ Uzum'dagi mahsulot narxlari moslashtirildi.",
            )
        except Exception as e:
            _answer_callback(callback["id"], "Xato yuz berdi")
            _edit_message(chat_id, message_id, f"{original_text}\n\n❌ Narxni sozlashda xato: {e}")


def listen():
    validate()
    print("Promo-bot tinglovchisi ishga tushdi, tugma bosilishini kutmoqda...")
    offset = get_offset()

    while True:
        try:
            resp = requests.get(
                f"{_api_base()}/getUpdates", params={"offset": offset, "timeout": 30}
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except Exception as e:
            print(f"getUpdates xatosi: {e}")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            callback = update.get("callback_query")
            if callback:
                print(f"Tugma bosildi: {callback.get('data')}")
                _handle_callback(callback)

        if updates:
            set_offset(offset)


if __name__ == "__main__":
    while True:
        try:
            listen()
        except Exception as e:
            print(f"Tinglovchi butunlay yiqildi, 10 soniyadan keyin qayta boshlanadi: {e}")
            traceback.print_exc()
            time.sleep(10)
