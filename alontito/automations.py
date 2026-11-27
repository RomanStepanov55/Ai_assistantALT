"""Reminders and schedules: an explicit skill, no model required.

"напомни через 20 минут …", "каждый день в 9:00 …", "по будням в 8:30 …",
"каждый понедельник в 18:00 …", "завтра в 9:00 …".

All time math takes `now` as a parameter so tests can freeze the clock;
nothing here calls datetime.now() on its own. State lives in
data/alontito/automations.json (gitignored), written atomically.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "alontito" / "automations.json"

WEEKDAYS = {"понедельник": 0, "вторник": 1, "среду": 2, "среда": 2,
            "четверг": 3, "пятницу": 4, "пятница": 4, "субботу": 5,
            "суббота": 5, "воскресенье": 6}

_NUM = r"(\d{1,3})"
_CLOCK = r"(\d{1,2})[:.](\d{2})|(\d{1,2})\s*(?:час|ч\b|:00)?"


@dataclass
class Automation:
    id: str
    text: str
    kind: str                       # once | daily | weekdays | weekly
    due_ts: Optional[float] = None  # for "once"
    hour: Optional[int] = None      # for repeating kinds
    minute: Optional[int] = None
    weekday: Optional[int] = None   # 0=Mon for weekly
    created: float = 0.0
    last_fired: Optional[float] = None
    enabled: bool = True

    def when_text(self) -> str:
        if self.kind == "once":
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.due_ts or 0))
        hm = f"{self.hour or 0:02d}:{self.minute or 0:02d}"
        names = ["понедельник", "вторник", "среда", "четверг", "пятница",
                 "суббота", "воскресенье"]
        if self.kind == "daily":
            return f"каждый день в {hm}"
        if self.kind == "weekdays":
            return f"по будням в {hm}"
        return f"каждый {names[self.weekday or 0]} в {hm}"

    def row(self) -> dict:
        return {"id": self.id, "text": self.text, "kind": self.kind,
                "when": self.when_text(), "enabled": self.enabled,
                "last_fired": self.last_fired}


# ----------------------------------------------------------------- parsing

def _parse_clock(hh: str, mm: Optional[str]) -> Optional[Tuple[int, int]]:
    try:
        hour = int(hh)
        minute = int(mm) if mm is not None else 0
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse(text: str, now: float) -> Optional[Automation]:
    """Parse one reminder phrase. None = not a reminder, never a guess."""
    low = (text or "").strip()
    if not low:
        return None
    m = re.match(r"^напомни[ть]*\s+(.*)$", low)
    if not m:
        return None
    rest = m.group(1).strip()

    # "через N минут/часов/секунд ..."
    m = re.match(rf"^через\s+{_NUM}\s*(секунд|сек|минут|мин|час[аов]*|ч\b)\s*(.*)$", rest)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        body = m.group(3).strip()
        if n <= 0 or not body:
            return None
        seconds = (n * 3600 if unit.startswith("час") or unit == "ч"
                   else n * 60 if unit.startswith("мин") else n)
        return Automation(id="", text=body, kind="once",
                          due_ts=now + seconds, created=now)

    # "каждый день в HH:MM ...", "ежедневно в HH:MM", "по будням в HH:MM",
    # "каждый <weekday> в HH:MM ..."
    m = re.match(r"^(каждый день|ежедневно|по будням|каждый будний день|"
                 r"кажд(?:ый|ую)\s+\S+)\s+(?:в\s+)?(\d{1,2})[:.](\d{2})\s*(.*)$", rest)
    if m:
        when, hh, mm, body = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        clock = _parse_clock(hh, mm)
        if not clock or not body:
            return None
        if when in ("каждый день", "ежедневно"):
            kind, weekday = "daily", None
        elif when in ("по будням", "каждый будний день"):
            kind, weekday = "weekdays", None
        else:
            name = when.replace("каждый ", "").replace("каждую ", "").strip()
            weekday = WEEKDAYS.get(name)
            if weekday is None:
                return None
            kind = "weekly"
        return Automation(id="", text=body, kind=kind, hour=clock[0],
                          minute=clock[1], weekday=weekday, created=now)

    # "завтра в HH:MM ...", "сегодня в HH:MM ..."
    m = re.match(r"^(завтра|сегодня)\s+(?:в\s+)?(\d{1,2})[:.](\d{2})\s*(.*)$", rest)
    if m:
        day, hh, mm, body = m.group(1), m.group(2), m.group(3), m.group(4).strip()
        clock = _parse_clock(hh, mm)
        if not clock or not body:
            return None
        lt = time.localtime(now)
        base = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                            clock[0], clock[1], 0, 0, 0, -1))
        due = base + (86400 if day == "завтра" else 0)
        if due <= now and day == "сегодня":
            due += 86400          # a passed time today fires tomorrow
        return Automation(id="", text=body, kind="once", due_ts=due, created=now)
    return None


# ------------------------------------------------------------------ storage

class Automations:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_PATH
        self.items: List[Automation] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for item in raw.get("items", []):
                self.items.append(Automation(
                    id=item.get("id") or "", text=item.get("text") or "",
                    kind=item.get("kind") or "once",
                    due_ts=item.get("due_ts"), hour=item.get("hour"),
                    minute=item.get("minute"), weekday=item.get("weekday"),
                    created=item.get("created") or 0.0,
                    last_fired=item.get("last_fired"),
                    enabled=bool(item.get("enabled", True))))
        except (OSError, ValueError):
            self.items = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"items": [vars(a) for a in self.items]}, ensure_ascii=False,
            indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def _new_id() -> str:
        return format(time.time_ns() % 10_000_000_000, "d")

    def add(self, auto: Automation) -> Automation:
        auto.id = auto.id or self._new_id()
        self.items.append(auto)
        self._save()
        return auto

    def remove(self, auto_id: str) -> bool:
        before = len(self.items)
        self.items = [a for a in self.items if a.id != auto_id]
        if len(self.items) != before:
            self._save()
            return True
        return False

    def set_enabled(self, auto_id: str, enabled: bool) -> bool:
        for a in self.items:
            if a.id == auto_id:
                a.enabled = enabled
                self._save()
                return True
        return False

    def list(self, include_done: bool = True) -> List[Automation]:
        items = [a for a in self.items if a.enabled or include_done]
        return sorted(items, key=lambda a: (a.due_ts or 0, a.created))

    def due(self, now: float) -> List[Automation]:
        """What should fire right now. Fires once per day for repeating items."""
        out = []
        lt = time.localtime(now)
        today = lt.tm_year, lt.tm_yday
        for a in self.items:
            if not a.enabled:
                continue
            if a.last_fired:
                fired_stamp = time.localtime(a.last_fired)
                if (fired_stamp.tm_year, fired_stamp.tm_yday) == today:
                    continue
            if a.kind == "once":
                if a.due_ts is not None and now >= a.due_ts:
                    out.append(a)
            else:
                if a.weekday is not None and lt.tm_wday != a.weekday:
                    continue
                if a.kind == "weekdays" and lt.tm_wday >= 5:
                    continue
                if now >= time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                       a.hour or 0, a.minute or 0, 0, 0, 0, -1)):
                    out.append(a)
        return out

    def mark_fired(self, auto_id: str, now: float) -> None:
        for a in self.items:
            if a.id == auto_id:
                a.last_fired = now
        self._save()
