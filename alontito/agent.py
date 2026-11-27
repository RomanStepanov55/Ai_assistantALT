"""The Alontito agent: one entry point for every device.

answer(text, device=...) is what the terminal, the web app and future
clients all call. The flow is honest by construction:

  1. explicit skills first - they never need a model:
       «запомни: …»         → a fact with the user as the source
       «напомни …»          → an automation (see automations.py)
       «статус»             → what is actually stored right now
       «что мы решали про X»→ recall from facts + experience memory
  2. otherwise the OWN brain (model.py) answers; the prompt carries the
     recalled facts and lessons, and the reply is stored as a PENDING
     training example - it reaches the dataset only after the user
     approves it on a device.
  3. if the own brain is off/unreachable, an honest offline reply says so.
     Offline replies are never stored as training data: templates are not
     knowledge.

Decision of 2026-11-27: no third-party model answers the user. The gateway
physically has no path to the teacher (tests/test_model.py).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .automations import Automations, parse as parse_reminder
from .memory import STATUS_OFFICIAL, Memory
from .model import (BrainConfig, ModelGateway,
                    ModelUnavailable, offline_reply)

ROOT = Path(__file__).resolve().parent.parent
CHAT_HISTORY_LIMIT = 8

SYSTEM_PROMPT = """Ты — Алонтито, личный ИИ-агент пользователя, живущий на его ПК.
Твоя этика (не обсуждаемая): честность важнее полноты; если данных не хватает —
скажи «не подтверждено» и назови, чего именно не хватает; догадка никогда не
выдаётся за факт; никаких покупок, трейдов и автоматизации игр; любые команды
на компьютере — только через явное подтверждение пользователя.
Отвечай по-русски, коротко и по делу. Ниже — память агента: факты имеют статусы,
«ГИПОТЕЗА» и «НЕ ПОДТВЕРЖДЕНО» — это не знания, а загадки."""


class Agent:
    def __init__(self, memory: Optional[Memory] = None,
                 gateway: Optional[ModelGateway] = None,
                 automations: Optional[Automations] = None,
                 learning=None, experience=None, pc_actions=None):
        self.memory = memory or Memory()
        self.automations = automations or Automations()
        self.pc = pc_actions
        self.learning = learning            # LearningEngine, lazy below
        self.experience = experience        # ExperienceMemory, lazy below
        self._gateway = gateway
        self._cog = None                    # CognitiveCore, lazy below
        self._autonomy_engine = None        # AutonomyEngine, lazy below
        self._synthesizer = None            # SkillSynthesizer, lazy below
        self._brain_error = ""
        try:
            self._gateway = gateway or ModelGateway()
        except ModelUnavailable as exc:     # non-local endpoint in config
            self._gateway = None
            self._brain_error = str(exc)

    def _pc(self):
        if self.pc is None:
            from .pc_actions import PcActions
            self.pc = PcActions()
        return self.pc

    # ------------------------------------------------------------ stores

    def _learning(self):
        if self.learning is None:
            from brain.learning_engine import LearningEngine
            self.learning = LearningEngine()
        return self.learning

    def _experience(self):
        if self.experience is None:
            from brain.experience_memory import ExperienceMemory
            self.experience = ExperienceMemory()
        return self.experience

    # ------------------------------------------------------------ autonomy

    def _autonomy_params(self) -> Dict[str, Any]:
        """Agent-level parameters the self-development ledger tunes."""
        try:
            from .selfdev import get_active_params
            return get_active_params(self.memory.data)
        except Exception:
            return {}

    def _cognition(self):
        """The reasoning pipeline; rebuilt when autonomy tunes review rounds."""
        rounds = int(self._autonomy_params().get("review_rounds", 2))
        if self._cog is None or self._cog.max_review_rounds != rounds:
            from brain.cognition import CognitiveCore
            self._cog = CognitiveCore(chat_fn=self._brain_chat,
                                      memory=self.memory,
                                      experience=self._experience(),
                                      max_review_rounds=rounds)
        return self._cog

    def _brain_chat(self, messages, on_delta=None) -> str:
        if self._gateway is None:
            raise ModelUnavailable("свой мозг не подключён")
        return self._gateway.chat(messages, on_delta=on_delta)["text"]

    def _autonomy(self):
        if self._autonomy_engine is None:
            from brain.autonomy import AutonomyEngine
            self._autonomy_engine = AutonomyEngine(
                memory=self.memory, automations=self.automations,
                learning=self._learning(), experience=self._experience())
        return self._autonomy_engine

    def autonomy_tick(self) -> List[Dict[str, Any]]:
        """One self-development step. Returns events for the UI; never raises."""
        try:
            return self._autonomy().tick()
        except Exception:                                   # autonomy must not break chat
            return []

    def synthesize_skill(self, name: str, task: str) -> Dict[str, Any]:
        """The agent writes its own tool: own brain drafts, AST gate checks,
        smoke test runs it, registry records provenance. Full self-development,
        bounded by construction."""
        from brain.skill_synthesis import SkillSynthesizer
        if self._gateway is None:
            return {"ok": False,
                    "detail": "свой мозг не подключён — синтез навыков требует модель"}
        if self._synthesizer is None:
            self._synthesizer = SkillSynthesizer()
        ok, detail = self._synthesizer.synthesize(name, task,
                                                  chat_fn=self._brain_chat)
        self.memory.journal("skill_synthesis", name=name, ok=ok, detail=detail)
        return {"ok": ok, "detail": detail}

    def synthesized_tools(self) -> List[Dict[str, Any]]:
        """The self-made tools registry (empty until the agent synthesizes)."""
        if self._synthesizer is None:
            return []
        return self._synthesizer.list()

    # ------------------------------------------------------------ status

    def status(self) -> Dict[str, Any]:
        st = self.memory.data
        own: Dict[str, Any] = {"enabled": False, "reachable": False,
                               "model": "", "detail": ""}
        if self._gateway is not None:
            try:
                own = self._gateway.own_status()
            except Exception as exc:                        # noqa: BLE001
                own = {"enabled": False, "reachable": False,
                       "detail": str(exc)}
        if self._brain_error and not own.get("detail"):
            own["detail"] = self._brain_error
        return {
            "sessions": st.get("sessions", 0),
            "facts": len(st.get("facts") or []),
            "open_questions": len(self.memory.open_questions()),
            "skills": len(st.get("skills") or {}),
            "automations": len([a for a in self.automations.list()
                                if a.enabled]),
            "brain": own,
            "training": self._learning().stats(),
            "autonomy": self._autonomy().status(),
        }

    # ------------------------------------------------------------ recall

    def _recall(self, text: str, limit: int = 4) -> Dict[str, List]:
        """Facts and past lessons relevant to the message. Read-only."""
        words = [w for w in
                 "".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split()
                 if len(w) >= 4]
        facts: List[Dict] = []
        for w in words:
            for fact in self.memory.find_facts(w):
                if fact not in facts:
                    facts.append(fact)
            if len(facts) >= limit:
                break
        if not facts:
            facts = self.memory.facts()[-limit:]
        lessons: List[Dict] = []
        try:
            lessons = self._experience().recall(text, limit=limit)
        except Exception:                                   # no store yet
            lessons = []
        return {"facts": facts, "lessons": lessons}

    def _memory_block(self, text: str) -> str:
        rec = self._recall(text)
        lines = []
        for f in rec["facts"]:
            lines.append(f"- [факт · {f.get('status')}] {f.get('text')}")
        for l in rec["lessons"]:
            lesson = (l.get("lesson") or l.get("outcome") or "").strip()
            if lesson:
                lines.append(f"- [опыт прошлого] {lesson}")
        return "\n".join(lines) if lines else "(память по теме пуста)"

    # ------------------------------------------------------------- skills

    def _skill_remember(self, text: str) -> Dict[str, Any]:
        body = text.split(":", 1)[1].strip() if ":" in text else \
            text[len("запомни"):].strip()
        if not body:
            return {"text": "что именно запомнить? Скажи «запомни: …» и факт.",
                    "source": "skill", "skill": "remember"}
        # The user's own word about their own life is the primary source:
        # the fact lands in memory immediately (device ✓ is for the BRAIN's
        # answers, not for what the user said directly).
        fact = self.memory.add_fact(body, STATUS_OFFICIAL, "пользователь")
        return {"text": f"запомнил: «{fact['text']}» (источник: ты, "
                        f"{fact['date']})",
                "source": "skill", "skill": "remember"}

    def _skill_remind(self, text: str) -> Dict[str, Any]:
        auto = parse_reminder(text, time.time())
        if auto is None:
            return {"text": "не понял время. Примеры: «напомни через 20 минут "
                            "…», «напомни завтра в 9:00 …», «каждый день в "
                            "9:00 …», «по будням в 8:30 …».",
                    "source": "skill", "skill": "remind"}
        stored = self.automations.add(auto)
        return {"text": f"поставил напоминание: {stored.when_text()} — "
                        f"{stored.text}",
                "source": "skill", "skill": "remind",
                "automation_id": stored.id}

    def _skill_recall(self, text: str) -> Dict[str, Any]:
        rec = self._recall(text, limit=6)
        lines = []
        for f in rec["facts"]:
            lines.append(f"{f.get('date')} · {f.get('text')} "
                         f"({f.get('status')}, источник: {f.get('source')})")
        for l in rec["lessons"]:
            lesson = (l.get("lesson") or l.get("outcome") or "").strip()
            when = time.strftime("%Y-%m-%d",
                                 time.localtime(l.get("created_at") or 0)) \
                if l.get("created_at") else ""
            if lesson:
                lines.append(f"{when} · опыт: {lesson}".strip())
        if not lines:
            return {"text": "по этому поводу в памяти ничего нет — и я не "
                            "буду выдумывать.",
                    "source": "skill", "skill": "recall"}
        return {"text": "вот что в памяти:\n" + "\n".join(lines[:8]),
                "source": "skill", "skill": "recall"}

    def _skill_status(self) -> Dict[str, Any]:
        st = self.status()
        brain = st["brain"]
        brain_line = (f"мозг: подключён ({brain.get('model')})"
                      if brain.get("enabled") and brain.get("reachable")
                      else "мозг: свой, ещё не подключён" +
                      (f" ({brain.get('detail')})" if brain.get("detail") else ""))
        tr = st["training"]
        return {"text": (f"статус: сессий {st['sessions']}, фактов {st['facts']}, "
                         f"напоминаний {st['automations']}; обучение: примеров "
                         f"{tr.get('examples', 0)}, одобрено {tr.get('approved', 0)}. "
                         + brain_line),
                "source": "skill", "skill": "status"}

    def _skill_autonomy(self) -> Dict[str, Any]:
        """What the agent is developing about itself right now - real data."""
        st = self._autonomy().status()
        metrics = st.get("metrics") or {}
        active = st.get("active")
        lines = []
        if active:
            lines.append(f"эксперимент: {active.get('change')} "
                         f"(попыток {active.get('tries')})")
        else:
            lines.append("активного эксперимента нет — жду измерений")
        if metrics.get("weakest"):
            lines.append(f"слабое место по метрикам: {metrics['weakest']}")
        for key, label in (("memory_health", "память"), ("helpfulness", "полезность"),
                           ("autonomy", "навыки"), ("punctuality", "пунктуальность")):
            value = metrics.get(key)
            if value is not None:
                lines.append(f"{label}: {value}")
        closed, accepted = st.get("closed") or 0, st.get("accepted") or 0
        lessons = st.get("lessons") or []
        lines.append(f"экспериментов закрыто {closed}, принято {accepted}")
        if lessons:
            lines.append("уроки: " + "; ".join(lessons[-3:]))
        return {"text": "саморазвитие (всё измерено, ничего не выдумано):\n" +
                        "\n".join(f"- {l}" for l in lines),
                "source": "skill", "skill": "autonomy"}

    def _try_skills(self, text: str) -> Optional[Dict[str, Any]]:
        low = (text or "").strip().lower()
        if low.startswith("запомни"):
            return self._skill_remember(text)
        if low.startswith("напомни"):
            return self._skill_remind(text)
        if low in ("статус", "как дела", "что по чем"):
            return self._skill_status()
        if any(low.startswith(p) for p in
               ("что мы решали", "что мы решили", "вспомни", "что известно про")):
            return self._skill_recall(text)
        if any(low.startswith(p) for p in
               ("что развиваешь", "что ты развиваешь", "саморазвитие",
                "как ты развиваешься")):
            return self._skill_autonomy()
        if low.startswith(("открой ссылку", "открой сайт", "открой папку",
                           "открой файл", "покажи папку", "прочитай файл")):
            return self._skill_pc(text)
        return None

    def _skill_pc(self, text: str) -> Dict[str, Any]:
        """PC action: propose only; a device must confirm with ✓."""
        low = (text or "").strip().lower()
        from .pc_actions import ActionDenied, PcActionsDisabled
        try:
            if low.startswith(("открой ссылку", "открой сайт")):
                url = text.split(None, 2)[-1].strip()
                request = self._pc().propose("open_url", url=url)
            elif low.startswith(("открой папку", "покажи папку")):
                path = text.split(None, 2)[-1].strip()
                request = self._pc().propose("list_dir", path=path)
            elif low.startswith("открой файл"):
                path = text.split(None, 2)[-1].strip()
                request = self._pc().propose("open_path", path=path)
            else:                                   # «прочитай файл …»
                path = text.split(None, 2)[-1].strip()
                request = self._pc().propose("read_text", path=path)
        except ActionDenied as exc:
            return {"text": f"нет: {exc}", "source": "skill", "skill": "pc"}
        return {
            "text": (f"предложил действие «{request['action']}». Ничего не "
                     f"сделаю, пока вы не подтвердите запрос "
                     f"#{request['request_id']} на любом устройстве "
                     f"(2 минуты). Действия на ПК "
                     + ("включены." if self._pc().enabled else
                        "выключены — включите их в Настройках с ПК.")),
            "source": "skill", "skill": "pc",
            "pc_request": request,
        }

    # ------------------------------------------------------------- answer

    def answer(self, text: str, *, device: str = "pc",
               history: Optional[List[Dict[str, str]]] = None,
               on_delta: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        """One turn with the agent. The only API every client uses."""
        text = (text or "").strip()
        if not text:
            return {"text": "скажи что-нибудь — я здесь.", "source": "skill"}

        skill = self._try_skills(text)
        if skill is not None:
            self.memory.journal("chat", device=device, source=skill["source"],
                                skill=skill.get("skill"), text=text[:200])
            return skill

        if self._gateway is None:
            reply = {"text": offline_reply(text, self.status()),
                     "source": "offline"}
        else:
            # The cognitive pipeline owns the brain path: plan -> draft ->
            # critique rounds -> grounding. It falls back to an honest
            # offline reply when the brain is off/unreachable mid-turn.
            try:
                cog = self._cognition().answer(text, history=history,
                                               on_delta=on_delta)
                try:
                    model_name = self._gateway.cfg.own_model
                except AttributeError:
                    model_name = getattr(self._gateway, "model", "")
                reply = {"text": cog.answer, "source": "own",
                         "model": model_name, "cognition": cog.meta()}
            except ModelUnavailable as exc:
                reply = {"text": offline_reply(text, self.status()),
                         "source": "offline", "brain_error": str(exc)}

        self._after_answer(text, reply, device=device)
        return reply

    def _build_messages(self, text: str,
                        history: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Legacy single-shot prompt builder, kept for MCP/tools diagnostics."""
        messages = [{"role": "system",
                     "content": SYSTEM_PROMPT + "\n\n[Память по теме]:\n" +
                                self._memory_block(text)}]
        for item in history[-CHAT_HISTORY_LIMIT:]:
            if isinstance(item, dict) and item.get("role") in ("user", "assistant") \
                    and (item.get("content") or "").strip():
                messages.append({"role": item["role"],
                                 "content": item["content"][:4000]})
        messages.append({"role": "user", "content": text[:8000]})
        return messages

    # ---------------------------------------------------- learning loop

    def _after_answer(self, prompt: str, reply: Dict[str, Any],
                      device: str) -> None:
        """Record the exchange for the own brain's training - PENDING only."""
        self.memory.journal("chat", device=device, source=reply.get("source"),
                            text=prompt[:200])
        if reply.get("source") != "own":
            return                      # offline templates are not knowledge
        try:
            self._learning().add_example(
                prompt, reply["text"], lesson="",
                source=f"chat:{device}",
                evidence="ЖДЁТ ОДОБРЕНИЯ на устройстве",
                approved=False)
        except Exception:                                   # learning must not break chat
            pass
        try:
            self._experience().add_experience(
                prompt, reply["text"], lesson="", reward=0.0,
                source=f"chat:{device}")
        except Exception:                                   # no store yet is fine
            pass

    # ------------------------------------------------------- training ops

    def pending_examples(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Training candidates waiting for a device ✓."""
        try:
            with self._learning()._connect() as c:
                rows = c.execute(
                    "SELECT fingerprint, created_at, prompt, answer, source "
                    "FROM learning_examples WHERE approved=0 AND exported=0 "
                    "ORDER BY id DESC LIMIT ?", (max(1, limit),)).fetchall()
        except Exception:
            return []
        return [{"fingerprint": r["fingerprint"], "prompt": r["prompt"],
                 "answer": r["answer"], "source": r["source"],
                 "created_at": r["created_at"]} for r in rows]

    def approve_example(self, fingerprint: str) -> bool:
        ok_flag = self._learning().approve(fingerprint, "device:confirmed")
        if ok_flag:
            self.memory.journal("training_approved", fingerprint=fingerprint)
        return ok_flag

    def dismiss_example(self, fingerprint: str) -> bool:
        """A ✗ on a device: the example is deleted, never silently kept."""
        try:
            with self._learning()._connect() as c:
                cur = c.execute("DELETE FROM learning_examples "
                                "WHERE fingerprint=? AND approved=0",
                                (fingerprint,))
            return cur.rowcount == 1
        except Exception:
            return False
