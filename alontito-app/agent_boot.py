"""Загрузчик с ЖЁСТКИМ ЗАТВОРОМ: нет окна Roblox = нет ввода. Watchdog держит фокус."""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
sys.path.insert(0, str(APP))
from backend.services import roblox_finder as rf  # noqa: E402


def log(m):
    print("[BOOT] " + m, flush=True)


def kill_tree(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 9)
        except Exception:
            pass


def main():
    hint_t = os.environ.get("ALONTITO_HINT_TITLE") or None
    hint_p = os.environ.get("ALONTITO_HINT_PROCESS") or None
    log("Этап 1/3 · поиск Roblox (обычный / Store / переименованный)...")
    target = rf.find_target(hint_t, hint_p)
    if target:
        log("Найден: %s (pid %s, exe %s)" % (target["title"] or "?", target["pid"], target["exe"]))
    else:
        log("Не найден — пробую запустить игру...")
        for a in rf.launch_attempts():
            log("Запуск: " + a)
        log("Этап 2/3 · ожидаю окно (до 90 с)...")
        target = rf.wait_for(90, hint_t, hint_p)

    # ЖЁСТКИЙ ЗАТВОР: без окна игры НЕ запускаем main.py (никаких кликов по рабочему столу).
    if not target or not target.get("hwnd"):
        log("ЗАТВОР: окно Roblox не найдено. Агент НЕ запущен — ввод заблокирован.")
        log("Открой Roblox вручную или укажи название окна/exe в Настройках, затем «Запустить».")
        return 2

    rf.focus(target["hwnd"])
    log("Фокус на окне игры установлен.")
    env = os.environ.copy()
    env["ALONTITO_ROBLOX_TITLE"] = target["title"] or "Roblox"
    if target["exe"]:
        env["ALONTITO_ROBLOX_PROCESS"] = target["exe"]
    env["ALONTITO_ROBLOX_PID"] = str(target["pid"])

    log("Этап 3/3 · запускаю автономный цикл main.py (только внутри игры)...")
    proc = subprocess.Popen([sys.executable, "-u", str(ROOT / "main.py")], env=env)

    stop = threading.Event()

    def watchdog():
        miss = 0
        while not stop.is_set() and proc.poll() is None:
            if not rf.window_alive(target["hwnd"]):
                miss += 1
                if miss >= 3:
                    log("WATCHDOG: окно игры исчезло — останавливаю агент (защита от кликов вне игры).")
                    kill_tree(proc.pid)
                    return
            else:
                miss = 0
                if not rf.is_foreground(target["hwnd"]):
                    rf.focus(target["hwnd"])
            time.sleep(2)

    threading.Thread(target=watchdog, daemon=True).start()
    rc = proc.wait()
    stop.set()
    log("main.py завершился с кодом %s." % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())