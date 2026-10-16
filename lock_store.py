import os
import time

LOCK_FILE = os.path.join(os.path.dirname(__file__), "data", "run.lock")
LOCK_STALE_SECONDS = 600


def acquire_lock(timeout=10):
    """Ikki jarayon (masalan rejalashtirilgan va qo'lda ishga tushirilgan
    tekshiruv) bir vaqtda ishlab ketmasligi uchun oddiy fayl-qulf."""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK_FILE)
            except FileNotFoundError:
                continue
            if age > LOCK_STALE_SECONDS:
                try:
                    os.remove(LOCK_FILE)
                except FileNotFoundError:
                    pass
                continue
            if time.time() > deadline:
                return False
            time.sleep(1)


def release_lock():
    try:
        os.remove(LOCK_FILE)
    except FileNotFoundError:
        pass
