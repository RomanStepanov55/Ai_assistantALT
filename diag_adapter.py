"""
Adapter diagnostics - READ ONLY, no clicks, no keys.

Usage:
    python diag_adapter.py

Prints step by step where the adapter loop gets stuck:
window -> focus -> capture -> observe -> available_actions -> choose_action
"""

import time
import traceback

from core.window import WindowManager
from core.input_controller import InputController
from vision.screen_eye import ScreenEye
from vision.ocr_engine import OCREngine
from vision.detector import GameDetector
from brain.learning_memory import LearningMemory
from brain.watcher import Watcher

from agent_actions import AgentActions
from navigation import Navigator
from pets import PetsDriver
from eggs import EggDriver
from boss import BossDriver
from rebirth import RebirthDriver

from games.muscle_legends import MuscleLegendsAdapter
from brain.adapter_policy import choose_action, COOLDOWNS


def step(n, title):
    print(f"\n[{n}] {title}")
    print("-" * 60)


def main():
    print("=" * 60)
    print("   ADAPTER DIAGNOSTICS (read-only)")
    print("=" * 60)

    # --- 1. class structure ---
    step(1, "Adapter class structure")
    for m in ("observe", "available_actions", "execute_action",
              "handle_interrupts", "_daily_rewards"):
        print(f"  {m:20s}: {hasattr(MuscleLegendsAdapter, m)}")

    if not hasattr(MuscleLegendsAdapter, "available_actions"):
        print("\n  !!! available_actions is NOT a class method.")
        print("  !!! It is nested inside observe() - fix the indentation.")
        return

    # --- 2. window ---
    step(2, "Window search")
    wm = WindowManager()
    t0 = time.time()
    try:
        hwnd = wm.find_roblox_window()
        print(f"  find_roblox_window() -> {hwnd}   ({time.time()-t0:.2f}s)")
    except Exception:
        traceback.print_exc()
        return

    if not hwnd:
        print("\n  !!! Roblox window NOT FOUND.")
        print("  !!! Start Roblox and join a game, then run again.")
        return

    try:
        print(f"  client rect (l,t,w,h): {wm.get_client_rect()}")
    except Exception:
        traceback.print_exc()

    # --- 3. focus ---
    step(3, "Focus")
    t0 = time.time()
    try:
        ok = wm.ensure_focused()
        print(f"  ensure_focused() -> {ok}   ({time.time()-t0:.2f}s)")
    except Exception:
        traceback.print_exc()
        return

    if not ok:
        print("\n  !!! Cannot focus the Roblox window.")
        print("  !!! Restore/maximize it manually on this virtual desktop.")
        return

    # --- 4. capture ---
    step(4, "Screen capture")
    eye = ScreenEye(wm)
    t0 = time.time()
    try:
        frame = eye.capture_full_window()
        if frame is None:
            print("  capture_full_window() -> None  !!!")
            return
        print(f"  frame size: {frame.size}   ({time.time()-t0:.2f}s)")
    except Exception:
        traceback.print_exc()
        return

    # --- 5. OCR ---
    step(5, "OCR engine (cold start can take 7-15s)")
    t0 = time.time()
    try:
        ocr = OCREngine()
        print(f"  OCREngine() built in {time.time()-t0:.2f}s")
        crop = eye.get_roi_crop("strength_counter", frame)
        t1 = time.time()
        lines = ocr.read_lines(crop, "diag_strength")
        print(f"  read_lines() -> {len(lines)} line(s) in {time.time()-t1:.2f}s")
        for l in lines[:5]:
            print(f"    {l.get('text')!r}")
    except Exception:
        traceback.print_exc()
        return

    # --- 6. full stack ---
    step(6, "Build stack")
    t0 = time.time()
    try:
        input_ctrl = InputController()
        detector = GameDetector(eye, ocr)
        watcher = Watcher(wm, eye, ocr)
        memory = LearningMemory()
        actions = AgentActions(wm, input_ctrl, eye, ocr, detector, memory)
        navigator = Navigator(wm, input_ctrl, eye, ocr, memory)
        pets = PetsDriver(actions)
        eggs = EggDriver(actions, navigator)
        boss = BossDriver(actions, navigator)
        rebirth = RebirthDriver(actions, memory)

        adapter = MuscleLegendsAdapter(
            wm=wm, input_ctrl=input_ctrl, eye=eye, ocr=ocr,
            detector=detector, watcher=watcher, memory=memory,
            actions=actions, navigator=navigator, pets=pets,
            eggs=eggs, boss=boss, rebirth=rebirth,
        )
        print(f"  stack built in {time.time()-t0:.2f}s")
    except Exception:
        traceback.print_exc()
        return

    # --- 7. observe ---
    step(7, "adapter.observe()  (this is where it usually hangs)")
    t0 = time.time()
    try:
        state = adapter.observe()
        print(f"  observe() took {time.time()-t0:.2f}s")
        print(f"  is_alive   : {state.is_alive}")
        print(f"  modal_open : {state.modal_open}")
        print(f"  events     : {state.events}")
        print(f"  metrics    : {state.metrics}")
    except Exception:
        traceback.print_exc()
        return

    # --- 8. actions ---
    step(8, "adapter.available_actions()")
    try:
        candidates = adapter.available_actions(state)
        print(f"  {len(candidates)} candidate(s):")
        for a in candidates:
            print(f"    {a.name:20s} [{a.category}]")
    except Exception:
        traceback.print_exc()
        return

    if not candidates:
        print("\n  !!! No candidates - the loop will idle forever.")
        return

    # --- 9. policy ---
    step(9, "choose_action()")
    ctx = {"last_used": {}, "fails": {}}
    try:
        action = choose_action(state, candidates, ctx)
        if action is None:
            print("  choose_action() -> None")
            print("  cooldowns:")
            for a in candidates:
                print(f"    {a.name:20s} cd={COOLDOWNS.get(a.name, 0.0)}")
        else:
            print(f"  choose_action() -> {action.name}")
            print("\n  OK: the loop WOULD execute this action.")
    except Exception:
        traceback.print_exc()
        return

    print("\n" + "=" * 60)
    print("   DIAGNOSTICS DONE (nothing was clicked)")
    print("=" * 60)


if __name__ == "__main__":
    main()