import re
import io
import time
import base64
import hashlib
import ctypes
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import mss
except ImportError:
    mss = None
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    RapidOCR = None

try:
    from pywinauto import Desktop
except ImportError:
    Desktop = None

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(-2)
except Exception:
    pass


SUFFIX_MULTIPLIERS: Dict[str, float] = {
    "": 1.0,
    "k": 1e3,
    "m": 1e6,
    "b": 1e9,
    "t": 1e12,
    "qa": 1e15,
    "qi": 1e18,
    "sx": 1e21,
    "sp": 1e24,
}


def parse_hud_number(raw_text: str) -> Optional[float]:
    cleaned = raw_text.replace(",", "").replace(" ", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)\s*(qa|qi|sx|sp|k|m|b|t)?\b", cleaned, re.IGNORECASE)
    if not match:
        return None
    val = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    return val * SUFFIX_MULTIPLIERS.get(suffix, 1.0)


@dataclass
class ScreenElement:
    mark_id: int
    text: str
    bbox: Tuple[int, int, int, int]
    center: Tuple[int, int]
    confidence: float
    source: str


class OmniEye:
    TRADE_KEYWORDS = ("trade request", "incoming trade", "обмен", "предлагает обмен", "accept trade")
    DECLINE_KEYWORDS = ("decline", "cancel", "reject", "отклонить", "отмена", "close", "x")
    RECONNECT_KEYWORDS = ("reconnect", "переподключиться", "disconnected")

    def __init__(self, memory=None):
        self.sct = mss.mss() if mss is not None else None
        self.ocr = RapidOCR() if RapidOCR is not None else None
        self.memory = memory

    @staticmethod
    def _image_fingerprint(img: Image.Image) -> str:
        """Perceptual difference hash; only this digest is retained, never the image."""
        small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(small.getdata())
        bits = []
        for y in range(8):
            for x in range(8):
                bits.append("1" if pixels[y * 9 + x] > pixels[y * 9 + x + 1] else "0")
        return f"{int(''.join(bits), 2):016x}"

    def capture_frame(self, monitor_idx: int = 1) -> Image.Image:
        if self.sct is None:
            raise RuntimeError("Screen capture dependency missing: install mss")
        monitors = self.sct.monitors
        idx = monitor_idx if monitor_idx < len(monitors) else 1
        raw = self.sct.grab(monitors[idx])
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    def scan_ocr(self, img: Image.Image) -> List[ScreenElement]:
        elements: List[ScreenElement] = []
        if self.ocr is None:
            return elements

        arr = np.array(img)
        result, _ = self.ocr(arr)
        if not result:
            return elements

        mark = 1
        for box, text, conf in result:
            if float(conf) < 0.55 or not text.strip():
                continue
            xs = [int(p[0]) for p in box]
            ys = [int(p[1]) for p in box]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            elements.append(
                ScreenElement(
                    mark_id=mark,
                    text=text.strip(),
                    bbox=(x1, y1, x2, y2),
                    center=(cx, cy),
                    confidence=float(conf),
                    source="ocr"
                )
            )
            mark += 1
        return elements

    def scan_windows_uia(self, max_items: int = 40) -> List[Dict[str, Any]]:
        if Desktop is None:
            return []
        ui_nodes: List[Dict[str, Any]] = []
        try:
            win = Desktop(backend="uia").get_active()
            for ctrl in win.descendants()[:max_items]:
                name = (ctrl.window_text() or "").strip()
                if not name:
                    continue
                rect = ctrl.rectangle()
                ui_nodes.append({
                    "name": name,
                    "type": ctrl.element_info.control_type,
                    "center": ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                })
        except Exception:
            pass
        return ui_nodes

    def render_set_of_mark(
        self,
        img: Image.Image,
        elements: List[ScreenElement]
    ) -> Tuple[Image.Image, str]:
        annotated = img.copy()
        draw = ImageDraw.Draw(annotated)
        font = ImageFont.load_default()

        for el in elements:
            x1, y1, x2, y2 = el.bbox
            draw.rectangle([x1, y1, x2, y2], outline=(0, 240, 255), width=2)
            badge = f"[{el.mark_id}]"
            draw.rectangle([x1, max(0, y1 - 14), x1 + len(badge) * 7 + 4, y1], fill=(176, 112, 255))
            draw.text((x1 + 2, max(0, y1 - 13)), badge, fill=(255, 255, 255), font=font)

        buf = io.BytesIO()
        if annotated.width > 1280:
            ratio = 1280 / annotated.width
            annotated = annotated.resize((1280, int(annotated.height * ratio)), Image.Resampling.LANCZOS)
        annotated.save(buf, format="JPEG", quality=82)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return annotated, b64

    def check_safety_reflexes(self, elements: List[ScreenElement]) -> Optional[Dict[str, Any]]:
        texts_lower = [e.text.lower() for e in elements]
        full_blob = " ".join(texts_lower)

        if any(k in full_blob for k in self.TRADE_KEYWORDS):
            for el in elements:
                if any(d in el.text.lower() for d in self.DECLINE_KEYWORDS):
                    return {
                        "reflex": "DECLINE_TRADE",
                        "click_xy": el.center,
                        "reason": f"Обнаружен запрос обмена, нажатие '{el.text}'"
                    }

        if any(k in full_blob for k in self.RECONNECT_KEYWORDS):
            for el in elements:
                if "reconnect" in el.text.lower() or "переподкл" in el.text.lower():
                    return {
                        "reflex": "AUTO_RECONNECT",
                        "click_xy": el.center,
                        "reason": "Обнаружен обрыв связи Roblox, нажатие Reconnect"
                    }
        return None

    def extract_hud_metrics(self, elements: List[ScreenElement]) -> Dict[str, float]:
        metrics: Dict[str, float] = {}
        for idx, el in enumerate(elements):
            val = parse_hud_number(el.text)
            if val is not None:
                label = f"metric_{idx}"
                alpha_part = re.sub(r"[\d.,\s]+(qa|qi|sx|sp|k|m|b|t)?", "", el.text, flags=re.I).strip(": -")
                if len(alpha_part) >= 2:
                    label = alpha_part.lower()
                elif idx > 0 and parse_hud_number(elements[idx - 1].text) is None:
                    prev_label = elements[idx - 1].text.strip(": -").lower()
                    if 2 <= len(prev_label) <= 20:
                        label = prev_label
                metrics[label] = val
        return metrics

    def observe_verified(self, delay_sec: float = 0.25) -> Dict[str, Any]:
        img1 = self.capture_frame()
        el1 = self.scan_ocr(img1)
        m1 = self.extract_hud_metrics(el1)

        time.sleep(delay_sec)

        img2 = self.capture_frame()
        el2 = self.scan_ocr(img2)
        m2 = self.extract_hud_metrics(el2)

        # A value is considered stable only when both independent OCR passes
        # observed it and they agree within 10%. A single read is not evidence.
        verified_metrics: Dict[str, float] = {}
        for k, v2 in m2.items():
            if k in m1 and (m1[k] == 0 or abs(v2 - m1[k]) / max(abs(m1[k]), 1.0) <= 0.10):
                verified_metrics[k] = v2

        reflex = self.check_safety_reflexes(el2)
        som_img, som_b64 = self.render_set_of_mark(img2, el2)
        element_payload = [
            {"id": e.mark_id, "text": e.text, "center": e.center, "conf": round(e.confidence, 2)}
            for e in el2
        ]
        windows = self.scan_windows_uia(max_items=20)
        visual_memory = None
        if self.memory is not None:
            # The image is used transiently to compute a hash. No image bytes or paths are stored.
            visual_memory = self.memory.remember_visual(
                self._image_fingerprint(img2), element_payload, verified_metrics, windows
            )
            visual_memory["similar_states"] = self.memory.similar_visual_states(
                element_payload, limit=3, image_hash=self._image_fingerprint(img2))

        return {
            "timestamp": time.time(),
            "elements": element_payload,
            "metrics": verified_metrics,
            "reflex_action": reflex,
            "som_image": som_img,
            "som_b64": som_b64,
            "uia_windows": windows,
            "visual_memory": visual_memory
        }
