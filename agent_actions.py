"""
AgentActions: OCR-guided safe actions for Muscle Legends.

Principle: never click blind coordinates inside modals. Instead:
  1. Capture the screen, OCR the modal region.
  2. Find the button by its TEXT (Collect / CLAIM / SPIN / Rebirth / X).
  3. Click the found rect center. If the button is not found - do NOT guess.

GOLDEN RULE is enforced at three levels:
  - config.ALLOWED_CLICK_ROIS whitelist (click_roi guard)
  - here: only whitelisted action words are ever clicked
  - quest Collect buttons are clicked ONLY if the row progress is complete
    (prevents paying gems to skip incomplete quests)
"""
import re
import time
from typing import Dict, List, Optional, Tuple

from config import ROIS
from vision.ocr_engine import CYRILLIC_MAP
from vision.ocr_engine import CROPS_DIR
from vision.template_reader import TemplateReader


def eye_crop_full(eye, frame):
    """Full-frame 'crop' (the whole client area) for scene scans."""
    return frame

# The only words the bot is allowed to click inside modals, by text.
SAFE_BUTTON_WORDS = {"collect", "claim", "spin", "rebirth", "x", "close", "reject", "decline", "no", "ok"}


def _word_match(text: str, words) -> bool:
    t = (text or "").lower()
    if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", t) for w in words):
        return True
    # Fuzzy pass: OCR mangles HUD letters ('Collect' -> 'Со11ест', 'СИАМ' for CLAIM)
    import difflib
    from vision.ocr_engine import OCREngine
    norm = (text or "").upper().translate(CYRILLIC_MAP).lower()
    tokens = re.split(r"[^a-z]+", norm)
    for w in words:
        if len(w) < 4:
            continue
        for tok in tokens:
            if len(tok) >= 3 and difflib.SequenceMatcher(None, tok, w).ratio() >= 0.7:
                return True
    return False


