"""The brain of Alontito: decide, measure, reflect, keep what works.

Three jobs, nothing else:

  plan()      - what is the single next thing to do, and WHY (with evidence)
  reflect()   - compare a measured run with the baseline before it
  propose()   - the one improvement the data currently supports

The Agent Builder rule is enforced structurally: reflect() returns WORSE and
propose() refuses when there is no measurement. A guess never becomes a
"improvement" here, because a wrong change that looks good in a log is worse
than no change at all.
"""
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .memory import (MISSING, STATUS_CONTRADICTION, STATUS_HYPOTHESIS,
                     STATUS_MEASURED, STATUS_UNCONFIRMED, Memory)
from .selfdev import (add_lesson, get_active_params, next_skill_name,
                      note_verdict, register_proposal, status as selfdev_status)

# A run counts as "the same" as the baseline when it stays inside this band.
# The HUD rounds strength to two significant digits, which is about +-0.4% at
# 2.7M, and one set varies by 70k-390k depending on weight, so 10% is the
# smallest band that is not noise.
SAME_BAND = 0.10
# Below this many attempts a success rate is not evidence of anything.
MIN_ATTEMPTS_FOR_RATE = 3
# A set that takes longer than this is worth looking at: verified sets run
# ~25-40 s, blind reads make them 85-115 s.
SLOW_SET_SECONDS = 90.0


@dataclass
class Decision:
    action: str                 # launch | observe | watch | close_dialog | train | wait | ask
    reason: str
    evidence: str = STATUS_UNCONFIRMED
    parameters: Dict = field(default_factory=dict)
    question: str = ""


@dataclass
class Verdict:
    name: str
    before: Optional[float]
    after: Optional[float]
    delta: Optional[float]
    per_minute: Optional[float]
    baseline: Optional[float]
    verdict: str                # лучше | хуже | без изменений | не подтверждено
    evidence: str
    text: str
    rollback: bool = False


