"""Чтение телеметрии и памяти агента: график силы, UCB-рейтинг, уроки."""
from __future__ import annotations
import json
import math
import re

from backend.config import TELEMETRY_FILE, MEMORY_FILE, CAPS_FILE

LINE_RE = re.compile(
    r"\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s+"
    r"Elapsed:\s*(?P<elapsed>[\d.]+)s\s*\|\s*"
    r"Str:\s*(?P<strength>[\d,]+)\s*\(\+(?P<rate>[\d.]+)/min\)\s*\|\s*"
    r"Gems:\s*(?P<gems>[\d,]+)\s*\|\s*"
    r"Rebirths:\s*(?P<rebirths>\d+)\s*\|\s*"
    r"HP:\s*(?P<hp>\d+)%\s*\|\s*"
    r"Action:\s*(?P<action>\S+)"
)

_tel = {"key": None, "samples": []}
_json_cache = {}


def _num(s):
    return int(s.replace(",", ""))


def read_samples(limit=300):
    p = TELEMETRY_FILE
    if not p.exists():
        return []
    st = p.stat()
    key = (st.st_mtime_ns, st.st_size)
    if _tel["key"] != key:
        out = []
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                m = LINE_RE.search(line)
                if not m:
                    continue
                g = m.groupdict()
                out.append({
                    "ts": g["ts"], "elapsed": float(g["elapsed"]),
                    "strength": _num(g["strength"]), "rate": float(g["rate"]),
                    "gems": _num(g["gems"]), "rebirths": int(g["rebirths"]),
                    "hp": int(g["hp"]), "action": g["action"],
                })
        except Exception:
            out = []
        _tel["samples"] = out
        _tel["key"] = key
    return _tel["samples"][-limit:]


def last_sample():
    s = read_samples(1)
    return s[-1] if s else None


def _read_json(path):
    if not path.exists():
        return {}
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _json_cache:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        _json_cache.clear()
        _json_cache[key] = data
    return _json_cache[key]


def capabilities_ranked():
    """UCB-формула один-в-один из brain/capabilities.py."""
    caps = _read_json(CAPS_FILE)
    if not isinstance(caps, dict):
        return []
    total = sum(int(c.get("attempts") or 0) for c in caps.values() if isinstance(c, dict))
    rows = []
    for name, c in caps.items():
        if not isinstance(c, dict):
            continue
        attempts = int(c.get("attempts") or 0)
        va = int(c.get("verified_attempts", attempts) or 0)
        vs = int(c.get("verified_successes", c.get("successes", 0)) or 0)
        rate = (vs / va) if va else 0.5
        base = {"strength": 3.0, "gems": 2.5, "items": 2.0, "info": 0.5}.get(c.get("reward_kind"), 1.0)
        exploit = float(c.get("ema_reward") or 0.0)
        explore = math.sqrt(math.log(total + 2) / (attempts + 1)) * 2.0
        fresh = 1.5 if attempts == 0 else 0.0
        score = base + exploit * 0.1 + rate + explore + fresh
        rows.append({
            "name": name, "description": c.get("description", ""),
            "reward_kind": c.get("reward_kind", ""), "attempts": attempts,
            "success_rate": round(rate, 2), "ema_reward": round(exploit, 2),
            "dynamic": bool(c.get("dynamic")), "score": round(score, 2),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


def learning():
    mem = _read_json(MEMORY_FILE)
    sd = mem.get("selfdev") if isinstance(mem.get("selfdev"), dict) else {}
    closed = sd.get("closed") or []
    return {
        "capabilities": capabilities_ranked(),
        "insights": (mem.get("learned_insights") or [])[-6:],
        "selfdev": {
            "active": sd.get("active"),
            "params": sd.get("params") or {},
            "lessons": (sd.get("lessons") or [])[-6:],
            "accepted": sum(1 for c in closed if isinstance(c, dict) and c.get("status") == "accepted"),
            "rejected": sum(1 for c in closed if isinstance(c, dict) and c.get("status") == "rejected"),
        },
        "sessions": mem.get("total_sessions"),
        "rebirths_known": mem.get("rebirths_known"),
    }