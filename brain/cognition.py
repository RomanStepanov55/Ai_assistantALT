"""Cognitive pipeline v2: how Alontito thinks, step by step.

This is the runtime scaffold around the OWN brain (alontito/model.py). The
weights stay the user's own; the scaffold is what makes a small local model
behave like a careful agent instead of a slot machine:

    route effort -> decompose -> plan+draft -> independent critique rounds
    -> grounding check against memory -> confidence-tagged answer

Design decisions:
  - Every model step goes through one injected ``chat_fn`` (messages,
    on_delta) -> str. Nothing here knows about HTTP: the agent wires it to
    ModelGateway, tests wire it to a stub. Stdlib only.
  - Deterministic layers (effort routing, decomposition, grounding) work
    with zero model calls, so honesty does not depend on the model being
    smart: a claim's support is measured against the memory store, never
    against the model's opinion of itself.
  - A critique round that comes back malformed never destroys a usable
    draft (learned from the Omni lab: "invalid_review_format" keeps it).
  - The answer carries metadata (effort, rounds, grounding, confidence) so
    the autonomy engine can MEASURE thinking quality instead of guessing
    whether a tweak helped.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Effort levels. Higher effort = more review rounds and an explicit plan.
EFFORT_ORDER = ("low", "medium", "high", "max")

MAX_EFFORT_HINTS = ("напиши модуль", "создай навык", "спроектируй", "спланируй",
                    "архитектур", "разберись почему", "найди причину",
                    "пошагово реши", "стратегия", "исследуй")
HIGH_EFFORT_HINTS = ("объясни", "почему", "сравни", "проанализируй",
                     "как лучше", "в чем разница", "в чём разница", "подробно",
                     "рассуждай", "оцени", "придумай")
MEDIUM_EFFORT_HINTS = ("как ", "что такое", "зачем", "когда ", "где ",
                       "сколько", "можно ли", "расскажи")


def route_effort(text: str) -> str:
    """Deterministic effort routing. The model is not asked how hard to think.

    max   - building/fixing something (module, skill, architecture, diagnosis)
    high  - reasoning/analysis is requested or the question is long
    medium- ordinary question
    low   - small talk, short factual
    """
    low = (text or "").strip().lower()
    if any(h in low for h in MAX_EFFORT_HINTS):
        return "max"
    if any(h in low for h in HIGH_EFFORT_HINTS) or len(low) > 160:
        return "high"
    if len(low) > 60 or low.rstrip().endswith("?") or \
            any(h in low for h in MEDIUM_EFFORT_HINTS):
        return "medium"
    return "low"


def rounds_for_effort(effort: str, max_rounds: int = 2) -> int:
    """Review rounds per effort. low = none (latency matters, risk doesn't).

    max_rounds comes from the selfdev ledger (autonomy may tune it 1..3):
    high/max get all of them, medium gets at most one, low gets none.
    """
    if effort in ("max", "high"):
        return max(1, min(3, int(max_rounds)))
    if effort == "medium":
        return min(1, max(0, int(max_rounds)))
    return 0


def decompose(text: str) -> List[str]:
    """Split a composite request into sub-questions (heuristic, no model).

    Used to structure the plan prompt; an empty list means "single task".
    """
    parts = [p.strip(" ;\t") for p in re.split(r"(?:\n+|;+|, затем | и затем | и потом )",
                                               (text or "").strip())
             if len(p.strip()) > 3]
    if len(parts) <= 1:
        questions = [q.strip() for q in (text or "").split("?") if len(q.strip()) > 3]
        parts = [q if q.endswith("?") else q + "?" for q in questions] or parts
    return parts[:6]


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class Grounding:
    """What in the draft has explicit support, measured not felt."""
    numbers_total: int = 0
    numbers_supported: int = 0
    unsupported: List[str] = field(default_factory=list)
    personal: bool = False        # question is about the user's own world

    @property
    def confidence(self) -> str:
        if self.numbers_total == 0:
            return "grounded"
        if self.numbers_supported == self.numbers_total:
            return "grounded"
        if self.numbers_supported == 0 and self.personal:
            return "unverified"
        return "partial"

    def to_dict(self) -> Dict[str, Any]:
        return {"numbers_total": self.numbers_total,
                "numbers_supported": self.numbers_supported,
                "unsupported": self.unsupported[:8],
                "personal": self.personal,
                "confidence": self.confidence}


PERSONAL_MARKS = ("мой", "моя", "моё", "мое", "мы ", "у меня", "я говорил",
                  "я сказал", "мы решали", "мы решили", "я просил",
                  "напоминал", "записывал", "мне ")


def grounding_check(question: str, answer: str, facts: List[Dict],
                    lessons: List[Dict]) -> Grounding:
    """Point every number in the draft at its source, or flag it.

    A number counts as supported when it appears in the user's question,
    in a memory fact, or in a past lesson (that IS its source). Personal
    questions get the strictest verdict: an unsupported number there is
    exactly the hallucination this project refuses to ship.
    """
    hay = " ".join([
        question or "",
        " ".join((f.get("text") or "") for f in facts or []),
        " ".join((l.get("lesson") or l.get("outcome") or "") for l in lessons or []),
    ])
    hay_numbers = set(_NUMBER_RE.findall(hay.replace(",", ".")))
    hay_numbers |= set(_NUMBER_RE.findall((question or "").replace(",", ".")))
    nums = _NUMBER_RE.findall((answer or "").replace(",", "."))
    g = Grounding(numbers_total=len(nums))
    q_low = (question or "").lower()
    g.personal = any(m in q_low for m in PERSONAL_MARKS)
    seen = set()
    for n in nums:
        if n in hay_numbers or n.replace(".", ",") in hay_numbers:
            g.numbers_supported += 1
        elif n not in seen:
            g.unsupported.append(n)
        seen.add(n)
    return g


@dataclass
class CognitionResult:
    answer: str
    effort: str
    subtasks: List[str]
    plan: List[str]
    reviews: List[Dict[str, Any]]
    grounding: Dict[str, Any]
    confidence: str
    timestamp: float

    def meta(self) -> Dict[str, Any]:
        """The part that goes into the reply metadata (and the metrics)."""
        return {"effort": self.effort, "rounds": len(self.reviews),
                "reviews": self.reviews, "grounding": self.grounding,
                "confidence": self.confidence}


PLAN_MARKER = "PLAN:"
DRAFT_MARKER = "DRAFT:"
REVIEW_PASS = "REVIEW: PASS"
REVIEW_FIXED = "REVIEW: FIXED"
FINAL_MARKER = "FINAL:"


def _split_plan_draft(raw: str) -> (List[str], str):
    """Parse "PLAN: ... DRAFT: ...". Missing markers = all of it is the draft.

    Small local models routinely forget the markers; the pipeline must not
    mistake a formatting slip for a refusal to think.
    """
    raw = (raw or "").strip()
    plan, draft = [], raw
    up = raw.upper()
    if PLAN_MARKER in up and DRAFT_MARKER in up:
        p_start = raw.upper().index(PLAN_MARKER) + len(PLAN_MARKER)
        d_start = raw.upper().index(DRAFT_MARKER)
        if d_start > p_start:
            plan_block = raw[p_start:d_start].strip()
            draft = raw[d_start + len(DRAFT_MARKER):].strip()
            plan = [ln.strip(" -*•\t") for ln in plan_block.splitlines()
                    if ln.strip().strip(" -*•\t")]
    return plan, draft


def _parse_review(raw: str) -> Dict[str, Any]:
    """Parse a critic verdict. Malformed critic output never overwrites a draft."""
    up = (raw or "").upper()
    if REVIEW_FIXED in up and FINAL_MARKER in raw:
        revised = raw[raw.index(FINAL_MARKER) + len(FINAL_MARKER):].strip()
        if revised:
            return {"status": "fixed", "answer": revised}
        return {"status": "invalid_review_format", "answer": ""}
    if REVIEW_PASS in up:
        return {"status": "passed", "answer": ""}
    return {"status": "invalid_review_format", "answer": ""}


class CognitiveCore:
    """One pipeline instance per agent. chat_fn is the ONLY door to the brain."""

    def __init__(self, chat_fn: Optional[Callable[..., str]] = None,
                 memory=None, experience=None, max_review_rounds: int = 2):
        self.chat_fn = chat_fn
        self.memory = memory
        self.experience = experience
        self.max_review_rounds = max_review_rounds

    # ------------------------------------------------------------ memory
    def _recall(self, text: str, limit: int = 4) -> Dict[str, List]:
        facts: List[Dict] = []
        if self.memory is not None:
            try:
                words = [w for w in
                         "".join(c if c.isalnum() else " " for c in (text or "").lower()).split()
                         if len(w) >= 4]
                for w in words:
                    for fact in self.memory.find_facts(w):
                        if fact not in facts:
                            facts.append(fact)
                    if len(facts) >= limit:
                        break
                if not facts:
                    facts = self.memory.facts()[-limit:]
            except Exception:                               # memory must not block thinking
                facts = []
        lessons: List[Dict] = []
        if self.experience is not None:
            try:
                lessons = self.experience.recall(text, limit=limit)
            except Exception:
                lessons = []
        return {"facts": facts, "lessons": lessons}

    # ------------------------------------------------------------ thinking
    def answer(self, text: str, *,
               history: Optional[List[Dict[str, str]]] = None,
               on_delta: Optional[Callable[[str], None]] = None) -> CognitionResult:
        """Full pipeline for one user message. Raises ModelUnavailable upstream."""
        if self.chat_fn is None:
            raise RuntimeError("CognitiveCore has no chat_fn: brain is not wired")
        effort = route_effort(text)
        subtasks = decompose(text) if effort in ("high", "max") else []
        rec = self._recall(text)
        rounds = rounds_for_effort(effort, self.max_review_rounds)

        plan_hint = ""
        if subtasks:
            plan_hint = ("Запрос состоит из нескольких частей:\n" +
                         "\n".join(f"- {s}" for s in subtasks) +
                         "\nРазбери каждую часть в плане.\n")
        sys_prompt = (
            "Ты — Алонтито, личный ИИ-агент. Отвечай по-русски, коротко и по делу. "
            "Честность важнее полноты: если данных не хватает — скажи «не подтверждено» "
            "и назови, чего не хватает. Догадка никогда не выдаётся за факт. "
            "Никаких игр, покупок и действий на компьютере без подтверждения.\n"
            "[Память по теме — факты имеют статусы, ГИПОТЕЗА и НЕ ПОДТВЕРЖДЕНО это не знания]:\n"
            + self._memory_text(rec))
        history_msgs = [m for m in (history or [])[-8:]
                        if isinstance(m, dict)
                        and m.get("role") in ("user", "assistant")
                        and (m.get("content") or "").strip()]

        base = [{"role": "system", "content": sys_prompt}] + history_msgs
        if effort in ("high", "max"):
            draft_prompt = (
                f"{plan_hint}Методично реши запрос:\n"
                f"1) составь краткий план (3-6 пунктов), отметь неизвестное;\n"
                f"2) дай черновик ответа.\n"
                f"Формат строго: PLAN: (пункты списком) затем DRAFT: (ответ).\n\n{text[:8000]}")
        else:
            draft_prompt = text[:8000]
        draft_raw = self.chat_fn(base + [{"role": "user", "content": draft_prompt}],
                                 on_delta=on_delta)
        plan, answer = _split_plan_draft(draft_raw)
        if not answer.strip():
            answer = draft_raw.strip()

        # ---- independent critique rounds --------------------------------
        reviews: List[Dict[str, Any]] = []
        for round_no in range(1, rounds + 1):
            critique_prompt = (
                f"Независимая проверка ответа (проход {round_no}/{rounds}).\n"
                f"Вопрос пользователя: {text[:2000]}\nЧерновик: {answer[:6000]}\n"
                "Проверь: соответствует ли ответ вопросу; опирается ли на память "
                "и честно ли помечает неизвестное; логика; полнота; противоречия. "
                "Не добавляй выдуманных фактов. Если дефектов нет — верни ровно "
                "REVIEW: PASS. Если есть — верни REVIEW: FIXED, краткое объяснение "
                "и после маркера FINAL: исправленный ответ целиком.")
            critique_raw = self.chat_fn(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": critique_prompt}])
            verdict = _parse_review(critique_raw)
            if verdict["status"] == "fixed":
                answer = verdict["answer"]
                reviews.append({"round": round_no, "status": "fixed"})
                continue
            reviews.append({"round": round_no, "status": verdict["status"]})
            if verdict["status"] == "passed":
                break

        # ---- grounding: point numbers at sources ------------------------
        g = grounding_check(text, answer, rec["facts"], rec["lessons"])
        confidence = g.confidence
        if confidence == "unverified":
            answer += ("\n\n⚠ Числа в ответе не подтверждаются памятью — "
                       "проверь их, прежде чем полагаться.")
        return CognitionResult(answer=answer.strip(), effort=effort,
                               subtasks=subtasks, plan=plan, reviews=reviews,
                               grounding=g.to_dict(), confidence=confidence,
                               timestamp=time.time())

    @staticmethod
    def _memory_text(rec: Dict[str, List]) -> str:
        lines = []
        for f in rec["facts"]:
            lines.append(f"- [факт · {f.get('status')}] {f.get('text')}")
        for l in rec["lessons"]:
            lesson = (l.get("lesson") or l.get("outcome") or "").strip()
            if lesson:
                lines.append(f"- [опыт прошлого] {lesson}")
        return "\n".join(lines) if lines else "(память по теме пуста)"
