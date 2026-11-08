"""Har bir Uzum to'lovi (oyiga 2 marta, ~20 mln) qanchasi tannarx, soliq va
sof foyda ekanini aniq hisoblaydi.

Uzum to'lov qaysi buyurtmalar uchun ekanini hech qayerda ko'rsatmaydi. Lekin
to'lov yaratilishi bilan unga kirgan buyurtmalar rasmiy moliyaviy hisobotda
"выведен" bo'ladi va "Выведено" ustunida summasi paydo bo'ladi. Shuning uchun
har kuni hisobotdan nusxa (snapshot) olamiz: yangi to'lovdan oldingi va keyingi
nusxalar farqi - aynan shu to'lovga kirgan buyurtmalar.

    Foyda = to'lov summasi - shu buyurtmalar tannarxi (ERP) - 1% soliq (sotuvdan)

Uzum xarajatlari (logistika, ombor, marketing) to'lovdan allaqachon ayirilgan:
ular = buyurtmalar uchun yozilgan summa - to'lov summasi.

systemd timer orqali har kuni ertalab ishga tushadi: venv/bin/python payout_profit.py
"""
import datetime as dt
import glob
import io
import json
import os
import time
import uuid

import openpyxl
import requests

from config import Config
from erp_client import get_prices_by_sku
from promo_client import PromoClient
from telegram_notify import send_report

TZ = dt.timezone(dt.timedelta(hours=5))
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SNAP_DIR = os.path.join(DATA_DIR, "fin_snapshots")
STATE_FILE = os.path.join(DATA_DIR, "payout_profit_state.json")
TAX_RATE = 0.01          # YaTT aylanma solig'i - tushumdan 1%
KEEP_SNAPSHOTS = 45      # kun


def _client():
    c = PromoClient(Config.UZUM_SHOP_IDS[0])
    return c, Config.UZUM_API_BASE_URL.split("/api/")[0] + "/api"


def _som(v):
    return f"{v:,.0f}".replace(",", " ")


# ---------- rasmiy hisobotni olish ----------

def _download_statement(c, base):
    """Kabinetdagi "Сформировать отчёт" bilan bir xil: FINANCIAL_STATEMENT_REPORT."""
    started = time.time() * 1000
    for attempt in range(6):
        r = c.session.post(base + "/seller/documents/create", timeout=60, json={
            "jobType": "FINANCIAL_STATEMENT_REPORT", "idempotencyKey": str(uuid.uuid4()),
            "contentType": "MS_EXCEL", "params": {}})
        if r.status_code != 429:  # 429 - shu turdagi hisobot allaqachon navbatda
            r.raise_for_status()
            break
        time.sleep(30)
    for _ in range(40):
        time.sleep(10)
        reqs = c.session.get(base + "/seller/documents/requests", timeout=30).json()["payload"]["requests"]
        done = [q for q in reqs if q["jobType"] == "FINANCIAL_STATEMENT_REPORT" and q["status"] == "COMPLETED"
                and (q.get("dateStarted") or 0) >= started - 120_000]
        if done:
            link = max(done, key=lambda q: q["dateStarted"])["result"]["link"]
            return requests.get(link, timeout=120).content
    raise RuntimeError("Uzum moliyaviy hisobotni 7 daqiqada tayyorlamadi")


def _parse_orders(xlsx_bytes):
    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = next(w for w in wb.worksheets if "Доходы" in w.title)
    col, orders = None, {}
    for r in ws.iter_rows(values_only=True):
        if r and r[0] and str(r[0]).startswith("ID заказа"):
            col = {str(h).split(" / ")[0].strip(): i for i, h in enumerate(r) if h}
            continue
        if not col or not r or not r[0] or not str(r[0]).isdigit():
            continue
        g = lambda name: r[col[name]] if name in col else None
        key = f"{r[0]}:{g('ID товара')}"  # bitta buyurtmada bir nechta SKU bo'lishi mumkin
        orders[key] = {"sku": g("Название товара"), "qty": g("Количество") or 0, "ret": g("Возвращено") or 0,
                       "price": g("Цена продажи") or 0, "uzum_cost": g("Закупочная цена") or 0,
                       "paid": g("Выведено") or 0}
    return orders


