# -*- coding: utf-8 -*-
"""
alontito/pet_terminal.py — подключение 3D Desktop Pet к терминалу Alontito (app.py).

Отдельный файл, чтобы не трогать app.py: тут команда /3d, вопрос при первом запуске и индикатор статуса
для шапки. В app.py нужно добавить 4 строки — см. INTEGRATION_3D.md.

    from alontito.pet_terminal import pet3d

    pet3d.first_run_prompt()                 # один раз спросит «Хочешь включить живого 3D-ассистента? [Y/n]»
    pet3d.autostart()                        # если уже включён — запустит вместе с терминалом
    if pet3d.handle_command(user_input):     # /3d [on|off|mode …]
        continue
    header += "  " + pet3d.badge()            # индикатор рядом со статусом MCP
"""
from __future__ import annotations

import atexit
import importlib.util
import time
from typing import Any, Callable, Dict, Optional

try:                                    # пакетный импорт (alontito.pet_terminal) и запуск из папки alontito/
    from .desktop_pet import ALL_CLIPS, ASSETS_DIR, MODE_HELP, MODES, PetController, find_asset, load_config, save_config
except ImportError:                     # pragma: no cover
    from desktop_pet import ALL_CLIPS, ASSETS_DIR, MODE_HELP, MODES, PetController, find_asset, load_config, save_config

QUESTION = "\033[38;2;200;140;255m✦ Хочешь включить живого 3D-ассистента на рабочем столе? [Y/n] \033[0m"

HELP = """\
/3d                 — статус и эта справка
/3d on | off        — включить / выключить (при выключении персонаж испаряется)
/3d mode [auto|free|still] — режим поведения (без аргумента — показать текущий)
/3d portal          — пусть достанет предмет из портала
/3d play <клип>     — показать анимацию: {clips}
/3d hide | show     — спрятать (испарить) / вернуть, не выключая процесс
/3d scale <0.4–2.5> — размер
/3d check           — диагностика (Qt, модель, three.js, активное окно)"""

