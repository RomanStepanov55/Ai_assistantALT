"""
Strategic Decision Maker v3 - the planner of the self-developing agent.

Priority ladder (each step returns the first pressing action):
  1. CLAIM_GIFTS      - free money, never late
  2. BOSS_FIGHT       - when a boss is around (travel + fight), capped
  3. REBIRTH          - free mechanic, threshold verified on-screen
  4. CHECK_QUESTS     - adaptive interval, only completed ones
  5. SPIN_WHEEL       - free spin only (never pay)
  6. PETS_MAINTENANCE - equip best, clean weak (capped)
  7. HATCH_EGG        - pets progression, above gems safety floor
  8. TRAINING         - efficiency-weighted rotation + discovery walks
                        (self-adaptation: new machines/eggs get discovered
                         and remembered without code changes)

GOLDEN RULE: only free progression. Spending is limited to egg hatching and
pet upgrades (user-requested), above GEMS_SAFETY_FLOOR, capped per action.
"""
import time
from typing import Dict, Any, List

from config import (get_rebirth_requirement, FEATURE_EGGS, FEATURE_PETS,
                    FEATURE_BOSS, FEATURE_REBIRTH)

BASE_ROTATION = [
    ("TRAIN_DUMBBELLS", 3),
    ("TRAIN_MACHINE", 2),
    ("TRAIN_PUSHUPS", 1),
    ("TRAIN_SITUPS", 1),
    ("TRAIN_AGILITY", 1),
]


class DecisionMaker:
    def __init__(self):
        self.last_wheel_spin = 0.0
        self.last_gift_check = 0.0
        self.last_gift_attempt = 0.0
        self.last_quest_check = 0.0
        self.last_pets_check = 0.0
        self.last_egg_attempt = 0.0
        self.last_discovery = 0.0
        self.rotation_pos = 0
        self.rotation_counter = 0
        self.quest_interval = 300.0
        self.quest_dry_streak = 0

    # ---------- learning ----------

    def note_quest_result(self, claims: int):
        if claims:
            self.quest_dry_streak = 0
            self.quest_interval = 300.0
        else:
            self.quest_dry_streak += 1
            self.quest_interval = min(900.0, 300.0 + 300.0 * self.quest_dry_streak)

    def _build_rotation(self, efficiency: Dict[str, float]) -> List[str]:
        rot: List[str] = []
        key_map = {
            "TRAIN_DUMBBELLS": "tool_1_hand_weights",
            "TRAIN_PUSHUPS": "pushups",
            "TRAIN_SITUPS": "situps",
            "TRAIN_MACHINE": "machine",
        }
        for action, weight in BASE_ROTATION:
            key = key_map.get(action)
            bonus = 0
            if key and efficiency.get(key, 0) > 0:
                bonus = min(2, int(efficiency[key] / 3))
            rot.extend([action] * (weight + bonus))
        return rot or ["TRAIN_DUMBBELLS"]

    # ---------- planner ----------

    def evaluate_strategy(self, hud_state: Dict[str, Any], rates: Dict[str, float],
                          efficiency: Dict[str, float] = None,
                          scene: Dict[str, Any] = None) -> Dict[str, Any]:
        now = time.time()
        scene = scene or {}
        strength = hud_state.get("strength", 0)
        rebirths = hud_state.get("rebirths", 0)
        gift_ready = hud_state.get("gift_ready", False)
        events = set(scene.get("events", []))
        efficiency = efficiency or {}

        # 1. Timed gifts (free)
        if gift_ready and (now - self.last_gift_attempt > 180.0):
            self.last_gift_attempt = now
            self.last_gift_check = now
            return {"action": "CLAIM_GIFTS", "priority": "HIGH",
                    "reason": "Free timed gift ready."}
        if now - self.last_gift_check > 600.0:
            self.last_gift_check = now
            self.last_gift_attempt = now
            return {"action": "CLAIM_GIFTS", "priority": "MEDIUM",
                    "reason": "Periodic gift check (10 min)."}

        # 2. Boss (user-requested: destroy bosses)
        if FEATURE_BOSS and "boss" in events:
            return {"action": "BOSS_FIGHT", "priority": "CRITICAL",
                    "reason": "Boss event on screen - travel & fight."}

        # 3. Rebirth (free mechanic; on-screen verification in the driver)
        if FEATURE_REBIRTH:
            req = get_rebirth_requirement(rebirths)
            if req > 0 and strength >= req:
                return {"action": "REBIRTH", "priority": "CRITICAL",
                        "reason": f"Strength {strength:,} >= target {req:,}."}

        # 4. Quests (adaptive interval)
        if now - self.last_quest_check > self.quest_interval:
            self.last_quest_check = now
            return {"action": "CHECK_QUESTS", "priority": "HIGH",
                    "reason": f"Quest check (interval {int(self.quest_interval)}s)."}

        # 5. Wheel (free spin only)
        if now - self.last_wheel_spin > 900.0:
            self.last_wheel_spin = now
            return {"action": "SPIN_WHEEL", "priority": "MEDIUM",
                    "reason": "Free wheel spin check (15 min)."}

        # 6. Pets maintenance (equip best / clean weak)
        if FEATURE_PETS and now - self.last_pets_check > 1200.0:
            self.last_pets_check = now
            return {"action": "PETS_MAINTENANCE", "priority": "MEDIUM",
                    "reason": "Equip best pets & clean weak ones (20 min)."}

        # 7. Eggs (user-requested; budget-guarded in the driver)
        if FEATURE_EGGS and now - self.last_egg_attempt > 1200.0:
            self.last_egg_attempt = now
            return {"action": "HATCH_EGG", "priority": "MEDIUM",
                    "reason": "Egg hatching attempt (20 min, budget-guarded)."}

        # 8. Training rotation with periodic discovery walks (self-adaptation)
        rotation = self._build_rotation(efficiency)
        self.rotation_counter += 1
        if self.rotation_counter >= 3:
            self.rotation_counter = 0
            self.rotation_pos = (self.rotation_pos + 1) % len(rotation)
        action = rotation[self.rotation_pos % len(rotation)]

        # discovery: every ~7 min walk & scan for NEW interactables
        if now - self.last_discovery > 420.0:
            self.last_discovery = now
            return {"action": "DISCOVER", "priority": "NORMAL",
                    "reason": "Discovery walk: find new machines/eggs/stands."}

        reasons = {
            "TRAIN_DUMBBELLS": "Lifting Hand Weights (Slot 2).",
            "TRAIN_MACHINE": "Gym machine session.",
            "TRAIN_PUSHUPS": "Pushups (Slot 7).",
            "TRAIN_SITUPS": "Situps (Slot 8).",
            "TRAIN_AGILITY": "Agility: treadmill or sprint.",
        }
        return {"action": action, "priority": "NORMAL",
                "reason": reasons.get(action, "Training rotation.")}
