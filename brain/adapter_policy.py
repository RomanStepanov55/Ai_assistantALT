"""
AdapterPolicy - temporary universal action selector.

This is the first bridge from the old Muscle-Legends-specific Mind
to the universal GameAdapter architecture.

Later this should be replaced by MetaBrain + LearningMemory + UCB scoring.
"""

import time
from typing import Dict, List, Optional

from games.game_adapter import GameAction, GameState


# Priorities for Muscle Legends.
# Later this should move into adapter/memory, not global policy.
PRIORITY = {
    "CLOSE_MODAL": 1000,

    "BOSS_FIGHT": 120,

    "DAILY_REWARDS": 95,
    "CLAIM_GIFTS": 90,
    "CHECK_QUESTS": 85,
    "SPIN_WHEEL": 80,

    "PETS_MAINTENANCE": 70,
    "HATCH_EGG": 65,
    "REBIRTH": 60,

    "TRAIN_MACHINE": 55,
    "TRAIN_AGILITY": 52,
    "TRAIN_DUMBBELLS": 50,
    "TRAIN_PUSHUPS": 48,
    "TRAIN_SITUPS": 46,

    "DISCOVER": 30,
}


# Simple cooldowns to avoid spamming the same action.
# Later this should live in adapter memory.
COOLDOWNS = {
    "CLOSE_MODAL": 2.0,

    "BOSS_FIGHT": 120.0,

    "DAILY_REWARDS": 3600.0,
    "CLAIM_GIFTS": 180.0,
    "CHECK_QUESTS": 300.0,
    "SPIN_WHEEL": 900.0,

    "PETS_MAINTENANCE": 1200.0,
    "HATCH_EGG": 1200.0,
    "REBIRTH": 600.0,

    "TRAIN_MACHINE": 45.0,
    "TRAIN_AGILITY": 45.0,
    "TRAIN_DUMBBELLS": 0.0,
    "TRAIN_PUSHUPS": 0.0,
    "TRAIN_SITUPS": 0.0,

    "DISCOVER": 420.0,
}


def _is_ready(name: str, ctx: Dict) -> bool:
    last_used = ctx.get("last_used", {}).get(name, 0.0)
    cooldown = COOLDOWNS.get(name, 0.0)
    return time.time() - last_used >= cooldown


def choose_action(
    state: GameState,
    actions: List[GameAction],
    ctx: Dict,
) -> Optional[GameAction]:
    """
    Very simple universal policy:
    - close modal first;
    - boss event gives boss high priority;
    - collect/free rewards before training;
    - failed actions get a small penalty.
    """

    if not actions:
        return None

    # If dead, do not choose normal actions.
    # handle_interrupts() should respawn first.
    if not state.is_alive:
        return None

    # If modal is open, CLOSE_MODAL is urgent.
    # Ignore cooldown here, otherwise the agent can get stuck.
    if state.modal_open:
        for a in actions:
            if a.name == "CLOSE_MODAL":
                return a

    ready = [a for a in actions if _is_ready(a.name, ctx)]
    if not ready:
        return None

    def score(action: GameAction) -> float:
        s = float(PRIORITY.get(action.name, 10.0))

        # Dangerous events.
        # In Muscle Legends, boss event should make boss fight top priority.
        if "boss" in state.events:
            if action.name == "BOSS_FIGHT":
                s += 200.0
            elif action.name in {
                "DISCOVER",
                "TRAIN_MACHINE",
                "TRAIN_AGILITY",
                "HATCH_EGG",
            }:
                s -= 100.0

        # Penalty for repeated failures.
        fails = ctx.get("fails", {}).get(action.name, 0)
        s -= min(30.0, fails * 6.0)

        # Free rewards slightly higher.
        if action.category == "collect":
            s += 5.0

        return s

    return max(ready, key=score)