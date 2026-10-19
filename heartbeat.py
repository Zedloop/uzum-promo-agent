import json
import os
import time

HEARTBEAT_FILE = os.path.join(os.path.dirname(__file__), "data", "heartbeat.json")


def load_heartbeat():
    try:
        with open(HEARTBEAT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def record_run(error=None):
    """Har bir tekshiruv sikli tugagach chaqiriladi - kuzatuvchi (watchdog)
    agent haqiqatan ishlab turganini shu vaqtlar orqali biladi."""
    data = load_heartbeat()
    now = int(time.time())
    if error is None:
        data["last_ok"] = now
        data.pop("last_error", None)
        data.pop("last_error_at", None)
    else:
        data["last_error"] = str(error)[:300]
        data["last_error_at"] = now
    os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
    with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