def take_snapshot():
    os.makedirs(SNAP_DIR, exist_ok=True)
    c, base = _client()
    orders = _parse_orders(_download_statement(c, base))
    now = time.time()
    path = os.path.join(SNAP_DIR, dt.datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d_%H%M") + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"taken_at": now, "orders": orders}, f, ensure_ascii=False)
    for old in sorted(glob.glob(os.path.join(SNAP_DIR, "*.json")))[:-KEEP_SNAPSHOTS]:
        os.remove(old)
    print(f"Snapshot: {len(orders)} buyurtma -> {os.path.basename(path)}", flush=True)


def _snapshots():
    out = []
    for p in sorted(glob.glob(os.path.join(SNAP_DIR, "*.json"))):
        with open(p, encoding="utf-8") as f:
            out.append((json.load(f)["taken_at"], p))
    return out


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["orders"]


# ---------- yangi to'lovlarni tahlil qilish ----------

def _iso_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _expenses(c, base, t0, t1):
    """Uzum xizmatlari (logistika, ombor, marketing...) - (t0, t1] oralig'ida sof yechilgan summa."""
    total, page = 0, 0
    while True:
        items = c.session.get(base + "/v1/seller/finance/expenses", timeout=60, params={
            "shopIds": ",".join(Config.UZUM_SHOP_IDS), "dateFrom": int(t0), "dateTo": int(t1),
            "page": page, "size": 100}).json().get("items") or []
        total += sum(e["amount"] if e["type"] == "PAYMENT" else -e["amount"] for e in items)
        if len(items) < 100:
            return total
        page += 1


def _analyze(payouts, before_path, after_path, erp, expenses):
    before, after = _load(before_path), _load(after_path)
    amount = sum(p["amount"] for p in payouts)
    credited = sales = cost = other = 0
    count = units = 0
    missing = {}
    for key, o in after.items():
        diff = o["paid"] - before.get(key, {}).get("paid", 0)
        if diff <= 0.5:
            continue
        q = max(0, (o["qty"] or 0) - (o["ret"] or 0))
        count += 1
        units += q
        credited += diff
        sales += (o["price"] or 0) * q
        info = erp.get(o["sku"])
        if info and info.get("tannarx"):
            cost += info["tannarx"] * q
            other += (info.get("boshqa_harajat") or 0) * q
        elif o["uzum_cost"] and o["uzum_cost"] < (o["price"] or 0) * 0.9:
            cost += o["uzum_cost"] * q  # ERP'da yo'q - Uzum kabinetidagi tannarx
            missing[o["sku"]] = missing.get(o["sku"], 0) + q
        else:
            missing[o["sku"]] = missing.get(o["sku"], 0) + q  # tannarx umuman noma'lum
    tax = sales * TAX_RATE
    profit = amount - cost - other - tax
    pct = lambda v: f"{v / amount * 100:.0f}%" if amount else ""
    dates = ", ".join(dt.datetime.fromtimestamp(_iso_ts(p["createdAt"]), TZ).strftime("%d.%m.%Y") for p in payouts)
    lines = [
        f"💰 To'lov tahlili ({dates}): {_som(amount)} so'm",
        "",
        f"📦 {count} ta buyurtma ({units:.0f} dona) uchun. Mijozlar to'lagan: {_som(sales)}",
        "",
        f"➖ Tovarlar tannarxi: {_som(cost)} ({pct(cost)})",
        f"➖ Soliq 1%: {_som(tax)} ({pct(tax)})",
    ]
    if other:
        lines.append(f"➖ Qadoq va boshqa: {_som(other)}")
    lines.append(f"✅ SOF FOYDA: {_som(profit)} so'm ({pct(profit)})")
    # Tekshiruv: shu buyurtmalar uchun Uzum yozgan summa - oldingi to'lovdan beri
    # yechilgan xarajatlar to'lov summasiga teng chiqsa, buyurtmalar ro'yxati to'g'ri
    gap = amount - (credited - expenses)
    rel = abs(gap) / amount * 100 if amount else 0
    lines += ["", f"🔎 Tekshiruv: buyurtmalar uchun yozilgan {_som(credited)} − Uzum xarajatlari {_som(expenses)} "
                  f"= {_som(credited - expenses)} | to'lov {_som(amount)}"]
    if rel <= 2:
        lines.append(f"✅ Mos keldi (farq {_som(gap)}, {rel:.1f}%) - hisob aniq.")
    else:
        lines.append(f"⚠️ Farq {_som(gap)} so'm ({rel:.1f}%) - to'lovga oldingi balans qoldig'i yoki tuzatishlar "
                     "qo'shilgan bo'lishi mumkin, foyda taxminiy.")
    if missing:
        lines += ["", f"⚠️ {sum(missing.values()):.0f} dona tovarning tannarxi ERP'da yo'q (Uzum kabinetidagi tannarx "
                      "olindi yoki noma'lum - foyda shuncha oshib ko'rinishi mumkin):"]
        lines += [f"• {sku} - {q:.0f} dona" for sku, q in sorted(missing.items(), key=lambda x: -x[1])[:10]]
    lines += ["", "ℹ️ Tannarx ERP'dagi joriy narx bo'yicha. Buyurtmalar ro'yxati Uzum'ning rasmiy hisobotidan."]
    return "\n".join(lines)


def check_payouts():
    c, base = _client()
    items = c.session.get(base + "/v1/seller/finance/withdrawals", params={"page": 0, "size": 20}, timeout=30).json()["items"]
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        # Birinchi ishga tushish: avvalgi to'lovlar uchun "oldingi" nusxa yo'q -
        # ularni o'tkazib yuboramiz, hisob keyingi to'lovdan boshlanadi
        state = {"done": [p["id"] for p in items]}
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
        print("Boshlang'ich holat saqlandi, keyingi to'lovlardan hisoblanadi", flush=True)
        return
    snaps = _snapshots()
    new = sorted([p for p in items if p["id"] not in state["done"] and p["status"] != "REJECTED"],
                 key=lambda p: p["createdAt"])
    if not new:
        return
    erp = None
    # Bir nusxalar oralig'iga tushgan to'lovlar (masalan oddiy + tezkor) birga hisoblanadi
    groups = {}
    for p in new:
        t = _iso_ts(p["createdAt"])
        before = [s for s in snaps if s[0] < t]
        after = [s for s in snaps if s[0] > t]
        if not after:
            continue  # keyingi nusxani kutamiz
        if not before:
            state["done"].append(p["id"])  # oldingi nusxa yo'q - aniq hisoblab bo'lmaydi
            continue
        groups.setdefault((before[-1][1], after[0][1]), []).append(p)
    valid = sorted((_iso_ts(p["createdAt"]) for p in items if p["status"] != "REJECTED"))
    for (b, a), payouts in groups.items():
        erp = erp or get_prices_by_sku()
        first = min(_iso_ts(p["createdAt"]) for p in payouts)
        last = max(_iso_ts(p["createdAt"]) for p in payouts)
        prev = max((t for t in valid if t < first), default=first - 16 * 86400)
        send_report(_analyze(payouts, b, a, erp, _expenses(c, base, prev, last)))
        state["done"] += [p["id"] for p in payouts]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


if __name__ == "__main__":
    try:
        take_snapshot()
        check_payouts()
    except Exception as e:
        send_report(f"❌ To'lov foydasi tahlilida xato: {e}")
        raise
