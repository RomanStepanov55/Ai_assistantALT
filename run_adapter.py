"""
Universal adapter runner (verbose).

Usage:
    python run_adapter.py
    python run_adapter.py --max-actions 10
    python run_adapter.py --tick        # print every loop tick

Never silent: if it idles, it says why.
"""

import argparse
import time

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
from brain.adapter_policy import choose_action

HEARTBEAT_SEC = 10.0
SLOW_OBSERVE_SEC = 5.0


def build_adapter_stack():
    print("[INIT] building stack...")
    t0 = time.time()

    wm = WindowManager()
    print("[INIT] WindowManager ok")

    input_ctrl = InputController()
    eye = ScreenEye(wm)
    print("[INIT] InputController + ScreenEye ok")

    ocr = OCREngine()
    print(f"[INIT] OCREngine ok ({time.time()-t0:.1f}s)")

    detector = GameDetector(eye, ocr)
    watcher = Watcher(wm, eye, ocr)
    memory = LearningMemory()

    actions = AgentActions(
        wm=wm, input_ctrl=input_ctrl, eye=eye,
        ocr=ocr, detector=detector, memory=memory,
    )
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

    print(f"[INIT] stack ready in {time.time()-t0:.1f}s")
    return adapter, wm


def main():
    parser = argparse.ArgumentParser(description="Universal Roblox GameAdapter runner")
    parser.add_argument("--max-actions", type=int, default=0,
                        help="Stop after N executed actions (0 = forever)")
    parser.add_argument("--tick", action="store_true",
                        help="Print every loop tick")
    args = parser.parse_args()

    print("=" * 65)
    print("   UNIVERSAL ADAPTER RUNNER")
    print("   GOLDEN RULE: no purchases, no cards, no billing - ever.")
    print("=" * 65)

    adapter, wm = build_adapter_stack()
    adapter.on_session_start()

    ctx = {"last_used": {}, "fails": {}}
    stats = {
        "started": time.time(),
        "actions": 0, "success": 0, "failed": 0,
        "interrupts": 0, "no_window": 0, "idle": 0,
    }

    last_heartbeat = 0.0
    last_status = "starting"
    last_candidates = 0
    last_state_txt = "?"

    print("[ADAPTER] Runner started. Entering main loop.")
    print("[ADAPTER] If nothing happens, a heartbeat is printed every 10s.")

    try:
        while True:
            now = time.time()

            if args.max_actions and stats["actions"] >= args.max_actions:
                print("[ADAPTER] --max-actions reached, stopping.")
                break

            # ---- heartbeat ----
            if now - last_heartbeat >= HEARTBEAT_SEC:
                up = int(now - stats["started"])
                print(
                    f"[HEARTBEAT {up:>5d}s] status={last_status} | "
                    f"state={last_state_txt} | candidates={last_candidates} | "
                    f"actions={stats['actions']} ok={stats['success']} "
                    f"fail={stats['failed']} idle={stats['idle']} "
                    f"nowin={stats['no_window']}"
                )
                last_heartbeat = now

            # ---- window ----
            if not wm.ensure_focused():
                stats["no_window"] += 1
                if last_status != "no_window":
                    print("[WAIT] Roblox window not found or not focusable. "
                          "Start the game / restore the window.")
                last_status = "no_window"
                time.sleep(2.0)
                continue

            # ---- observe ----
            t_obs = time.time()
            try:
                state = adapter.observe()
            except Exception as e:
                print(f"[ADAPTER] observe error: {e}")
                last_status = "observe_error"
                time.sleep(2.0)
                continue

            obs_dt = time.time() - t_obs
            if obs_dt > SLOW_OBSERVE_SEC:
                print(f"[SLOW] observe() took {obs_dt:.1f}s (OCR is cold or stuck)")

            last_state_txt = (
                f"alive={state.is_alive} modal={state.modal_open} "
                f"str={state.metrics.get('strength', 0)} "
                f"ev={','.join(state.events) or '-'}"
            )
            if args.tick:
                print(f"[TICK] {last_state_txt}  ({obs_dt:.2f}s)")

            # ---- interrupts ----
            try:
                handled = adapter.handle_interrupts(state)
                if handled:
                    stats["interrupts"] += 1
                    print(f"[ADAPTER] interrupt: {handled}")
                    last_status = f"interrupt:{handled}"
                    time.sleep(1.0)
                    continue
            except Exception as e:
                print(f"[ADAPTER] interrupt error: {e}")

            # ---- choose ----
            try:
                candidates = adapter.available_actions(state)
                last_candidates = len(candidates)
                action = choose_action(state, candidates, ctx)
            except Exception as e:
                print(f"[ADAPTER] choose_action error: {e}")
                last_status = "choose_error"
                time.sleep(1.0)
                continue

            if not candidates:
                stats["idle"] += 1
                if last_status != "no_candidates":
                    print("[IDLE] adapter returned 0 available actions.")
                last_status = "no_candidates"
                time.sleep(1.0)
                continue

            if action is None:
                stats["idle"] += 1
                if last_status != "cooldown":
                    print(f"[IDLE] all {len(candidates)} actions on cooldown.")
                last_status = "cooldown"
                time.sleep(1.0)
                continue

            # ---- execute ----
            print(f"[ADAPTER] chosen: {action.name} | {action.description}")
            last_status = f"exec:{action.name}"

            try:
                result = adapter.execute_action(action, state)
            except Exception as e:
                print(f"[ADAPTER] execute error in {action.name}: {e}")
                ctx["fails"][action.name] = ctx["fails"].get(action.name, 0) + 1
                stats["failed"] += 1
                time.sleep(1.0)
                continue

            stats["actions"] += 1
            ctx["last_used"][action.name] = time.time()

            if result.success:
                stats["success"] += 1
                ctx["fails"][action.name] = 0
            else:
                stats["failed"] += 1
                ctx["fails"][action.name] = ctx["fails"].get(action.name, 0) + 1

            print(
                f"[ADAPTER] {action.name}: "
                f"{'OK' if result.success else 'no result'} | "
                f"reward={result.reward:.3f} | "
                f"cost={result.cost_seconds:.1f}s | "
                f"{result.detail}"
            )

            time.sleep(0.7)

    except KeyboardInterrupt:
        print("\n[ADAPTER] Stopped by user.")

    finally:
        stats["duration_sec"] = round(time.time() - stats["started"], 1)
        try:
            adapter.on_session_end(stats)
        except Exception:
            pass

        print("[ADAPTER] Session stats:")
        for k in ("actions", "success", "failed", "interrupts",
                  "idle", "no_window", "duration_sec"):
            print(f"  {k:12s}: {stats[k]}")


if __name__ == "__main__":
    main()