class AgentActions:
    def __init__(self, wm, input_ctrl, eye, ocr, detector=None, memory=None):
        self.wm = wm
        self.input = input_ctrl
        self.eye = eye
        self.ocr = ocr
        self.detector = detector
        self.memory = memory
        self.reader = TemplateReader()
        # One shared perception engine for every HUD read. It captures through
        # the injected ScreenEye, so tests and live runs go down the same path,
        # and it caches its self-calibrated layout between reads.
        self._perceiver = None
        # Which training item is believed to be held, per exercise. The hotbar
        # slot key TOGGLES (measured), so pressing it blindly is destructive.
        self._equip_state: Dict[str, Optional[bool]] = {}

    @property
    def perceiver(self):
        """Lazy Eye bound to this agent's window/reader (same glyph store)."""
        if self._perceiver is None:
            from vision.eye import Eye
            self._perceiver = Eye(screen_eye=self.eye, ocr=self.ocr,
                                  reader=self.reader)
        return self._perceiver

    def train_agility_run(self, duration_sec: float = 3.5):
        """Forward to InputController's agility sprint."""
        self.input.train_agility_run(duration_sec=duration_sec)

    # ---------- low-level helpers ----------

    def _modal_lines(self, tag: str) -> Tuple[List[Dict], Optional[object]]:
        """Captures a fresh frame and OCRs the modal_area region."""
        frame = self.eye.capture_full_window()
        if frame is None:
            return [], None
        crop = self.eye.get_roi_crop("modal_area", frame)
        lines = self.ocr.read_lines(crop, tag)
        return lines, frame

    def _line_to_screen(self, roi_name: str, line: Dict, frame) -> Optional[Tuple[int, int]]:
        """Converts a line rect (crop pixels) -> absolute screen point (its center)."""
        roi = ROIS[roi_name]
        fw, fh = frame.size
        ox = roi["x"] * fw + line["x"] + line["w"] / 2.0
        oy = roi["y"] * fh + line["y"] + line["h"] / 2.0
        left, top, cw, ch = self.wm.get_client_rect()
        if cw == 0 or ch == 0:
            return None
        return (int(left + ox), int(top + oy))

    def _click_line(self, roi_name: str, line: Dict, frame) -> bool:
        pt = self._line_to_screen(roi_name, line, frame)
        if not pt:
            return False
        self.input.mouse_click(pt[0], pt[1])
        return True

    def close_modal_safe(self, attempts: int = 2) -> bool:
        """
        Closes any modal via the X button, verifying by OCR that the modal is gone.
        Never uses ESC. Parks the cursor on the safe point afterwards.
        """
        for _ in range(attempts):
            self.input.click_roi("modal_close_btn", self.wm)
            time.sleep(0.5)
            self.input.center_cursor(self.wm)
            lines, _ = self._modal_lines("close_check")
            markers = [l for l in lines if _word_match(l["text"], {"collect", "claim", "spin", "quests", "rewards"})]
            if not markers:
                return True
        return False

    # ---------- popup defense ----------

    def handle_popups(self) -> str:
        """
        Full-screen popup defense. Handles (in priority order):
          - trade offers        -> click Reject (NEVER accept trades)
          - friend requests     -> click NO
          - use-item dialogs    -> click NO (never consume items/boosts)
          - brawl join dialogs  -> declined with NO (free; they cover the click area)
          - player cards        -> dismissed by clicking empty space
        Golden rule: the words YES/Trade/OK on offers are NEVER clicked.
        Returns a description of what was handled ('' if nothing).
        """
        handled = ""
        if not self.wm.ensure_focused():
            return handled

        frame = self.eye.capture_full_window()
        if frame is None:
            return handled
        fw, fh = frame.size
        left, top, cw, ch = self.wm.get_client_rect()
        if cw == 0 or ch == 0:
            return handled

        crop = self.eye.get_roi_crop("modal_area", frame)
        lines = self.ocr.read_lines(crop, "popups")
        joined = " ".join((l.get("text") or "") for l in lines)

        def _norm(s: str) -> str:
            return (s or "").upper().translate(CYRILLIC_MAP).lower()

        def _line_point(line):
            ox = ROIS["modal_area"]["x"] * fw + line["x"] + line["w"] / 2.0
            oy = ROIS["modal_area"]["y"] * fh + line["y"] + line["h"] / 2.0
            return (int(left + ox), int(top + oy))

        nj = _norm(joined)

        # SELF-DEVELOPMENT: an unknown modal gets photographed for future
        # mapping (max 1 per 10 min) - the bot documents what it cannot read.
        if joined.strip() and joined not in getattr(self, "_last_unknown", ""):
            if time.time() - getattr(self, "_last_unknown_ts", 0) > 600:
                try:
                    from config import DEBUG_DIR
                    lm = DEBUG_DIR / "learn_me"
                    lm.mkdir(exist_ok=True)
                    frame.save(lm / f"unknown_ui_{int(time.time())}.png")
                    self._last_unknown = joined
                    self._last_unknown_ts = time.time()
                    print("[LEARN] Unknown UI captured to debug_frames/learn_me/")
                except Exception:
                    pass

        # 1. Trade offer -> Reject (never Trade!)
        if "trade offer" in nj or "has just sent" in nj:
            for l in lines:
                t = _norm(l.get("text"))
                if "reject" in t:
                    pt = _line_point(l)
                    self.input.mouse_click(pt[0], pt[1])
                    time.sleep(0.6)
                    return "rejected trade offer"
            # fallback: never click Trade! -> click NO-ish region right of Trade button
            return "trade offer seen, no Reject button found (left alone)"

        # 2. Friend request -> NO
        if "friend request" in nj:
            for l in lines:
                t = _norm(l.get("text"))
                if re.fullmatch(r"no!?", t.strip()) or t.strip() == "no":
                    pt = _line_point(l)
                    self.input.mouse_click(pt[0], pt[1])
                    time.sleep(0.5)
                    return "declined friend request"

        # 3. Use-item dialog -> YES (the user WANTS boosts used - they are
        #    free inventory consumables, never purchases)
        if "use your" in nj and ("shake" in nj or "potion" in nj or "boost" in nj):
            for l in lines:
                t = _norm(l.get("text"))
                if t.strip() in ("yes", "yes!"):
                    pt = _line_point(l)
                    self.input.mouse_click(pt[0], pt[1])
                    time.sleep(0.5)
                    return "used boost (YES per user request)"

        # 4. Player card (Examine Avatar / Block / Report Abuse) -> dismiss
        if "examine avatar" in nj or "report abuse" in nj:
            self.input.center_cursor(self.wm)
            sx, sy = self.wm.to_screen_coords(0.30, 0.75)
            self.input.mouse_click(sx, sy)
            time.sleep(0.5)
            return "dismissed player card"

        # 5. Persistent YES/NO dialogs (boss travel, brawl join prompts) block all
        #    UI. Count consecutive sightings; after 2, close via the dialog X or NO.
        #    Measured 2026-11-17: the brawl invite "A brawl is starting on Desert
        #    Ring, want to join? YES/NO" sits exactly over the training click
        #    area, and leaving it alone cost a whole 20-rep burst (25 s, 0 gain).
        #    Declining is free - no purchase, no gem cost.
        yn = ("yes" in nj) and ("no" in nj or "no!" in nj)
        if yn:
            self._yn_sightings = getattr(self, "_yn_sightings", 0) + 1
            if self._yn_sightings >= 2:
                self._yn_sightings = 0
                for l in lines:
                    t = _norm(l.get("text"))
                    if t.strip() in ("x", "no", "no!"):
                        pt = _line_point(l)
                        self.input.mouse_click(pt[0], pt[1])
                        time.sleep(0.6)
                        return f"closed persistent dialog via '{l.get('text')}'"
                self.input.center_cursor(self.wm)
                sx, sy = self.wm.to_screen_coords(0.30, 0.75)
                self.input.mouse_click(sx, sy)
                return "dismissed persistent dialog by clicking away"
        else:
            self._yn_sightings = 0

        return handled

    # ---------- training with verification ----------

    def smart_lift(self, lifts: int = 6, equip: str = "dumbbells", max_attempts: int = 3,
                   dialog_open: Optional[bool] = None) -> Optional[int]:
        """
        Lifts with VERIFIED gains: reads strength before/after each set; if the
        game ignored the reps (character seated on a machine, stuck, dead...),
        recovers (SPACE dismount + walk back) and retries.

        Returns:
          > 0  - verified strength gain,
            0  - VERIFIED no gain (the recovery loop ran and failed),
          None - could not verify at all (badge unreadable). The reps were still
                 executed safely, but nothing is learned from this set.

        Returning None instead of a fake gain is essential: the old optimistic
        `return 1` made every read failure look like a successful set, so the
        bench-seat trap became invisible and the capability stats (success rate,
        reward) were polluted with fabricated numbers.
        """
        equip_fn = {
            "dumbbells": self.input.equip_dumbbells,
            "pushups": self.input.equip_pushups,
            "situps": self.input.equip_situps,
        }.get(equip, self.input.equip_dumbbells)

        # HARD GATE: no burst unless THIS frame is proven to be the game HUD.
        # Measured 2026-11-17: the play session had ended and the Roblox client
        # was showing its own music page; 4 x 20 clicks were delivered into
        # that UI because only the READS were guarded, never the clicking.
        frame = self.eye.capture_full_window()
        if frame is None or not self.perceiver.frame_is_game(frame):
            print("[SMART-LIFT] refused: the captured frame is not the game HUD "
                  "(session ended / app screen). No clicks sent.")
            return None
        flags_open = None
        if dialog_open is None:
            # Only direct callers (experiments, main.py test paths) pay for this
            # check: it costs 4-8 s of OCR. The adapter already measured the
            # scene and passes its answer in.
            flags_open = self.perceiver._ui_flags(frame, deep=True)
            dialog_open = bool(flags_open.get("modal_open"))
        if dialog_open:
            word = (flags_open or {}).get("modal_word")
            print(f"[SMART-LIFT] refused: a dialog is open ({word!r}) - centre "
                  f"clicks would land on its buttons. Close it first.")
            return None

        # The hotbar slot key is a TOGGLE, not "equip". Measured live
        # 2026-11-16 (debug_frames/equip_*): with the dumbbells already held,
        # one press of slot 2 took them OFF (20 reps -> 0 gain, no highlight
        # on the slot) and the next press put them back (+390,000, highlight
        # returned). Pressing the slot before EVERY attempt therefore
        # alternated held/not-held, which is exactly why the first burst of
        # each set after the first was silently ignored. The held state is now
        # tracked from verified gains instead of being re-asserted blindly.
        held = self._equip_state.get(equip)  # None = unknown

        unverified = 0
        for attempt in range(max_attempts):
            pressed = False

            if attempt > 0:
                # Trap recovery ONLY on retries: SPACE dismounts a bench seat,
                # S steps back. It must not run before the first burst -
                # measured: two consecutive 20-rep bursts without it gained
                # +80,000 each, while the prefix walked the character off the
                # platform and cost ~1.4 s per attempt.
                self.input.tap_key("SPACE", 0.08)
                time.sleep(0.3)
                self.input.key_down("S")
                time.sleep(0.8)
                self.input.key_up("S")
                time.sleep(0.3)

            if held is not True:
                equip_fn()
                pressed = True
                held = True
                time.sleep(0.5)

            before = self.read_strength()
            delivered = self.input.smooth_lift(lifts=lifts, animation_delay=0.65,
                                               wm=self.wm)
            if delivered is not None and delivered < lifts:
                # The burst was cut short because the game lost the foreground.
                # Counting those missing clicks as "the game ignored the set"
                # would teach the wrong lesson, so this attempt is unverified.
                unverified += 1
                print(f"[SMART-LIFT] burst aborted: {delivered}/{lifts} clicks "
                      f"delivered (window lost focus) - not a rep set.")
                time.sleep(0.4)
                continue
            time.sleep(2.2)  # let the badge count-up animation settle
            after = self.read_strength()

            if before is None or after is None:
                unverified += 1
                print(f"[SMART-LIFT] read unverified (before={before}, after={after}) "
                      f"- reps executed, no learning claim.")
                # one honest retry with a longer settle window, then give up
                continue
            if after > before:
                # mid-count frames can read garbage (e.g. '256.6M' for 56.6M):
                # a >2x jump is suspicious - re-read once to confirm
                if before > 0 and after > before * 2:
                    confirm = self.read_strength()
                    if confirm is not None and confirm < before * 2:
                        after = confirm
                print(f"[SMART-LIFT] set gain: +{after - before:,}" if attempt == 0
                      else f"[SMART-LIFT] recovered on attempt {attempt + 1}: +{after - before:,}")
                self._equip_state[equip] = True
                return after - before

            if pressed:
                # The press made in THIS attempt took the item off (measured
                # toggle), so nothing was held and the reps could not count.
                # Any other cause (seat, stuck) keeps the item marked as held
                # and is handled by the retry prefix above.
                held = False
            self._equip_state[equip] = held
            print(f"[SMART-LIFT] no gain ({before:,} -> {after:,}), attempt {attempt + 1}: "
                  f"recovering (SPACE + step back + re-hold item)")

        if unverified >= max_attempts:
            print("[SMART-LIFT] badge unreadable on every attempt - verification "
                  "skipped (check window size / ROI; see diag_reader.py).")
            return None
        print("[SMART-LIFT] still no gains after recovery attempts.")
        return 0

    # ---------- stats ----------

    def read_strength(self, allow_relocate: bool = True) -> Optional[int]:
        """Strength badge value, or None when no trustworthy reading exists.

        Reads through the EYE (vision.eye.Eye), not through ad-hoc crops:
          * the badge is located from the frame itself (window size / DPI / gym
            layout changes no longer blind it; `allow_relocate` is kept for
            callers that want to forbid the search);
          * the value must pass the eye's validation + confidence gate;
          * a frame that does not show the game HUD (Roblox occluded, another
            window on top) yields None - a grabbed foreign window was measured
            reporting a plausible strength value that came from a music player.

        CRITICAL: None means "unknown", NOT "zero" and NOT "success".
        """
        from vision.eye import READABLE_CONF

        self.wm.ensure_focused()
        perceiver = self.perceiver
        for i in range(6):  # the badge counts up during training
            frame = self.eye.capture_full_window()
            if frame is None:
                return None
            if not perceiver.frame_is_game(frame):
                print("[EYE] no game HUD in the captured frame "
                      "(Roblox occluded/minimized) - strength unknown")
                return None
            reading = perceiver.read_once("strength", frame, rescue=False)
            value = reading.get("value")
            if value is not None and reading.get("confidence", 0) >= READABLE_CONF:
                return int(value)
            if not allow_relocate and i == 0:
                return None
            time.sleep(0.4 if i == 0 else 1.2)
        return None

    def read_hud(self) -> Dict:
        if self.detector is None:
            return {}
        return self.detector.extract_full_hud_state()

    # ---------- actions ----------

    def claim_gifts(self) -> bool:
        """
        Claims the timed gift. In Muscle Legends clicking the CLAIM box grants
        the reward INSTANTLY (no modal opens) - verified by a gems delta.
        If a modal does open, its Collect/Claim buttons are clicked via OCR.
        """
        if not self.wm.ensure_focused():
            return False
        self.handle_popups()

        ready = False
        for attempt in range(3):
            frame = self.eye.capture_full_window()
            crop = self.eye.get_roi_crop("timed_gift_btn", frame)
            lines = self.ocr.read_lines(crop, "gift")
            ready = any(self.ocr.looks_like_claim(l.get("text")) for l in lines)
            if not ready and crop is not None:
                # CLAIM is white-on-blue: try a white-text pass for contrast
                try:
                    from PIL import Image as _Img
                    w, h = crop.size
                    big = crop.resize((w * 3, h * 3), _Img.Resampling.LANCZOS)
                    from vision.template_reader import _remove_border_ink
                    import numpy as _np
                    arr = _np.asarray(big.convert("L"))
                    white = _Img.fromarray(_np.where(arr > 190, 0, 255).astype("uint8"))
                    wpath = CROPS_DIR / "gift_white_prep.png"
                    white.save(wpath)
                    data = self.ocr.server.request(str(wpath.resolve()))
                    if data:
                        for l in data.get("lines", []):
                            if self.ocr.looks_like_claim(l.get("text")):
                                ready = True
                                break
                except Exception:
                    pass
            if ready:
                break
            time.sleep(1.0)
        if not ready:
            print("[GIFTS] Gift not ready yet (no CLAIM text).")
            return False

        gems_before = self.read_gems()

        if not self.input.click_roi("timed_gift_btn", self.wm):
            return False
        time.sleep(1.2)

        # If a modal did open, click its Collect/Claim buttons via OCR
        lines, frame = self._modal_lines("gift_modal")
        targets = [l for l in lines if _word_match(l["text"], {"collect", "claim"})]
        clicked = []
        for t in targets[:4]:
            pt = self._line_to_screen("modal_area", t, frame)
            if pt and all(abs(pt[0]-x) > 25 or abs(pt[1]-y) > 25 for x, y in clicked):
                self.input.mouse_click(pt[0], pt[1])
                clicked.append(pt)
                time.sleep(0.4)
        if targets:
            self.close_modal_safe()

        gems_after = self.read_gems()
        if gems_before is not None and gems_after is not None:
            delta = gems_after - gems_before
            print(f"[GIFTS] gems delta: {delta:+,} ({gems_before:,} -> {gems_after:,})")
            if delta < 0:
                print("[GIFTS] !!! GEMS DECREASED - flagging for review.")
                return False
            return delta > 0 or bool(clicked)
        print("[GIFTS] gift clicked (gems unreadable to verify).")
        return True


    def find_machine(self, max_total: float = 16.0) -> bool:
        """
        Walks in a search pattern until the E-prompt appears (machine in reach).
        Directions: W -> D -> S -> A, bounded by max_total seconds.
        Returns True when an E-prompt is visible.
        """
        deadline = time.time() + max_total
        dirs = [("W", 1.4), ("D", 1.2), ("S", 1.4), ("A", 1.2)]
        i = 0
        while time.time() < deadline:
            key, dur = dirs[i % len(dirs)]
            i += 1
            self.input.key_down(key)
            for _ in range(int(dur / 0.35)):
                time.sleep(0.35)
                if self._e_prompt_visible():
                    self.input.key_up(key)
                    return True
            self.input.key_up(key)
            time.sleep(0.1)
        return self._e_prompt_visible()

    def _e_prompt_visible(self) -> bool:
        frame = self.eye.capture_full_window()
        lines = self.ocr.read_lines(self.eye.get_roi_crop("e_prompt", frame), "machine_walk")
        return any((l.get("text") or "").strip().upper().translate(CYRILLIC_MAP) == "E"
                   for l in lines)

    def machine_session(self, reps: int = 8) -> str:
        """
        Full machine session: assumes the E-prompt is visible.
        Reads the machine name: Treadmill-like machines run automatically
        (wait mode); bench/deadlift machines need clicks (reps mode).
        Returns the mode used: 'wait' | 'reps' | 'failed'.
        """
        raw = self.ocr.read_text_from_image(
            self.eye.get_roi_crop("machine_info", self.eye.capture_full_window()),
            "machine_name")
        name = (raw or "").lower().translate(CYRILLIC_MAP)
        mode = "wait" if ("treadmi" in name or "бегов" in name or "dorojk" in name) else "reps"
        print(f"[MACHINE] name OCR: '{raw}' -> mode={mode}")

        self.input.tap_key("E", 0.12)
        time.sleep(1.0)

        if mode == "wait":
            # treadmill-style: the machine does the work, just hold position
            time.sleep(8.0)
        else:
            self.input.center_cursor(self.wm)
            for i in range(reps):
                self.input.mouse_click(button="left", duration=0.06)
                time.sleep(0.65)

        self.input.tap_key("SPACE", 0.1)   # dismount
        time.sleep(0.4)
        return mode


    def look_around(self) -> Dict:
        """
        The eye looks AROUND the character (the user's requirement):
        zoom the camera out, rotate ~180 degrees slowly while OCR-scanning
        several frames, and report what is seen (danger, eggs, boards, players).
        """
        findings = {"texts": [], "danger": False, "eggs": False}
        try:
            self.input.camera_zoom("out", clicks=4)
            time.sleep(0.4)
            for step in range(6):
                frame = self.eye.capture_full_window()
                lines = self.ocr.read_lines(eye_crop_full(self.eye, frame), "look_around")
                text = " ".join((l.get("text") or "") for l in lines).lower()
                findings["texts"].append(text[:120])
                if any(k in text for k in ("boss", "brawl", "kill")):
                    findings["danger"] = True
                if any(k in text for k in ("egg", "hatc")):
                    findings["eggs"] = True
                self.input.camera_rotate(seconds=0.7, speed=14)
                time.sleep(0.2)
        except Exception as e:
            findings["error"] = str(e)
        return findings

    def read_gems(self):
        """Gems/candy badge value, or None when it cannot be trusted.

        Goes through the same eye as strength, so both counters are read with
        the same self-calibrated geometry and the same "is this the game?"
        gate. Supports both badge variants (blue gems / green candy).

        TWO AGREEING FRAMES ARE REQUIRED. Measured failure 2026-11-16 (EXP-01):
        a single-sample read returned 6,100,000 while the badge showed
        "16.1M" - the leading digit was dropped, and that corrupt value
        reached metrics_delta as "+5,000,000 gems". Unlike strength, the gems
        badge does not count up during training, so two identical readings of
        two independent captures cost ~0.7 s and make a one-off glyph loss
        unable to pass as data. Disagreement yields None (unknown), never a
        guess: a guessed LOWER value would look like spending, a guessed
        HIGHER value could authorise spending above the real balance.
        """
        from vision.eye import READABLE_CONF

        perceiver = self.perceiver
        prev = None
        for _ in range(4):
            frame = self.eye.capture_full_window()
            if frame is None:
                return None
            if not perceiver.frame_is_game(frame):
                print("[EYE] no game HUD in the captured frame - gems unknown")
                return None
            reading = perceiver.read_once("gems", frame, rescue=False)
            value = reading.get("value")
            if value is not None and reading.get("confidence", 0) >= READABLE_CONF:
                value = int(value)
                if prev is not None and value == prev:
                    return value
                prev = value
            else:
                prev = None
            time.sleep(0.45)
        if prev is not None:
            print(f"[EYE] gems: readings never agreed (last {prev:,}) - unknown")
        return None

    def spin_wheel(self) -> bool:
        """
        Opens the wheel and clicks ONLY the FREE SPIN button.
        GOLDEN RULE: buttons like '+1 Spin <gems>' / '+10 Spins <gems>' cost
        gems (currency that can be bought with real money) and are NEVER
        clicked. If no free spin exists - just close the modal.
        """
        import re as _re
        if not self.wm.ensure_focused():
            return False
        self.handle_popups()

        if not self.input.click_roi("wheel_btn", self.wm):
            return False
        time.sleep(1.0)

        lines, frame = self._modal_lines("wheel_modal")
        free_spin = None
        for l in lines:
            t = (l.get("text") or "").lower().translate(CYRILLIC_MAP)
            compact = _re.sub(r"[^a-z]", "", t)
            if "freespin" in compact:
                free_spin = l
                break
        if not free_spin:
            print("[WHEEL] No FREE SPIN available - closing modal (never pay for spins).")
            self.close_modal_safe()
            return False

        self._click_line("modal_area", free_spin, frame)
        print("[WHEEL] FREE SPIN clicked, waiting 4.5s for the wheel...")
        time.sleep(4.5)
        # collect the reward / close result popup via X
        self.close_modal_safe()
        self.close_modal_safe()
        return True

    def check_quests(self) -> List[str]:
        """
        Opens QUESTS and claims ONLY completed quests (progress like '5K/5K').
        Incomplete quests are never touched -> no gems are ever wasted on skip.
        """
        if not self.wm.ensure_focused():
            return []
        self.handle_popups()

        if not self.input.click_roi("quests_btn", self.wm):
            return []
        time.sleep(0.9)

        lines, frame = self._modal_lines("quests_modal")
        claims: List[str] = []

        collects = [l for l in lines if _word_match(l["text"], {"collect"})]
        for cl in collects:
            cy = cl["y"] + cl["h"] / 2.0
            progress = None
            for l in lines:
                if l is cl:
                    continue
                ly = l["y"] + l["h"] / 2.0
                if abs(ly - cy) <= max(cl["h"], l["h"]) * 0.9:
                    p = self.ocr.parse_progress(l["text"])
                    if p:
                        progress = p
                        break

            if progress and progress[1] > 0 and progress[0] >= progress[1]:
                if self._click_line("modal_area", cl, frame):
                    claims.append(cl["text"])
                    time.sleep(0.4)
            else:
                print(f"[QUESTS] Skipping Collect (quest incomplete or no progress read): "
                      f"'{cl['text']}' progress={progress}")

        time.sleep(0.4)
        self.close_modal_safe()
        if claims:
            print(f"[QUESTS] Claimed {len(claims)} completed quest(s).")
        else:
            print("[QUESTS] No completed quests to claim.")
        return claims

    def trigger_rebirth(self) -> bool:
        """
        Opens the rebirth modal, clicks the bottom-most 'Rebirth' button (the confirm),
        then verifies success by a large strength drop. Aborts safely if unsure.
        """
        self.handle_popups()
        before = self.read_strength()
        if not self.wm.ensure_focused():
            return False

        if not self.input.click_roi("rebirth_btn", self.wm):
            return False
        time.sleep(0.9)

        lines, frame = self._modal_lines("rebirth_modal")
        cands = [l for l in lines if "rebirth" in (l["text"] or "").lower()]
        if not cands:
            print("[REBIRTH] Rebirth modal/button not found - closing.")
            self.close_modal_safe()
            return False

        # The confirm button sits lower than the modal title; pick the bottom-most match.
        btn = max(cands, key=lambda l: l["y"] + l["h"])
        self._click_line("modal_area", btn, frame)
        time.sleep(1.8)

        after = self.read_strength()
        if before and after and after < before * 0.6:
            print(f"[REBIRTH] SUCCESS: strength {before:,} -> {after:,}")
            return True

        print(f"[REBIRTH] Not confirmed (before={before}, after={after}) - closing modal.")
        self.close_modal_safe()
        return False
