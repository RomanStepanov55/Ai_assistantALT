"""
Calibrated Config for Roblox: Muscle Legends
ROIs are normalized (0..1) to the Roblox CLIENT area.
Recalibrated 05.11.2026 on a 1920x1050 windowed-maximized client.

============================================================================
 GOLDEN RULE (hard requirement from the user, MUST NEVER BE VIOLATED):
   - NEVER purchase anything for real money.
   - NEVER attach cards / enable any billing.
   - NEVER click: Auto Lift (gamepass), STORE, PACKS, SPOOKY PASS, CODES,
     BENEFITS, TRADE, or anything that can lead to a Robux/payment page.
 Enforced by ALLOWED_CLICK_ROIS whitelist in InputController.click_roi:
 any ROI not in the whitelist is refused, no exceptions.
============================================================================
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# DPI AWARENESS BOOTSTRAP (must run before any window/capture/click code).
# Without it the bot sees a virtualized 1280x720 desktop on a 150%-scaled
# display: ImageGrab crops physical pixels numerically (cutting the right side
# of the UI) while SetCursorPos multiplies coordinates -> everything misaligns.
# Making the process DPI-aware puts capture, window coords and clicks into ONE
# physical-pixel coordinate system.
# ---------------------------------------------------------------------------
import ctypes
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-2))  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# UTF-8 OUTPUT BOOTSTRAP.
# The capability descriptions and log lines are Russian. When stdout is
# redirected to a file (python main.py > agent_run.log) Python falls back to the
# Windows ANSI codepage, so the log ends up as "�������" garbage and the operator
# cannot read what the agent decided. Force UTF-8 with replacement instead.
# ---------------------------------------------------------------------------
import sys as _sys
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "debug_frames"
DATA_DIR = BASE_DIR / "data"
MEMORY_FILE = DATA_DIR / "memory.json"
TELEMETRY_FILE = DATA_DIR / "telemetry.log"

DEBUG_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

WINDOW_TITLE_SUBSTRING = "Roblox"
PROCESS_NAMES = ["RobloxPlayerBeta.exe", "RobloxPlayer.exe"]

# Hardware DirectInput Scan Codes
SCAN_CODES = {
    "W": 0x11,
    "A": 0x1E,
    "S": 0x1F,
    "D": 0x20,
    "SPACE": 0x39,
    "E": 0x12,
    "Q": 0x10,
    "R": 0x13,
    "TAB": 0x0F,
    "F11": 0x57,   # Roblox fullscreen toggle (UI layout depends on render resolution)
    "ESC": 0x01,   # DEFINED BUT FORBIDDEN: never send ESC (opens Roblox system menu)
    "1": 0x02,  # Fists
    "2": 0x03,  # Hand Weights / Dumbbells (PRIMARY)
    "3": 0x04,  # Potion
    "4": 0x05,  # Potion
    "5": 0x06,  # Aura
    "6": 0x07,  # Handstand
    "7": 0x08,  # Pushups
    "8": 0x09,  # Situps
    "9": 0x0A,  # Special
    "0": 0x0B,  # Bar/Food
}

# EXACT In-Game Screen ROIs & Button Locations (normalized to client area)
ROIS = {
    # Top stats bar
    "strength_counter": {"x": 0.320, "y": 0.015, "w": 0.180, "h": 0.100},  # Yellow badge
    "gems_counter":     {"x": 0.503, "y": 0.015, "w": 0.175, "h": 0.100},  # Green candy/gems badge
    "wheel_btn":        {"x": 0.680, "y": 0.015, "w": 0.066, "h": 0.100},  # WHEEL button
    "autolift_btn":     {"x": 0.760, "y": 0.025, "w": 0.148, "h": 0.085},  # NEVER CLICK (paid gamepass)

    # Left action buttons
    "stats_btn":        {"x": 0.005, "y": 0.315, "w": 0.115, "h": 0.065},  # STATS
    "rebirth_btn":      {"x": 0.005, "y": 0.395, "w": 0.115, "h": 0.065},  # REBIRTH
    "store_btn":        {"x": 0.005, "y": 0.475, "w": 0.115, "h": 0.070},  # NEVER CLICK (robux store)
    "items_btn":        {"x": 0.005, "y": 0.558, "w": 0.115, "h": 0.065},  # ITEMS
    "trade_btn":        {"x": 0.005, "y": 0.640, "w": 0.115, "h": 0.065},  # NEVER CLICK

    # Right action buttons
    "quests_btn":       {"x": 0.886, "y": 0.390, "w": 0.108, "h": 0.062},  # QUESTS
    "clans_btn":        {"x": 0.886, "y": 0.468, "w": 0.108, "h": 0.055},  # NEVER CLICK
    "moves_btn":        {"x": 0.886, "y": 0.532, "w": 0.108, "h": 0.055},  # NEVER CLICK
    "timed_gift_btn":   {"x": 0.881, "y": 0.596, "w": 0.060, "h": 0.087},  # Timed Gift Box / CLAIM
    "codes_btn":        {"x": 0.943, "y": 0.596, "w": 0.055, "h": 0.087},  # NEVER CLICK

    # Modal helpers
    "modal_close_btn":  {"x": 0.748, "y": 0.163, "w": 0.048, "h": 0.068},  # X in top-right of dialogs
    "modal_area":       {"x": 0.295, "y": 0.125, "w": 0.510, "h": 0.730},  # OCR region for opened dialogs
    "event_banner":     {"x": 0.300, "y": 0.125, "w": 0.400, "h": 0.050},  # Brawl/Boss/Raid announcements
    "e_prompt":         {"x": 0.290, "y": 0.430, "w": 0.090, "h": 0.100},  # 'E' interact prompt (machine nearby)
    "machine_info":     {"x": 0.160, "y": 0.400, "w": 0.320, "h": 0.180},  # machine name text (Bench Press / Treadmill...)
}

# The ONLY ROIs the bot may ever click. Everything else is refused by GUARD.
ALLOWED_CLICK_ROIS = {
    "wheel_btn",
    "quests_btn",
    "timed_gift_btn",
    "rebirth_btn",
    "modal_close_btn",
    "items_btn",   # pets/inventory panel (safe to open; purchases inside are
                   # blocked by the OCR word-filter in the pets driver)
}

# Cursor parking spot (center of the play area, on the character) before lifting clicks
CURSOR_SAFE_POINT = {"x": 0.50, "y": 0.50}

# ---------------------------------------------------------------------------
# ECONOMY / BUDGET (golden rule: never spend money-backed currency)
# Gems are earned in-game but CAN be bought with Robux -> they are treated as
# money-backed. Spending is allowed ONLY for user-requested progression
# (egg hatching, pet upgrades) and only above the safety floor.
# ---------------------------------------------------------------------------
GEMS_SAFETY_FLOOR = 100_000      # never spend below this
SPEND_WHITELIST = {"egg_hatch", "pet_upgrade"}   # the only permitted spend reasons
MAX_SPEND_PER_ACTION = 1_000_000  # hard cap per single spend

# Feature flags (all OFF until verified in-game together with the user)
FEATURE_EGGS = True
FEATURE_PETS = True
FEATURE_BOSS = True
FEATURE_REBIRTH = True            # learned: driver verifies the on-screen requirement

# Reps per training set. Measured live 2026-11-16 (one exercise, verified
# strength before/after): 3 sets x 6 reps = 16 358 strength/min; 3 sets x 20
# reps = 124 598 strength/min. The per-set overhead (dismount, walk back,
# settle, two badge reads) dominates short sets; longer sets amortise it.
LIFTS_PER_SET = 20

# UI panel buttons that are safe to open (no purchase dialogs inside on open)
SAFE_PANEL_BUTTONS = {"items_btn"}   # pets/inventory panel

# Boss fight limits
PETS_DELETE_RATIO = 0.10          # delete only pets below 10% of the best
BOSS_FIGHT_SECONDS = 45.0
BOSS_RETREAT_WALK = 2.5

# Muscle Legends Progression Rules & Gym Thresholds
GYMS = [
    {"name": "Starter Gym",     "req_rebirths": 0,  "req_strength": 0,          "multiplier": 1.0},
    {"name": "Frost Gym",       "req_rebirths": 1,  "req_strength": 10_000,     "multiplier": 2.0},
    {"name": "Mythical Gym",    "req_rebirths": 3,  "req_strength": 100_000,    "multiplier": 4.0},
    {"name": "Eternal Gym",     "req_rebirths": 5,  "req_strength": 1_000_000,  "multiplier": 8.0},
    {"name": "Legend Gym",      "req_rebirths": 10, "req_strength": 5_000_000,  "multiplier": 16.0},
    {"name": "Muscle King Gym", "req_rebirths": 30, "req_strength": 50_000_000, "multiplier": 32.0},
]

# Auto-reconnection & Timing
STAT_SAMPLE_INTERVAL_SEC = 5.0   # How often to measure stats & calculate rates
DECISION_INTERVAL_SEC = 15.0     # How often high-level strategic decisions are evaluated

def get_rebirth_requirement(rebirth_count: int) -> int:
    base = 10_000
    if rebirth_count <= 0:
        return base
    return int(base * (1.5 ** rebirth_count))
