#!/usr/bin/env python3
"""
SkillAutonomy v5 — мозг, который НЕ повторяет одно действие по кругу и
постепенно расширяет кругозор (curriculum learning для агента).

Решает три проблемы:
  1. Зацикливание: одно и то же действие не выполняется больше K раз подряд,
     если оно не доминирует с явным отрывом (dominance margin).
  2. Стагнация: если EMA-награда перестала расти, включается exploration boost —
     агент охотнее пробует редкие действия.
  3. Кругозор: действия открываются уровнями (curriculum). Новые способности
     разблокируются, когда текущие освоены (success rate >= mastery_threshold),
     — агент учится расширять репертуар, а не долбить одну кнопку.

Использование:
    auto = SkillAutonomy()
    allowed = auto.allowed_actions()                 # стартовый репертуар
    choice  = auto.choose(allowed, bandit_choice)    # фильтр после bandit
    auto.record(choice, reward=0.7)
    news = auto.maybe_unlock()                       # -> ["SPIN_WHEEL", ...]
Состояние сохраняется: auto.save("data/skill_autonomy.json").
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

# Путь по умолчанию абсолютный: модуль работает из любого CWD (как и остальной
# проект — config.py задаёт DATA_DIR от корня репозитория).
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "skill_autonomy.json"


class SkillAutonomy:
    def __init__(self, max_consecutive: int = 3, dominance_margin: float = 0.25,
                 stagnation_window: int = 24, mastery_threshold: float = 0.7,
                 min_attempts_to_unlock: int = 5, curiosity_boost: float = 1.5,
                 path: Optional[str] = None):
        self.max_consecutive = max_consecutive
        self.dominance_margin = dominance_margin
        self.mastery_threshold = mastery_threshold
        self.min_attempts_to_unlock = min_attempts_to_unlock
        self.curiosity_boost = curiosity_boost
        self.path = Path(path) if path else DEFAULT_PATH

        self._consec: Dict[str, int] = {}
        self._last: Optional[str] = None
        self._last_used: Dict[str, float] = {}
        self._stats: Dict[str, Dict[str, float]] = {}   # action -> {n, mean}
        self._history: Deque[float] = deque(maxlen=stagnation_window)
        # Имена действий - словарь CapabilityRegistry проекта (brain/mind.py):
        # CLAIM_GIFTS, CHECK_QUESTS, PETS_MAINTENANCE, TRAIN_AGILITY. В исходном
        # обновлении были CLAIM_GIFT / CLAIM_QUESTS / PETS_EQUIP - таких
        # возможностей в проекте нет, и они никогда бы не выбрались.
        self._levels: List[List[str]] = [
            ["TRAIN_DUMBBELLS"],
            ["TRAIN_PUSHUPS", "TRAIN_SITUPS"],
            ["TRAIN_MACHINE", "TRAIN_AGILITY", "SPIN_WHEEL", "CLAIM_GIFTS"],
            ["CHECK_QUESTS", "HATCH_EGG", "PETS_MAINTENANCE", "REBIRTH"],
        ]
        self._unlocked_idx = 0
        self._pending: List[str] = []

    @property
    def unlocked_level(self) -> int:
        """Номер открытого уровня curriculum (0 = только первый список)."""
        return self._unlocked_idx

    # ---------------- выбор ----------------
    def _mean(self, a: str) -> float:
        return self._stats.get(a, {}).get("mean", 0.5)

    def _is_dominant(self, action: str, candidates: List[str]) -> bool:
        others = [self._mean(c) for c in candidates if c != action]
        if not others:
            return True
        return self._mean(action) >= max(others) + self.dominance_margin

    def choose(self, candidates: List[str], bandit_choice: str) -> str:
        """Финальный выбор: пропускает решение bandit через фильтр ротации."""
        now = time.time()
        pick = bandit_choice
        consec = self._consec.get(bandit_choice, 0)
        if consec >= self.max_consecutive - 1 and not self._is_dominant(bandit_choice, candidates):
            others = [c for c in candidates if c != bandit_choice]
            if others:
                pick = min(others, key=lambda c: self._last_used.get(c, 0.0))
        self._last = pick
        self._consec[pick] = self._consec.get(pick, 0) + 1
        for c in candidates:
            if c != pick:
                self._consec[c] = 0
        self._last_used[pick] = now
        return pick

    def record(self, action: str, reward: float) -> None:
        st = self._stats.setdefault(action, {"n": 0.0, "mean": 0.5})
        st["n"] += 1
        st["mean"] += (reward - st["mean"]) / st["n"]
        self._history.append(min(max(reward, 0.0), 1.0))

    # ---------------- стагнация / буст ----------------
    def stagnation_level(self) -> float:
        """0..1: насколько награда перестала расти (по наклону окна)."""
        h = list(self._history)
        if len(h) < self._history.maxlen:
            return 0.0
        n = len(h)
        mx = (n - 1) / 2.0
        my = sum(h) / n
        num = sum((i - mx) * (v - my) for i, v in enumerate(h))
        den = sum((i - mx) ** 2 for i in range(n))
        slope = num / den if den else 0.0
        return max(0.0, min(1.0, 1.0 - abs(slope) / 0.02))

    @property
    def exploration_boost(self) -> float:
        return 1.0 + self.curiosity_boost * self.stagnation_level()

    # ---------------- кругозор (curriculum) ----------------
    def allowed_actions(self) -> List[str]:
        acts: List[str] = []
        for lvl in self._levels[: self._unlocked_idx + 1]:
            acts.extend(lvl)
        return acts

    def maybe_unlock(self) -> List[str]:
        """Если текущий уровень освоен — открыть следующий. Возвращает новые."""
        if self._unlocked_idx >= len(self._levels) - 1:
            return []
        current = self._levels[self._unlocked_idx]
        for a in current:
            st = self._stats.get(a, {})
            if st.get("n", 0) < self.min_attempts_to_unlock or st.get("mean", 0) < self.mastery_threshold:
                return []
        self._unlocked_idx += 1
        news = list(self._levels[self._unlocked_idx])
        self._pending.extend(news)
        return news

    # ---------------- persistence ----------------
    def save(self, path: Optional[str] = None) -> None:
        """Атомарная запись состояния (оборванный диск не портит файл)."""
        p = Path(path) if path else self.path
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "consec": self._consec, "last": self._last,
            "last_used": self._last_used, "stats": self._stats,
            "unlocked_idx": self._unlocked_idx, "history": list(self._history),
        }, ensure_ascii=False)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, p)

    @classmethod
    def load(cls, path: Optional[str] = None, **kw) -> "SkillAutonomy":
        """Бросает ValueError на битый JSON: вызывающий решает, чем это заменить."""
        obj = cls(path=path, **kw)
        p = obj.path
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            obj._consec = {k: int(v) for k, v in d.get("consec", {}).items()}
            obj._last = d.get("last")
            obj._last_used = {k: float(v) for k, v in d.get("last_used", {}).items()}
            obj._stats = {k: {"n": float(v.get("n", 0)), "mean": float(v.get("mean", 0.5))}
                          for k, v in d.get("stats", {}).items()}
            obj._unlocked_idx = int(d.get("unlocked_idx", 0))
            obj._history.extend(d.get("history", []))
        return obj
