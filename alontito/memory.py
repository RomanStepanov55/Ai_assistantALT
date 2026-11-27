"""Persistent memory for Alontito.

Everything the agent claims must be able to point at WHERE it came from, so
memory stores facts with one of nine evidence statuses and never a bare
number. Two stores:

  data/alontito/memory.json    - skills, facts, open questions, runs
  data/alontito/journal.jsonl  - append-only event log (one JSON per line)

Writes are atomic (tmp file + replace) because a half-written brain is worse
than no brain: the next start would read a truncated JSON and silently forget
everything.
"""
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = ROOT / "data" / "alontito"
MEMORY_FILE = MEMORY_DIR / "memory.json"
JOURNAL_FILE = MEMORY_DIR / "journal.jsonl"

# The nine evidence statuses of this project. A statement without one of these
# is not knowledge, it is a guess.
STATUS_OFFICIAL = "ПОДТВЕРЖДЕНО ОФИЦИАЛЬНЫМ ИСТОЧНИКОМ"
STATUS_INTERFACE = "ПОДТВЕРЖДЕНО ИНТЕРФЕЙСОМ"
STATUS_MEASURED = "ИЗМЕРЕНО В ЭКСПЕРИМЕНТЕ"
STATUS_SECONDARY = "СООБЩАЕТСЯ ВТОРИЧНЫМ"
STATUS_HYPOTHESIS = "ГИПОТЕЗА"
STATUS_OUTDATED = "УСТАРЕВШИЕ"
STATUS_CONTRADICTION = "ПРОТИВОРЕЧИЕ"
STATUS_UNCONFIRMED = "НЕ ПОДТВЕРЖДЕНО"
STATUS_OTHER_GAME = "ОТНОСИТСЯ К ДРУГОЙ ИГРЕ"

STATUSES = (STATUS_OFFICIAL, STATUS_INTERFACE, STATUS_MEASURED, STATUS_SECONDARY,
            STATUS_HYPOTHESIS, STATUS_OUTDATED, STATUS_CONTRADICTION,
            STATUS_UNCONFIRMED, STATUS_OTHER_GAME)

MISSING = "В доступных источниках не подтверждено. Нужна проверка внутри игры"


@dataclass
class SkillStat:
    """Measured performance of one capability, not a guess about it."""
    name: str
    attempts: int = 0
    successes: int = 0
    reward: float = 0.0          # strength gained, or other metric units
    seconds: float = 0.0
    last_error: str = ""
    last_used: float = 0.0
    notes: str = ""

    @property
    def success_rate(self) -> Optional[float]:
        return (self.successes / self.attempts) if self.attempts else None

    @property
    def per_minute(self) -> Optional[float]:
        if not self.reward or not self.seconds:
            return None
        return self.reward / self.seconds * 60.0

    def row(self):
        rate = self.success_rate
        per_min = self.per_minute
        return [
            self.name,
            str(self.attempts),
            "—" if rate is None else f"{rate * 100:.0f}%",
            "—" if per_min is None else f"{per_min:,.0f}",
            f"{self.seconds / self.attempts:.0f}s" if self.attempts else "—",
            self.last_error[:28] or "—",
        ]


