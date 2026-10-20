import json
import os
import uuid

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PENDING_FILE = os.path.join(DATA_DIR, "pending_decisions.json")
OFFSET_FILE = os.path.join(DATA_DIR, "telegram_offset.json")
SETTLED_FILE = os.path.join(DATA_DIR, "settled_products.json")


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_decision(payload):
    decisions = _load(PENDING_FILE, {})
    decision_id = uuid.uuid4().hex[:10]
    decisions[decision_id] = payload
    _save(PENDING_FILE, decisions)
    return decision_id


def _key(d):
    return (str(d["shop_id"]), str(d["sale_id"]), str(d["product_id"]))


def pop_decision(decision_id):
    """Qarorni oladi va o'sha mahsulot/aksiya bo'yicha qolgan takroriy
    qarorlarni ham o'chiradi - bir mahsulotga bitta javob yetarli."""
    decisions = _load(PENDING_FILE, {})
    payload = decisions.pop(decision_id, None)
    if payload:
        decisions = {k: v for k, v in decisions.items() if _key(v) != _key(payload)}
    _save(PENDING_FILE, decisions)
    return payload


def has_pending(shop_id, sale_id, product_id):
    key = (str(shop_id), str(sale_id), str(product_id))
    return any(_key(v) == key for v in _load(PENDING_FILE, {}).values())


def mark_settled(shop_id, sale_id, product_id):
    """'⚙️ Nastroykadan sozlash' tanlangan mahsulot - agent o'sha aksiyada
    unga boshqa tegmaydi (aks holda keyingi siklda avtomatik nastroyka
    foydalanuvchi qo'ygan narxni bosib ketardi)."""
    settled = _load(SETTLED_FILE, [])
    key = [str(shop_id), str(sale_id), str(product_id)]
    if key not in settled:
        settled.append(key)
        _save(SETTLED_FILE, settled)


def is_settled(shop_id, sale_id, product_id):
    return [str(shop_id), str(sale_id), str(product_id)] in _load(SETTLED_FILE, [])


def get_offset():
    return _load(OFFSET_FILE, {}).get("offset", 0)


def set_offset(offset):
    _save(OFFSET_FILE, {"offset": offset})
