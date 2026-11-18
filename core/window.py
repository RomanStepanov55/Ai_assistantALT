"""
Window Manager for Roblox
Handles finding the Roblox client window, retrieving its geometry, 
activating it, and mapping normalized coordinates to screen pixels.
"""
import ctypes
import time
from typing import Optional, Tuple
import psutil
import win32gui
import win32process
import win32con

from config import WINDOW_TITLE_SUBSTRING, PROCESS_NAMES


class WindowManager:
    def __init__(self):
        self.hwnd: Optional[int] = None
        self.last_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self.attach_interactive_desktop()

    def attach_interactive_desktop(self):
        """Ensures the thread is attached to the interactive user desktop 'Default'."""
        try:
            user32 = ctypes.windll.user32
            h_desk = user32.OpenDesktopW('Default', 0, False, 0x01FF) # GENERIC_ALL
            if h_desk:
                user32.SetThreadDesktop(h_desk)
        except Exception:
            pass

    def _hwnd_is_roblox_process(self, hwnd) -> bool:
        """True only if the window belongs to a real Roblox process (not any app
        whose title merely contains the word 'Roblox')."""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            name = (psutil.Process(pid).name() or "").lower()
            return any(p.lower() in name for p in PROCESS_NAMES)
        except Exception:
            return False

    def find_roblox_window(self) -> Optional[int]:
        """Searches for the active Roblox window (process-verified)."""
        self.attach_interactive_desktop()
        user32 = ctypes.windll.user32

        # 1. Fast direct lookup by exact title, verified by process
        hwnd = user32.FindWindowW(None, "Roblox")
        if hwnd and user32.IsWindow(hwnd) and self._hwnd_is_roblox_process(hwnd):
            self.hwnd = hwnd
            return hwnd

        target_hwnd = None
        # 2. Title substring search, but ONLY among windows of Roblox processes
        def enum_title_callback(hwnd, extra):
            nonlocal target_hwnd
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if WINDOW_TITLE_SUBSTRING.lower() in title.lower():
                    if self._hwnd_is_roblox_process(hwnd):
                        rect = win32gui.GetClientRect(hwnd)
                        width = rect[2] - rect[0]
                        height = rect[3] - rect[1]
                        if width > 400 and height > 300:
                            target_hwnd = hwnd
                            return False
            return True

        try:
            win32gui.EnumWindows(enum_title_callback, None)
        except Exception:
            pass

        if target_hwnd:
            self.hwnd = target_hwnd
            return target_hwnd

        # 2. Fallback: Search by running Roblox process PIDs
        roblox_pids = set()
        for p in psutil.process_iter(['pid', 'name']):
            try:
                if any(proc.lower() in p.info['name'].lower() for proc in PROCESS_NAMES):
                    roblox_pids.add(p.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if roblox_pids:
            def enum_pid_callback(hwnd, extra):
                nonlocal target_hwnd
                if win32gui.IsWindowVisible(hwnd):
                    try:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if pid in roblox_pids:
                            rect = win32gui.GetClientRect(hwnd)
                            if (rect[2] - rect[0]) > 400:
                                target_hwnd = hwnd
                                return False
                    except Exception:
                        pass
                return True

            try:
                win32gui.EnumWindows(enum_pid_callback, None)
            except Exception:
                pass

        self.hwnd = target_hwnd
        return target_hwnd

    def ensure_focused(self) -> bool:
        """Brings the Roblox window to the foreground on THIS desktop.
        Escalation ladder: ALT-trick -> minimize/restore (pulls the window from
        another virtual desktop!) -> AttachThreadInput -> SwitchToThisWindow."""
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            if not self.find_roblox_window():
                return False

        user32 = ctypes.windll.user32
        try:
            if win32gui.IsIconic(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                time.sleep(0.15)

            # ROIs are calibrated for the maximized client -> keep it maximized
            if not ctypes.windll.user32.IsZoomed(self.hwnd):
                win32gui.ShowWindow(self.hwnd, win32con.SW_MAXIMIZE)
                time.sleep(0.3)

            if win32gui.GetForegroundWindow() == self.hwnd:
                return True

            def alt_setfg():
                user32.keybd_event(0x12, 0, 0, 0)   # ALT down
                try:
                    win32gui.SetForegroundWindow(self.hwnd)
                finally:
                    user32.keybd_event(0x12, 0, 2, 0)  # ALT up
                time.sleep(0.12)

            # Trick 1: plain ALT-unlock SetForegroundWindow
            alt_setfg()

            # Trick 2: minimize + restore - pulls the window to the CURRENT
            # virtual desktop (the game may have been left on another one),
            # then maximize (the bot's ROIs are calibrated for the maximized UI)
            if win32gui.GetForegroundWindow() != self.hwnd:
                win32gui.ShowWindow(self.hwnd, win32con.SW_MINIMIZE)
                time.sleep(0.4)
                win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
                time.sleep(0.4)
                win32gui.ShowWindow(self.hwnd, win32con.SW_MAXIMIZE)
                time.sleep(0.4)
                alt_setfg()

            # Trick 3: AttachThreadInput to the current foreground thread
            if win32gui.GetForegroundWindow() != self.hwnd:
                try:
                    fg = win32gui.GetForegroundWindow()
                    fg_tid = user32.GetWindowThreadProcessId(fg, None)
                    my_tid = user32.GetWindowThreadProcessId(self.hwnd, None)
                    if fg_tid and my_tid and user32.AttachThreadInput(my_tid, fg_tid, True):
                        try:
                            win32gui.SetForegroundWindow(self.hwnd)
                            win32gui.SetFocus(self.hwnd)
                        finally:
                            user32.AttachThreadInput(my_tid, fg_tid, False)
                except Exception:
                    pass
                time.sleep(0.08)

            # Trick 4: SwitchToThisWindow (undocumented but reliable)
            if win32gui.GetForegroundWindow() != self.hwnd:
                try:
                    user32.SwitchToThisWindow(self.hwnd, True)
                except Exception:
                    pass
                time.sleep(0.08)

            return win32gui.GetForegroundWindow() == self.hwnd
        except Exception:
            return False

    def get_client_rect(self) -> Tuple[int, int, int, int]:
        """
        Returns client rectangle (left, top, width, height) in screen coordinates,
        excluding borders and title bar.
        """
        if not self.hwnd or not win32gui.IsWindow(self.hwnd):
            self.find_roblox_window()
            if not self.hwnd:
                return (0, 0, 0, 0)

        point = win32gui.ClientToScreen(self.hwnd, (0, 0))
        c_rect = win32gui.GetClientRect(self.hwnd)
        width = c_rect[2] - c_rect[0]
        height = c_rect[3] - c_rect[1]
        
        self.last_rect = (point[0], point[1], width, height)
        return self.last_rect

    def to_screen_coords(self, rel_x: float, rel_y: float) -> Tuple[int, int]:
        """
        Converts relative normalized coordinates (0.0 to 1.0) inside the 
        Roblox client window into absolute screen pixel coordinates (X, Y).
        """
        left, top, width, height = self.get_client_rect()
        if width == 0 or height == 0:
            return (0, 0)
        
        abs_x = int(left + (rel_x * width))
        abs_y = int(top + (rel_y * height))
        return (abs_x, abs_y)
