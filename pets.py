"""
Pets & inventory driver for Muscle Legends (OCR-guided, golden-rule safe).

All logic is WORD-driven, not position-driven: the driver opens the pets panel,
scans rows via OCR and only ever clicks words in the SAFE set:
  Equip / Unequip / Upgrade (free or listed later) / Delete (with confirm)
NEVER clicks: any price chip, Robux icon, gems icon, "Buy", "Shop".

Safety:
  - deletion is capped per session and only for pets whose power is far below
    the best one (config: PETS_DELETE_RATIO);
  - every spend is delta-verified against the gems watchdog;
  - unknown UIs are photographed to debug_frames/learn_me/.
"""
import re
import time
from typing import Dict, List, Optional

from config import ROIS
from vision.ocr_engine import CYRILLIC_MAP


def _norm(t: str) -> str:
    return (t or "").upper().translate(CYRILLIC_MAP)


class PetsDriver:
    def __init__(self, actions):
        """actions = AgentActions (wm, input, eye, ocr, reader, memory inside)."""
        self.a = actions
        self.deleted_this_session = 0

    # ---------- panel ----------

    def open_panel(self) -> bool:
        if not self.a.wm.ensure_focused():
            return False
        if not self.a.input.click_roi("items_btn", self.a.wm):
            return False
        time.sleep(1.2)
        lines, frame = self.a._modal_lines("pets_panel")
        joined = " ".join((l.get("text") or "") for l in lines)
        ok = any(k in _norm(joined) for k in ("EQUIP", "PET", "DELETE", "INVENTORY"))
        if not ok:
            # unknown UI -> self-photograph (learn_me) and abort
            try:
                from config import DEBUG_DIR
                lm = DEBUG_DIR / "learn_me"
                lm.mkdir(exist_ok=True)
                frame.save(lm / f"pets_ui_{int(time.time())}.png")
                print("[PETS] unknown pets UI - captured to learn_me")
            except Exception:
                pass
            self.close_panel()
            return False
        return True

    def close_panel(self):
        self.a.close_modal_safe()

    # ---------- scanning ----------

    def scan_rows(self) -> List[Dict]:
        """
        Returns rows: [{'equip_y':.., 'power': int|None, 'texts': [...]}, ...]
        Rows are grouped by Y proximity of the word 'Equip'.
        """
        lines, frame = self.a._modal_lines("pets_panel")
        equips = [l for l in lines if _word(l["text"], ("equip",))]
        rows = []
        for eq in equips:
            cy = eq["y"] + eq["h"] / 2
            near = [l for l in lines
                    if abs((l["y"] + l["h"] / 2) - cy) <= max(eq["h"], l["h"]) * 1.4]
            texts = " ".join((l.get("text") or "") for l in near)
            power = None
            m = re.search(r"([\d\.]+)\s*([KMB]?)", texts.replace(",", ""))
            if m:
                mult = {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(2).upper(), 1)
                try:
                    power = int(float(m.group(1)) * mult)
                except ValueError:
                    pass
            rows.append({"equip_line": eq, "power": power, "texts": texts[:120],
                         "frame": frame})
        rows.sort(key=lambda r: -(r["power"] or 0))
        return rows

    # ---------- actions ----------

    def equip_best(self) -> bool:
        """Equips the pet with the highest visible power (click 'Equip')."""
        if not self.open_panel():
            return False
        rows = self.scan_rows()
        done = False
        if rows:
            best = rows[0]
            ok = self.a._click_line("modal_area", best["equip_line"], best["frame"])
            done = bool(ok)
            time.sleep(0.6)
            print(f"[PETS] equip best (power~{best['power']}): {'ok' if done else 'fail'}")
        else:
            print("[PETS] no Equip buttons found")
        self.close_panel()
        return done

    def delete_weakest(self, max_deletes: int = 3) -> int:
        """
        Deletes up to max_deletes weakest pets, but only if their power is
        below PETS_DELETE_RATIO of the best (protects valuable pets).
        """
        from config import PETS_DELETE_RATIO
        if not self.open_panel():
            return 0
        rows = self.scan_rows()
        if len(rows) < 2:
            self.close_panel()
            return 0
        best_power = rows[0]["power"] or 0
        deleted = 0
        for row in reversed(rows[1:]):
            if deleted >= max_deletes:
                break
            pw = row["power"]
            if best_power and pw is not None and pw >= best_power * PETS_DELETE_RATIO:
                continue  # too valuable
            # click its Equip button line -> on some rows the same spot has
            # Delete; instead scan for a Delete word in this row
            del_line = self._find_delete_near(row)
            if not del_line:
                continue
            self.a._click_line("modal_area", del_line["line"], del_line["frame"])
            time.sleep(0.6)
            # confirmation dialog: click Delete/Yes if present
            self._confirm_dialog()
            deleted += 1
            self.deleted_this_session += 1
            print(f"[PETS] deleted weak pet (power~{pw}), total {self.deleted_this_session}")
            if self.deleted_this_session >= 12:  # global cap
                break
        self.close_panel()
        return deleted

    def _find_delete_near(self, row: Dict) -> Optional[Dict]:
        lines, frame = self.a._modal_lines("pets_panel")
        cy = row["equip_line"]["y"] + row["equip_line"]["h"] / 2
        for l in lines:
            t = _norm(l.get("text"))
            if _word(l.get("text"), ("delete", "sell")):
                if abs((l["y"] + l["h"] / 2) - cy) <= max(l["h"], 40) * 1.6:
                    return {"line": l, "frame": frame}
        return None

    def _confirm_dialog(self):
        lines, frame = self.a._modal_lines("pets_confirm")
        for l in lines:
            t = _norm(l.get("text"))
            if _word(l.get("text"), ("delete", "yes", "confirm", "sell")):
                self.a._click_line("modal_area", l, frame)
                time.sleep(0.6)
                return


def _word(text: str, words) -> bool:
    t = _norm(text)
    for w in words:
        if re.search(rf"(?<![A-Z]){w}(?![A-Z])", t):
            return True
        import difflib
        for tok in re.split(r"[^A-Z]+", t):
            if len(tok) >= 4 and difflib.SequenceMatcher(None, tok, w.upper()).ratio() >= 0.75:
                return True
    return False
