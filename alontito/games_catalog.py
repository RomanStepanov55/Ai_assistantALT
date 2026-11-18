"""Roblox game catalog and launcher.

Identity rule of this project: a game is identified by PLACE ID, never by its
name or icon. Two Muscle/Gym simulators with near-identical names exist, so a
name-based guess is how an experiment ends up describing another game.

Launching uses the official `roblox://placeId=...` deep link: it is the same
path the Roblox website uses, it starts the normal client, and it needs no
credentials. No injection, no client modification.
"""
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .memory import STATUS_INTERFACE, STATUS_MEASURED, STATUS_OFFICIAL

# Roblox writes its own session log here. The log is the only local source that
# states the place id in the client's own words, so it is the identity proof.
LOGS_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Roblox" / "logs"
_PLACE_RE = re.compile(r"place[_\s]?id\W{0,6}(\d{5,})", re.IGNORECASE)
_UNIVERSE_RE = re.compile(r"universe[_\s]?id\W{0,6}(\d{5,})", re.IGNORECASE)


@dataclass
class RobloxGame:
    key: str
    name: str
    place_id: int
    universe_id: Optional[int]
    note: str
    evidence: str

    def row(self):
        return [self.key, self.name, str(self.place_id), self.note]


CATALOG: List[RobloxGame] = [
    RobloxGame(
        key="muscle",
        name="Muscle Legends",
        place_id=3623096087,
        universe_id=1268927906,
        note="основная игра агента: гантели, жим, брусья, браво",
        evidence=STATUS_OFFICIAL,
    ),
]


def list_games() -> List[RobloxGame]:
    return list(CATALOG)


def find(key_or_name: str) -> Optional[RobloxGame]:
    needle = (key_or_name or "").strip().lower()
    for game in CATALOG:
        if needle in (game.key.lower(), game.name.lower(),
                      str(game.place_id), str(game.universe_id)):
            return game
    for game in CATALOG:                       # partial name match
        if needle and needle in game.name.lower():
            return game
    return None


def add_custom(name: str, place_id: int) -> RobloxGame:
    """Register a game the user explicitly identified by place id."""
    key = "".join(ch for ch in name.lower().replace(" ", "_") if ch.isalnum() or ch == "_")
    game = RobloxGame(key=key or f"place_{place_id}", name=name,
                      place_id=int(place_id), universe_id=None,
                      note="добавлено пользователем по Place ID",
                      evidence=STATUS_INTERFACE)
    CATALOG[:] = [g for g in CATALOG if g.place_id != int(place_id)] + [game]
    return game


# ---------------------------------------------------------------- launching


def launch(game: RobloxGame) -> Tuple[bool, str]:
    """Open the experience through the official deep link."""
    url = f"roblox://placeId={game.place_id}"
    try:
        if os.name == "nt":
            os.startfile(url)                       # ShellExecute, same as a browser
        elif sys.platform == "darwin":
            subprocess.Popen(["open", url])
        else:
            subprocess.Popen(["xdg-open", url])
        return True, url
    except Exception as e:
        return False, f"{url} -> {e}"


def window_state() -> dict:
    """Read-only look at the Roblox window. Never clicks."""
    try:
        from core.window import WindowManager
    except Exception as e:
        return {"found": False, "detail": f"модули недоступны: {e}"}
    try:
        wm = WindowManager()
        hwnd = wm.find_roblox_window()
        if not hwnd:
            return {"found": False, "detail": "окно Roblox не найдено"}
        left, top, width, height = wm.get_client_rect()
        return {"found": True, "hwnd": hwnd,
                "client": [left, top, width, height],
                "detail": "окно найдено"}
    except Exception as e:
        return {"found": False, "detail": str(e)}


def wait_for_game(timeout: float = 120.0, poll: float = 4.0) -> Tuple[bool, str]:
    """Wait until a frame actually proves the game HUD is on screen.

    "A Roblox window exists" is not proof: after a session ends the client
    keeps the same window and shows its own pages (measured 2026-11-17, a music
    page). Only the HUD badges in the current frame count.
    """
    deadline = time.time() + timeout
    last = "ожидание"
    while time.time() < deadline:
        state = window_state()
        if not state.get("found"):
            last = state.get("detail", "окно не найдено")
        else:
            try:
                from vision.eye import Eye
                from core.window import WindowManager
                eye = Eye(WindowManager())
                frame = eye.capture()
                if frame is None:
                    last = "кадр не получен"
                elif not eye.frame_is_game(frame):
                    last = "окно есть, HUD игры не виден (загрузка/меню)"
                else:
                    return True, "HUD игры подтверждён в текущем кадре"
            except Exception as e:
                last = str(e)
        time.sleep(poll)
    return False, last


# ------------------------------------------------------------------ identity


