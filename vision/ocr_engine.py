"""
OCR & Metric Extractor for Muscle Legends.

Speed strategy:
  - A PERSISTENT WinRT OCR server process (vision/ocr_server.ps1) answers
    requests over stdin/stdout (~0.1-0.3s per read instead of 2-6s spawns).
  - A reader thread + queue protects against hangs; if the server dies or
    stalls, read_lines transparently falls back to one-shot PowerShell calls.
Preprocessing: 3x upscale + grayscale + contrast for text regions.
Returns both plain text AND line bounding boxes (for OCR-guided clicking).
"""
import re
import json
import queue
import time
import uuid
import subprocess
import threading
from pathlib import Path
from typing import Optional, List, Dict
from PIL import Image, ImageEnhance

from config import DEBUG_DIR, BASE_DIR

CROPS_DIR = DEBUG_DIR / "crops"
CROPS_DIR.mkdir(parents=True, exist_ok=True)
WIN_OCR_SCRIPT = BASE_DIR / "vision" / "win_ocr.ps1"
OCR_SERVER_SCRIPT = BASE_DIR / "vision" / "ocr_server.ps1"

SUFFIX_MULT = {
    "": 1,
    "K": 1_000,
    "M": 1_000_000,
    "B": 1_000_000_000,
    "T": 1_000_000_000_000,
    "Q": 1_000_000_000_000_000,
}

# Cyrillic homoglyphs produced by WinRT OCR with a Russian language profile
CYRILLIC_MAP = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M",
    "О": "O", "Р": "P", "Т": "T", "Х": "X", "У": "Y",
    "а": "a", "в": "b", "с": "c", "е": "e", "н": "h", "к": "k", "м": "m",
    "о": "o", "р": "p", "т": "t", "х": "x", "у": "y",
    "ё": "e",
})


class _OcrServer:
    """Persistent PowerShell OCR process with a reader thread and timeouts."""

    def __init__(self):
        self.proc = None
        self.q = None
        self.lock = threading.Lock()
        self.dead_until = 0.0   # auto-revive: server retried after 60s
        self._first_request = True

    def _start(self):
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(OCR_SERVER_SCRIPT.resolve()), "-Scale", "3"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace",
        )
        self.q = queue.Queue()
        t = threading.Thread(target=self._reader, daemon=True)
        t.start()

    def _reader(self):
        try:
            for line in self.proc.stdout:
                self.q.put(line)
        except Exception:
            pass
        self.q.put(None)  # EOF marker

    def request(self, image_path: str, timeout: float = 8.0) -> Optional[Dict]:
        """Sends one path, returns parsed JSON dict or None (server dead/slow)."""
        with self.lock:
            if time.time() < self.dead_until:
                return None
            try:
                if self.proc is None or self.proc.poll() is not None:
                    self._start()
                self.proc.stdin.write(image_path + "\n")
                self.proc.stdin.flush()
                item = self.q.get(timeout=timeout)
            except Exception:
                # hang or broken pipe: retry the server after 60s
                self.dead_until = time.time() + 60.0
                try:
                    self.proc.kill()
                except Exception:
                    pass
                return None
            if item is None:
                self.dead_until = time.time() + 60.0
                return None
            try:
                return json.loads(item.strip())
            except Exception:
                return None

    def reset(self):
        with self.lock:
            self.dead = False
            try:
                if self.proc:
                    self.proc.kill()
            except Exception:
                pass
            self.proc = None


