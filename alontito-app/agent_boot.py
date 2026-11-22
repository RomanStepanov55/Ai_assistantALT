"""Загрузчик: находит/запускает Roblox (любой клиент), передаёт цель в main.py."""
import os
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent
ROOT = APP.parent
sys.path.insert(0, str(APP))
from backend.services import roblox_finder as rf  # noqa: E402


def log(m):
    print("[BOOT] " + m, flush=True)


def main():
    hint_t = os.environ.get("ALONTITO_HINT_TITLE") or None
    hint_p = os.environ.get("ALONTITO_HINT_PROCESS") or None
    log("Этап 1/3 · поиск Roblox (обычный / Store / переименованный)...")
    target = rf.find_target(hint_t, hint_p)
    if target:
        log("Найден: %s (pid %s, exe %s)" % (target["title"] or "?", target["pid"], target["exe"]))
    else:
        log("Не найден — запускаю игру...")
        for a in rf.launch_attempts():
            log("Запуск: " + a)
        log("Этап 2/3 · ожидаю окно (до 90 с)...")
        target = rf.wait_for(90, hint_t, hint_p)
        if target:
            log("Окно появилось: %s" % (target["title"] or "?"))
        else:
            log("Окно не найдено — передаю main.py (сообщит причину).")
    env = os.environ.copy()
    if target:
        if target["title"]:
            env["ALONTITO_ROBLOX_TITLE"] = target["title"]
        if target["exe"]:
            env["ALONTITO_ROBLOX_PROCESS"] = target["exe"]
        env["ALONTITO_ROBLOX_PID"] = str(target["pid"])
    log("Этап 3/3 · запускаю автономный цикл main.py...")
    rc = subprocess.call([sys.executable, "-u", str(ROOT / "main.py")], env=env)
    log("main.py завершился с кодом %s." % rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())