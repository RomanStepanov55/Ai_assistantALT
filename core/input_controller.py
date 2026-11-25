"""
DirectInput & Hardware Controller for Roblox
Uses Windows SendInput API with hardware scancodes (DirectX compatible).
Never uses ESC key to avoid opening the Roblox system menu.

GOLDEN RULE enforcement lives here: click_roi() refuses any ROI that is not
in config.ALLOWED_CLICK_ROIS -> purchase/billing UI can never be clicked.

HARD RULE #2 (2026-11-24): the agent never plays for the user. Every key, mouse
button and cursor move passes through _guard(), which raises GameInputDisabled
while config.GAME_INPUT_ENABLED is False. This is the single choke point for
input in the whole project: the legacy drivers below are kept for reference
and tests, but none of them can reach the game.
"""
import ctypes
import time
from typing import Union

import config
from config import SCAN_CODES, ROIS, ALLOWED_CLICK_ROIS, CURSOR_SAFE_POINT


class GameInputDisabled(RuntimeError):
    """No input can be sent: config.GAME_INPUT_ENABLED is False (HARD RULE #2)."""

    def __init__(self, what: str):
        super().__init__(f"ввод в игру отключён ({what}): агент не играет "
                         f"за пользователя")

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

KEYEVENTF_KEYDOWN = 0x0000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_short),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("union", INPUT_UNION),
    ]


