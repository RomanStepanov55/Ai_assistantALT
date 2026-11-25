"""
Background Watcher ("the Eye") v2 for Muscle Legends - the ideal observer.

Runs against the persistent OCR server (fast: a full scene observation takes
~1-2s), so it can poll every few seconds. It is READ-ONLY (never clicks) and
produces:

  data/scene_state.json  - structured state for the brain and the operator AI
  data/eye_journal.log   - human-readable timeline

Signals per observation:
  - Strength / Gems (parsed + raw)
  - Event banners (brawl / boss / raid / event)
  - Open modal detection (and WHICH modal)
  - Timed gift readiness (CLAIM)
  - E-prompt visibility (standing near gym equipment)
  - Screen motion (stuck-frame detection)
"""
import json
import re
import time
from datetime import datetime
from typing import Dict, Optional

from config import DATA_DIR, ROIS
from vision.ocr_engine import CYRILLIC_MAP

SCENE_STATE_FILE = DATA_DIR / "scene_state.json"
EYE_JOURNAL_FILE = DATA_DIR / "eye_journal.log"

EVENT_KEYWORDS = ["brawl", "boss", "raid", "spawning", "spawned", "defeat", "winner", "event"]
MODAL_KEYWORDS = ["collect", "claim", "spin", "quests", "rewards", "rebirth", "store", "packs", "travel"]


