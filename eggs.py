"""
Egg hatching driver (user-requested feature).

Eggs in Muscle Legends cost earned currency (gems/candy) - the user explicitly
requested egg opening. The golden rule still applies: NEVER spend gems below
GEMS_SAFETY_FLOOR, never touch anything priced in Robux, cap spend per action.

The driver is WORD-driven: it searches for an egg stand / egg button via the
E-prompt discovery or on-screen words ('Egg', 'Hatch'), opens it, picks the
affordable egg, presses Hatch, closes result popups.
"""
import re
import time
from typing import Optional

from config import ROIS, GEMS_SAFETY_FLOOR, MAX_SPEND_PER_ACTION
from vision.ocr_engine import CYRILLIC_MAP


def _norm(t: str) -> str:
    return (t or "").upper().translate(CYRILLIC_MAP)


class EggDriver:
    def __init__(self, actions, navigator):
        self.a = actions
        self.nav = navigator

    # ---------- helpers ----------

    def _read_gems(self) -> Optional[int]:
        return self.a.read_gems()

    def _find_price_in_lines(self, lines) -> Optional[int]:
        """Finds a gems price near a 'Hatch'/'Buy' word (pattern: N or N.NN
        near gem icon lines). Returns the price or None."""
        for l in lines:
            t = _norm(l.get("text"))
            m = re.search(r"^\+?\d+\s*SPIN", t)  # wheel prices - skip
            if m:
                continue
            if re.fullmatch(r"\d{2,4}", t.strip()):
                try:
                    return int(t.strip())
                except ValueError:
                    pass
        return None

    # ---------- main flow ----------

    def hatch_egg(self, scene=None) -> bool:
        """
        1. Discover an egg stand (walk pattern, keyword 'egg' in the name).
        2. Open it (E), OCR the egg menu, pick the affordable egg (Hatch/Buy).
        3. Verify gems delta; abort if below safety floor.
        """
        if not self.a.wm.ensure_focused():
            return False
        gems_before = self._read_gems()
        if gems_before is not None and gems_before <= GEMS_SAFETY_FLOOR + MAX_SPEND_PER_ACTION:
            print(f"[EGGS] gems {gems_before:,} too close to safety floor - skipping.")
            return False

        print("[EGGS] searching for an egg stand...")
        name = self.nav.discover(seconds=10.0, scene=scene,
                                 want_keywords=("egg", "яиц", "hatch"))
        if not name:
            print("[EGGS] no egg stand found this pass.")
            return False
        if "egg" not in (name or "").lower():
            print(f"[EGGS] '{name}' is not an egg stand - aborting.")
            self.a.input.tap_key("SPACE", 0.1)
            return False

        # open the stand
        self.a.input.tap_key("E", 0.12)
        time.sleep(1.5)

        lines, frame = self.a._modal_lines("egg_menu")
        joined = " ".join((l.get("text") or "") for l in lines)
        if not any(k in _norm(joined) for k in ("HATCH", "EGG", "BUY")):
            print("[EGGS] egg menu did not open - closing.")
            self.a.close_modal_safe()
            return False

        # choose a Hatch/Buy button whose price we can afford
        hatch_lines = [l for l in lines if _word_hatch(l.get("text"))]
        for l in hatch_lines:
            price = self._price_for_line(lines, l)
            if price is None:
                continue
            if gems_before is not None and gems_before - price < GEMS_SAFETY_FLOOR:
                print(f"[EGGS] price {price:,} would break the safety floor - skip.")
                continue
            if price > MAX_SPEND_PER_ACTION:
                print(f"[EGGS] price {price:,} above per-action cap - skip.")
                continue
            print(f"[EGGS] hatching egg for {price:,} gems...")
            self.a._click_line("modal_area", l, frame)
            time.sleep(1.2)
            # result popup -> close
            self.a.close_modal_safe()
            self.a.close_modal_safe()
            gems_after = self._read_gems()
            if gems_before is not None and gems_after is not None:
                print(f"[EGGS] gems delta: {gems_after - gems_before:+,}")
                if gems_after < gems_before:
                    print("[EGGS] !!! GEMS DECREASED - spend recorded.")
            return True

        print("[EGGS] no affordable hatch button found - closing.")
        self.a.close_modal_safe()
        return False

    def _price_for_line(self, lines, target) -> Optional[int]:
        """A price is a digit line overlapping the target button's Y range."""
        cy = target["y"] + target["h"] / 2
        best = None
        for l in lines:
            t = _norm(l.get("text")).strip()
            if re.fullmatch(r"[\d\s,\.]{2,7}", t.strip()):
                ly = l["y"] + l["h"] / 2
                if abs(ly - cy) <= max(l["h"], target["h"]) * 0.8:
                    digits = re.sub(r"[^\d]", "", t)
                    if digits:
                        best = int(digits)
        return best


def _word_hatch(text: str) -> bool:
    t = _norm(text)
    import difflib
    for tok in re.split(r"[^A-Z]+", t):
        if difflib.SequenceMatcher(None, tok, "HATCH").ratio() >= 0.75:
            return True
    return False
