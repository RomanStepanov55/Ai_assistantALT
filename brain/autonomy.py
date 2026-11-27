"""Autonomy engine: Alontito develops itself, on evidence only.

The user gave the agent full freedom over its OWN development - what that
means here, structurally:

  1. routines  the agent schedules its own maintenance (nightly memory
               consolidation, periodic reflection) and keeps its own
               schedule in memory; it does not need to be told to run them
  2. measures  every cycle reads REAL counters (approval rate of its
               answers, skill success rates, memory contradictions,
               automation punctuality) - never vibes
  3. proposes  exactly ONE change at a time (selfdev ledger), chosen by the
               weakest measured dimension; a proposal that cannot name its
               metric is not a proposal
  4. verdicts  the next cycles measure the metric again; ±10% noise band;
               "лучше" locks the change, "хуже" rolls parameters back,
               two "без изменений" close it as inconclusive
  5. lessons   every closed experiment becomes a durable lesson; unmeasured
               runs do not become lessons (E-001 rule, kept verbatim)

Freedom is NOT absence of brakes: ResourceGovernor paces the cycles, the
selfdev ledger keeps one experiment at a time, and nothing here touches the
user's own machine beyond the agent's own stores.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from . import consolidation
from .resource_governor import ResourceGovernor

SAME_BAND = 0.10                    # ±10% = noise, not improvement
DEFAULT_INTERVAL_MIN = 45
DEFAULT_MAX_ROUNDS = 2


class AutonomyEngine:
    """One tick advances self-development by at most one step."""

    def __init__(self, memory, automations=None, learning=None,
                 experience=None, clock: Callable[[], float] = time.time,
                 synthesizer=None):
        self.memory = memory
        self.automations = automations
        self.learning = learning
        self.experience = experience
        self.clock = clock
        self.synthesizer = synthesizer          # optional SkillSynthesizer
        self._t0 = self.clock()
        self._last_routine: Dict[str, float] = {}
        self.governor = ResourceGovernor(target_hz=1 / 60.0, burst=1.0,
                                         clock=lambda: self.clock() - self._t0)

    # ------------------------------------------------------------ metrics
    def measure(self) -> Dict[str, Any]:
        """Real counters only. Absent data stays None, never 0."""
        data = self.memory.data
        facts = data.get("facts") or []
        contradictions = sum(1 for f in facts
                             if f.get("status") == "ПРОТИВОРЕЧИЕ")
        outdated = sum(1 for f in facts if f.get("status") == "УСТАРЕВШИЕ")
        memory_health = None
        if facts:
            memory_health = round(1.0 - (contradictions + outdated) / len(facts), 3)

        helpfulness = None
        examples = None
        if self.learning is not None:
            try:
                st = self.learning.stats()
                examples = st.get("examples", 0)
                approved = st.get("approved", 0)
                if examples:
                    helpfulness = round(approved / examples, 3)
            except Exception:
                pass

        autonomy_score = None
        skills = [s for s in (data.get("skills") or {}).values() if s.get("attempts")]
        if skills:
            rates = [s.get("successes", 0) / s["attempts"] for s in skills
                     if s.get("attempts")]
            autonomy_score = round(sum(rates) / len(rates), 3)

        punctuality = None
        if self.automations is not None:
            repeating = [a for a in self.automations.list() if a.kind != "once"]
            if repeating:
                punctual = sum(1 for a in repeating if a.last_fired)
                punctuality = round(punctual / len(repeating), 3)

        weakest = self._weakest({"memory_health": memory_health,
                                 "helpfulness": helpfulness,
                                 "autonomy": autonomy_score,
                                 "punctuality": punctuality})
        return {"facts": len(facts), "contradictions": contradictions,
                "outdated": outdated, "memory_health": memory_health,
                "examples": examples, "helpfulness": helpfulness,
                "autonomy": autonomy_score, "punctuality": punctuality,
                "weakest": weakest}

    @staticmethod
    def _weakest(scores: Dict[str, Optional[float]]) -> Optional[str]:
        measured = {k: v for k, v in scores.items() if v is not None}
        if not measured:
            return None
        return min(measured, key=measured.get)

    # ------------------------------------------------------------ routines
    def run_routines(self) -> List[Dict[str, Any]]:
        """Maintenance the agent does for itself, on its own schedule."""
        events: List[Dict[str, Any]] = []
        now = self.clock()
        params = self._params()
        interval = max(15, int(params.get("interval_min", DEFAULT_INTERVAL_MIN))) * 60
        if not self.governor.acquire():
            return events
        last = self._last_routine.get("consolidation", 0)
        if now - last >= interval:
            report = consolidation.consolidate(self.memory)
            self._last_routine["consolidation"] = now
            self.memory.journal("autonomy_routine", routine="consolidation",
                                deduped=report["deduped"],
                                contradictions=report["contradictions"],
                                decayed=report["decayed"])
            events.append({"type": "routine", "kind": "consolidation",
                           "detail": (f"консолидация: дублей {report['deduped']}, "
                                      f"противоречий {report['contradictions']}, "
                                      f"устаревших {report['decayed']}")})
        return events

    # ------------------------------------------------------------ proposals
    def _params(self) -> Dict[str, Any]:
        """Agent-level tunable parameters (selfdev ledger)."""
        from alontito.selfdev import get_active_params
        return get_active_params(self.memory.data)

    def _propose(self, metrics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The ONE change the measurements support, or None.

        Catalogue is ordered: memory integrity first, answer quality second,
        autonomy third. A proposal is registered only when its metric exists
        (None = not measured = not proposable).
        """
        from alontito import selfdev
        m = metrics
        if m.get("contradictions"):
            return {"problem": f"противоречий в памяти: {m['contradictions']}",
                    "change": "консолидация памяти при каждом цикле (уменьшить интервал)",
                    "metric": "memory_health",
                    "rollback": "вернуть интервал",
                    "evidence": f"фактов {m.get('facts')}, противоречий {m['contradictions']}",
                    "param": {"interval_min": 15}}
        if m.get("helpfulness") is not None and m.get("helpfulness", 1.0) < 0.5 \
                and (m.get("examples") or 0) >= 3:
            rounds = int(self._params().get("review_rounds", DEFAULT_MAX_ROUNDS))
            if rounds < 3:
                return {"problem": "меньше половины ответов одобрено",
                        "change": f"глубже проверка ответов: раундов критики {rounds}→{rounds + 1}",
                        "metric": "helpfulness",
                        "rollback": "вернуть количество раундов",
                        "evidence": f"одобрено {m['helpfulness'] * 100:.0f}% из {m['examples']}",
                        "param": {"review_rounds": rounds + 1}}
        if m.get("punctuality") is not None and m.get("punctuality", 1.0) < 0.5:
            return {"problem": "напоминания срабатывают нерегулярно",
                    "change": "сократить цикл автономии (интервал → 30 мин)",
                    "metric": "punctuality",
                    "rollback": "вернуть интервал",
                    "evidence": f"сработало {m['punctuality'] * 100:.0f}% повторяющихся",
                    "param": {"interval_min": 30}}
        return None

    def tick(self) -> List[Dict[str, Any]]:
        """One autonomy step: routine if due, verdict if measurable, proposal if idle."""
        from alontito import selfdev
        events = self.run_routines()
        data = self.memory.data
        metrics = self.measure()
        active = selfdev.bucket(data).get("active")
        interval_s = max(15, int(self._params().get(
            "interval_min", DEFAULT_INTERVAL_MIN))) * 60

        if active:
            # judge no sooner than one full interval after opening: the metric
            # needs time to reflect the change, an instant verdict is noise
            opened_ts = float(active.get("opened_ts") or 0)
            if opened_ts and self.clock() - opened_ts >= interval_s:
                verdict = self._judge_active(metrics)
                if verdict:
                    kind, text = verdict
                    selfdev.note_verdict(data, kind, text, None)
                    events.append({"type": "experiment", "kind": kind,
                                   "detail": f"{active.get('change')}: {text}"})

        if not selfdev.bucket(data).get("active"):
            proposal = self._propose(metrics)
            if proposal is not None:
                param = proposal.get("param") or {}
                if param:
                    # the experiment IS the change: apply it, then measure
                    selfdev.set_active_params(data, param)
                item = selfdev.register_proposal(data, proposal)
                item["metric_before"] = metrics.get(proposal["metric"])
                item["opened_ts"] = self.clock()        # judged on OUR clock
                events.append({"type": "experiment", "kind": "proposal",
                               "detail": proposal["change"]})
        self.memory.save()
        return events

    def _judge_active(self, metrics: Dict[str, Any]) -> Optional[tuple]:
        """Compare the active experiment's metric with its baseline.

        No number before and after means no verdict - the experiment just
        stays open (не смог измерить ≠ получилось).
        """
        from alontito import selfdev
        box = selfdev.bucket(self.memory.data)
        active = box.get("active")
        if not active:
            return None
        metric_name = active.get("metric") or ""
        before = active.get("metric_before")
        after = metrics.get(metric_name)
        if before is None or after is None:
            return None
        if before:
            ratio = after / before
        elif after:
            # from zero to a measured value: improvement by definition,
            # expressed as "double the noise band" so the maths stays finite
            ratio = 1.0 + SAME_BAND * 2
        else:
            ratio = 1.0
        if ratio > 1.0 + SAME_BAND:
            return "лучше", (f"метрика {metric_name}: {before} → {after} "
                             f"({(ratio - 1) * 100:+.0f}%) — изменение остаётся")
        if ratio < 1.0 - SAME_BAND:
            return "хуже", (f"метрика {metric_name}: {before} → {after} "
                            f"({(ratio - 1) * 100:+.0f}%) — откат")
        return "без изменений", (f"метрика {metric_name}: {before} → {after} — "
                                 f"внутри полосы шума ±{int(SAME_BAND * 100)}%")

    # ------------------------------------------------------------ reporting
    def status(self) -> Dict[str, Any]:
        from alontito.selfdev import status as selfdev_status
        metrics = self.measure()
        out = selfdev_status(self.memory.data)
        out["metrics"] = metrics
        out["routines_last"] = dict(self._last_routine)
        return out