def current_place(logs_dir: Optional[Path] = None, max_age_sec: float = 6 * 3600,
                  scan: int = 40, now: Optional[float] = None) -> Dict:
    """Read the place id from the Roblox client's own log.

    A name or an icon never identifies a place: two experiences can be called
    almost the same. The log line `placeid:3623096087` is what the client
    itself recorded, so it is treated as interface evidence.

    A stale log is refused: a place from yesterday says nothing about today's
    session.
    """
    directory = Path(logs_dir or LOGS_DIR)
    now = time.time() if now is None else now
    if not directory.exists():
        return {"found": False, "detail": f"папка логов не найдена: {directory}"}

    files = sorted((p for p in directory.glob("*.log") if p.is_file()),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:max(1, scan)]
    for path in files:
        age = now - path.stat().st_mtime
        if age > max_age_sec:
            break                       # files are newest-first: the rest is older
        try:
            if path.stat().st_size > 12_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        place = _PLACE_RE.search(text)
        if not place:
            continue
        universe = _UNIVERSE_RE.search(text)
        game = find(place.group(1))
        return {
            "found": True,
            "place_id": int(place.group(1)),
            "universe_id": (int(universe.group(1)) if universe else None),
            "game": game,
            "log": path.name,
            "age_sec": round(age),
            "evidence": STATUS_INTERFACE,
        }
    return {"found": False,
            "detail": "свежий лог клиента Roblox не найден (place id не подтверждён)"}


def _hud_visible() -> Tuple[bool, str]:
    """Cheap frame check: is a game HUD on screen right now?"""
    try:
        from core.window import WindowManager
        from vision.eye import Eye
        eye = Eye(WindowManager())
        frame = eye.capture()
        if frame is None:
            return False, "кадр не получен"
        if eye.frame_is_game(frame):
            return True, "HUD игры виден"
        return False, "окно есть, HUD игры не виден"
    except Exception as e:
        return False, str(e)


# ------------------------------------------------------------------ autostart


def autostart(preferred: Optional[RobloxGame] = None,
              on_step=None, hud_wait: float = 45.0,
              launch_wait: float = 180.0) -> Dict:
    """Find Roblox and the needed place, then open it if nothing is running.

    Order matters:
      1. already playing the right place  -> attach, launch nothing;
      2. client open but on its own pages -> launch the needed place;
      3. nothing running                  -> launch the needed place.

    "Right place" is proven by the client log when a fresh one exists, and by
    the HUD frame otherwise. The HUD alone says "some game", never "the game",
    so the identity field is filled only from the log.
    """
    step = on_step or (lambda *_: None)
    wanted = preferred or (CATALOG[0] if CATALOG else None)
    log_place = current_place()

    state = window_state()
    if state.get("found"):
        step("окно Roblox найдено")
        if log_place.get("found"):
            name = (log_place.get("game").name if log_place.get("game")
                    else f"place {log_place['place_id']}")
            step(f"в логе клиента: {name} · place {log_place['place_id']}")
            if log_place.get("game"):
                wanted = log_place["game"]          # keep what is already open
        try:
            from core.window import WindowManager
            WindowManager().ensure_focused()
        except Exception:
            pass

        hud, why = _hud_visible()
        if not hud:
            step("HUD пока не виден — жду загрузку")
            hud, why = wait_for_game(timeout=hud_wait, poll=3.0)
        if hud:
            place = current_place()
            if not place.get("found"):
                place = log_place
            # The HUD proves "a game", the log proves "this place". If the log
            # names a different place than the wanted one, the wanted name must
            # not be attached to it.
            shown = wanted
            if wanted is None:
                shown = place.get("game") if place.get("found") else None
            elif place.get("found") and place.get("place_id") != wanted.place_id:
                shown = place.get("game")          # None if the place is unknown
            return {
                "attached": True, "launched": False, "game": shown,
                "place": place if place.get("found") else None,
                "identity": ("подтверждено логом клиента"
                             if place.get("found") else
                             "HUD есть, place id логом не подтверждён"),
                "detail": "игра уже на экране",
            }
        step(f"это не игровой кадр ({why}) — открываю нужный плейс")

    if wanted is None:
        return {"attached": False, "launched": False, "game": None,
                "place": log_place if log_place.get("found") else None,
                "identity": "каталог пуст", "detail": "нечего открывать"}

    step(f"запускаю {wanted.name} · place {wanted.place_id}")
    launched, detail = launch(wanted)
    if not launched:
        return {"attached": False, "launched": False, "game": wanted,
                "place": None, "identity": "запуск не удался",
                "detail": detail}

    confirmed, why = wait_for_game(timeout=launch_wait, poll=4.0)
    place = current_place()
    identity = "не подтверждено"
    if place.get("found"):
        identity = "подтверждено логом клиента"
        if place.get("place_id") != wanted.place_id:
            identity = (f"лог показывает другой place: {place['place_id']} "
                        f"вместо {wanted.place_id}")
    return {
        "attached": bool(confirmed),
        "launched": True,
        "game": wanted,
        "place": place if place.get("found") else None,
        "identity": identity,
        "detail": "HUD игры подтверждён" if confirmed else why,
    }
