"""
ScreenEye: The Visual Perception Engine for AI Agents
Handles high-speed frame capture of the Roblox window, ROI extraction,
visual debugging overlays, and motion/stuck detection.
"""
import time
from pathlib import Path
from typing import Optional, Dict, Tuple
from PIL import Image, ImageGrab, ImageDraw, ImageFont

from config import DEBUG_DIR, ROIS
from core.window import WindowManager


class ScreenEye:
    def __init__(self, window_manager: WindowManager):
        self.wm = window_manager
        self.last_frame: Optional[Image.Image] = None
        self.last_capture_time = 0.0

    def capture_full_window(self) -> Optional[Image.Image]:
        """
        Captures the exact client area of the Roblox window or primary monitor.
        If running in non-interactive/headless mode, generates a calibration frame.
        """
        left, top, width, height = self.wm.get_client_rect()
        try:
            if width > 100 and height > 100:
                bbox = (left, top, left + width, top + height)
                img = ImageGrab.grab(bbox=bbox)
            else:
                img = ImageGrab.grab()
            self.last_frame = img
            self.last_capture_time = time.time()
            return img
        except Exception:
            # Fallback calibration canvas for testing / non-interactive shells
            w, h = (1280, 720) if (width <= 0 or height <= 0) else (width, height)
            calibration_img = Image.new("RGB", (w, h), color=(30, 32, 40))
            draw = ImageDraw.Draw(calibration_img)
            draw.text((w // 2 - 120, h // 2), "Muscle Legends - Calibration Eye", fill="#FFFFFF")
            self.last_frame = calibration_img
            self.last_capture_time = time.time()
            return calibration_img

    def get_roi_crop(self, roi_name: str, frame: Optional[Image.Image] = None) -> Optional[Image.Image]:
        """Crops a specific Region of Interest (e.g. strength_counter, gems_counter)."""
        if roi_name not in ROIS:
            return None

        if frame is None:
            frame = self.last_frame
            if frame is None:
                frame = self.capture_full_window()

        if frame is None:
            return None

        w, h = frame.size
        roi = ROIS[roi_name]

        x1 = int(roi["x"] * w)
        y1 = int(roi["y"] * h)
        x2 = int((roi["x"] + roi["w"]) * w)
        y2 = int((roi["y"] + roi["h"]) * h)

        return frame.crop((x1, y1, x2, y2))

    def save_annotated_debug(self, filename: str = "latest_annotated.png") -> Optional[Path]:
        """
        Creates an annotated visual snapshot showing all detected ROIs
        and overlay info for AI inspection and debugging.
        """
        frame = self.capture_full_window()
        if frame is None:
            return None

        annotated = frame.copy()
        draw = ImageDraw.Draw(annotated)
        w, h = annotated.size

        # Color palette for different HUD components
        colors = {
            "strength_counter": "#00FF66",
            "gems_counter":     "#00CCFF",
            "rebirth_counter":  "#FF9900",
            "health_bar":       "#FF3333",
            "rebirth_menu_btn": "#FFFF00",
            "pets_menu_btn":    "#FF00FF",
            "shop_menu_btn":    "#00FFFF",
            "center_interact":  "#FFFFFF",
            "safezone_banner":  "#33FF33",
        }

        for name, roi in ROIS.items():
            x1 = int(roi["x"] * w)
            y1 = int(roi["y"] * h)
            x2 = int((roi["x"] + roi["w"]) * w)
            y2 = int((roi["y"] + roi["h"]) * h)

            box_color = colors.get(name, "#FFFFFF")
            draw.rectangle([x1, y1, x2, y2], outline=box_color, width=2)
            draw.text((x1 + 4, y1 + 2), name, fill=box_color)

        # Header overlay
        header_text = f"Eye Snapshot - {time.strftime('%Y-%m-%d %H:%M:%S')} - Res: {w}x{h}"
        draw.rectangle([0, 0, w, 24], fill="#111111")
        draw.text((10, 4), header_text, fill="#EEEEEE")

        target_path = DEBUG_DIR / filename
        annotated.save(target_path)
        return target_path

    def check_motion(self, current_frame: Image.Image, previous_frame: Image.Image, threshold: float = 0.02) -> bool:
        """
        Determines if there is visual motion between two frames.
        Used to detect if the character is stuck or if screen is frozen.
        """
        if current_frame.size != previous_frame.size:
            return True

        # Fast downscaled diff
        img1 = current_frame.convert("L").resize((64, 64))
        img2 = previous_frame.convert("L").resize((64, 64))

        diff = 0
        p1 = list(img1.getdata())
        p2 = list(img2.getdata())
        total_pixels = len(p1)

        for a, b in zip(p1, p2):
            if abs(a - b) > 15:
                diff += 1

        motion_ratio = diff / total_pixels
        return motion_ratio > threshold
