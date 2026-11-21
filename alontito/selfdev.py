# -*- coding: utf-8 -*-
"""Self-development ledger for Alontito.

One experiment at a time. A change is accepted only when a measured run is
better than the baseline; a worse run rejects it and rolls parameters back.
Unmeasured runs do not become lessons.

Stored inside memory.json under the key ``selfdev`` so a restart does not
forget which change is still open.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

KEY = "selfdev"
MAX_LESSONS = 30
MAX_CLOSED = 40

DEFAULT_PARAMS: Dict[str, Any] = {
    "action": "TRAIN_DUMBBELLS",
    "sets": 3,
    "reps": 20,
    "delay": 0.08,
}


def _blank() -> Dict[str, Any]:
    return {
        "version": 1,
        "active": None,
        "closed": [],
        "lessons": [],
        "params": dict(DEFAULT_PARAMS),
        "prev_params": dict(DEFAULT_PARAMS),
    }


def bucket(data: Dict[str, Any]) -> Dict[str, Any]:
    raw = data.get(KEY)
    if not isinstance(raw, dict):
        raw = _blank()
        data[KEY] = raw
    raw.setdefault("version", 1)
    raw.setdefault("active", None)
    raw.setdefault("closed", [])
    raw.setdefault("lessons", [])
    raw.setdefault("params", dict(DEFAULT_PARAMS))
    raw.setdefault("prev_params", dict(DEFAULT_PARAMS))
    return raw


def _now() -> float:
    return time.time()


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def rank_skills(skills, include_untried: bool = False) -> List[tuple]:
    """UCB over skills.
    
    For known skills with measurements, balances exploitation of high gains
    per minute with exploration of less frequently tested skills.
    Untried skills stay out by default to avoid guessing unmeasured actions,
    but can be sampled when explicitly requested.
    """
    known = [s for s in skills if s.attempts and s.per_minute is not None]
    untried = [s for s in skills if not s.attempts or s.per_minute is None]
    
    if not known and not untried:
        return []
        
    total = sum(s.attempts for s in known) if known else 0
    scored = []
    
    for s in known:
        exploit = float(s.per_minute or 0.0)
        rate = s.success_rate if s.success_rate is not None else 0.5
        explore = math.sqrt(math.log(total + 2) / (s.attempts + 1))
        # scale explore into the same order as strength-per-minute
        score = exploit * rate + explore * max(exploit, 1.0) * 0.15
        scored.append((score, s))
        
    if include_untried and untried:
        # Give untried skills a chance to establish their baseline
        base_score = max((t[0] for t in scored), default=100.0) + 10.0
        for s in untried:
            scored.append((base_score, s))

    scored.sort(key=lambda t: -t[0])
    return scored


def next_skill_name(skills) -> Optional[str]:
    ranked = rank_skills(skills)
    if not ranked:
        return None
    return ranked[0][1].name


def get_active_params(data: Dict[str, Any]) -> Dict[str, Any]:
    """Get active training parameters."""
    box = bucket(data)
    params = box.get("params")
    if not isinstance(params, dict):
        params = dict(DEFAULT_PARAMS)
        box["params"] = params
    return dict(params)


def set_active_params(data: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Tune active parameters and keep previous parameters for rollback."""
    box = bucket(data)
    current = box.get("params") or dict(DEFAULT_PARAMS)
    box["prev_params"] = dict(current)
    updated = dict(current)
    updated.update(params)
    box["params"] = updated
    return updated


def rollback_params(data: Dict[str, Any]) -> Dict[str, Any]:
    """Revert parameters to the last known stable baseline."""
    box = bucket(data)
    prev = box.get("prev_params") or dict(DEFAULT_PARAMS)
    box["params"] = dict(prev)
    return dict(box["params"])


def register_proposal(data: Dict[str, Any], proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Keep a single open experiment. A new proposal replaces a still-proposed
    one, but never an experiment that already has measured tries.
    """
    box = bucket(data)
    active = box.get("active")
    change = (proposal or {}).get("change") or ""
    if active and active.get("change") == change:
        return active
    if active and int(active.get("tries") or 0) > 0:
        return active
    item = {
        "id": int(_now()),
        "status": "active",
        "problem": proposal.get("problem", ""),
        "change": change,
        "metric": proposal.get("metric", ""),
        "rollback": proposal.get("rollback", ""),
        "evidence": proposal.get("evidence", ""),
        "opened": _stamp(),
        "tries": 0,
        "last_verdict": "",
    }
    box["active"] = item
    return item


def note_verdict(data: Dict[str, Any], verdict: str, text: str,
                 per_minute: Optional[float]) -> Optional[Dict[str, Any]]:
    """Fold one reflection into the open experiment.

    лучше  -> accepted lesson, locks in parameters
    хуже   -> rejected, automatically rolls active parameters back
    иначе  -> stay open; two "без изменений" close it as inconclusive
    """
    box = bucket(data)
    active = box.get("active")
    if not active:
        return None
    active["tries"] = int(active.get("tries") or 0) + 1
    active["last_verdict"] = verdict
    active["last_text"] = text
    if per_minute is not None:
        active["last_per_minute"] = round(float(per_minute))

    if verdict == "лучше":
        # Lock in parameters as the new baseline
        box["prev_params"] = dict(box.get("params") or DEFAULT_PARAMS)
        _close(box, active, "accepted")
        _lesson(box, f"оставить: {active.get('change')} ({text})")
    elif verdict == "хуже":
        # Roll back active parameters
        rollback_params(data)
        _close(box, active, "rejected")
        _lesson(box, f"откатить: {active.get('change')} ({text})")
    elif verdict == "без изменений" and active["tries"] >= 2:
        _close(box, active, "inconclusive")
        _lesson(box, f"не меняет темп: {active.get('change')}")
    return box.get("active") or active


def _close(box: Dict[str, Any], item: Dict[str, Any], status: str) -> None:
    item["status"] = status
    item["closed"] = _stamp()
    box["closed"] = (box.get("closed") or [])[-MAX_CLOSED:] + [dict(item)]
    box["active"] = None


def _lesson(box: Dict[str, Any], text: str) -> None:
    lessons = box.setdefault("lessons", [])
    if text and text not in lessons:
        lessons.append(text)
        if len(lessons) > MAX_LESSONS:
            del lessons[0]


def add_lesson(data: Dict[str, Any], text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    _lesson(bucket(data), text)


def status(data: Dict[str, Any]) -> Dict[str, Any]:
    box = bucket(data)
    return {
        "active": box.get("active"),
        "lessons": list(box.get("lessons") or [])[-8:],
        "closed": len(box.get("closed") or []),
        "accepted": sum(1 for c in box.get("closed") or [] if c.get("status") == "accepted"),
        "rejected": sum(1 for c in box.get("closed") or [] if c.get("status") == "rejected"),
        "params": get_active_params(data),
    }
