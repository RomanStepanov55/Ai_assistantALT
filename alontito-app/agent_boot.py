"""Загрузчик агента: находит/запускает Roblox + Muscle Legends, затем main.py.

Все строки печатаются с flush, чтобы панель «Поток агента» видела этапы сразу.
"""
import ctypes
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import PLACE_ID, ROBLOX_URI  # noqa: E402


def log(m):
    print("[BOOT] " + m, flush=True)


def roblox_hwnd():
    if sys.platform != "win32":
        return None
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, l):
        n = u.GetWindowTextLengthW(h)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, buf, n + 1)
            if "Roblox" in buf.value:
                found.append(h)
        return True

    try:
        u.EnumWindows(proc(cb), 0)
    except Exception:
        return None
    return found[0] if found else None


def launch_game():
    log("Roblox не найден — запускаю Muscle Legends (placeId %d)..." % PLACE_ID)
    try:
        os.startfile(ROBLOX_URI)  # noqa: S606 (Windows protocol)
        return True
    except Exception as e:
        log("Протокол roblox:// не сработал (%s) — открываю网页 через браузер." % e)
        try:
            webbrowser.open("https://www.roblox.com/games/%d/" % PLACE_ID)
            return True
        except Exception as e2:
            log("Не удалось открыть игру: %s" % e2)
            return False


def main():
    log("Этап 1/3 · проверка окна Roblox...")
    if roblox_hwnd():
        log("Окно Roblox уже открыто.")
    else:
        launch_game()
        log("Этап 2/3 · ожидаю появления окна Roblox (до 90 с)...")
        t0 = time.time()
        while time.time() - t0 < 90:
            if roblox_hwnd():
                log("Окно Roblox появилось (%.1f с)." % (time.time() - t0))
                break
            time.sleep(2)
        else:
            log("Окно так и не появилось — передаю управление main.py (он сообщит причину).")
    log("Этап 3/3 · запускаю автономный цикл main.py...")
    rc = subprocess.call([sys.executable, "-u", str(ROOT / "main.py")])
    log("main.py завершился с кодом %s." % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())