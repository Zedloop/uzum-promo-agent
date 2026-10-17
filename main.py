import time
from datetime import datetime

from config import Config, validate
from erp_client import get_prices_by_sku, hisobla_marja
from promo_client import PromoClient
from telegram_notify import notify_product_rejected, send_summary

# Faqat "Amal qiladi" (haqiqatan boshlangan) aksiyalarga qo'shishga urinamiz.
# "CREATED" statusi "Rejalandi" (hali boshlanmagan, kelajakdagi) aksiyani
# bildiradi - bunga hozir qo'shib bo'lmaydi, shuning uchun kerak emas.
ACTIVE_STATUSES = {"ACTIVE"}


def process_shop(shop_id, prices_by_sku, added, nastroyka_only, bogsiz_unlinked):
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

            # Mahsulotning ERP'da bog'langan (mos) SKU'larini yig'amiz - Uzum
            # bitta mahsulotning BARCHA SKU variantlarini (masalan fleshkaning
            # 16/32/64/128 GB o'lchamlari) bitta so'rovda birga talab qiladi,
            # shuning uchun SKU-SKU emas, MAHSULOT bo'yicha ishlaymiz.
            matched = []
            for sku in skus:
                sku_code = sku.get("skuTitle")
                sku_id = sku.get("skuId")
                price_info = prices_by_sku.get(sku_code)
                if not price_info:
                    bogsiz_unlinked.add((shop_id, sku_code))
                    continue
                if not price_info["sale_price"]:
                    continue
                matched.append(
                    {
                        "sku_id": sku_id,
                        "sku_code": sku_code,
                        "nomi": price_info["nomi"],
                        "your_price": int(price_info["sale_price"]),
                        "recommended_price": int(sku.get("recommendedPrice") or sku.get("currentSellPrice") or 0),
                        "ikpu": price_info.get("ikpu"),
                        "tannarx": price_info["tannarx"],
                        "logistika": price_info["logistika"],
                        "boshqa_harajat": price_info["boshqa_harajat"],
                    }
                )

            if not matched:
                continue

            # Aksiyaga qo'shishdan oldin, nastroykadan mos SKU'larning asosiy
            # Narx VA Sotish narxini ikkisini ham ERP narxi+50000 ga
            # tenglashtirib chiqib ketamiz (chegirmasiz, oddiy narx sifatida
            # saqlanadi) - shunda mahsulot "aynan shu narxda sotilgan" degan
            # tarix hosil bo'ladi. Qolgan (ERP'da bog'lanmagan) SKU'lar
            # o'zgarishsiz qoldiriladi.
            sku_updates = {m["sku_id"]: {"new_sell_price": m["your_price"], "ikpu": m["ikpu"]} for m in matched}
            nastroyka_muvaffaqiyatli = False
            try:
                client.update_product_skus(product_id, sku_updates)
                nastroyka_muvaffaqiyatli = True
            except Exception as e:
                print(f"[{shop_id}] '{title}': productId={product_id} - nastroykadan sozlashda xato: {e}")

            # Aksiyaga qo'shish so'rovida ham BARCHA SKU'lar bo'lishi shart -
            # mos kelganlari ERP narxida, qolganlari joriy (o'zgarishsiz)
            # narxida.
            sku_list_payload = []
            for sku in skus:
                sku_id = sku.get("skuId")
                m = next((x for x in matched if x["sku_id"] == sku_id), None)
                if m:
                    sku_list_payload.append({"skuId": sku_id, "newSalePrice": m["your_price"]})
                else:
                    sku_list_payload.append(
                        {"skuId": sku_id, "newSalePrice": int(sku.get("currentSellPrice") or 0)}
                    )

            item = {"productId": product_id, "skuList": sku_list_payload}
            try:
                client.add_products(sale_id, [item])
                for m in matched:
                    print(f"[{shop_id}] '{title}': {m['sku_code']} ({m['nomi']}) -> {m['your_price']} qo'shildi.")
                    added.append(
                        {"sku_code": m["sku_code"], "nomi": m["nomi"], "price": m["your_price"], "sale_title": title}
                    )
                qoshildi += 1
            except Exception as e:
                print(f"[{shop_id}] '{title}': productId={product_id} RAD ETILDI - {e}")
                for m in matched:
                    _, m["marja_foiz"] = hisobla_marja(
                        m["recommended_price"], m["tannarx"], m["logistika"], m["boshqa_harajat"]
                    )
                notify_product_rejected(
                    shop_id=shop_id,
                    sale_id=sale_id,
                    sale_title=title,
                    product_id=product_id,
                    skus=matched,
                )
                # Faqat nastroyka HAQIQATAN muvaffaqiyatli bo'lgan hollardagina
                # "narx sozlandi" ro'yxatiga qo'shamiz - aks holda hech narsa
                # o'zgarmagan bo'lsa ham hisobotda "narx sozlandi" deb
                # noto'g'ri ko'rsatilib qolardi.
                if nastroyka_muvaffaqiyatli:
                    for m in matched:
                        nastroyka_only.append(
                            {"sku_code": m["sku_code"], "nomi": m["nomi"], "price": m["your_price"], "sale_title": title}
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
    bogsiz_unlinked = set()

    for shop_id in Config.UZUM_SHOP_IDS:
        try:
            process_shop(shop_id, prices_by_sku, added, nastroyka_only, bogsiz_unlinked)
        except Exception as e:
            print(f"[{shop_id}] Do'konni tekshirishda XATO - {e}")

    send_summary(added, nastroyka_only, sorted(bogsiz_unlinked))


if __name__ == "__main__":
    print(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    run()
