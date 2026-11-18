"""
Capability Registry - the bot's own LIST OF POSSIBILITIES.

Every thing the agent CAN do is a Capability object with:
  - a precondition (when it is available right now)
  - an executor (the actual driver call)
  - self-measured statistics: attempts, successes, EMA reward, avg cost
  - a cooldown

The registry PERSISTS to data/capabilities.json, so the accumulated
self-knowledge survives restarts. NEW capabilities can be added at runtime
(discovered interactables, new events) - the list of possibilities grows by
itself. The Mind (brain/mind.py) picks among capabilities by UCB-style
scoring: exploit what works, explore what is untried.
"""
import json
import math
import time
from typing import Callable, Dict, List, Optional

from config import DATA_DIR

CAPS_FILE = DATA_DIR / "capabilities.json"


class Capability:
    def __init__(self, name: str, description: str, when: Callable, execute: Callable,
                 reward_kind: str = "strength", cooldown: float = 0.0,
                 dynamic: bool = False, danger_ok: bool = True):
        self.name = name
        self.description = description
        self.when = when                  # (hud, scene, ctx) -> bool
        self.execute = execute            # (ctx) -> bool (success)
        self.reward_kind = reward_kind    # 'strength' | 'gems' | 'items' | 'info'
        self.cooldown = cooldown          # min seconds between uses
        self.dynamic = dynamic            # True = added at runtime (discovery)
        self.danger_ok = danger_ok        # can run while boss danger active
        # self-measured stats
        self.attempts = 0
        self.successes = 0
        self.verified_attempts = 0        # subset whose outcome was MEASURED
        self.verified_successes = 0
        self.ema_reward = 0.0             # exponential moving average of reward
        self.avg_cost = 0.0               # seconds
        self.last_used = 0.0
        self.last_result = ""

    # ---------- persistence ----------

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "description": self.description,
            "reward_kind": self.reward_kind, "cooldown": self.cooldown,
            "dynamic": self.dynamic, "danger_ok": self.danger_ok,
            "attempts": self.attempts, "successes": self.successes,
            "verified_attempts": self.verified_attempts,
            "verified_successes": self.verified_successes,
            "ema_reward": round(self.ema_reward, 2),
            "avg_cost": round(self.avg_cost, 2),
            "last_used": self.last_used, "last_result": self.last_result,
        }

    def load_stats(self, d: Dict):
        self.attempts = d.get("attempts", 0)
        self.successes = d.get("successes", 0)
        # legacy saves have no verified counters: treat the old data as verified
        # so historical knowledge is not silently thrown away
        self.verified_attempts = d.get("verified_attempts", self.attempts)
        self.verified_successes = d.get("verified_successes", self.successes)
        self.ema_reward = d.get("ema_reward", 0.0)
        self.avg_cost = d.get("avg_cost", 0.0)
        self.last_used = d.get("last_used", 0.0)
        self.last_result = d.get("last_result", "")


class CapabilityRegistry:
    def __init__(self):
        self.caps: Dict[str, Capability] = {}
        self._load()

    def _load(self):
        try:
            if CAPS_FILE.exists():
                data = json.loads(CAPS_FILE.read_text(encoding="utf-8"))
                for name, stats in data.items():
                    if name in self.caps:
                        self.caps[name].load_stats(stats)
        except Exception:
            pass

    def save(self):
        try:
            data = {name: c.to_dict() for name, c in self.caps.items()}
            CAPS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
        except Exception:
            pass

    def register(self, cap: Capability):
        if cap.name in self.caps:
            cap.load_stats(self.caps[cap.name].to_dict())  # keep self-knowledge
        self.caps[cap.name] = cap

    def add_dynamic(self, name: str, description: str, when: Callable,
                    execute: Callable, reward_kind: str = "info") -> Capability:
        """Registers a NEWLY DISCOVERED capability at runtime (self-growth)."""
        cap = Capability(name, description, when, execute,
                         reward_kind=reward_kind, dynamic=True, cooldown=1800.0)
        self.register(cap)
        self.save()
        print(f"[MIND] new capability registered: {name}")
        return cap

    def available(self, hud, scene, ctx) -> List[Capability]:
        now = time.time()
        out = []
        for c in self.caps.values():
            if now < c.last_used + c.cooldown:
                continue
            try:
                if c.when(hud, scene, ctx):
                    out.append(c)
            except Exception:
                continue
        return out

    def feedback(self, name: str, success: bool, reward: float, cost: float,
                 result: str = "", verified: bool = True):
        c = self.caps.get(name)
        if not c:
            return
        c.attempts += 1
        if verified:
            c.verified_attempts += 1
            if success:
                c.verified_successes += 1
        if success:
            c.successes += 1
        alpha = 0.35  # EMA speed: recent outcomes weigh more (adapts to updates)
        c.ema_reward = c.ema_reward * (1 - alpha) + reward * alpha
        c.avg_cost = c.avg_cost * (1 - alpha) + cost * alpha
        c.last_used = time.time()
        c.last_result = result[:160]
        self.save()

    # ---------- scoring (UCB-style: exploit + explore) ----------

    def success_rate(self, c: Capability) -> float:
        """Success rate over VERIFIED samples only.

        Unverified runs (the bot could not read the badge) say nothing about the
        strategy's quality. Counting them as wins - or as losses - silently
        biases the whole self-learning loop, which is exactly what happened when
        smart_lift reported a fake +1 on every read failure.
        """
        if not c.verified_attempts:
            return 0.5   # no evidence yet: neutral prior, let exploration decide
        return c.verified_successes / c.verified_attempts

    def score(self, c: Capability, total_attempts: int) -> float:
        base = {"strength": 3.0, "gems": 2.5, "items": 2.0, "info": 0.5}.get(c.reward_kind, 1.0)
        exploit = c.ema_reward
        success_bias = self.success_rate(c)
        explore = math.sqrt(math.log(total_attempts + 2) / (c.attempts + 1)) * 2.0
        fresh_bonus = 1.5 if c.attempts == 0 else 0.0
        return base + exploit * 0.1 + success_bias + explore + fresh_bonus

    def ranked(self, hud, scene, ctx) -> List[tuple]:
        avail = self.available(hud, scene, ctx)
        total = sum(c.attempts for c in self.caps.values())
        scored = [(self.score(c, total), c) for c in avail]
        scored.sort(key=lambda t: -t[0])
        return scored

    def playbook_text(self, hud=None, scene=None, ctx=None) -> str:
        """The AI's own self-written instruction, regenerated from its stats.
        Available capabilities first (by decision score), then the rest."""
        total = sum(c.attempts for c in self.caps.values())
        avail = {c.name for c in self.available(hud or {}, scene or {}, ctx or {})}
        rows = sorted(
            self.caps.values(),
            key=lambda c: (
                c.name not in avail,  # available first
                -(self.score(c, total)),
            ),
        )
        lines = ["Текущая политика агента (сам себя переписал):"]
        for i, c in enumerate(rows, 1):
            if c.verified_attempts:
                rate = f"проверено {c.verified_successes}/{c.verified_attempts}"
            elif c.attempts:
                rate = f"{c.attempts} попыток без проверки"
            else:
                rate = "не пробовал"
            tag = "" if c.name in avail else " [сейчас недоступно]"
            dyn = " [найдено самим]" if c.dynamic else ""
            lines.append(f"  {i}. {c.name} — {c.description} "
                         f"[успех {rate}, выгода {c.ema_reward:.1f}, {c.reward_kind}]"
                         f"{dyn}{tag}")
        return "\n".join(lines)
