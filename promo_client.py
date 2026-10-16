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

    def get_product(self, product_id):
        url = f"{self.product_base_url}/shop/{self.shop_id}/product"
        response = self._request("GET", url, params={"productId": product_id})
        return response.json()

    def update_sku_price(self, product_id, sku_id, new_sell_price, ikpu=None, markup=PRICE_MARKUP):
        """Mahsulotning asosiy 'Narx/Chegirma' sozlamalarini yangilaydi, shunda
        'Sotish narxi' = new_sell_price bo'ladi (Narx = new_sell_price + markup,
        Chegirma = markup). Boshqa barcha maydonlar (barcode, dimensions,
        characteristics) Uzum'dan olingan joriy holatidan O'ZGARTIRILMASDAN
        qaytarib yuboriladi - shunday qilib mahsulot ma'lumotlari buzilmaydi.

        'ikpu' (soliq kodi) alohida beriladi, chunki Uzum'ning GET /product
        so'rovi bu maydonni hech qachon to'g'ri ko'rsatmaydi (hatto
        muvaffaqiyatli saqlangandan keyin ham doim null qaytaradi - bu
        Uzum tomonidagi alohida xususiyat/xato). Shuning uchun ikpu qiymati
        mahalliy ERP bazasidan (foydalanuvchi bir marta qo'lda kiritgan)
        olinishi kerak."""
        product = self.get_product(product_id)

        sku_list = product.get("skuList", [])
        target = next((s for s in sku_list if s.get("skuId") == sku_id), None)
        if target is None:
            raise ValueError(f"productId={product_id} ichida skuId={sku_id} topilmadi")

        if not ikpu:
            raise ValueError(
                "IKPU (soliq kodi) ERP'da to'ldirilmagan - bu mahsulotda narxni "
                "avtomatik sozlab bo'lmaydi."
            )

        # "skuForProduct" - mahsulot nomi uchun SKU qisqa qismi (masalan
        # "BYKSHOP-VIZITKA" to'liq nomidan faqat "VIZITKA" qismi kerak -
        # do'kon prefiksidan keyingi, birinchi "-" belgisidan keyingi qism).
        full_title = target.get("skuFullTitle") or product.get("skuTitle", "")
        sku_for_product = full_title.split("-", 1)[1] if "-" in full_title else full_title

        new_full_price = int(new_sell_price) + markup
        payload = {
            "productId": product_id,
            "skuForProduct": sku_for_product,
            "skuList": [
                {
                    "id": target.get("skuId"),
                    "fullPrice": new_full_price,
                    "sellPrice": int(new_sell_price),
                    "ikpu": ikpu,
                    "skuTitle": target.get("skuFullTitle") or target.get("skuTitle"),
                    "barcode": target.get("barcode"),
                    "dimensions": target.get("skuDimension"),
                    "skuCharacteristicList": target.get("characteristicsList", []),
                    "status": None,
                }
            ],
            "skuTitlesForCustomCharacteristics": [],
        }

        url = f"{self.product_base_url}/shop/{self.shop_id}/product/sendSkuData"
        response = self._request("POST", url, json=payload)
        return response.json()