class Brain:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.data = memory.data
        self.data.setdefault("baseline", {})
        self.data.setdefault("history", [])

    # ------------------------------------------------------------------
    # planning
    # ------------------------------------------------------------------

    def plan(self, health: Optional[dict] = None,
             attached: bool = False) -> Decision:
        """Choose ONE action from what is actually known right now.

        health is what the last read returned: {'is_game': bool, 'strength':
        int|None, 'gems': int|None, 'modal_open': bool, 'events': [...]}.
        """
        health = health or {}

        if not attached:
            return Decision(
                "launch",
                "окно Roblox не найдено — без окна играть негде",
                STATUS_MEASURED,
                {"hint": "/games затем /play muscle"},
            )

        if health and not health.get("is_game", True):
            return Decision(
                "wait",
                "окно есть, но HUD игры не виден: загрузка, меню или вкладка "
                "клиента (после сессии клиент показывает свои страницы)",
                STATUS_MEASURED,
                {"hint": "/status через 10-20 секунд"},
            )

        if health.get("modal_open"):
            return Decision(
                "close_dialog",
                "открыт диалог: он перекрывает область кликов и съедает подходы",
                STATUS_MEASURED,
            )

        if not health or health.get("strength") is None:
            return Decision(
                "observe",
                "сила не прочитана — сначала нужен читаемый кадр, потом действия",
                STATUS_UNCONFIRMED,
                {"hint": "/status или /attach"},
            )

        train = self.memory.skill("train")
        rate = train.success_rate
        if train.attempts >= MIN_ATTEMPTS_FOR_RATE and rate is not None and rate < 0.6:
            return Decision(
                "observe",
                f"подходы проходят только в {rate * 100:.0f}% случаев "
                f"({train.successes}/{train.attempts}) — сначала надо понять "
                f"причину, а не долбить дальше",
                STATUS_MEASURED,
                {"hint": "/learn покажет предложение"},
            )

        if rate is None or rate >= 0.6:
            preferred = next_skill_name(self.memory.skills()) or "train"
            params = get_active_params(self.data)
            cycle = selfdev_status(self.data)
            extra = ""
            active = cycle.get("active")
            if active:
                extra = f"; открыт эксперимент: {active.get('change')}"
            return Decision(
                "train",
                "HUD читается, диалога нет, предыдущие подходы проходили" + extra,
                STATUS_MEASURED,
                {"sets": params.get("sets", 3), "action": params.get("action", "TRAIN_DUMBBELLS"), "skill": preferred},
            )

        return Decision("observe", "нет данных для решения", STATUS_UNCONFIRMED)

    # ------------------------------------------------------------------
    # reflection
    # ------------------------------------------------------------------

    def baseline(self, name: str) -> Optional[float]:
        item = self.data["baseline"].get(name) or {}
        value = item.get("per_minute")
        return float(value) if value else None

    def reflect(self, name: str, before: Optional[float], after: Optional[float],
                seconds: float) -> Verdict:
        """Compare one measured run with the baseline stored before it.

        No measurement means no verdict. This is the whole point: a change is
        never declared good because it felt good.
        """
        if before is None or after is None or seconds <= 0:
            return Verdict(name, before, after, None, None, self.baseline(name),
                           "не подтверждено", STATUS_UNCONFIRMED,
                           "замер неполный: нет числа до/после или времени — "
                           f"{MISSING}. Улучшение не засчитывается.",
                           rollback=False)

        delta = after - before
        per_minute = delta / seconds * 60.0
        base = self.baseline(name)
        record = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "delta": delta,
                  "per_minute": round(per_minute), "seconds": round(seconds, 1)}
        self.data["history"].append({"name": name, **record})
        self.data["history"] = self.data["history"][-200:]

        if base is None:
            verdict, rollback = "без изменений", False
            text = ("первый замер записан как базовая линия; сравнивать пока "
                    "не с чем")
            self.data["baseline"][name] = {"per_minute": round(per_minute),
                                           "at": record["at"]}
        else:
            ratio = (per_minute / base) if base else 0.0
            if ratio > 1.0 + SAME_BAND:
                verdict, rollback = "лучше", False
                text = (f"{per_minute:,.0f}/мин против базовой {base:,.0f}/мин "
                        f"({(ratio - 1) * 100:+.0f}%) — изменение остаётся")
                self.data["baseline"][name] = {"per_minute": round(per_minute),
                                               "at": record["at"]}
            elif ratio < 1.0 - SAME_BAND:
                verdict, rollback = "хуже", True
                text = (f"{per_minute:,.0f}/мин против базовой {base:,.0f}/мин "
                        f"({(ratio - 1) * 100:+.0f}%) — откатить изменение")
            else:
                verdict, rollback = "без изменений", False
                text = (f"{per_minute:,.0f}/мин против базовой {base:,.0f}/мин — "
                        f"разница внутри полосы шума ±{int(SAME_BAND * 100)}%")

        if delta <= 0:
            verdict, rollback = "не подтверждено", False
            text = (f"прирост {delta:+,} — нулевой или отрицательный, это не "
                    f"улучшение. {MISSING}.")

        self.memory.journal("reflection", name=name, verdict=verdict,
                            delta=delta, per_minute=round(per_minute),
                            baseline=base)
        note_verdict(self.data, verdict, text,
                     per_minute if delta > 0 else None)
        self.memory.save()
        return Verdict(name, before, after, delta, per_minute, base, verdict,
                       STATUS_MEASURED if delta > 0 else STATUS_UNCONFIRMED,
                       text, rollback)

    # ------------------------------------------------------------------
    # improvement proposal
    # ------------------------------------------------------------------

    def propose(self) -> Optional[Dict]:
        """The ONE change the measurements support, or None.

        Order matters: a real error beats a speed idea, because an error
        corrupts data while slowness only costs time.
        """
        train = self.memory.skill("train")

        if train.attempts and "dialog" in (train.last_error or "").lower():
            return self._proposal(
                "диалог перекрывает клики",
                "закрывать диалог до подхода, а не после неудачи",
                "доля подтверждённых подходов",
                "если доля не вырастет за 3 подхода — вернуть прежний порядок",
                STATUS_MEASURED,
            )

        if train.attempts >= MIN_ATTEMPTS_FOR_RATE and train.success_rate is not None \
                and train.success_rate < 0.6:
            return self._proposal(
                f"подходы проходят {train.successes}/{train.attempts}",
                "проверять подтверждение экипировки перед первым повтором",
                "доля подтверждённых подходов",
                "если доля не вырастет за 3 подхода — откатить проверку",
                STATUS_HYPOTHESIS,
            )

        if train.attempts and train.seconds and \
                train.seconds / train.attempts > SLOW_SET_SECONDS:
            return self._proposal(
                f"подход занимает {train.seconds / train.attempts:.0f} с",
                "считать прирост только из подтверждения внутри подхода",
                "секунды на подход",
                "если подтверждённый прирост пропадёт — вернуть внешний замер",
                STATUS_MEASURED,
            )

        if not train.attempts:
            return None
        return None

    def _proposal(self, problem, change, metric, rollback, evidence) -> Dict:
        item = {"problem": problem, "change": change, "metric": metric,
                "rollback": rollback, "evidence": evidence,
                "created": time.time()}
        self.memory.add_question(f"Улучшение: {change}", metric, rollback)
        self.memory.journal("proposal", **{k: v for k, v in item.items()
                                           if k != "created"})
        register_proposal(self.data, item)
        self.memory.save()
        return item

    # ------------------------------------------------------------------
    # reporting
    # ------------------------------------------------------------------

    def summary(self) -> Dict:
        runs = self.data.get("runs") or []
        gains = [r for r in runs if r.get("gain")]
        rates = [r.get("per_minute") for r in gains if r.get("per_minute")]
        skills = self.memory.skills()
        best = self.memory.best_skill()
        verified = sum(1 for s in skills if s.attempts)
        return {
            "sessions": self.data.get("sessions", 0),
            "runs": len(runs),
            "verified_runs": len(gains),
            "total_gain": self.data.get("total_strength_gain", 0),
            "avg_per_minute": (round(sum(rates) / len(rates)) if rates else None),
            "best_skill": (best.name if best else None),
            "best_per_minute": (round(best.per_minute) if best and best.per_minute else None),
            "skills_used": verified,
            "open_questions": len(self.memory.open_questions()),
            "facts": len(self.memory.facts()),
            "contradictions": len(self.memory.facts(STATUS_CONTRADICTION)),
            "selfdev": selfdev_status(self.data),
        }

    def last_runs(self, n: int = 5) -> List[Dict]:
        return list(reversed(self.data.get("runs") or []))[:n]

    def remember_lesson(self, text: str) -> None:
        """A verified lesson. Duplicates are dropped."""
        add_lesson(self.data, text)
        self.memory.save()

    def development(self) -> Dict:
        return selfdev_status(self.data)


