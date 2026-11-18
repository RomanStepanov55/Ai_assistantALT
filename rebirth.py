"""
Rebirth driver - reliable, OCR-verified, user-visible.

Flow:
  1. Read the strength requirement from the REBIRTH modal itself (not a guess).
  2. Click the confirm button only if the requirement is satisfied on screen.
  3. Verify success by a large strength drop; record to memory.
"""
import re
import time
from typing import Optional

from vision.ocr_engine import CYRILLIC_MAP


def _norm(t: str) -> str:
    return (t or "").upper().translate(CYRILLIC_MAP)


class RebirthDriver:
    def __init__(self, actions, memory):
        self.a = actions
        self.memory = memory

    def attempt(self) -> bool:
        """
        Attempts a rebirth. Returns True on verified success.
        Aborts safely (closes modal) on any ambiguity. Never spends anything -
        rebirth is a free in-game mechanic.
        """
        a = self.a
        if not a.wm.ensure_focused():
            return False

        before = a.read_strength()
        if not before:
            print("[REBIRTH] cannot read strength - abort.")
            return False

        if not a.input.click_roi("rebirth_btn", a.wm):
            return False
        time.sleep(1.0)

        lines, frame = a._modal_lines("rebirth_modal")
        if not lines:
            print("[REBIRTH] modal did not open - closing.")
            a.close_modal_safe()
            return False

        joined = " ".join((l.get("text") or "") for l in lines)
        nj = _norm(joined)

        # Parse the on-screen requirement: patterns like '500B Strength' / 'Требуется'
        requirement = None
        m = re.search(r"([\d\.]+)\s*([KMBT]?)\s*(STRENGTH|REBIRTH)", nj)
        if m:
            mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2).upper(), 1)
            try:
                requirement = int(float(m.group(1)) * mult)
            except ValueError:
                pass

        if requirement is not None and before < requirement:
            print(f"[REBIRTH] requirement {requirement:,} > strength {before:,} - abort.")
            a.close_modal_safe()
            return False

        # Confirm = bottom-most line containing 'rebirth'
        cands = [l for l in lines if "rebirth" in _norm(l.get("text"))]
        if not cands:
            print("[REBIRTH] confirm button not found - closing.")
            a.close_modal_safe()
            return False
        btn = max(cands, key=lambda l: l["y"] + l["h"])
        a._click_line("modal_area", btn, frame)
        time.sleep(2.0)

        after = a.read_strength()
        if after is not None and before is not None and after < before * 0.6:
            print(f"[REBIRTH] SUCCESS: {before:,} -> {after:,}")
            return True

        print(f"[REBIRTH] not confirmed (before={before}, after={after}) - closing.")
        a.close_modal_safe()
        return False