class InputController:
    def __init__(self):
        from core.platform import require_windows
        require_windows("ввод в игру (DirectInput)")
        self.user32 = ctypes.windll.user32

    @staticmethod
    def _guard(what: str) -> None:
        """HARD RULE #2: read the flag at call time, never at import time."""
        if not config.GAME_INPUT_ENABLED:
            raise GameInputDisabled(what)

    def _send_input(self, input_obj: INPUT):
        self._guard("клавиша или кнопка мыши")
        self.user32.SendInput(1, ctypes.byref(input_obj), ctypes.sizeof(INPUT))

    def key_down(self, key_name: str):
        if key_name.upper() == "ESC":
            return  # GOLDEN RULE + UX RULE: ESC opens the Roblox system menu. Never send.
        code = SCAN_CODES.get(key_name.upper())
        if not code:
            return
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki.wVk = 0
        inp.union.ki.wScan = code
        inp.union.ki.dwFlags = KEYEVENTF_SCANCODE
        self._send_input(inp)

    def key_up(self, key_name: str):
        if key_name.upper() == "ESC":
            return
        code = SCAN_CODES.get(key_name.upper())
        if not code:
            return
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki.wVk = 0
        inp.union.ki.wScan = code
        inp.union.ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
        self._send_input(inp)

    def tap_key(self, key_name: str, duration: float = 0.05):
        self.key_down(key_name)
        time.sleep(duration)
        self.key_up(key_name)

    def move_cursor_to(self, screen_x: int, screen_y: int):
        self._guard("курсор")
        self.user32.SetCursorPos(screen_x, screen_y)

    def center_cursor(self, wm):
        """Parks the cursor on the safe center point (over the character)."""
        self._guard("курсор (center_cursor)")
        sx, sy = wm.to_screen_coords(CURSOR_SAFE_POINT["x"], CURSOR_SAFE_POINT["y"])
        self.move_cursor_to(sx, sy)
        time.sleep(0.03)

    def mouse_click(self, screen_x: int = None, screen_y: int = None, button: str = "left", duration: float = 0.05):
        if screen_x is not None and screen_y is not None:
            self.move_cursor_to(screen_x, screen_y)
            time.sleep(0.04)

        down_flag = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
        up_flag = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP

        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.union.mi.dwFlags = down_flag
        self._send_input(inp)

        time.sleep(duration)

        inp.union.mi.dwFlags = up_flag
        self._send_input(inp)

    def select_slot(self, slot_num: int):
        key_str = str(slot_num)
        self.tap_key(key_str, 0.06)
        time.sleep(0.12)

    def equip_dumbbells(self):
        """Slot 2 in Muscle Legends is Hand Weights / Dumbbells."""
        self.select_slot(2)

    def equip_pushups(self):
        """Slot 7 in Muscle Legends is Pushups."""
        self.select_slot(7)

    def equip_situps(self):
        """Slot 8 in Muscle Legends is Situps."""
        self.select_slot(8)

    def smooth_lift(self, lifts: int = 10, animation_delay: float = 0.65, wm=None) -> int:
        """
        Executes lifts matching the game's ~0.65s rep animation.
        Parks the cursor on the safe center point first, so no rep ever lands
        on a HUD button.

        Returns the number of clicks actually delivered (0..lifts). SendInput
        goes to whatever window is in FRONT: if the game lost focus to a
        terminal/browser, the clicks would land there. Measured: a 20-rep set
        reported "no gain" because the whole burst went to another window.
        """
        import win32gui

        if wm is not None:
            self.center_cursor(wm)
        delivered = 0
        for _ in range(lifts):
            if wm is not None and win32gui.GetForegroundWindow() != wm.hwnd:
                if not wm.ensure_focused():
                    break
                self.center_cursor(wm)
            self.mouse_click(button="left", duration=0.06)
            delivered += 1
            time.sleep(animation_delay)
        return delivered

    def train_machine_routine(self, reps: int = 8, wm=None):
        """
        Interacts with nearby gym machine (barbell, bench press, deadlift) via 'E',
        performs reps, then dismounts with 'SPACE'.
        """
        self.tap_key("E", 0.1)
        time.sleep(0.5)
        self.smooth_lift(lifts=reps, animation_delay=0.65, wm=wm)
        self.tap_key("SPACE", 0.08)  # Dismount
        time.sleep(0.3)

    # ---------- camera controls ----------
    # Roblox camera: mouse wheel = zoom, RIGHT-button drag = rotate.

    def camera_zoom(self, direction: str = "out", clicks: int = 3):
        """Zooms the camera via synthetic mouse wheel ('out' shows more of the
        surroundings)."""
        wheel_flag = 0x0800  # MOUSEEVENTF_WHEEL
        amount = -120 if direction == "out" else 120
        inp = INPUT()
        inp.type = INPUT_MOUSE
        for _ in range(clicks):
            inp.union.mi.dwFlags = wheel_flag
            inp.union.mi.mouseData = amount & 0xFFFFFFFF
            self._send_input(inp)
            time.sleep(0.08)

    def camera_rotate(self, seconds: float = 1.0, speed: int = 12):
        """Rotates the camera by holding the RIGHT mouse button while moving
        the cursor horizontally (Roblox look-around)."""
        import ctypes as _ct
        user32 = self.user32
        pt = _ct.wintypes.POINT() if hasattr(_ct, 'wintypes') else None
        class _POINT(_ct.Structure):
            _fields_ = [('x', _ct.c_long), ('y', _ct.c_long)]
        pt = _POINT()
        user32.GetCursorPos(_ct.byref(pt))
        cx, cy = pt.x, pt.y

        MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.union.mi.dwFlags = MOUSEEVENTF_RIGHTDOWN
        self._send_input(inp)
        time.sleep(0.05)
        steps = max(4, int(seconds / 0.05))
        dx = speed
        for _ in range(steps):
            cx += dx
            self.move_cursor_to(cx, cy)      # через _guard, не в обход
            time.sleep(0.05)
        inp.union.mi.dwFlags = MOUSEEVENTF_RIGHTUP
        self._send_input(inp)
        time.sleep(0.1)

    def train_agility_run(self, duration_sec: float = 4.0):
        """Runs to train Agility (Speed)."""
        self.key_down("W")
        time.sleep(duration_sec)
        self.key_up("W")

    def click_roi(self, roi_name: str, wm) -> bool:
        """
        Clicks the center of a named UI button.
        GUARD: refuses any ROI not whitelisted in config.ALLOWED_CLICK_ROIS
        (store, autolift, codes, trade, clans, moves... can never be clicked).
        HARD RULE #2 fires first: no click at all while game input is disabled.
        """
        self._guard(f"click_roi({roi_name})")
        roi = ROIS.get(roi_name)
        if not roi:
            return False
        if roi_name not in ALLOWED_CLICK_ROIS:
            print(f"[GUARD] Refused click on '{roi_name}' - not in ALLOWED_CLICK_ROIS (golden rule protection).")
            return False
        cx = roi["x"] + (roi["w"] / 2.0)
        cy = roi["y"] + (roi["h"] / 2.0)
        sx, sy = wm.to_screen_coords(cx, cy)
        self.mouse_click(sx, sy)
        return True

    def close_modal(self, wm, toggle_roi_name: str = None):
        """
        Closes an open modal WITHOUT pressing ESC:
        clicks the modal X button, then parks the cursor on the safe point.
        """
        if toggle_roi_name and toggle_roi_name in ALLOWED_CLICK_ROIS:
            self.click_roi(toggle_roi_name, wm)
            time.sleep(0.3)
        else:
            self.click_roi("modal_close_btn", wm)
            time.sleep(0.3)
        self.center_cursor(wm)