class OCREngine:
    def __init__(self):
        self.script_path = str(WIN_OCR_SCRIPT.resolve())
        self.server = _OcrServer()

    @staticmethod
    def parse_stat_string(raw_text: str) -> Optional[int]:
        if not raw_text:
            return None
        cleaned = raw_text.strip().replace(",", ".").replace(" ", "").upper()
        cleaned = cleaned.translate(CYRILLIC_MAP)
        match = re.search(r"([\d\.]+)\s*([KMBTQ]?)", cleaned)
        if not match:
            return None
        num_part = match.group(1)
        suffix = match.group(2)
        try:
            val = float(num_part)
            return int(val * SUFFIX_MULT.get(suffix, 1))
        except ValueError:
            return None

    @staticmethod
    def parse_progress(raw_text: str):
        """Parses quest progress like '21/5K' or '5K/5K' -> (done, required) or None."""
        if not raw_text:
            return None
        m = re.search(
            r"([\d\.]+)\s*([KMBT]?)\s*/\s*([\d\.]+)\s*([KMBT]?)",
            raw_text,
            re.IGNORECASE,
        )
        if not m:
            return None

        def val(num: str, suf: str) -> float:
            return float(num) * SUFFIX_MULT.get(suf.upper(), 1)

        try:
            return (val(m.group(1), m.group(2)), val(m.group(3), m.group(4)))
        except ValueError:
            return None

    def read_lines(self, image: Image.Image, tag: str = "crop") -> List[Dict]:
        """
        Preprocesses (3x upscale, grayscale, contrast x3) and runs OCR.
        Returns a list of {"text", "x", "y", "w", "h"} in ORIGINAL crop pixels.
        Uses the persistent server; falls back to one-shot PowerShell.
        Empty list if nothing readable.
        """
        if image is None:
            return []
        w, h = image.size
        if w < 10 or h < 10:
            return []

        im_large = image.resize((w * 3, h * 3), Image.Resampling.LANCZOS)
        gray = im_large.convert("L")
        contrasted = ImageEnhance.Contrast(gray).enhance(3.0)

        # unique name per call: two threads/processes must never collide
        prep_path = CROPS_DIR / f"{tag}_{uuid.uuid4().hex[:6]}_prep.png"
        contrasted.save(prep_path)

        # 1) persistent server (fast path)
        data = self.server.request(str(prep_path.resolve()))
        if data is not None:
            lines = data.get("lines", [])
            if lines:
                return [l for l in lines if isinstance(l, dict) and l.get("text")]
            return []

        # 2) fallback: one-shot PowerShell call
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", self.script_path, "-ImagePath", str(prep_path.resolve()),
        ]
        try:
            res = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="oem", errors="replace", timeout=8.0,
            )
        except Exception:
            return []

        output = (res.stdout or "").strip()
        if not output:
            return []
        try:
            data = json.loads(output)
            lines = data.get("lines", [])
            return [l for l in lines if isinstance(l, dict) and l.get("text")]
        except Exception:
            # Broken/legacy output: return NOTHING (a fake 0,0 line would cause
            # blind clicks at the crop corner - never acceptable).
            return []

    def read_text_from_image(self, image: Image.Image, tag: str = "crop") -> str:
        """Plain-text convenience wrapper over read_lines()."""
        lines = self.read_lines(image, tag)
        return " ".join(l["text"] for l in lines).strip()

    def read_badge_number(self, crop: Image.Image, ink, tag: str = "badge") -> Optional[str]:
        """OCR second opinion for a HUD badge, read from the CLEAN ink mask.

        Returns a normalized badge string ("14.0M") or None. Kept separate from
        read_text_from_image because the raw coloured badge is unreadable for
        WinRT OCR: the stylized digits sit on a saturated gradient, while the
        binarized mask (black on white) reads reliably. This is the anchor the
        eye uses to verify - and to teach - its own glyph templates.
        """
        if crop is None or ink is None or not int(ink.sum()):
            return None
        try:
            from vision.template_reader import ink_to_image
            img = ink_to_image(ink, size=crop.size, upscale=4)
            text = self.read_text_from_image(img, tag)
            label = self.normalize_ocr_number(text)
            if not label:
                return None
            from vision.template_reader import is_badge_number
            return label if is_badge_number(label) is not None else None
        except Exception:
            return None

    @staticmethod
    def normalize_ocr_number(raw_text: str) -> Optional[str]:
        """
        Normalizes a badge-like OCR string ('81е8к' -> '81.8K') mapping Cyrillic
        homoglyphs and common OCR confusions. Returns None if the string does not
        look like a number badge (protection against learning from scene text).
        """
        if not raw_text:
            return None
        if len(raw_text) > 12:
            return None
        s = raw_text.strip().upper().translate(CYRILLIC_MAP)
        s = s.replace(",", ".").replace(" ", "")
        out = []
        for i, ch in enumerate(s):
            if ch.isdigit() or ch in ".KMBT":
                out.append(ch)
            elif ch == "E":
                out.append("." if 0 < i < len(s) - 1 else "")
            elif ch in ("O", "Q", "D"):
                out.append("0")
            elif ch in ("I", "L", "J"):
                out.append("1")
            elif ch == "Z":
                out.append("2")
            elif ch == "A":
                out.append("4")
            elif ch == "S":
                out.append("5")
            elif ch == "G":
                out.append("6")
            elif ch == "B":
                out.append("8")
        text = "".join(out)
        if not text or not re.fullmatch(r"\d+(?:\.\d+)?[KMBT]?", text):
            return None
        return text

    @staticmethod
    def looks_like_claim(raw_text: str) -> bool:
        """Fuzzy CLAIM detection: OCR often returns homoglyphs ('СИАМ' for CLAIM)."""
        import difflib
        if not raw_text:
            return False
        t = raw_text.upper().translate(CYRILLIC_MAP).lower()
        alpha = "".join(ch for ch in t if ch.isalpha())
        if not alpha:
            return False
        if "claim" in alpha:
            return True
        return difflib.SequenceMatcher(None, alpha, "claim").ratio() >= 0.6
