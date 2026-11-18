"""
HUD & Game Elements Detector for Muscle Legends
Extracts numeric values from the game screen and monitors UI state.

Strength badge: WinRT OCR works (dark digits on yellow) and ALSO teaches the
template reader (auto-learn with a strict normalization guard).
Gems/candy badge: WinRT OCR fails on the stylized orange digits -> read by the
template reader (same font as the strength badge).
"""
from typing import Dict, Any, Optional
from PIL import Image

from vision.screen_eye import ScreenEye
from vision.ocr_engine import OCREngine
from vision.template_reader import (TemplateReader, binarize_strength,
                                   binarize_gems, is_badge_number)


class GameDetector:
    def __init__(self, eye: ScreenEye, ocr: OCREngine):
        self.eye = eye
        self.ocr = ocr
        self.reader = TemplateReader()
        self.last_known_strength = 0
        self.last_known_gems = 0

    def extract_full_hud_state(self, frame: Optional[Image.Image] = None) -> Dict[str, Any]:
        if frame is None:
            frame = self.eye.capture_full_window()

        if frame is None:
            return {
                "valid": False,
                "strength": self.last_known_strength,
                "gems": self.last_known_gems,
                "rebirths": 0,
                "gift_ready": False,
            }

        # 1. Strength (top center-left yellow badge): template reader primary,
        #    WinRT OCR fallback + strict auto-learn (mismatches allowed ONLY at '?')
        strength_crop = self.eye.get_roi_crop("strength_counter", frame)
        raw_str = self.ocr.read_text_from_image(strength_crop, "strength") if strength_crop else ""
        norm_str = self.ocr.normalize_ocr_number(raw_str)
        parsed_str = self.ocr.parse_stat_string(norm_str) if norm_str else None

        tpl_str_val = None
        if strength_crop:
            try:
                ink = binarize_strength(strength_crop)
                if int(ink.sum()) >= 200:  # badge must actually be on screen
                    t_text, t_conf = self.reader.read(ink)
                    if t_conf >= 0.7 and is_badge_number(t_text) is not None:
                        tpl_str_val = self.ocr.parse_stat_string(t_text)
                    # auto-learn: OCR label must agree with the template read
                    # character-by-character; differences only where template has '?'
                    label = self.ocr.normalize_ocr_number(raw_str)
                    if label and t_text and t_conf >= 0.6:
                        self.reader.learn_partial(ink, t_text, label)
            except Exception:
                pass

        for cand in (tpl_str_val, parsed_str):
            if cand and cand > 0:
                last = self.last_known_strength
                # sanity gate: badge OCR during animations can produce garbage;
                # accept only plausible moves (x0.2 .. x5), else keep last known
                if last == 0 or cand <= last or cand <= last * 5:
                    # decreases are always accepted (rebirth/reset/spend),
                    # increases only up to x5 (count-up noise guard)
                    self.last_known_strength = cand
                break

        # 2. Candy/Gems (second top badge, green or blue variant): template reader primary
        gems_crop = self.eye.get_roi_crop("gems_counter", frame)
        raw_gems = self.ocr.read_text_from_image(gems_crop, "gems") if gems_crop else ""
        norm_gems = self.ocr.normalize_ocr_number(raw_gems)
        parsed_gems = self.ocr.parse_stat_string(norm_gems) if norm_gems else None

        tpl_gems_val = None
        if gems_crop:
            try:
                ink = binarize_gems(gems_crop)
                if int(ink.sum()) >= 200:  # badge must actually be on screen
                    t_text, t_conf = self.reader.read(ink)
                    if t_conf >= 0.7 and is_badge_number(t_text) is not None:
                        tpl_gems_val = self.ocr.parse_stat_string(t_text)
            except Exception:
                pass

        for cand in (tpl_gems_val, parsed_gems):
            if cand and cand > 0:
                last = self.last_known_gems
                if last == 0 or cand <= last or cand <= last * 5:
                    self.last_known_gems = cand
                break

        # 3. Timed Gift Ready Check ("CLAIM" vs countdown timer)
        gift_crop = self.eye.get_roi_crop("timed_gift_btn", frame)
        raw_gift = self.ocr.read_text_from_image(gift_crop, "gift") if gift_crop else ""
        gift_ready = self.ocr.looks_like_claim(raw_gift)

        return {
            "valid": True,
            "strength": self.last_known_strength,
            "gems": self.last_known_gems,
            "rebirths": 0,
            "gift_ready": gift_ready,
            "raw_str_text": raw_str,
            "raw_gems_text": raw_gems,
            "known_glyphs": self.reader.known_chars(),
        }
