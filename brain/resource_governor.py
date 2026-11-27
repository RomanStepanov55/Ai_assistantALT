#!/usr/bin/env python3
"""
ResourceGovernor v5 — ограничитель нагрузки фонового агента.

Принципы:
  - Token bucket: цикл агента получает "токены" работы; токенов нет -> агент спит.
  - Адаптивный sleep: при высокой загрузке CPU (опционально psutil) и при
    исчерпании токенов сон удлиняется; при простое — укорачивается.
  - Адаптивные интервалы: подсистемы (OCR, скриншот, рефлексия) работают
    не чаще своего периода, и период растёт, если подсистема падает/нагружает.

В проекте задаёт ритм фоновой работы: цикл автономии (brain/autonomy.py)
берёт токен перед каждой рутиной, поэтому саморазвитие никогда не съедает
CPU пульта и чата.
"""
from __future__ import annotations

import time
from typing import Callable, Dict, Optional


class ResourceGovernor:
    def __init__(self, target_hz: float = 25.0, burst: float = 2.0,
                 clock: Callable[[], float] = time.monotonic):
        self.rate = float(target_hz)          # токенов в секунду
        self.capacity = max(float(burst), 1.0)
        self.tokens = self.capacity
        self._clock = clock
        self._updated = clock()
        self.iterations = 0

    # ---- token bucket ----
    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(now - self._updated, 0.0)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self._updated = now

    def acquire(self, cost: float = 1.0) -> bool:
        """Попытка занять `cost` токенов. True = можно работать."""
        self._refill()
        if self.tokens >= cost:
            self.tokens -= cost
            self.iterations += 1
            return True
        return False

    def recommended_sleep(self, cpu_percent: Optional[float] = None,
                          base: float = 0.05) -> float:
        """Рекомендуемая пауза: больше при высоком CPU и пустом бакете."""
        sleep = base
        if cpu_percent is not None:
            if cpu_percent >= 85.0:
                sleep *= 4.0
            elif cpu_percent >= 70.0:
                sleep *= 2.5
            elif cpu_percent <= 30.0 and self.tokens > self.capacity * 0.5:
                sleep *= 0.5                     # система простаивает — живее
        if self.tokens < 1.0:
            deficit = 1.0 - self.tokens
            sleep = max(sleep, deficit / self.rate)
        return min(sleep, 1.5)                   # потолок, чтобы не выглядеть зависшим

    # ---- адаптивные интервалы подсистем ----
    def schedule(self, last_run: float, interval: float, now: float,
                 overloaded: bool = False) -> float:
        """Возвращает НОВЫЙ интервал для подсистемы. Интервал растёт, если
        подсистема признана перегруженной, и медленно возвращается к базовому."""
        if overloaded:
            return min(interval * 1.5, 300.0)
        if now - last_run > interval * 3:        # давно не бегали — ужимаемся
            return max(interval * 0.9, 5.0)
        return interval


def cpu_percent() -> Optional[float]:
    """CPU загрузка, если доступен psutil, иначе None."""
    try:
        import psutil  # type: ignore
        return psutil.cpu_percent(interval=None)
    except Exception:
        return None
