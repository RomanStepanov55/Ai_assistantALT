#!/usr/bin/env python3
"""
NeuroLab — расширенное когнитивное ядро Alontito (v4).

Добавляет к существующему UCB1-планировщику:
  1. ThompsonSamplingBandit — байесовский выбор действий (Beta-распределение),
     устойчивее UCB1 на малых выборках и не требует ручного коэффициента explore.
  2. CuriosityEngine — внутренняя награда за новизну: действия, которые давно
     не пробовали, получают бонус (intrinsic motivation).
  3. ExperimentLab — менеджер гипотез: baseline -> изменение -> замер ->
     вердикт (better / worse / inconclusive) с полосой шума ±10% и откатом.
  4. LessonRegistry — долговременная память уроков (JSON), EMA-оценка темпа
     прироста, автоматический cooldown неудачных действий.
  5. Persistence — все состояния сохраняются в data/neuro_lab.json.

Безопасность: класс не выполняет действий сам, только ранжирует и верифицирует.
Все изменения параметров идут через переданный callback guard(action) -> bool.

Пример интеграции (в brain/adapter_policy.py или рядом с UCB1):

    from brain.neuro_lab import NeuroLab
    lab = NeuroLab.load()                    # или NeuroLab() для чистого старта
    choice = lab.select(["TRAIN_DUMBBELLS", "TRAIN_PUSHUPS", "TRAIN_SITUPS",
                         "TRAIN_MACHINE", "SPIN_WHEEL"])
    ... выполнение ...
    lab.feed(choice, strength_delta=42.0, seconds=63.0, success=True)
    lab.save()

    # эксперимент (полоса шума ±10%, откат):
    before = measure()                                # темп до изменения
    exp = lab.experiments.begin(
        "pushups_faster_rep", "короче пауза между повторами - больше силы/мин",
        baseline_params={"rep_delay": 0.65},          # что было
        baseline_metrics=before,
        trial_params={"rep_delay": 0.55})             # что пробуем
    ... применить exp.trial_params, потренировать ...
    lab.experiments.measure_after(measure())          # темп после
    verdict = lab.experiments.judge(exp)              # better | worse | inconclusive
    if verdict == "worse":
        lab.experiments.rollback(exp)                 # вернуть baseline-параметры

Шкала награды: темп в этой игре - десятки и сотни тысяч единиц в минуту,
поэтому reward в feed() считается относительно лучшего известного темпа, а не
по абсолютной шкале.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

NOISE_BAND = 0.10          # полоса шума ±10% (как в UCB1-ядре)
EMA_ALPHA = 0.25           # скорость EMA-оценки темпа прироста
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "neuro_lab.json"


# ---------------------------------------------------------------------------
# 1. Thompson Sampling (Beta-Bernoulli bandit)
# ---------------------------------------------------------------------------
class ThompsonArm:
    """Одно действие: Beta(alpha, beta) апостериорная оценка успеха."""
    __slots__ = ("alpha", "beta", "attempts", "successes", "total_reward", "last_used")

    def __init__(self):
        self.alpha = 1.0          # априор: 1 псевдо-успех
        self.beta = 1.0           # априор: 1 псевдо-провал
        self.attempts = 0
        self.successes = 0
        self.total_reward = 0.0
        self.last_used = 0.0

    def sample(self) -> float:
        return random.betavariate(self.alpha, self.beta)

    def update(self, reward: float) -> None:
        r = min(max(reward, 0.0), 1.0)
        self.alpha += r
        self.beta += (1.0 - r)
        self.attempts += 1
        self.successes += 1 if r >= 0.5 else 0
        self.total_reward += r
        self.last_used = time.time()

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def to_dict(self) -> dict:
        return {"alpha": self.alpha, "beta": self.beta, "attempts": self.attempts,
                "successes": self.successes, "total_reward": self.total_reward,
                "last_used": self.last_used}

    @classmethod
    def from_dict(cls, d: dict) -> "ThompsonArm":
        a = cls()
        a.alpha = float(d.get("alpha", 1.0)); a.beta = float(d.get("beta", 1.0))
        a.attempts = int(d.get("attempts", 0)); a.successes = int(d.get("successes", 0))
        a.total_reward = float(d.get("total_reward", 0.0))
        a.last_used = float(d.get("last_used", 0.0))
        if not (a.alpha > 0.0 and a.beta > 0.0):
            a.alpha = a.beta = 1.0        # betavariate не определён для 0/NaN
        return a


class ThompsonBandit:
    """Байесовский выбор действий. Действия с малым числом попыток автоматически
    исследуются (широкий Beta), лучшие эксплуатируются."""

    def __init__(self, arms: Optional[Dict[str, ThompsonArm]] = None):
        self.arms: Dict[str, ThompsonArm] = arms or {}

    def ensure(self, name: str) -> ThompsonArm:
        if name not in self.arms:
            self.arms[name] = ThompsonArm()
        return self.arms[name]

    def select(self, candidates: List[str],
               curiosity: Optional["CuriosityEngine"] = None,
               rates: Optional[Dict[str, float]] = None) -> str:
        best, best_score = candidates[0], -1.0
        for name in candidates:
            arm = self.ensure(name)
            score = arm.sample()
            if curiosity is not None:
                score *= (1.0 + curiosity.bonus(name))
            if rates and arm.attempts > 0:
                score *= (1.0 + min(rates.get(name, 0.0) / 100.0, 0.5))
            if score > best_score:
                best, best_score = name, score
        return best

    def update(self, name: str, reward: float) -> None:
        self.ensure(name).update(reward)

    def to_dict(self) -> dict:
        return {k: v.to_dict() for k, v in self.arms.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "ThompsonBandit":
        return cls({k: ThompsonArm.from_dict(v) for k, v in d.items()})


# ---------------------------------------------------------------------------
# 2. Curiosity Engine — внутренняя награда за новизну
# ---------------------------------------------------------------------------
class CuriosityEngine:
    """Бонус = C / sqrt(attempts + 1) — менее пробованные действия интереснее.
    Дополнительно учитывает время с последнего использования (забывание)."""

    def __init__(self, strength: float = 0.35, decay_hours: float = 6.0):
        self.strength = strength
        self.decay_hours = decay_hours

    def bonus(self, name: str, arm: Optional[ThompsonArm] = None,
              attempts: int = 0, last_used: float = 0.0) -> float:
        n = attempts if arm is None else arm.attempts
        t = last_used if arm is None else arm.last_used
        fresh = 1.0
        if t > 0:
            hours = (time.time() - t) / 3600.0
            fresh = 1.0 + min(hours / self.decay_hours, 1.0)
        return self.strength * fresh / math.sqrt(n + 1.0)


# ---------------------------------------------------------------------------
# 3. Experiment Lab — гипотеза -> замер -> вердикт -> (откат)
# ---------------------------------------------------------------------------
@dataclass
class Experiment:
    name: str
    hypothesis: str
    baseline_params: dict
    baseline_metrics: dict = field(default_factory=dict)
    trial_params: Optional[dict] = None    # что проверяем; None = сам baseline
    created_at: float = field(default_factory=time.time)
    measured_after: Optional[dict] = None
    verdict: Optional[str] = None          # better | worse | inconclusive
    locked_params: Optional[dict] = None   # параметры, давшие "better"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Experiment":
        return cls(**d)


class ExperimentLab:
    """Строгий цикл саморазвития. Сравнивает метрики с учётом полосы шума."""

    def __init__(self, guard: Optional[Callable[[str], bool]] = None):
        self.active: Optional[Experiment] = None
        self.lessons: List[dict] = []
        self.guard = guard or (lambda action: True)   # safety: allow-list проверка

    def begin(self, name: str, hypothesis: str,
              baseline_params: dict, baseline_metrics: dict,
              trial_params: Optional[dict] = None) -> Experiment:
        if self.active is not None and self.active.verdict is None:
            raise RuntimeError(f"Experiment '{self.active.name}' is still active")
        self.active = Experiment(name=name, hypothesis=hypothesis,
                                 baseline_params=dict(baseline_params),
                                 baseline_metrics=dict(baseline_metrics),
                                 trial_params=dict(trial_params) if trial_params else None)
        return self.active

    def measure_after(self, metrics: dict) -> Experiment:
        if self.active is None:
            raise RuntimeError("No active experiment")
        self.active.measured_after = dict(metrics)
        return self.active

    @staticmethod
    def _delta(before: dict, after: dict, key: str) -> Optional[float]:
        b, a = before.get(key), after.get(key)
        if b is None or a is None or b == 0:
            return None
        return (a - b) / abs(b)

    def judge(self, exp: Optional[Experiment] = None,
              key: str = "strength_per_min") -> str:
        exp = exp or self.active
        if exp is None or exp.measured_after is None:
            raise RuntimeError("Nothing to judge")
        d = self._delta(exp.baseline_metrics, exp.measured_after, key)
        # в урок попадают параметры, которые ПРОВЕРЯЛИСЬ: планировщик
        # переиспользует их для уроков с вердиктом "better"
        tested = dict(exp.trial_params or exp.baseline_params)
        if d is None:
            exp.verdict = "inconclusive"
        elif d > NOISE_BAND:
            exp.verdict = "better"
            exp.locked_params = tested          # новый эталон
        elif d < -NOISE_BAND:
            exp.verdict = "worse"
        else:
            exp.verdict = "inconclusive"
        self.lessons.append({
            "ts": time.time(), "name": exp.name,
            "hypothesis": exp.hypothesis, "key": key,
            "delta": d, "verdict": exp.verdict,
            "params": tested,
            "baseline_params": dict(exp.baseline_params),
        })
        return exp.verdict

    def rollback(self, exp: Optional[Experiment] = None) -> dict:
        exp = exp or self.active
        if exp is None:
            raise RuntimeError("No experiment to rollback")
        if not self.guard("ROLLBACK"):
            raise PermissionError("Safety guard rejected rollback")
        return dict(exp.baseline_params)

    def to_dict(self) -> dict:
        return {"active": self.active.to_dict() if self.active else None,
                "lessons": self.lessons[-500:]}

    @classmethod
    def from_dict(cls, d: dict, guard=None) -> "ExperimentLab":
        lab = cls(guard=guard)
        if d.get("active"):
            lab.active = Experiment.from_dict(d["active"])
        lab.lessons = list(d.get("lessons", []))
        return lab


# ---------------------------------------------------------------------------
# 4. NeuroLab — фасад: bandit + curiosity + experiments + EMA + persistence
# ---------------------------------------------------------------------------
class NeuroLab:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.bandit = ThompsonBandit()
        self.curiosity = CuriosityEngine()
        self.experiments = ExperimentLab()
        self.ema_rates: Dict[str, float] = {}       # действие -> EMA темп прироста/мин
        self.cooldowns: Dict[str, float] = {}       # действие -> ts до которого бан

    # ---- выбор и обратная связь ----
    def select(self, candidates: List[str]) -> str:
        now = time.time()
        allowed = [c for c in candidates if self.cooldowns.get(c, 0.0) < now] or list(candidates)
        choice = self.bandit.select(allowed, self.curiosity, self.ema_rates)
        for name in allowed:
            arm = self.bandit.ensure(name)
            # curiosity bonus требует arm, вызовем напрямую для симметрии API
            _ = self.curiosity.bonus(name, arm=arm)
        return choice

    def feed(self, action: str, strength_delta: float, seconds: float,
             success: bool, cooldown_on_fail: float = 120.0,
             measured: bool = True) -> float:
        """Обновить модель. Возвращает reward в [0,1].

        Успех = 0.5 + 0.5 * (доля темпа от лучшего известного), неудача = четверть
        от этого. Доля, а не абсолютная шкала: темп измеряется десятками тысяч в
        минуту, и любая фиксированная шкала насыщается для всех действий сразу.
        Берётся EMA-темп: одиночный подход часто даёт 0 из-за округления HUD.

        measured=False (бейдж не читался) - "не смог измерить ≠ получилось"
        (E-001 проекта): нейтральная награда 0.5/0.125, EMA не трогается.
        """
        rate = (strength_delta / seconds * 60.0) if seconds > 0 else 0.0
        if measured:
            prev = self.ema_rates.get(action, rate)
            self.ema_rates[action] = (1 - EMA_ALPHA) * prev + EMA_ALPHA * rate
        reward = 0.5 + 0.5 * self.pace_share(action) if measured else 0.5
        if not success:
            reward *= 0.25
            self.cooldowns[action] = time.time() + cooldown_on_fail
        self.bandit.update(action, reward)
        return reward

    def pace_share(self, action: str) -> float:
        """Доля EMA-темпа действия от лучшего известного темпа, 0..1."""
        best = max(self.ema_rates.values())
        if best <= 0.0:
            return 0.0
        return min(1.0, self.ema_rates.get(action, 0.0) / best)

    # ---- persistence ----
    def save(self) -> None:
        """Атомарная запись: оборванная запись не портит накопленное состояние."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 4,
            "bandit": self.bandit.to_dict(),
            "ema_rates": self.ema_rates,
            "cooldowns": self.cooldowns,
            "experiments": self.experiments.to_dict(),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "NeuroLab":
        """Бросает ValueError, если файл не JSON: вызывающий решает, чем это заменить."""
        p = Path(path)
        lab = cls(p)
        if not p.exists():
            return lab
        d = json.loads(p.read_text(encoding="utf-8"))
        lab.bandit = ThompsonBandit.from_dict(d.get("bandit", {}))
        lab.ema_rates = {k: float(v) for k, v in d.get("ema_rates", {}).items()}
        lab.cooldowns = {k: float(v) for k, v in d.get("cooldowns", {}).items()}
        lab.experiments = ExperimentLab.from_dict(d.get("experiments", {}))
        return lab

    # ---- отчёт ----
    def report(self) -> str:
        lines = ["=== NeuroLab v4 report ==="]
        for name, arm in sorted(self.bandit.arms.items(),
                                key=lambda kv: -kv[1].mean):
            lines.append(
                f"{name:20s} mean={arm.mean:.3f} attempts={arm.attempts:4d} "
                f"ema_rate={self.ema_rates.get(name, 0.0):7.2f}/min "
                f"cooldown={'yes' if self.cooldowns.get(name, 0) > time.time() else 'no'}")
        if self.experiments.active:
            a = self.experiments.active
            lines.append(f"active experiment: {a.name} (verdict={a.verdict or 'pending'})")
        lines.append(f"lessons recorded: {len(self.experiments.lessons)}")
        return "\n".join(lines)