class Memory:
    VERSION = 1

    def __init__(self, path: Path = MEMORY_FILE):
        self.path = Path(path)
        self.data: Dict = {
            "version": self.VERSION,
            "created": time.time(),
            "skills": {},
            "facts": [],
            "questions": [],
            "runs": [],
            "total_strength_gain": 0,
            "sessions": 0,
        }
        self.load()

    # ------------------------------------------------------------------ io

    def load(self):
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:
            broken = self.path.with_suffix(".broken.json")
            try:
                self.path.replace(broken)
            except Exception:
                pass
            self.journal("memory_unreadable", error=str(e), moved_to=str(broken))
            return
        if isinstance(raw, dict):
            self.data.update(raw)
            self.data.setdefault("skills", {})
            self.data.setdefault("facts", [])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def journal(self, kind: str, **payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.time(), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "kind": kind, **payload}
        with self.path.parent.joinpath(JOURNAL_FILE.name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # -------------------------------------------------------------- skills

    def skill(self, name: str) -> SkillStat:
        raw = self.data["skills"].get(name)
        stat = SkillStat(name=name)
        if raw:
            for key, value in raw.items():
                if hasattr(stat, key):
                    setattr(stat, key, value)
        return stat

    def record_skill(self, name: str, success: bool, reward: float = 0.0,
                     seconds: float = 0.0, error: str = "", notes: str = ""):
        stat = self.skill(name)
        stat.attempts += 1
        if success:
            stat.successes += 1
        stat.reward += max(0.0, float(reward or 0.0))
        stat.seconds += max(0.0, float(seconds or 0.0))
        stat.last_used = time.time()
        stat.last_error = (error or "")[:120]
        if notes:
            stat.notes = notes
        self.data["skills"][name] = asdict(stat)
        self.save()
        self.journal("skill", name=name, success=success, reward=reward,
                     seconds=round(seconds, 1), error=error)
        return stat

    def skills(self) -> List[SkillStat]:
        return sorted((self.skill(n) for n in self.data["skills"]),
                      key=lambda s: s.name)

    # --------------------------------------------------------------- facts

    def add_fact(self, text: str, status: str, source: str,
                 date: Optional[str] = None) -> Dict:
        if status not in STATUSES:
            raise ValueError(f"unknown evidence status: {status}")
        fact = {"text": text, "status": status, "source": source,
                "date": date or time.strftime("%Y-%m-%d"), "added": time.time()}
        self.data["facts"].append(fact)
        self.save()
        self.journal("fact", text=text, status=status, source=source)
        return fact

    def facts(self, status: Optional[str] = None) -> List[Dict]:
        items = self.data.get("facts", [])
        if status:
            items = [f for f in items if f.get("status") == status]
        return items

    def find_facts(self, needle: str) -> List[Dict]:
        needle = (needle or "").lower()
        return [f for f in self.data.get("facts", [])
                if needle in (f.get("text") or "").lower()]

    # ---------------------------------------------------------- questions

    def add_question(self, question: str, metric: str, how: str) -> Dict:
        for q in self.data["questions"]:
            if q.get("question") == question:
                return q
        item = {"question": question, "metric": metric, "how": how,
                "status": "открыт", "created": time.time(), "tries": 0}
        self.data["questions"].append(item)
        self.save()
        self.journal("question", question=question, metric=metric)
        return item

    def open_questions(self) -> List[Dict]:
        return [q for q in self.data.get("questions", [])
                if q.get("status") == "открыт"]

    def mark_question(self, question: str, status: str):
        for q in self.data["questions"]:
            if q.get("question") == question:
                q["status"] = status
        self.save()

    # --------------------------------------------------------------- runs

    def record_run(self, gain: Optional[float], seconds: float, note: str = ""):
        run = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "gain": gain,
               "seconds": round(seconds, 1),
               "per_minute": (round(gain / seconds * 60) if gain and seconds else None),
               "note": note}
        self.data["runs"].append(run)
        self.data["runs"] = self.data["runs"][-100:]
        if gain and gain > 0:
            self.data["total_strength_gain"] = (self.data.get("total_strength_gain") or 0) + gain
        self.save()
        self.journal("run", **run)
        return run

    # ------------------------------------------------------------- summary

    def select_game(self, name: str, place_id: int):
        self.data["selected_game"] = {"name": name, "place_id": place_id}
        self.save()

    def selected_game(self) -> Optional[Dict]:
        return self.data.get("selected_game")

    def best_skill(self) -> Optional[SkillStat]:
        candidates = [s for s in self.skills() if s.attempts >= 1]
        if not candidates:
            return None
        return max(candidates, key=lambda s: (s.per_minute or 0, s.success_rate or 0))

    def session_tick(self):
        self.data["sessions"] = int(self.data.get("sessions") or 0) + 1
        self.save()
