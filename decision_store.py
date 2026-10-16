import json
import os
import uuid

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PENDING_FILE = os.path.join(DATA_DIR, "pending_decisions.json")
OFFSET_FILE = os.path.join(DATA_DIR, "telegram_offset.json")


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


def pop_decision(decision_id):
    decisions = _load(PENDING_FILE, {})
    payload = decisions.pop(decision_id, None)
    _save(PENDING_FILE, decisions)
    return payload


def get_offset():
    return _load(OFFSET_FILE, {}).get("offset", 0)


def set_offset(offset):
    _save(OFFSET_FILE, {"offset": offset})