class Watcher:
    def __init__(self, wm, eye, ocr):
        self.wm = wm
        self.eye = eye
        self.ocr = ocr
        self.last_state: Optional[Dict] = None
        self._prev_frame_small = None

    def observe(self, save: bool = True) -> Dict:
        state: Dict = {
            "timestamp": time.time(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        left, top, width, height = self.wm.get_client_rect()
        if width == 0 or height == 0:
            state["roblox_found"] = False
        else:
            state["roblox_found"] = True
            # HARD RULE #2 (2026-11-24): passive observation. The old line
            # self.wm.ensure_focused() raised and dragged the Roblox window to
            # the front (ALT keypress, minimize/restore); the companion must
            # not steal the user's desktop.
            frame = self.eye.capture_full_window()

            # The window is no longer raised, so whatever is on top may be
            # captured instead (the terminal itself, a browser...). Without
            # the HUD badge plates in THIS frame the read proves nothing -
            # the same proof Eye uses (E-009): no numbers are produced at all.
            if not self.frame_is_game(frame):
                state["game_visible"] = False
                state["strength_raw"] = ""
                state["strength"] = None
                state["gems_raw"] = ""
                state["gems"] = None
                state["event_text"] = ""
                state["events"] = []
                state["modal_open"] = False
                state["modal_hits"] = []
                state["gift_ready"] = False
                state["near_machine"] = False
                state["likely_dead"] = False     # a covered window is not a death
                state["motion"] = None
                self._dead_streak = 0
                self.last_state = state
                if save:
                    self._save_state(state)
                    self._append_journal(state)
                return state
            state["game_visible"] = True

            # 1. Top HUD stats (template reader inside detector handles quality)
            str_crop = self.eye.get_roi_crop("strength_counter", frame)
            gems_crop = self.eye.get_roi_crop("gems_counter", frame)

            try:
                from vision.template_reader import binarize_strength, binarize_gems
                s_ink = binarize_strength(str_crop) if str_crop else None
                if s_ink is not None and int(s_ink.sum()) >= 200:
                    t, c = self.ocr_parse_templates(str_crop, s_ink)
                    state["strength_raw"] = t
                    state["strength"] = self.ocr.parse_stat_string(t) if t and "?" not in t else None
                else:
                    state["strength_raw"] = ""
                    state["strength"] = None
                g_ink = binarize_gems(gems_crop) if gems_crop else None
                if g_ink is not None and int(g_ink.sum()) >= 200:
                    t, c = self.ocr_parse_templates(gems_crop, g_ink, gems=True)
                    state["gems_raw"] = t
                    state["gems"] = self.ocr.parse_stat_string(t) if t and "?" not in t else None
                else:
                    state["gems_raw"] = ""
                    state["gems"] = None
            except Exception:
                state["strength_raw"] = ""
                state["strength"] = None
                state["gems_raw"] = ""
                state["gems"] = None

            # 2. Event banner (brawl / boss / raid announcements) - stylized
            #    font garbles OCR, so match keywords fuzzily against tokens
            raw_ev = self.ocr.read_text_from_image(
                self.eye.get_roi_crop("event_banner", frame), "w_events")
            state["event_text"] = raw_ev
            import difflib
            tokens = re.split(r"[^a-zA-Z]+", raw_ev.lower())
            events = set()
            for k in EVENT_KEYWORDS:
                if k in raw_ev.lower():
                    events.add(k)
                    continue
                for tok in tokens:
                    if len(tok) >= 4 and difflib.SequenceMatcher(None, tok, k).ratio() >= 0.72:
                        events.add(k)
                        break
            state["events"] = sorted(events)

            # 3. Modal / dialog detection
            modal_lines = self.ocr.read_lines(
                self.eye.get_roi_crop("modal_area", frame), "w_modal")
            hits = [l["text"] for l in modal_lines
                    if any(k in (l["text"] or "").lower() for k in MODAL_KEYWORDS)]
            state["modal_open"] = bool(hits)
            state["modal_hits"] = hits[:3]

            # 4. Timed gift readiness
            gift_lines = self.ocr.read_lines(
                self.eye.get_roi_crop("timed_gift_btn", frame), "w_gift")
            state["gift_ready"] = any(self.ocr.looks_like_claim(l.get("text")) for l in gift_lines)

            # 5. E-prompt (standing near gym equipment)
            e_lines = self.ocr.read_lines(
                self.eye.get_roi_crop("e_prompt", frame), "w_eprompt")
            state["near_machine"] = any(
                (l.get("text") or "").strip().upper().translate(CYRILLIC_MAP) == "E"
                for l in e_lines)

            # 5b. Death detection: both badges must be unreadable for
            # SEVERAL consecutive observations (OCR hiccups happen when the
            # camera is inside geometry - those are NOT deaths).
            try:
                s_empty = state.get("strength_raw") == "" or state.get("strength") is None
                g_missing = state.get("gems_raw") in ("", None) and state.get("gems") is None
                if s_empty and g_missing:
                    self._dead_streak = getattr(self, "_dead_streak", 0) + 1
                else:
                    self._dead_streak = 0
                state["likely_dead"] = self._dead_streak >= 3
                state["dead_streak"] = self._dead_streak
            except Exception:
                state["likely_dead"] = False

            # 6. Motion (stuck screen detection)
            try:
                small = frame.convert("L").resize((48, 27))
                if self._prev_frame_small is not None:
                    state["motion"] = self._motion_diff(small, self._prev_frame_small)
                else:
                    state["motion"] = None
                self._prev_frame_small = small
            except Exception:
                state["motion"] = None

        self.last_state = state
        if save:
            self._save_state(state)
            self._append_journal(state)
        return state

    def frame_is_game(self, frame) -> bool:
        """Proof that THIS frame shows the game HUD: badge plates found in the
        current frame (E-009). A saved layout is not evidence, an occluded
        window is not a game."""
        if frame is None:
            return False
        try:
            from vision.template_reader import locate_badges
            return bool(locate_badges(frame))
        except Exception:
            return False

    def ocr_parse_templates(self, crop, ink, gems: bool = False):
        from vision.template_reader import TemplateReader
        if not hasattr(self, "_treader"):
            self._treader = TemplateReader()
        return self._treader.read(ink)

    @staticmethod
    def _motion_diff(a, b) -> float:
        pa, pb = list(a.getdata()), list(b.getdata())
        diff = sum(1 for x, y in zip(pa, pb) if abs(x - y) > 15)
        return round(diff / len(pa), 3) if pa else None

    def _save_state(self, state: Dict):
        try:
            with open(SCENE_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _append_journal(self, state: Dict):
        try:
            if not state.get("roblox_found"):
                line = f"[{state['time']}] Roblox window NOT found.\n"
            elif not state.get("game_visible", True):
                line = (f"[{state['time']}] NO-GAME-HUD: window found, but the frame "
                        f"shows no game (covered by another window or not in a game).\n")
            else:
                str_txt = state.get("strength_raw") or "?"
                gems_txt = state.get("gems_raw") or "-"
                gift = "READY" if state.get("gift_ready") else "-"
                modal = "open:" + ",".join(state.get("modal_hits", [])) if state.get("modal_open") else "none"
                events = ",".join(state.get("events", [])) or "none"
                mach = "E" if state.get("near_machine") else "-"
                line = (f"[{state['time']}] Str: {str_txt} | Gems: {gems_txt} | "
                        f"Gift: {gift} | Modal: {modal} | Machine: {mach} | Events: {events}\n")
            with open(EYE_JOURNAL_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