ANSI = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m", "dim": "\033[2m", "reset": "\033[0m"}


def qt_available() -> bool:
    """Есть ли PyQt6/PySide6 с WebEngine (без импорта самого Qt — это быстро и безопасно)."""
    for pkg in ("PyQt6.QtWebEngineWidgets", "PySide6.QtWebEngineWidgets"):
        try:
            if importlib.util.find_spec(pkg) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


class PetTerminal:
    def __init__(self, say: Callable[[str], None] = print, ask: Callable[[str], str] = input):
        self.say, self.ask = say, ask
        self.cfg: Dict[str, Any] = load_config()
        self.ctl = PetController(self.cfg)
        atexit.register(self.shutdown)

    # ------------------------------------------------------------------ индикатор для шапки
    _BADGES = {
        "off": ("○", "выкл", "dim", "grey50"),
        "starting": ("◌", "запуск", "yellow", "yellow"),
        "on": ("●", "вкл", "green", "green"),
        "hiding": ("◌", "выкл…", "yellow", "yellow"),
        "error": ("✖", "ошибка", "red", "red"),
    }
    _ACT = {"coding": "кодит", "roblox": "Roblox", "portal": "портал", "walk": "гуляет", "sit": "сидит", "rubik": "кубик",
            "drag": "несут", "fall": "падает", "land": "приземл.", "hidden": "скрыт", "dissolving": "испаряется"}

    def badge(self, fmt: str = "plain", verbose: bool = False) -> str:
        """Строка статуса 3D-агента. fmt: plain | ansi | rich | html (prompt_toolkit HTML)."""
        st = self.ctl.status
        icon, word, ansi_c, rich_c = self._BADGES.get(st, self._BADGES["off"])
        extra = ""
        if st == "on":
            extra = f" · {self.ctl.mode}"
            if verbose and self.ctl.pet_state:
                extra += f" · {self._ACT.get(self.ctl.pet_state, self.ctl.pet_state)}"
        text = f"3D {icon} {word}{extra}"
        if fmt == "ansi":
            return f"{ANSI[ansi_c]}{text}{ANSI['reset']}"
        if fmt == "rich":
            return f"[{rich_c}]{text}[/{rich_c}]"
        if fmt == "html":
            return f"<style fg=\"{'ansigreen' if ansi_c == 'green' else 'ansiyellow' if ansi_c == 'yellow' else 'ansired' if ansi_c == 'red' else 'ansibrightblack'}\">{text}</style>"
        return text

    def on_change(self, fn: Callable[[Dict[str, Any]], None]) -> None:
        """Подписка на изменения статуса (чтобы перерисовать шапку)."""
        self.ctl.on_change(fn)

    @staticmethod
    def _frame(title: str, lines):
        """Framed block through the app's UI layer, or None when it is absent.

        pet_terminal is also usable on its own, so the import stays optional:
        without alontito.ui the caller falls back to plain lines.
        """
        box = term_width = None
        try:
            from .ui import box, term_width          # package import
        except Exception:
            try:
                from alontito.ui import box, term_width   # script import
            except Exception:
                return None
        return box(title, lines, width=term_width())

    # ------------------------------------------------------------------ запуск / остановка
    def _save(self) -> None:
        try:
            save_config(self.cfg)
        except OSError as e:
            self.say(f"[3D] не удалось сохранить настройки: {e}")

    def start(self, wait: bool = True, timeout: float = 45.0) -> bool:
        if self.ctl.running and self.ctl.status in ("on", "starting"):
            return True
        if not qt_available():
            self.say("[3D] Не найден PyQt6/PySide6 с WebEngine. Установи:  pip install PyQt6 PyQt6-WebEngine")
            self.ctl.status, self.ctl.detail = "error", "нет PyQt6"
            return False
        if not find_asset("character", self.cfg):
            self.say(f"[3D] Не найдена модель персонажа. Положи hooded_neon_character_rigged.glb в {ASSETS_DIR}")
            self.ctl.status, self.ctl.detail = "error", "нет модели"
            return False
        self.cfg["mode"] = self.cfg.get("mode", "auto")
        if not self.ctl.start():
            self.say(f"[3D] {self.ctl.detail}")
            return False
        if not wait:
            return True
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.ctl.status == "on":
                return True
            if self.ctl.status == "error" or not self.ctl.running:
                break
            time.sleep(0.15)
        if self.ctl.status != "on":
            self.say(f"[3D] Не удалось запустить: {self.ctl.detail or 'таймаут'}")
            tail = "\n".join(self.ctl.log[-4:])
            if tail:
                self.say(tail)
            return False
        return True

    def autostart(self) -> None:
        """Вызывать при старте терминала: если ассистент включён в настройках — поднять его (без ожидания)."""
        if self.cfg.get("enabled") and not self.ctl.running:
            self.start(wait=False)

    def shutdown(self) -> None:
        try:
            self.ctl.stop(timeout=4.0)
        except Exception:                                              # noqa: BLE001
            pass

    # ------------------------------------------------------------------ первый запуск
    def first_run_prompt(self) -> bool:
        if self.cfg.get("asked"):
            return False
        try:
            answer = self.ask(QUESTION).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        yes = answer in ("", "y", "yes", "д", "да", "ага", "конечно")
        self.cfg["asked"], self.cfg["enabled"] = True, yes
        self._save()
        if yes:
            self.say("\033[38;2;120;255;160m✔ Отлично, запускаю 3D-ассистента…\033[0m")
            if self.start():
                self.say("\033[38;2;180;150;255m› Готово! Перетаскивай мышью (ЛКМ), ПКМ — меню, /3d — команды в терминале.\033[0m")
                return True
        else:
            self.say("\033[38;2;160;140;190m› Хорошо. Включить позже можно командой /3d on.\033[0m")
        return False

    # ------------------------------------------------------------------ команда /3d
    def handle_command(self, line: str) -> bool:
        """True, если строка — команда /3d (и она обработана)."""
        parts = line.strip().split()
        if not parts or parts[0].lower() not in ("/3d", "/3д"):
            return False
        args = [a.lower() for a in parts[1:]]
        sub = args[0] if args else ""
        say = self.say
        if sub in ("", "status", "help", "?"):
            head = f"{self.badge()}   режим: {self.ctl.mode}   {self.ctl.detail}".rstrip()
            body = HELP.format(clips=", ".join(ALL_CLIPS)).splitlines()
            framed = self._frame("3D-АССИСТЕНТ", [head, ""] + body)
            if framed is None:
                say(head)
                say("\n".join(body))
            else:
                say(framed)
        elif sub == "on":
            self.cfg["enabled"] = self.cfg["asked"] = True
            self._save()
            if self.start():
                say("[3D] Включён.")
        elif sub == "off":
            self.cfg["enabled"] = False
            self._save()
            if self.ctl.running:
                say("[3D] Выключаю (испарение)…")
            self.ctl.stop()
            say("[3D] Выключен.")
        elif sub == "mode" or sub in MODES:
            mode = args[1] if sub == "mode" and len(args) > 1 else (sub if sub in MODES else "")
            if not mode:
                say(f"[3D] Режим: {self.ctl.mode}")
                for m in MODES:
                    say(f"   {'→' if m == self.ctl.mode else ' '} {m:<5} — {MODE_HELP[m]}")
            elif mode not in MODES:
                say(f"[3D] Неизвестный режим «{mode}». Доступно: {', '.join(MODES)}")
            else:
                self.cfg["mode"] = mode
                self._save()
                self.ctl.mode = mode
                if self.ctl.running:
                    self.ctl.set_mode(mode)
                say(f"[3D] Режим: {mode} — {MODE_HELP[mode]}")
        elif sub in ("portal", "hide", "show"):
            if not self._need_running():
                return True
            self.ctl.send(cmd=sub)
        elif sub == "play":
            clip = args[1] if len(args) > 1 else ""
            clip = {"coding": "activity_coding", "rubik": "activity_rubik", "sit": "sit_idle", "roblox": "roblox_watch_back",
                    "panic": "drag_panic", "drop": "drag_drop"}.get(clip, clip)
            if clip not in ALL_CLIPS:
                say(f"[3D] Клипы: {', '.join(ALL_CLIPS)}")
            elif self._need_running():
                self.ctl.send(cmd="play", clip=clip)
        elif sub == "scale":
            try:
                v = float(args[1].replace(",", "."))
                assert 0.4 <= v <= 2.5
            except (IndexError, ValueError, AssertionError):
                say("[3D] Использование: /3d scale 0.4…2.5   (сейчас %.2f)" % float(self.cfg.get("scale", 0.8)))
                return True
            self.cfg["scale"] = v
            self._save()
            if self.ctl.running:
                self.ctl.send(cmd="scale", value=v)
            say(f"[3D] Размер: {v}")
        elif sub == "check":
            from .desktop_pet import run_check
            run_check()
        else:
            say(f"[3D] Не знаю «{sub}». Список: /3d help")
        return True

    def _need_running(self) -> bool:
        if self.ctl.running and self.ctl.status == "on":
            return True
        self.say("[3D] Ассистент не запущен — сначала /3d on")
        return False


pet3d = PetTerminal()
