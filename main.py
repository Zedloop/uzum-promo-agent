import time
from datetime import datetime

from config import Config, validate
from erp_client import get_prices_by_sku, hisobla_marja
from promo_client import PRICE_MARKUP, PromoClient
from telegram_notify import notify_price_rejected, send_summary

# Faqat "Amal qiladi" (haqiqatan boshlangan) aksiyalarga qo'shishga urinamiz.
# "CREATED" statusi "Rejalandi" (hali boshlanmagan, kelajakdagi) aksiyani
# bildiradi - bunga hozir qo'shib bo'lmaydi, shuning uchun kerak emas.
ACTIVE_STATUSES = {"ACTIVE"}


def process_shop(shop_id, prices_by_sku, added, nastroyka_only):
    client = PromoClient(shop_id)
    sales = client.list_sales()

    for sale in sales:
        status = sale.get("status")
        if status not in ACTIVE_STATUSES:
            continue

        sale_id = sale.get("id")
        title = sale.get("title")
        suitable = client.get_suitable_products(sale_id)
        items = suitable.get("content") if isinstance(suitable, dict) else suitable
        if not items:
            print(f"[{shop_id}] '{title}' (id={sale_id}): mos mahsulot umuman yo'q.")
            continue

        qoshildi = 0
        for product in items:
            product_id = product.get("productId")

            skus = client.get_product_skus(sale_id, product_id)
            time.sleep(0.3)

            for sku in skus:
                sku_code = sku.get("skuTitle")
                sku_id = sku.get("skuId")
                price_info = prices_by_sku.get(sku_code)
                if not price_info or not price_info["sale_price"]:
                    continue

                new_price = int(price_info["sale_price"])
                item = {"productId": product_id, "skuList": [{"skuId": sku_id, "newSalePrice": new_price}]}

                # Aksiyaga qo'shishdan oldin, nastroykadan mahsulotning asosiy
                # Narx VA Sotish narxini ikkisini ham ERP narxi+50000 ga
                # tenglashtirib chiqib ketamiz (chegirmasiz, oddiy narx sifatida
                # saqlanadi) - shunda mahsulot "aynan shu narxda sotilgan" degan
                # tarix hosil bo'ladi. Faqat shundan KEYIN, aksiyaga haqiqiy ERP
                # narxida (past narxda) qo'shishga urinamiz - aynan shu farq
                # Uzum uchun haqiqiy chegirma bo'lib ko'rinadi.
                try:
                    client.update_sku_price(
                        product_id, sku_id, new_price + PRICE_MARKUP, ikpu=price_info.get("ikpu"), markup=0
                    )
                except Exception as e:
                    print(f"[{shop_id}] '{title}': {sku_code} - narxni nastroykadan sozlashda xato: {e}")

                # Har bir mahsulotni alohida yuboramiz - shunda Uzum bittasini
                # (masalan talab qilingan chegirmaga mos kelmasa) rad etsa ham,
                # qolganlari baribir qo'shiladi.
                try:
                    client.add_products(sale_id, [item])
                    print(f"[{shop_id}] '{title}': {sku_code} ({price_info['nomi']}) -> {new_price} qo'shildi.")
                    qoshildi += 1
                    added.append(
                        {"sku_code": sku_code, "nomi": price_info["nomi"], "price": new_price, "sale_title": title}
                    )
                except Exception as e:
                    print(
                        f"[{shop_id}] '{title}': {sku_code} ({price_info['nomi']}) -> {new_price} "
                        f"RAD ETILDI - {e}"
                    )
                    recommended_price = sku.get("recommendedPrice") or sku.get("currentSellPrice") or 0
                    _, marja_foiz = hisobla_marja(
                        recommended_price,
                        price_info["tannarx"],
                        price_info["logistika"],
                        price_info["boshqa_harajat"],
                    )
                    notify_price_rejected(
                        shop_id=shop_id,
                        sale_id=sale_id,
                        sale_title=title,
                        product_id=product_id,
                        sku_id=sku_id,
                        sku_code=sku_code,
                        nomi=price_info["nomi"],
                        your_price=new_price,
                        recommended_price=int(recommended_price),
                        marja_foiz=marja_foiz,
                        ikpu=price_info.get("ikpu"),
                    )
                    nastroyka_only.append(
                        {"sku_code": sku_code, "nomi": price_info["nomi"], "price": new_price, "sale_title": title}
                    )
                time.sleep(0.3)

        if qoshildi == 0:
            print(f"[{shop_id}] '{title}' (id={sale_id}): mos keluvchi ERP mahsuloti topilmadi.")


def run():
    validate()

    prices_by_sku = get_prices_by_sku()
    print(f"ERP'dan {len(prices_by_sku)} ta SKU narxi yuklandi.")

    added = []
    nastroyka_only = []

    for shop_id in Config.UZUM_SHOP_IDS:
        try:
            process_shop(shop_id, prices_by_sku, added, nastroyka_only)
        except Exception as e:
            print(f"[{shop_id}] Do'konni tekshirishda XATO - {e}")

    send_summary(added, nastroyka_only)


if __name__ == "__main__":
    print(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    run()
