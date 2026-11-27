"""The brain of Alontito: reflect, remember lessons, route free text.

Three jobs, nothing else:

  reflect()          - compare a measured run with the baseline before it
  summary()          - what is known from the recorded runs
  interpret()        - route a human sentence to a terminal command

HARD RULE #2 (2026-11-24) and the 2026-11-27 pivot: Alontito does not play and
there is no game window to plan about any more, so the old plan() with its
launch/observe/wait decisions is gone. "не смог измерить ≠ получилось" still
applies: a guess never becomes an "improvement" here.
"""
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from .memory import (MISSING, STATUS_CONTRADICTION,
                     STATUS_MEASURED, STATUS_UNCONFIRMED, Memory)
from .selfdev import (add_lesson, note_verdict,
                      status as selfdev_status)

# A run counts as "the same" as the baseline when it stays inside this band.
# 10% is the smallest band that is not noise for the rates this project
# measured (a single verified window varies by ~20% between repeats).
SAME_BAND = 0.10


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

        Since the pivot Alontito has no routine of its own to improve. It
        always answers None; /learn says so instead of inventing a change.
        """
        return None

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
    ("/autonomy", ("саморазвит", "что развиваешь", "как ты развиваешься",
                   "эксперимент")),
    ("/learn", ("учись", "что дальше", "что известно")),
    ("/memory", ("память", "что знаешь", "факты")),
    ("/skills", ("навык", "умения", "навыки")),
    ("/safety", ("безопас", "правил", "запрещ")),
    ("/mcp", ("мкп", "мсп", "mcp", "мост", "подключи ии")),
    ("/3d", ("3d", "3д", "маскот", "питомец", "компаньон", "персонаж")),
    ("/quit", ("выход", "пока", "закройся", "quit")),
]


def interpret(text: str) -> Optional[str]:
    """Map a human sentence to a command, or None when unsure.

    Returning None is a feature: since the pivot free text goes to the agent
    chat (alontito/agent.py), so a phrase that looks like small talk must not
    silently become a command. Playing phrases route to nothing at all.
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
