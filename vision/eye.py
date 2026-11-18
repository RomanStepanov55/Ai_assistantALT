"""
THE EYE - structured perception layer for a self-developing agent.

Why a new layer instead of a few more crops
-------------------------------------------
The brain can only be as good as its evidence. Every learning signal
(capability reward, playbook rewrite, UCB ranking) is derived from what the eye
reports, so an inaccurate or over-confident eye does not make the bot "less
smooth" - it teaches the wrong lesson and the agent confidently optimizes the
wrong thing.

Design rules
------------
1. NEVER GUESS. Every field is either measured (with a confidence) or null.
   A failed read is `None` + a warning, never a plausible substitute and never
   the previous value pretending to be fresh.
2. SELF-CALIBRATING. HUD geometry is found from the frame itself (badge colours,
   anchors), so a window resize / DPI change / another gym layout cannot blind
   it. The calibrated ROIs from config.py are only a fast hint.
3. INDEPENDENT SECOND OPINION. Each number is read twice with different
   binarization and accepted only when the readings agree; a disagreement lowers
   confidence instead of picking the prettier one.
4. TEMPORAL CONSENSUS. The badge counts up during training, so a single frame is
   not trustworthy. A value is accepted after it repeats (stable), otherwise the
   median of the burst is used and marked `stable=False`.
5. READ-ONLY. The eye never clicks, never types, never focuses windows for input.
   It only looks. Safety invariants stay in the input layer.

Everything it observes is one JSON-serializable dict (see `observe()`), so the
brain, the operator and offline tools all consume exactly the same evidence.
"""
import json
import re
import time
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

from config import DATA_DIR, DEBUG_DIR, ROIS
from vision.ocr_engine import CYRILLIC_MAP, OCREngine
from vision.screen_eye import ScreenEye
from vision.template_reader import (TemplateReader, binarize_gems,
                                    binarize_strength, is_badge_number,
                                    locate_badges)

LAYOUT_FILE = DATA_DIR / "eye_layout.json"

# A reading below this confidence is reported but flagged as unreliable; the
# learning layer must not treat it as evidence.
READABLE_CONF = 0.80
# Glyph slots that come back as '?' are never resolved by guessing.
UNKNOWN = "?"

# Colours used by the annotated overlay (BGR-ish hex, PIL wants RGB)
C_OK = "#00FF66"
C_WARN = "#FFB300"
C_BAD = "#FF2D2D"
C_INFO = "#00CCFF"
C_DANGER = "#FF00AA"


def _clamp_box(box, w: int, h: int) -> Tuple[int, int, int, int]:
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1 = max(0, min(x1, w - 1)), max(0, min(y1, h - 1))
    x2, y2 = max(x1 + 1, min(x2, w)), max(y1 + 1, min(y2, h))
    return x1, y1, x2, y2