# ---------------------------------------------------------------------------
# natural language routing: a small, honest intent matcher
# ---------------------------------------------------------------------------

_INTENTS = [
    ("/auto", ("найди игру", "найди роблокс", "автопоиск", "подключись",
               "найди плейс")),
    ("/play", ("запусти", "запуск", "открой игру", "играть", "врубай")),
    ("/train", ("тренир", "качай", "качнуть", "подход", "гантел", "занимайся")),
    ("/watch", ("смотри", "наблюдай", "понаблюдай", "что видишь", "покажи экран")),
    ("/status", ("статус", "как дела", "проверь", "готов")),
    ("/learn", ("учись", "развивайся", "улучши", "предложи", "что дальше")),
    ("/memory", ("память", "что знаешь", "факты")),
    ("/skills", ("навык", "умения", "навыки")),
    ("/safety", ("безопас", "правил", "запрещ")),
    ("/games", ("игры", "какие игры", "каталог")),
    ("/mcp", ("мкп", "мсп", "mcp", "мост", "подключи ии")),
    ("/3d", ("3d", "3д", "маскот", "питомец", "компаньон", "персонаж")),
    ("/quit", ("выход", "пока", "закройся", "quit")),
]


def interpret(text: str) -> Optional[str]:
    """Map a human sentence to a command, or None when unsure.

    Returning None is a feature: guessing the wrong destructive-sounding
    intent is worse than asking the user to type /help.
    """
    low = (text or "").strip().lower()
    if not low:
        return None
    if low.startswith("/"):
        return low
    for command, needles in _INTENTS:
        if any(n in low for n in needles):
            return command
    return None
