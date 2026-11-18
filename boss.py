"""
Boss fighting driver (user-requested feature).

Flow when the eye sees a boss event:
  1. Answer the travel dialog YES (OCR-verified: the dialog must contain
     'travel' + 'boss'/'gym' context - a bare YES is never clicked anywhere).
  2. At the arena: attack = click on the boss area while the boss HP is
     visible; jump occasionally; watch for own death (HUD check).
  3. Retreat after BOSS_FIGHT_SECONDS or when the fight can't be confirmed;
     death -> respawn handled by the main loop.

Golden rule: boss rewards are FREE; nothing in the boss flow spends currency.
"""
import time
from typing import Dict, Optional

from config import BOSS_FIGHT_SECONDS, BOSS_RETREAT_WALK
from vision.ocr_engine import CYRILLIC_MAP


def _norm(t: str) -> str:
    return (t or "").upper().translate(CYRILLIC_MAP)


class BossDriver:
    def __init__(self, actions, navigator):
        self.a = actions
        self.nav = navigator

    def boss_travel_pending(self, scene: Optional[Dict] = None) -> bool:
        """
        Detects the boss TRAVEL dialog and clicks YES on it. A YES is allowed
        ONLY when the dialog text clearly references a boss/gym travel.
        """
        lines, frame = self.a._modal_lines("boss_travel")
        joined = " ".join((l.get("text") or "") for l in lines)
        nj = _norm(joined)
        if "travel" not in nj:
            return False
        if not any(k in nj for k in ("BOSS", "GYM", "RING", "ARENA")):
            return False
        for l in lines:
            if _norm(l.get("text")).strip() in ("YES", "YES!"):
                pt = self.a._line_to_screen("modal_area", l, frame)
                if pt:
                    self.a.input.mouse_click(pt[0], pt[1])
                    print("[BOSS] travel YES clicked (verified boss dialog).")
                    time.sleep(2.0)
                    return True
        return False

    def fight(self, scene: Optional[Dict] = None) -> Dict:
        """
        Attacks the boss for BOSS_FIGHT_SECONDS: clicks at the boss area
        (screen center-low), jumps to dodge. Returns a summary dict.
        """
        if not self.a.wm.ensure_focused():
            return {"fought": False}
        print(f"[BOSS] fighting for {int(BOSS_FIGHT_SECONDS)}s...")
        start = time.time()
        clicks = 0
        while time.time() - start < BOSS_FIGHT_SECONDS:
            self.a.input.center_cursor(self.a.wm)
            self.a.input.mouse_click(button="left", duration=0.05)
            clicks += 1
            time.sleep(0.5)
            if clicks % 8 == 0:
                self.a.input.tap_key("SPACE", 0.1)  # dodge jump
                time.sleep(0.3)
        print(f"[BOSS] fight finished ({clicks} attacks) - retreating.")
        self.retreat()
        return {"fought": True, "clicks": clicks}

    def retreat(self):
        """Walks away from the fight area after the boss session."""
        for d in ("S", "A", "S"):
            self.nav.walk(d, BOSS_RETREAT_WALK / 2, jump_every=1.2)
        time.sleep(0.5)
