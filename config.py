"""
Alontito - personal AI agent (companion). Shared paths and process bootstraps.

The Roblox game layer was removed with the 2026-11-27 pivot ("Alontito 2.0"):
the agent has no input drivers, no HUD reader and no game catalog any more.
What is left here is what every remaining subsystem needs: project paths,
DPI awareness and UTF-8 output. The old input kill switch is gone together
with the input code itself - there is nothing left to switch.
"""
import ctypes
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# DPI AWARENESS BOOTSTRAP (must run before any window/capture code).
# The desktop pet places itself by physical pixels; without this a 150%-scaled
# display reports a virtualized 1280x720 desktop and the pet lands elsewhere.
# ---------------------------------------------------------------------------
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# UTF-8 OUTPUT BOOTSTRAP.
# Logs and panels are Russian. When stdout is redirected to a file, Python
# falls back to the Windows ANSI codepage and the log becomes unreadable
# garbage. Force UTF-8 with replacement instead.
# ---------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
