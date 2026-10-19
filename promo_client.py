import base64

import requests

from config import Config
from uzum_auth import fetch_new_token, get_valid_token

PRICE_MARKUP = 50000  # "Narx" (fullPrice) har doim "Sotish narxi"dan shuncha yuqori qilib qo'yiladi


class PromoClient:
    """Uzum seller 'Marketing / Aksiyalar' (katta savdo) API'si bilan ishlash.

    DevTools orqali aniqlangan, rasmiy hujjatlashtirilmagan ichki so'rovlar:
    - GET  {base}/{shopId}/marketing/sales?page=&size=&saleType=ALL
    - GET  {base}/{shopId}/marketing/sales/{saleId}
    - GET  {base}/{shopId}/marketing/sales/{saleId}/suitable-products?search=&page=&size=&orderBy=
    - POST {base}/{shopId}/marketing/sales/{saleId}/products
    """

    def __init__(self, shop_id):
        self.shop_id = str(shop_id)
        self.base_url = Config.UZUM_MARKETING_BASE_URL.rstrip("/")
        self.product_base_url = Config.UZUM_API_BASE_URL.rstrip("/")
        self.session = requests.Session()
        self._set_auth_header(get_valid_token())

    def _set_auth_header(self, token):
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
        )

    def _request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 60)
        response = self.session.request(method, url, **kwargs)
        if response.status_code == 401 and Config.UZUM_USERNAME and Config.UZUM_PASSWORD:
            self._set_auth_header(fetch_new_token())
            response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response

    def list_sales(self, sale_type="ALL", page=0, size=24):
        url = f"{self.base_url}/{self.shop_id}/marketing/sales"
        response = self._request(
            "GET", url, params={"page": page, "size": size, "saleType": sale_type}
        )
        return response.json().get("payload", [])

    def get_sale(self, sale_id):
        url = f"{self.base_url}/{self.shop_id}/marketing/sales/{sale_id}"
        response = self._request("GET", url)
        return response.json().get("payload", {})

    def get_suitable_products(self, sale_id, page=0, size=100):
        url = f"{self.base_url}/{self.shop_id}/marketing/sales/{sale_id}/suitable-products"
        response = self._request(
            "GET",
            url,
            params={"search": "", "page": page, "size": size, "orderBy": "AVAILABLE_COUNT"},
        )
        return response.json().get("payload", [])

    def get_product_skus(self, sale_id, product_id):
        url = (
            f"{self.base_url}/{self.shop_id}/marketing/sales/{sale_id}"
            f"/suitable-products/{product_id}/sku"
        )
        response = self._request("GET", url)
        return response.json().get("payload", [])

    def add_products(self, sale_id, products):
        """products: [{"productId": ..., "skuList": [{"skuId": ..., "newSalePrice": ...}]}]"""
        url = f"{self.base_url}/{self.shop_id}/marketing/sales/{sale_id}/products"
        response = self._request("POST", url, json={"products": products})
        return response.json()

    def list_returns(self, size=100):
        """'Ta'minot -> Qaytarishlar' (Yaroqsiz / Qaytarilgan) ro'yxati -
        barcha sahifalarni yig'ib qaytaradi."""
        url = f"{self.product_base_url}/shop/{self.shop_id}/return"
        result = []
        page = 0
        while True:
            response = self._request("GET", url, params={"page": page, "size": size})
            items = response.json().get("payload") or []
            result.extend(items)
            if len(items) < size:
                return result
            page += 1

    def list_invoices(self, size=100):
        """'Ta'minlashlar -> FBO' ro'yxati (barcha sahifalar)."""
        url = f"{self.product_base_url}/shop/{self.shop_id}/invoice"
        result = []
        page = 0
        while True:
            items = self._request("GET", url, params={"page": page, "size": size}).json() or []
            result.extend(items)
            if len(items) < size:
                return result
            page += 1

    def get_invoice_pdf(self, invoice_id):
        """Ta'minlash aktining PDF fayli (bytes). Uzum uni JSON ichida base64
        ko'rinishida qaytaradi."""
        url = f"{self.product_base_url}/shop/{self.shop_id}/invoice/printInvoice"
        data = self._request("GET", url, params={"invoiceId": invoice_id}).json()
        return base64.b64decode(data["pdf"])

    def list_products(self, size=100):
        """Do'kondagi barcha mahsulotlar (SKU qoldiqlari bilan). Filtrsiz -
        aks holda qoldig'i tugagan mahsulotlar ro'yxatga tushmay qoladi."""
        url = f"{self.product_base_url}/shop/{self.shop_id}/product/getProducts"
        result = []
        page = 0
        while True:
            response = self._request(
                "GET",
                url,
                params={"searchQuery": "", "sortBy": "id", "order": "descending", "size": size, "page": page},
            )
            items = response.json().get("productList") or []
            result.extend(items)
            if len(items) < size:
                return result
            page += 1

    def list_finance_orders(self, shop_ids, date_from, date_to, size=100):
        """'Moliya -> Sotuvlar' ro'yxati. date_from/date_to - unix soniyalarda."""
        url = Config.UZUM_API_BASE_URL.split("/api/")[0] + "/api/v1/seller/finance/orders"
        params = {"shopIds": ",".join(map(str, shop_ids)), "dateFrom": date_from, "dateTo": date_to, "size": size}
        result = []
        page = 0
        while True:
            response = self._request("GET", url, params={**params, "page": page})
            data = response.json()
            items = data.get("items") or []
            result.extend(items)
            if len(items) < size or len(result) >= (data.get("total") or 0):
                return result
            page += 1

    def get_product(self, product_id):
        url = f"{self.product_base_url}/shop/{self.shop_id}/product"
        response = self._request("GET", url, params={"productId": product_id})
        return response.json()

    def update_product_skus(self, product_id, sku_updates, markup=PRICE_MARKUP):
        """Mahsulotning asosiy 'Narx/Chegirma' sozlamalarini yangilaydi.

        Uzum bitta mahsulotning BARCHA SKU variantlarini (masalan fleshkaning
        16/32/64/128 GB o'lchamlari) bitta so'rovda birga talab qiladi - faqat
        bittasini yuborsak "Not valid Sku Titles from SkuDto!" deb rad etadi.
        Shuning uchun bu metod mahsulotning barcha SKU'larini oladi:
        - 'sku_updates' ichida ko'rsatilgan SKU'lar uchun: Sotish narxi =
          yangi narx, Narx = yangi narx + markup (tarix yaratish).
        - Qolgan (o'zgartirilmaydigan) SKU'lar uchun: joriy narxi
          o'zgarishsiz qaytarib yuboriladi - ularga tegilmaydi.

        'sku_updates': {sku_id: {"new_sell_price": ..., "ikpu": ...}}
        """
        product = self.get_product(product_id)
        sku_list = product.get("skuList", [])
        if not sku_list:
            raise ValueError(f"productId={product_id} uchun SKU topilmadi")

        sku_entries = []
        for target in sku_list:
            sku_id = target.get("skuId")
            update = sku_updates.get(sku_id)

            if update is not None:
                if not update.get("ikpu"):
                    raise ValueError(
                        f"IKPU (soliq kodi) ERP'da to'ldirilmagan (skuId={sku_id}) - "
                        "bu mahsulotda narxni avtomatik sozlab bo'lmaydi."
                    )
                new_sell_price = int(update["new_sell_price"])
                full_price = new_sell_price + markup
                ikpu = update["ikpu"]
            else:
                # O'zgartirilmaydi - joriy narxi saqlanadi
                current_price = int(target.get("price") or 0)
                new_sell_price = current_price
                full_price = current_price
                ikpu = None

            # "skuForProduct" - Uzum'ning o'zi shu SKU uchun ishlatadigan qisqa
            # nom (masalan "DMSH-SNDKMIC-16" to'liq nomidan "16"). Bu
            # get_product javobida "skuTitle" sifatida to'g'ridan-to'g'ri
            # beriladi.
            sku_entries.append(
                {
                    "id": sku_id,
                    "fullPrice": full_price,
                    "sellPrice": new_sell_price,
                    "ikpu": ikpu,
                    "skuTitle": target.get("skuFullTitle") or target.get("skuTitle"),
                    "barcode": target.get("barcode"),
                    "dimensions": target.get("skuDimension"),
                    "skuCharacteristicList": target.get("characteristicsList", []),
                    "status": None,
                }
            )

        # Barcha SKU'lar bitta mahsulotga tegishli bo'lgani uchun
        # "skuForProduct" ham bittagina - birinchi (yoki o'zgartirilayotgan)
        # SKU'ning qisqa nomidan olamiz.
        changed_target = next(
            (s for s in sku_list if s.get("skuId") in sku_updates), sku_list[0]
        )
        sku_for_product = changed_target.get("skuTitle") or ""

        payload = {
            "productId": product_id,
            "skuForProduct": sku_for_product,
            "skuList": sku_entries,
            "skuTitlesForCustomCharacteristics": [],
        }

        url = f"{self.product_base_url}/shop/{self.shop_id}/product/sendSkuData"
        response = self._request("POST", url, json=payload)
        return response.json()
