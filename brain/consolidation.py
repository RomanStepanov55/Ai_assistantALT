"""Memory consolidation: keep the fact store clean without rewriting history.

The autonomy engine runs this on a schedule. What it does with real rules:

  - dedupe        near-duplicate facts collapse to the earliest, highest
                  status copy (token-overlap >= DEDUPE_THRESHOLD)
  - contradictions same subject, different rest -> both marked ПРОТИВОРЕЧИЕ,
                  an open question is registered so the conflict is not lost
  - decay         СООБЩАЕТСЯ ВТОРИЧНЫМ / ГИПОТЕЗА older than DECAY_DAYS with
                  no re-confirmation -> УСТАРЕВШИЕ (still readable, no longer
                  presented as current)
  - promotable    hypotheses that name a measurable thing are listed for the
                  autonomy engine - promotion stays a human/measured act,
                  consolidation only NOMINATES

Honesty rule: consolidation never deletes a user-sourced fact and never
upgrades a status on its own. It only demotes, merges exact duplicates and
flags conflicts.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from alontito.memory import (STATUS_CONTRADICTION, STATUS_HYPOTHESIS,
                             STATUS_OFFICIAL, STATUS_OUTDATED,
                             STATUS_SECONDARY, Memory)

DEDUPE_THRESHOLD = 0.75          # Jaccard on word tokens
DECAY_DAYS = 60
MEASURE_HINTS = ("замер", "измер", "мин", "%", "шт", "раз")


def _tokens(text: str) -> set:
    return {t for t in re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower())}


def _overlap(a: set, b: set) -> float:
    """Jaccard: shared vocabulary over total vocabulary. min-length overlap
    would merge «ключи в ящике» with «ключи в ящике, запасные в сумке»."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _status_rank(status: str) -> int:
    """Lower is stronger evidence. Used to keep the better copy when merging."""
    order = (STATUS_OFFICIAL, "ПОДТВЕРЖДЕНО ИНТЕРФЕЙСОМ", "ИЗМЕРЕНО В ЭКСПЕРИМЕНТЕ",
             STATUS_SECONDARY, STATUS_HYPOTHESIS, STATUS_OUTDATED,
             STATUS_CONTRADICTION, "НЕ ПОДТВЕРЖДЕНО")
    try:
        return order.index(status)
    except ValueError:
        return len(order)


def _digits(text: str) -> frozenset:
    """Digit tokens must match for a merge: «встреча в 9:00» and «встреча в
    12:00» overlap lexically but are different facts."""
    return frozenset(re.findall(r"\d+", (text or "")))


def _subject(fact: Dict) -> str:
    """First words IN DOCUMENT ORDER (a set's hash order would split the
    same subject into arbitrary groups)."""
    words = [w for w in re.findall(r"[a-zа-яё0-9]{3,}",
                                   (fact.get("text") or "").lower())][:2]
    return " ".join(words)


def consolidate(memory: Memory, *, now: Optional[float] = None) -> Dict[str, Any]:
    """One consolidation pass. Returns a report; every change is journaled."""
    now = now if now is not None else time.time()
    facts: List[Dict] = list(memory.data.get("facts") or [])
    report: Dict[str, Any] = {"facts_before": len(facts), "deduped": 0,
                              "contradictions": 0, "decayed": 0,
                              "promotable": [], "facts_after": len(facts)}
    if not facts:
        return report

    changed = False

    # ---- dedupe: near-duplicates keep the earliest, strongest copy ----
    keep: List[Dict] = []
    for fact in sorted(facts, key=lambda f: (f.get("added") or 0)):
        toks = _tokens(fact.get("text") or "")
        digs = _digits(fact.get("text") or "")
        twin = None
        for kept in keep:
            if digs != _digits(kept.get("text") or ""):
                continue
            if _overlap(toks, _tokens(kept.get("text") or "")) >= DEDUPE_THRESHOLD:
                twin = kept
                break
        if twin is None:
            keep.append(fact)
            continue
        changed = True
        report["deduped"] += 1
        if _status_rank(fact.get("status") or "") < _status_rank(twin.get("status") or ""):
            twin["status"] = fact.get("status")
            twin["source"] = fact.get("source")
            twin["date"] = fact.get("date")
        memory.journal("consolidation_dedupe", kept=twin.get("text")[:120],
                       removed=fact.get("text")[:120])

    # ---- contradictions: same subject, different claim, not a twin ----
    # (twins were already merged above; whatever survives with the same
    # subject prefix differs in content or in numbers - both are conflicts)
    by_subject: Dict[str, List[Dict]] = {}
    for fact in keep:
        by_subject.setdefault(_subject(fact), []).append(fact)
    for subject, group in by_subject.items():
        if len(group) < 2 or not subject:
            continue
        texts = {(f.get("text") or "").lower() for f in group}
        if len(texts) == len(group):
            strong = [f for f in group if f.get("status") not in
                      (STATUS_HYPOTHESIS, STATUS_OUTDATED)]
            if len(strong) >= 2:
                for f in group:
                    if f.get("status") != STATUS_CONTRADICTION:
                        f["status"] = STATUS_CONTRADICTION
                        changed = True
                        report["contradictions"] += 1
                existing = {q.get("question") for q in memory.data.get("questions") or []}
                question = f"противоречие в памяти: {group[0].get('text')[:80]}…"
                if question not in existing:
                    memory.add_question(question, metric="истинный факт",
                                        how="спросить пользователя или измерить")

    # ---- decay: old unconfirmed claims stop posing as current ----
    for fact in keep:
        if fact.get("status") in (STATUS_SECONDARY, STATUS_HYPOTHESIS):
            added = fact.get("added") or 0
            if added and (now - added) > DECAY_DAYS * 86400:
                fact["status"] = STATUS_OUTDATED
                changed = True
                report["decayed"] += 1
                memory.journal("consolidation_decay", text=fact.get("text")[:120])

    # ---- promotable hypotheses: nominate, never promote ----
    for fact in keep:
        if fact.get("status") == STATUS_HYPOTHESIS:
            text_low = (fact.get("text") or "").lower()
            if any(h in text_low for h in MEASURE_HINTS):
                report["promotable"].append(fact.get("text")[:120])

    if changed:
        memory.data["facts"] = keep
        memory.save()
        memory.journal("consolidation", **{k: v for k, v in report.items()
                                           if k != "promotable"})
    report["facts_after"] = len(keep)
    return report
