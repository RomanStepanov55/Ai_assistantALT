"""
Read-only companion entry point for Muscle Legends
  python main.py             -> passive watcher (scene journal, same as --watch)
  python main.py --monitor   -> continuous Eye monitor (live HUD stats & growth)
  python main.py --watch     -> passive Watcher (scene journal)

HARD RULE #2 (user decision, 2026-11-24): the agent NEVER plays for the user.
It watches, learns and consults; every keyboard/mouse path is disabled at the
InputController level (config.GAME_INPUT_ENABLED is False), and observation is
passive: nothing is ever raised, maximized or focused. The former autonomous
loops (adapter / mind / test-input / wheel / gift / quests / rebirth) were
removed with the pivot; their history stays in git.
"""
import sys
import time
import argparse

from config import STAT_SAMPLE_INTERVAL_SEC
from core.window import WindowManager
from vision.screen_eye import ScreenEye
from vision.ocr_engine import OCREngine
from vision.detector import GameDetector
from brain.watcher import Watcher


def build_stack():
    """Read-only perception stack: window, eye, OCR, detector, watcher."""
    wm = WindowManager()
    eye = ScreenEye(wm)
    ocr = OCREngine()
    detector = GameDetector(eye, ocr)
    watcher = Watcher(wm, eye, ocr)
    return wm, eye, ocr, detector, watcher


def run_continuous_monitor(wm: WindowManager, eye: ScreenEye, detector: GameDetector):
    """Continuous real-time HUD monitor showing true stats from the game screen."""
    print("=" * 60)
    print("     CONTINUOUS SCREEN EYE & HUD MONITOR (Ctrl+C to stop)")
    print("=" * 60)
    try:
        frame_idx = 0
        while True:
            if wm.find_roblox_window():
                hud = detector.extract_full_hud_state()
                frame_idx += 1
                eye.save_annotated_debug("latest_annotated.png")

                sys.stdout.write(
                    f"\r[EYE #{frame_idx:04d}] "
                    f"Strength: {hud['strength']:,} | "
                    f"Candy/Gems: {hud['gems']:,} | "
                    f"Gift Ready: {hud['gift_ready']} | "
                    f"Raw: '{hud.get('raw_str_text')}' / '{hud.get('raw_gems_text')}'"
                )
                sys.stdout.flush()
            else:
                sys.stdout.write("\r[WAIT] Looking for active Roblox window...")
                sys.stdout.flush()
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[MONITOR] Stopped.")


def run_watcher(watcher: Watcher):
    """Passive background eye. Writes scene_state.json + eye_journal.log."""
    print("=" * 60)
    print("   PASSIVE BACKGROUND WATCHER (read-only eye, Ctrl+C to stop)")
    print("   -> data/scene_state.json | data/eye_journal.log")
    print("=" * 60)
    try:
        i = 0
        while True:
            state = watcher.observe()
            i += 1
            if not state.get("roblox_found"):
                sys.stdout.write("\r[WATCH] Roblox window not found...")
            elif not state.get("game_visible", True):
                sys.stdout.write("\r[WATCH] window found, NO game HUD in frame "
                                 "(covered by another window or not in a game)...")
            else:
                sys.stdout.write(
                    f"\r[WATCH #{i:04d}] Str: {state.get('strength_raw') or '?'} | "
                    f"Gems: {state.get('gems_raw') or '?'} | "
                    f"Gift: {'READY' if state.get('gift_ready') else '-'} | "
                    f"Modal: {'open' if state.get('modal_open') else 'none'} | "
                    f"Events: {','.join(state.get('events', [])) or 'none'}"
                )
            sys.stdout.flush()
            time.sleep(10.0)
    except KeyboardInterrupt:
        print("\n[WATCH] Stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Muscle Legends read-only companion (observation only)")
    parser.add_argument("--monitor", action="store_true",
                        help="Run continuous background Eye monitor")
    parser.add_argument("--watch", action="store_true",
                        help="Passive watcher (scene journal, default mode)")
    args = parser.parse_args()

    wm, eye, ocr, detector, watcher = build_stack()
    if args.monitor:
        run_continuous_monitor(wm, eye, detector)
    else:
        run_watcher(watcher)