def _roi_box(name: str, size: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    roi = ROIS.get(name)
    if not roi:
        return None
    w, h = size
    return _clamp_box((roi["x"] * w, roi["y"] * h,
                       (roi["x"] + roi["w"]) * w, (roi["y"] + roi["h"]) * h), w, h)


# ----------------------------------------------------------------------
# Dialog evidence
# ----------------------------------------------------------------------
# "Three OCR lines in the middle of the screen" is NOT a dialog. Measured on the
# saved corpus: that rule flagged 36 of 46 frames, including this agent's own
# editor window, and the policy treats modal_open as URGENT - so it clicked a
# blind "close" coordinate inside a live game, twice per attempt. A dialog must
# show a CONTROL word, i.e. the thing you answer it with. Banner/HUD/terminal
# text has none of them.
#
# "join" and "trade" were removed after a second measurement (2026-11-17): the
# in-world sign "Join the game community in our Groups to have +10% more cash"
# and the permanent TRADE HUD button both sit inside modal_area, so training
# frames were reported as "dialog open" (debug_frames/fix_set4.png). Text that
# is visible in every frame can never prove that a dialog is open.
_DIALOG_WORDS = ("yes", "no", "ok", "okay", "claim", "collect", "spin",
                 "travel", "close", "reject", "decline", "skip", "confirm",
                 "cancel", "buy", "sell", "equip")


def dialog_word(lines: List[Dict]) -> Optional[str]:
    """Returns the control word that proves a dialog is on screen, or None.

    Fuzzy on purpose: OCR mangles HUD letters ('CLAIM' -> 'СИАМ', 'YES' ->
    'УЕ5'), and the click guard normalises the same way. A near miss is only
    accepted for words long enough to be distinctive.
    """
    import difflib
    for line in lines:
        norm = (line.get("text") or "").upper().translate(CYRILLIC_MAP).lower()
        for tok in (t for t in re.split(r"[^a-z]+", norm) if t):
            if tok in _DIALOG_WORDS:
                return tok
            if len(tok) < 3:
                continue
            for w in _DIALOG_WORDS:
                if len(w) >= 4 and difflib.SequenceMatcher(None, tok, w).ratio() >= 0.78:
                    return f"{tok}~{w}"
    return None


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix = max(0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    ua = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / ua if ua > 0 else 0.0


class Eye:
    """One capture -> one structured, confidence-bearing scene."""

    def __init__(self, wm=None, ocr: Optional[OCREngine] = None,
                 reader: Optional[TemplateReader] = None,
                 screen_eye: Optional[ScreenEye] = None):
        if screen_eye is None:
            if wm is None:
                from core.window import WindowManager
                wm = WindowManager()
            screen_eye = ScreenEye(wm)
        self.eye = screen_eye
        self.wm = getattr(screen_eye, "wm", wm)
        self.ocr = ocr or OCREngine()
        self.reader = reader or TemplateReader()

        self.layout: Dict[str, Tuple[int, int, int, int]] = {}
        self._layout_size: Tuple[int, int] = (0, 0)
        self._cached_source: Optional[str] = None
        self._best_source: Dict[str, str] = {}
        self._prev_small: Optional[np.ndarray] = None
        self._locate_frame: Optional[Image.Image] = None
        self._locate_cache: List[Dict] = []
        self.last_scene: Optional[Dict] = None
        self.stats = {"captures": 0, "reads": 0, "failures": 0, "last_capture_ms": 0.0}
        self._last_good: Dict[str, Dict] = {}
        self._load_layout()

    # ==================================================================
    # capture
    # ==================================================================

    def capture(self) -> Optional[Image.Image]:
        t0 = time.time()
        try:
            frame = self.eye.capture_full_window()
        except Exception:
            frame = None
        self.stats["last_capture_ms"] = (time.time() - t0) * 1000.0
        self.stats["captures"] += 1
        if frame is None or frame.width < 320 or frame.height < 240:
            return None
        return frame

    def focused(self) -> bool:
        """True when the Roblox window is the foreground window.

        Reads still work when it is not focused (we grab the client area
        directly), but synthetic input will not reach the game, so the brain
        must know the difference.
        """
        try:
            import win32gui
            hwnd = getattr(self.wm, "hwnd", None)
            if not hwnd:
                hwnd = self.wm.find_roblox_window()
            return bool(hwnd) and win32gui.GetForegroundWindow() == hwnd
        except Exception:
            return False

    # ==================================================================
    # HUD layout (self-calibration)
    # ==================================================================

    def _load_layout(self):
        try:
            if LAYOUT_FILE.exists():
                data = json.loads(LAYOUT_FILE.read_text(encoding="utf-8"))
                self._layout_size = tuple(data.get("size", (0, 0)))
                for kind, box in data.get("boxes", {}).items():
                    self.layout[kind] = tuple(box)
                if self.layout:
                    # Only LOCATED geometry is persisted (see calibrate), so a
                    # layout on disk is proof the badges were found at this size.
                    self._cached_source = "auto"
        except Exception:
            pass

    def _save_layout(self, size):
        try:
            LAYOUT_FILE.write_text(json.dumps({
                "size": list(size),
                "boxes": {k: list(v) for k, v in self.layout.items()},
                "saved": time.time(),
            }, indent=2), encoding="utf-8")
        except Exception:
            pass

    def frame_is_game(self, frame: Image.Image) -> bool:
        """Cheap structural proof that this frame shows the game HUD.

        A screen grab of an OCCLUDED Roblox window returns whatever is on top -
        measured: a music player was grabbed and the old reader reported a
        strength value that had never existed. A badge plate found by colour in
        the top HUD band is that proof.

        A saved layout is deliberately NOT evidence. The Roblox app Home page
        and the game window have the same client size, so "this size was
        calibrated before" vouched for frames that contained no HUD at all
        (measured: 4 Home/loading frames were reported as game frames and then
        as "unreadable counters" - a wrong statement about what the eye saw).
        """
        try:
            return bool(self._locate(frame))
        except Exception:
            return False

    def _located_strength(self, frame: Image.Image) -> Optional[Tuple[int, int, int, int]]:
        """Largest plausible yellow strip in the HUD band = the strength badge."""
        w, h = frame.size
        cands = [b for b in self._locate(frame) if b["kind"] == "strength"]
        best, best_area = None, 0
        for b in cands:
            x1, y1, x2, y2 = b["box"]
            bw, bh = x2 - x1, y2 - y1
            if bh <= 0 or bw / bh < 1.6 or bw / bh > 12:
                continue                      # not a badge strip
            if y1 > 0.14 * h:
                continue                      # not the top HUD row
            if bw * bh > best_area:
                best, best_area = b["box"], bw * bh
        if best is None:
            return None
        return _clamp_box(best, w, h)

    def _locate(self, frame: Image.Image) -> List[Dict]:
        """Badge auto-location, memoised per frame.

        locate_badges() scans the whole HUD band; the eye asks for it several
        times (calibration, strength candidates, gems candidates). Caching the
        frame object itself (not its id) guarantees the cache can never be
        confused by Python reusing an id for a different frame.
        """
        if self._locate_frame is frame:
            return self._locate_cache
        self._locate_frame = frame
        w, h = frame.size
        raw = locate_badges(frame)
        # A badge plate is a LARGE plate: on a 1920-wide client it is ~270-320 px
        # wide and ~90 px tall (relw ~0.16). Colour alone also lights up small
        # bright UI elements of OTHER windows - measured: a 72x28 px green button
        # of an editor was reported as a "gems" badge, which then vouched for a
        # frame that contained no game at all. Size is the honest discriminator.
        self._locate_cache = [
            b for b in raw
            if (b["box"][2] - b["box"][0]) >= 0.06 * w
            and (b["box"][3] - b["box"][1]) >= 0.03 * h
        ]
        return self._locate_cache

    def _gems_anchored(self, strength_box, frame: Image.Image) -> Tuple[int, int, int, int]:
        """The gems badge always sits immediately RIGHT of the strength badge,
        same row, ~same width. Anchoring to the found badge is layout-proof and
        does not depend on the badge's colour (blue on blue sky is unsegmentable)."""
        w, h = frame.size
        x1, y1, x2, y2 = strength_box
        bw, bh = x2 - x1, y2 - y1
        gx1 = x2 + int(bw * 0.06)
        # The gems badge is slightly WIDER than the strength badge (its icon
        # sits on the other side), and a 1.05 factor clipped the suffix
        # ("14.0M" -> "14.0" - a valid-looking number that is 1000x wrong).
        gx2 = gx1 + int(bw * 1.10)
        return _clamp_box((gx1, y1 - int(bh * 0.08), gx2, y2 + int(bh * 0.08)), w, h)

    def calibrate(self, frame: Image.Image, force: bool = False) -> Dict:
        """Finds the HUD geometry from the frame itself, verifies it by reading.

        Returns {"source": "auto"|"roi"|"none", "cached": bool, "strength": box,
        "gems": box}. `auto` means the badges were LOCATED in this frame (proof
        that the frame is the game); `roi` means the fixed config boxes were
        used because nothing was located - honest, but not proof of anything.
        """
        w, h = frame.size
        if (not force and self.layout and self._layout_size == (w, h)
                and self._cached_source):
            return {"source": self._cached_source, "cached": True,
                    **{k: self.layout.get(k) for k in ("strength", "gems")}}

        strength = self._located_strength(frame)
        source = "auto"
        if strength is None:
            strength = _roi_box("strength_counter", (w, h))
            source = "roi"
        if strength is None:
            return {"source": "none", "cached": False, "strength": None, "gems": None}

        gems = self._gems_anchored(strength, frame)
        self.layout = {"strength": strength, "gems": gems}
        self._layout_size = (w, h)
        self._cached_source = source
        self._best_source = {"strength": "layout", "gems": "layout"}
        # Only a LOCATED geometry is worth persisting: saving an ROI fallback
        # would let one odd-sized frame (a scan, a resized window) overwrite the
        # real calibration with guessed boxes.
        if source == "auto":
            self._save_layout((w, h))
        return {"source": source, "cached": False, "strength": strength, "gems": gems}

    # ==================================================================
    # number reading
    # ==================================================================

    @staticmethod
    def _inks(kind: str, crop: Image.Image):
        """Independent binarizations of the same badge crop, lazily.

        A generator on purpose: binarizing the blue gems badge costs ~120 ms,
        so masks that would only repeat a good reading must never be computed.
        The template reader decides from shapes; when two different masks yield
        the same text, the reading is corroborated by an independent path.
        """
        if kind == "strength":
            # Otsu first: it follows the actual brightness, so a dimmed scene
            # (a modal is open) is still readable. The fixed cut is the second,
            # independent opinion.
            yield "ink_otsu", binarize_strength(crop)
            yield "ink110", binarize_strength(crop, thr=110)
        else:
            yield "gems_auto", binarize_gems(crop)
            yield "gems_dark", binarize_gems(crop, variant="dark")
            yield "gems_orange", binarize_gems(crop, variant="orange")

    def _candidate_boxes(self, kind: str, frame: Image.Image) -> List[Tuple[str, tuple]]:
        w, h = frame.size
        boxes: List[Tuple[str, tuple]] = []

        def add(source, box):
            if not box:
                return
            box = _clamp_box(box, w, h)
            if all(_iou(box, b) < 0.9 for _, b in boxes):
                boxes.append((source, box))

        # Geometry is only valid for the frame size it was calibrated on. A
        # scan walks frames of different sizes; reusing a 1920x1046 box on a
        # 1280x697 frame would measure the wrong pixels and still "read" a
        # number - the worst failure mode this layer exists to prevent.
        layout_ok = self._layout_size == (w, h)
        preferred = self._best_source.get(kind) if layout_ok else None
        if preferred and self.layout.get(kind):
            add(preferred, self.layout[kind])

        if kind == "strength":
            if layout_ok:
                add("layout", self.layout.get("strength"))
            add("roi", _roi_box("strength_counter", (w, h)))
            add("located", self._located_strength(frame))
        else:
            sbox = (self.layout.get("strength") if layout_ok else None) \
                or self._located_strength(frame)
            if sbox:
                add("anchored", self._gems_anchored(sbox, frame))
            if layout_ok:
                add("layout", self.layout.get("gems"))
            add("roi", _roi_box("gems_counter", (w, h)))
            cands = [b for b in self._locate(frame)
                     if b["kind"] in ("gems", "gems_blue")]
            cands.sort(key=lambda b: -b["area"])
            for b in cands[:2]:
                add("located", b["box"])
        return boxes

    def read_once(self, kind: str, frame: Image.Image, rescue: bool = True) -> Dict:
        """One frame, all candidate crops/masks -> best validated reading.

        Every attempt is recorded in `attempts`, so a failure can be diagnosed
        offline ("the badge is at x=976 but every mask returned '?'").

        `rescue=False` skips the OCR second opinion. Callers use it when the
        frame has already failed the "is this the game?" geometry check: OCR is
        a ~1-5 s cold-start path, and spending it on a frame that provably has
        no HUD would stall the bot for nothing.
        """
        w, h = frame.size
        attempts = []
        best = None
        for source, box in self._candidate_boxes(kind, frame):
            crop = frame.crop(box)
            for mask_name, ink in self._inks(kind, crop):
                if int(ink.sum()) < 200:
                    attempts.append({"source": source, "mask": mask_name,
                                     "text": None, "conf": 0.0, "why": "no ink"})
                    continue
                try:
                    text, conf = self.reader.read(ink)
                except Exception as exc:
                    attempts.append({"source": source, "mask": mask_name,
                                     "text": None, "conf": 0.0, "why": str(exc)})
                    continue
                valid = bool(text) and UNKNOWN not in text and is_badge_number(text) is not None
                attempts.append({"source": source, "mask": mask_name,
                                 "text": text, "conf": round(float(conf), 3),
                                 "valid": valid})
                if not valid:
                    continue
                value = OCREngine.parse_stat_string(text)
                if value is None:
                    continue
                cand = {"kind": kind, "value": int(value), "text": text,
                        "confidence": float(conf), "source": f"{source}/{mask_name}",
                        "box": list(box), "attempts": attempts}
                if best is None or cand["confidence"] > best["confidence"]:
                    best = cand
                if best["confidence"] >= 0.93:
                    break                   # confident: skip remaining masks
            if best and best["confidence"] >= 0.93:
                break                       # good enough, do not spend more time

        if best is not None:
            self.stats["reads"] += 1
            return best

        self.stats["failures"] += 1
        if not rescue:
            return {"kind": kind, "value": None, "text": None, "confidence": 0.0,
                    "source": "none", "box": None, "attempts": attempts}
        # OCR rescue is a read of the BADGE, so it requires a badge in this
        # frame. Without this gate the rescue happily OCRs whatever text is on
        # screen: measured on an editor window it returned "00" as strength = 0
        # - the exact "plausible number nobody measured" failure.
        if not self._locate(frame):
            return {"kind": kind, "value": None, "text": None, "confidence": 0.0,
                    "source": "none", "box": None, "attempts": attempts}
        # Independent second opinion from OCR (only when the fast path failed):
        # it can rescue a badge whose glyph style is not in the store yet, and
        # its label is what teaches the store (additive, anchor-verified).
        ocr_rescue = self._ocr_rescue(kind, frame, attempts)
        if ocr_rescue is not None:
            return ocr_rescue
        return {"kind": kind, "value": None, "text": None, "confidence": 0.0,
                "source": "none", "box": None, "attempts": attempts}

    def _ocr_rescue(self, kind: str, frame: Image.Image, attempts: List[Dict]) -> Optional[Dict]:
        """Slow path: clean-OCR the badge, then TEACH the reader from it.

        The rendered ink mask is black-on-white, which WinRT OCR reads well
        ("14.0M", "2.76M"). When the label agrees with the template on all
        anchors, the '?' slots are learned. Returns a reading only when the OCR
        label is itself a valid badge number - it never overrides a measured
        template reading with a guess.
        """
        best_fail = None
        for a in attempts:
            if a.get("text") and a.get("conf", 0) > (best_fail or {}).get("conf", 0):
                best_fail = a
        for source, box in self._candidate_boxes(kind, frame)[:2]:
            crop = frame.crop(box)
            inks = list(islice(self._inks(kind, crop), 2))
            for mask_name, ink in inks:
                if int(ink.sum()) < 200:
                    continue
                label = self.ocr.read_badge_number(crop, ink, f"eye_{kind}")
                if not label:
                    continue
                template_text = best_fail.get("text") if best_fail and best_fail.get("source") == source else None
                learned = 0
                if template_text:
                    learned = self.reader.learn_partial(ink, template_text, label)
                value = OCREngine.parse_stat_string(label)
                if value is None:
                    continue
                return {"kind": kind, "value": int(value), "text": label,
                        "confidence": 0.70,      # OCR-only: valid, not as sure as a shape match
                        "source": f"ocr/{source}", "box": list(box),
                        "learned_glyphs": learned, "attempts": attempts}
        return None

    def read_counter(self, kind: str, timeout: float = 1.4,
                     need_stable: int = 2, interval: float = 0.07,
                     frame: Optional[Image.Image] = None,
                     temporal: bool = True, rescue: bool = True) -> Dict:
        """Reads a HUD counter until it is STABLE (temporal consensus).

        During training the badge animates (counts up), so one frame can catch a
        half-drawn number. `need_stable` identical consecutive readings in a
        burst are required; on timeout the median of the burst is returned and
        marked `stable=False` - the caller can then decide whether to trust it.
        """
        samples: List[Dict] = []
        last_value, streak = None, 0
        t0 = time.time()
        # A supplied frame is a SAVED moment: it must be read exactly once and
        # never be replaced by a fresh capture. Doing otherwise mixed two
        # different moments (and even two different window sizes) inside a
        # single scene, so a 1280x697 frame reported the 1920x1046 frame's
        # numbers - a fabricated measurement.
        fixed_frame = frame
        while True:
            fr = fixed_frame if fixed_frame is not None else self.capture()
            if fr is not None:
                if fixed_frame is None and self._layout_size \
                        and fr.size != self._layout_size:
                    self.calibrate(fr)      # window resized during the burst
                reading = self.read_once(kind, fr, rescue=rescue)
                if reading["value"] is not None:
                    samples.append(reading)
                    if reading["value"] == last_value:
                        streak += 1
                    else:
                        last_value, streak = reading["value"], 1
                    if not temporal or streak >= need_stable:
                        reading["stable"] = True
                        reading["samples"] = len(samples)
                        return reading
            if fixed_frame is not None or time.time() - t0 >= timeout:
                break                       # a saved frame gets exactly one look
            time.sleep(interval)

        if not samples:
            return {"kind": kind, "value": None, "text": None, "confidence": 0.0,
                    "source": "none", "box": None, "stable": False, "samples": 0}

        # No stable pair: take the most frequent value, then the highest value
        # among ties (a count-up animation only goes UP, so the max is the most
        # settled number we have seen).
        values = [s["value"] for s in samples]
        mode = max(set(values), key=lambda v: (values.count(v), v))
        agree = values.count(mode) / len(values)
        chosen = next(s for s in reversed(samples) if s["value"] == mode)
        chosen = dict(chosen)
        chosen["confidence"] = round(chosen["confidence"] * (0.85 + 0.15 * agree), 3)
        chosen["stable"] = False
        chosen["samples"] = len(samples)
        chosen["disagreement"] = round(1.0 - agree, 3)
        return chosen

    # ==================================================================
    # scene
    # ==================================================================

    @staticmethod
    def _motion(prev: Optional[np.ndarray], frame: Image.Image) -> Optional[float]:
        try:
            small = np.asarray(frame.convert("L").resize((64, 64)), dtype=np.int16)
        except Exception:
            return None
        if prev is None or prev.shape != small.shape:
            return None
        return float((np.abs(small - prev) > 15).mean())

    def _ui_flags(self, frame: Image.Image, deep: bool) -> Dict:
        w, h = frame.size
        flags: Dict = {"modal_open": None, "modal_text": "", "event_text": "",
                       "gift_ready": None, "e_prompt": None}
        # Cheap, OCR-free: the interact keycap is a small bright block near the
        # character. Low confidence by design - it is a hint, not a measurement.
        box = _roi_box("e_prompt", (w, h))
        if box:
            crop = frame.crop(box).convert("L")
            arr = np.asarray(crop)
            ratio = float((arr > 235).mean())
            flags["e_prompt"] = {"present": ratio > 0.02,
                                 "white_ratio": round(ratio, 3),
                                 "confidence": 0.45}
        if not deep:
            return flags

        frame_size = frame.size
        ev = self.eye.get_roi_crop("event_banner", frame)
        if ev is not None:
            flags["event_text"] = self.ocr.read_text_from_image(ev, "eye_event")
        gift = self.eye.get_roi_crop("timed_gift_btn", frame)
        if gift is not None:
            lines = self.ocr.read_lines(gift, "eye_gift")
            flags["gift_ready"] = any(self.ocr.looks_like_claim(l.get("text")) for l in lines)
        modal = self.eye.get_roi_crop("modal_area", frame)
        if modal is not None:
            lines = self.ocr.read_lines(modal, "eye_modal")
            text = " ".join(l.get("text", "") for l in lines).strip()
            word = dialog_word(lines)
            flags["modal_text"] = text[:400]
            flags["modal_lines"] = len(lines)
            flags["modal_word"] = word
            # A dialog is claimed only with a control word: see _DIALOG_WORDS.
            # And only for THIS game: an editor frame whose log contains 'close'
            # was reported as an open dialog, so the badge gate is mandatory.
            on_hud = bool(self._locate(frame))
            flags["modal_open"] = bool(on_hud and len(lines) >= 3
                                       and len(text) >= 12 and word)
            flags["modal_confidence"] = 0.75 if flags["modal_open"] else 0.0
            flags["modal_area"] = list(_roi_box("modal_area", frame_size) or [])
        return flags

    def _danger_zones(self, frame_size) -> Dict[str, List[int]]:
        """Advisory no-click zones (the actual block is in the input layer).

        The eye exposes them so a planner can check its target BEFORE clicking
        and so an offline reviewer can see that the bot knows what to avoid.
        """
        out = {}
        for name in ("autolift_btn", "store_btn", "trade_btn", "codes_btn"):
            box = _roi_box(name, frame_size)
            if box:
                out[name] = list(box)
        return out

    def glyph_health(self) -> Dict:
        audit = None
        try:
            p = DATA_DIR / "glyph_audit.json"
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                poisoned = [f"{c}#{r['index']}" for c, v in data.get("report", {}).items()
                            for r in v.get("rows", []) if r.get("poisoned")]
                audit = {"poisoned": poisoned,
                         "evidence": data.get("stats", {}).get("agreed")}
        except Exception:
            audit = None
        return {"chars": sorted(self.reader.templates.keys()),
                "missing": self.reader.missing_chars(),
                "audit": audit}

    def observe(self, deep: bool = False, temporal: bool = True,
                frame: Optional[Image.Image] = None) -> Dict:
        """One structured scene. This is the eye's only public product."""
        t0 = time.time()
        fr = frame if frame is not None else self.capture()
        if fr is None:
            return {"ok": False, "ts": time.time(),
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "warnings": ["no frame: Roblox window not found"],
                    "hud": {}, "ui": {}, "quality": {"readable": False, "confidence": 0.0}}

        w, h = fr.size
        calib = self.calibrate(fr)
        # Only badges FOUND in this frame are proof. A cached layout for the same
        # window size is not: the Roblox app Home page has the same client size
        # as the game and carries no HUD at all.
        located = [b for b in self._locate(fr)
                   if b.get("kind") in ("strength", "gems")]
        # Only spend the slow OCR rescue when badges were actually found here.
        # Otherwise a covered or non-game window costs seconds for nothing.
        rescue = bool(located)
        strength = self.read_counter("strength", temporal=temporal, frame=fr,
                                     rescue=rescue)
        # second sample is cheap: the frame is already here for offline use
        gems = self.read_counter("gems", temporal=temporal, frame=fr,
                                 rescue=rescue)

        # Is this frame actually the game? A screen grab of an OCCLUDED Roblox
        # window returns whatever is on top (measured: an editor window was
        # captured and the eye happily reported strength 0 from its pixels).
        # Two independent vouchers, both produced by THIS frame: the badge
        # geometry was found here, or both badges read confidently and look
        # like real badges.
        measured = [r for r in (strength, gems)
                    if r.get("value") is not None and r.get("confidence", 0) >= 0.85]
        game_visible = bool(located) or len(measured) == 2

        for kind, reading in (("strength", strength), ("gems", gems)):
            if reading["value"] is not None:
                self._last_good[kind] = {**reading, "ts": time.time()}

        # Offline frames are independent moments (a scan of saved PNGs), so a
        # value cached from ANOTHER frame must never be presented as this
        # frame's reading - that is the exact "plausible number nobody measured"
        # failure the eye exists to prevent. Stale history is a live-only
        # convenience, and never on a frame that is not the game.
        allow_stale = frame is None and game_visible

        hud = {}
        warnings = []
        if not game_visible:
            warnings.append("HUD not found: this frame does not show the game "
                            "badges (Roblox occluded/minimized, this is the "
                            "Roblox app/Home or a loading screen, or the window "
                            "was resized and the HUD must be re-calibrated)")
        for kind, reading in (("strength", strength), ("gems", gems)):
            entry = {
                "value": reading.get("value"),
                "text": reading.get("text"),
                "confidence": round(float(reading.get("confidence", 0.0)), 3),
                "source": reading.get("source"),
                "stable": reading.get("stable", False),
                "stale": False,
                "age": 0.0,
                "box": reading.get("box"),
            }
            if entry["value"] is None:
                last = self._last_good.get(kind) if allow_stale else None
                if last:
                    entry.update(value=last["value"], text=last["text"],
                                 confidence=0.0, source="last_good",
                                 stale=True, age=round(time.time() - last["ts"], 1),
                                 box=last.get("box"))
                    warnings.append(f"{kind}: unreadable, showing last value "
                                    f"({last['text']}, {entry['age']}s old)")
                else:
                    warnings.append(f"{kind}: unreadable (no history)")
            elif not entry["stable"]:
                warnings.append(f"{kind}: read {entry['text']} without a stable "
                                f"repeat")
            elif entry["confidence"] < READABLE_CONF:
                warnings.append(f"{kind}: low confidence {entry['confidence']}")
            hud[kind] = entry

        motion = self._motion(self._prev_small, fr)
        try:
            self._prev_small = np.asarray(fr.convert("L").resize((64, 64)), dtype=np.int16)
        except Exception:
            pass

        luma = 0.0
        try:
            luma = float(np.asarray(fr.convert("L").resize((64, 64))).mean())
        except Exception:
            pass

        fresh = [r for r in (strength, gems) if r.get("value") is not None and r.get("stable")]
        conf = (sum(r["confidence"] for r in fresh) / len(fresh)) if fresh else 0.0
        scene = {
            "ok": True,
            "ts": time.time(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "frame": {"w": w, "h": h,
                      "capture_ms": round(self.stats["last_capture_ms"], 1),
                      "mean_luma": round(luma, 1),
                      "game_visible": game_visible,
                      "focused": self.focused()},
            "layout": {"source": calib.get("source"),
                       "strength_box": list(calib["strength"]) if calib.get("strength") else None,
                       "gems_box": list(calib["gems"]) if calib.get("gems") else None},
            "hud": hud,
            "ui": self._ui_flags(fr, deep),
            "danger": self._danger_zones((w, h)),
            "motion": None if motion is None else round(motion, 4),
            "quality": {"readable": bool(game_visible) and
                                    all(hud[k]["value"] is not None and
                                        not hud[k]["stale"] for k in ("strength", "gems")),
                        "game_visible": game_visible,
                        "fresh_fields": len(fresh),
                        "confidence": round(conf, 3),
                        "warnings": warnings},
            "eye": {"glyphs": self.glyph_health(),
                    "stats": dict(self.stats),
                    "read_ms": round((time.time() - t0) * 1000.0, 1)},
        }
        self.last_scene = scene
        return scene

    # ==================================================================
    # annotated view (the "normal eye for offline viewing")
    # ==================================================================

    def annotate(self, scene: Optional[Dict] = None,
                 frame: Optional[Image.Image] = None) -> Optional[Image.Image]:
        scene = scene or self.last_scene
        fr = frame if frame is not None else self.capture()
        if fr is None:
            return None
        img = fr.convert("RGB").copy()
        draw = ImageDraw.Draw(img)
        w, h = img.size

        def label(box, text, color):
            x1, y1, x2, y2 = box
            draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
            tw = draw.textlength(text)
            ty = max(0, y1 - 16)
            draw.rectangle([x1, ty, x1 + tw + 6, ty + 14], fill="#000000")
            draw.text((x1 + 3, ty + 1), text, fill=color)

        hud = (scene or {}).get("hud", {})
        for kind, reading in hud.items():
            box = reading.get("box")
            if not box:
                continue
            if reading.get("value") is None:
                color, text = C_BAD, f"{kind}: UNREADABLE"
            elif reading.get("stale"):
                color, text = C_WARN, f"{kind}: {reading.get('text')} STALE"
            else:
                color = C_OK if reading.get("confidence", 0) >= READABLE_CONF else C_WARN
                text = (f"{kind}: {reading.get('text')} "
                        f"({reading.get('confidence', 0):.2f})")
            label(box, text, color)

        for name, box in ((scene or {}).get("danger") or {}).items():
            x1, y1, x2, y2 = box
            draw.rectangle([x1, y1, x2, y2], outline=C_DANGER, width=2)
            draw.text((x1 + 2, y2 - 13), f"NO-CLICK {name}", fill=C_DANGER)

        quality = (scene or {}).get("quality", {})
        frame_info = (scene or {}).get("frame", {})
        head = (f"EYE {scene.get('time', '') if scene else ''}  "
                f"{w}x{h}  capture={frame_info.get('capture_ms', '?')}ms  "
                f"focused={frame_info.get('focused')}  "
                f"confidence={quality.get('confidence', 0)}")
        draw.rectangle([0, 0, w, 22], fill="#101010")
        draw.text((8, 4), head, fill="#EEEEEE")

        # A frame where the HUD was never found must LOOK wrong at a glance:
        # otherwise an operator (or the agent) reads the annotation as a valid
        # measurement of the game.
        if not quality.get("game_visible", True):
            draw.rectangle([0, 22, w, 48], fill="#400000")
            draw.text((8, 27), "NO GAME HUD IN THIS FRAME - values below are NOT "
                               "measurements of the game", fill=C_BAD)

        warn = quality.get("warnings") or []
        if warn:
            yy = h - 20 * len(warn) - 6
            draw.rectangle([0, yy - 2, w, h], fill="#180000")
            for i, line in enumerate(warn):
                draw.text((8, yy + 18 * i), "! " + line, fill=C_WARN)

        glyphs = ((scene or {}).get("eye", {}) or {}).get("glyphs", {})
        missing = glyphs.get("missing") or []
        if missing:
            draw.text((8, 52 if not quality.get("game_visible", True) else 26),
                      f"missing glyphs: {missing}", fill=C_INFO)
        return img

    def save_annotated(self, scene: Optional[Dict] = None,
                       name: Optional[str] = None,
                       frame: Optional[Image.Image] = None) -> Optional[Path]:
        img = self.annotate(scene, frame)
        if img is None:
            return None
        name = name or f"eye_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        path = DEBUG_DIR / name
        img.save(path)
        return path

    # ==================================================================
    # self-check
    # ==================================================================

    def self_check(self) -> Dict:
        """Honest health report: what the eye can and cannot see right now."""
        frame = self.capture()
        if frame is None:
            return {"verdict": "blind", "reason": "no frame from the Roblox window",
                    "glyphs": self.glyph_health()}
        calib = self.calibrate(frame, force=True)
        # A health report is a claim about THIS window. If the frame does not
        # show the game badges, reading must not happen at all: an OCR pass over
        # whatever is on screen returned "00" -> strength 0 from an editor
        # window, which is worse than "blind" because it looks like data.
        if not self._locate(frame):
            return {"verdict": "blind",
                    "reason": "this frame is not the game HUD (badges not found)",
                    "layout": {"source": calib.get("source"),
                               "strength_box": calib.get("strength"),
                               "gems_box": calib.get("gems")},
                    "checks": {k: {"text": None, "value": None,
                                   "confidence": 0.0, "source": "none"}
                               for k in ("strength", "gems")},
                    "glyphs": self.glyph_health(),
                    "capture_ms": round(self.stats["last_capture_ms"], 1)}
        strength = self.read_once("strength", frame)
        gems = self.read_once("gems", frame)
        checks = {
            "strength": {"text": strength["text"], "value": strength["value"],
                         "confidence": round(strength["confidence"], 3),
                         "source": strength["source"]},
            "gems": {"text": gems["text"], "value": gems["value"],
                     "confidence": round(gems["confidence"], 3),
                     "source": gems["source"]},
        }
        ok_n = sum(1 for c in checks.values() if c["value"] is not None
                   and c["confidence"] >= READABLE_CONF)
        verdict = "ok" if ok_n == 2 else ("degraded" if ok_n == 1 else "blind")
        return {"verdict": verdict, "layout": {"source": calib.get("source"),
                                               "strength_box": calib.get("strength"),
                                               "gems_box": calib.get("gems")},
                "checks": checks, "glyphs": self.glyph_health(),
                "capture_ms": round(self.stats["last_capture_ms"], 1)}
