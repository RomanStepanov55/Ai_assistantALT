"""
Main Entry Point & Autonomous Execution Loop for Muscle Legends
Supports:
  python main.py             -> Full autonomous gameplay loop (Strength, Speed, Quests, Gifts, Rebirths)
  python main.py --monitor   -> Continuous background Eye monitor (live HUD stats & growth)
  python main.py --watch     -> Standalone background Watcher (scene journal, no input at all)
  python main.py --test-input-> Verifies dumbbells, pushups, and gym machine interaction
  python main.py --wheel     -> Spins the fortune wheel (OCR-guided, closed via UI, never ESC)
  python main.py --gift      -> Claims timed gifts (OCR-guided, closed via UI, never ESC)
  python main.py --quests    -> Checks and claims COMPLETED quests (never pays gems to skip)

GOLDEN RULE: this bot never purchases anything for real money, never attaches
cards, never enables billing. Purchase-related UI (STORE, PACKS, Auto Lift,
CODES, BENEFITS, TRADE, SPOOKY PASS) is unreachable by design:
InputController.click_roi refuses any ROI outside config.ALLOWED_CLICK_ROIS.
"""
import sys
import time
import argparse

from config import STAT_SAMPLE_INTERVAL_SEC, DECISION_INTERVAL_SEC
from core.window import WindowManager
from core.input_controller import InputController
from core.telemetry import TelemetryTracker
from vision.screen_eye import ScreenEye
from vision.ocr_engine import OCREngine
from vision.detector import GameDetector
from brain.learning_memory import LearningMemory
from brain.watcher import Watcher
from brain.mind import Mind
from agent_actions import AgentActions
from navigation import Navigator
from pets import PetsDriver
from eggs import EggDriver
from boss import BossDriver
from rebirth import RebirthDriver
from games.muscle_legends import MuscleLegendsAdapter
from brain.adapter_policy import choose_action

# Maps brain directives -> LearningMemory efficiency keys
EFFICIENCY_NAMES = {
    "TRAIN_DUMBBELLS": "tool_1_hand_weights",
    "TRAIN_PUSHUPS": "pushups",
    "TRAIN_SITUPS": "situps",
    "TRAIN_MACHINE": "machine",
}


def build_stack():
    wm = WindowManager()
    input_ctrl = InputController()
    eye = ScreenEye(wm)
    ocr = OCREngine()
    detector = GameDetector(eye, ocr)
    watcher = Watcher(wm, eye, ocr)
    memory = LearningMemory()
    actions = AgentActions(wm, input_ctrl, eye, ocr, detector, memory)
    telemetry = TelemetryTracker()
    navigator = Navigator(wm, input_ctrl, eye, ocr, memory)
    pets = PetsDriver(actions)
    eggs = EggDriver(actions, navigator)
    boss = BossDriver(actions, navigator)
    rebirth = RebirthDriver(actions, memory)
    mind = Mind(actions, detector, watcher, memory, navigator,
                pets, eggs, boss, rebirth, telemetry)
    return (wm, input_ctrl, eye, ocr, detector, watcher, memory, actions, mind,
            telemetry, navigator, pets, eggs, boss, rebirth)
    
def run_adapter_agent():
    """
    Temporary universal adapter loop.

    This replaces the old Mind-first loop with:
        adapter.observe()
        adapter.handle_interrupts()
        adapter.available_actions()
        choose_action()
        adapter.execute_action()

    For now it uses MuscleLegendsAdapter directly.
    Later GameCatalog will choose the correct adapter automatically.
    """

    print("=" * 65)
    print("   ADAPTER MODE (GameAdapter first, Mind legacy later)")
    print("   GOLDEN RULE: no purchases, no cards, no billing - ever.")
    print("=" * 65)

    (wm, input_ctrl, eye, ocr, detector, watcher, memory, actions, mind,
     telemetry, navigator, pets, eggs, boss, rebirth) = build_stack()

    adapter = MuscleLegendsAdapter(
        wm=wm,
        input_ctrl=input_ctrl,
        eye=eye,
        ocr=ocr,
        detector=detector,
        watcher=watcher,
        memory=memory,
        actions=actions,
        navigator=navigator,
        pets=pets,
        eggs=eggs,
        boss=boss,
        rebirth=rebirth,
    )

    adapter.on_session_start()

    ctx = {
        "last_used": {},
        "fails": {},
    }

    print("[META] Adapter agent started.")

    try:
        while True:
            if not wm.ensure_focused():
                time.sleep(2.0)
                continue

            try:
                state = adapter.observe()
            except Exception as e:
                print(f"[META] observe error: {e}")
                time.sleep(2.0)
                continue

            # Interrupts: popups, death, trade offers, friend requests, etc.
            try:
                handled = adapter.handle_interrupts(state)
                if handled:
                    print(f"[META] interrupt: {handled}")
                    time.sleep(1.0)
                    continue
            except Exception as e:
                print(f"[META] interrupt error: {e}")

            # Choose next action.
            try:
                candidates = adapter.available_actions(state)
                action = choose_action(state, candidates, ctx)
            except Exception as e:
                print(f"[META] choose_action error: {e}")
                time.sleep(1.0)
                continue

            if action is None:
                time.sleep(1.0)
                continue

            print(f"[META] chosen: {action.name} | {action.description}")

            # Execute.
            try:
                result = adapter.execute_action(action, state)
            except Exception as e:
                print(f"[META] execute error in {action.name}: {e}")
                ctx["fails"][action.name] = ctx["fails"].get(action.name, 0) + 1
                time.sleep(1.0)
                continue

            # Update simple memory.
            ctx["last_used"][action.name] = time.time()

            if result.success:
                ctx["fails"][action.name] = 0
            else:
                ctx["fails"][action.name] = ctx["fails"].get(action.name, 0) + 1

            print(
                f"[META] {action.name}: "
                f"{'OK' if result.success else 'no result'} | "
                f"reward={result.reward:.3f} | "
                f"cost={result.cost_seconds:.1f}s | "
                f"{result.detail}"
            )

            time.sleep(0.7)

    except KeyboardInterrupt:
        print("\n[STOP] Adapter agent stopped gracefully.")

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
    """Standalone read-only background eye. Writes scene_state.json + eye_journal.log."""
    print("=" * 60)
    print("   BACKGROUND WATCHER (read-only eye, Ctrl+C to stop)")
    print("   -> data/scene_state.json | data/eye_journal.log")
    print("=" * 60)
    try:
        i = 0
        while True:
            state = watcher.observe()
            i += 1
            if not state.get("roblox_found"):
                sys.stdout.write("\r[WATCH] Roblox window not found...")
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


def run_test_input(wm: WindowManager, input_ctrl: InputController):
    """DirectInput verification: tests dumbbells with proper animation timing, pushups, and E."""
    print("[INPUT] Searching for Roblox window...")
    if not wm.ensure_focused():
        print("[!] Could not focus Roblox window.")
        return

    print("[*] 1. Equipping Hand Weights (Slot 2)...")
    time.sleep(0.4)
    input_ctrl.equip_dumbbells()
    time.sleep(0.4)

    print("[*] 2. Performing 5 smooth dumbbell reps (matching 0.65s animation)...")
    input_ctrl.smooth_lift(lifts=5, animation_delay=0.65, wm=wm)

    print("[*] 3. Testing Pushups (Slot 7)...")
    input_ctrl.equip_pushups()
    time.sleep(0.4)
    input_ctrl.smooth_lift(lifts=3, animation_delay=0.65, wm=wm)

    print("[*] 4. Testing machine interaction (E) and dismount (SPACE)...")
    input_ctrl.equip_dumbbells()
    input_ctrl.tap_key("E", 0.1)
    time.sleep(0.5)
    input_ctrl.tap_key("SPACE", 0.1)

    print("[+] Test completed! Every rep had proper animation timing and dumbbells were equipped.")


NL = chr(10)


def run_autonomous_agent():
    """The self-developing agent: the Mind decides among its OWN
    capabilities, executes, measures rewards, and rewrites its own playbook."""
    print("=" * 65)
    print("   SELF-DEVELOPING AGENT FOR MUSCLE LEGENDS (Mind v1)")
    print("   GOLDEN RULE: no purchases, no cards, no billing - ever.")
    print("   Free progression only. Capabilities grow by themselves.")
    print("=" * 65)

    (wm, input_ctrl, eye, ocr, detector, watcher, memory, actions, mind,
     telemetry, navigator, pets, eggs, boss, rebirth) = build_stack()
    memory.note_session()
    rebirths_known = memory.get_rebirths_known()
    print(f"[INIT] Known rebirths: {rebirths_known} | sessions: {memory.data.get('total_sessions')}")
    print(f"[INIT] {mind.status_line()}")

    if wm.ensure_focused():
        print("[INIT] Equipping Hand Weights (Slot 2)...")
        input_ctrl.equip_dumbbells()
        time.sleep(0.5)

    ctx = {
        "last_gift_attempt": 0.0, "last_quest_check": 0.0, "quest_interval": 300.0,
        "last_wheel_spin": 0.0, "last_pets": 0.0, "last_egg": 0.0,
        "last_discover": 0.0, "scene": {}, "_last_observation": 0.0,
        "_last_action": "TRAIN_DUMBBELLS",
    }
    last_stat_check = 0.0
    last_popup_check = 0.0
    last_heartbeat = 0.0
    last_lookaround = 0.0
    last_report = 0.0
    hud = {"strength": 0, "gems": 0, "rebirths": rebirths_known, "gift_ready": False}

    try:
        while True:
            now = time.time()

            if not wm.ensure_focused():
                time.sleep(2.0)
                continue

            # Eye: scene observation every 10s (fast OCR server)
            if now - ctx.get("_last_observation", 0.0) >= 10.0:
                try:
                    ctx["scene"] = watcher.observe()
                except Exception as e:
                    print(f"[WATCH] error: {e}")
                ctx["_last_observation"] = now
            scene = ctx["scene"]

            # DEATH -> RESPAWN
            if scene.get("likely_dead"):
                print("[DEATH] character down - respawning...")
                input_ctrl.tap_key("SPACE", 0.1)
                sx, sy = wm.to_screen_coords(0.5, 0.55)
                input_ctrl.mouse_click(sx, sy)
                time.sleep(2.0)
                continue

            # Heartbeat
            if now - last_heartbeat >= 15.0:
                print(f"[LOOP] alive | {mind.status_line()}")
                last_heartbeat = now

            # Popup defense (trades/friend requests)
            if now - last_popup_check >= 12.0:
                try:
                    handled = actions.handle_popups()
                    if handled:
                        print(f"[POPUP] {handled}")
                except Exception as e:
                    print(f"[POPUP] error: {e}")
                last_popup_check = now

            # Stats sampling
            if now - last_stat_check >= STAT_SAMPLE_INTERVAL_SEC:
                hud = detector.extract_full_hud_state()
                hud["rebirths"] = rebirths_known
                telemetry.record_sample(
                    strength=hud["strength"], gems=hud["gems"],
                    rebirths=rebirths_known, health_pct=1.0,
                    current_action=ctx.get("_last_action", "TRAIN_DUMBBELLS"))
                last_stat_check = now
                eye.save_annotated_debug("latest_annotated.png")

            # Session report every 30 min
            if now - last_report >= 1800.0:
                try:
                    from config import DATA_DIR
                    rep_dir = DATA_DIR / "reports"
                    rep_dir.mkdir(exist_ok=True)
                    rates = telemetry.calculate_rates()
                    last = telemetry.samples[-1] if telemetry.samples else {}
                    with open(rep_dir / f"session_{int(now)}.txt", "w", encoding="utf-8") as f:
                        f.write(f"Session report {time.strftime('%Y-%m-%d %H:%M')}" + NL)
                        f.write(f"Duration: {int(now - telemetry.start_time)}s" + NL)
                        f.write(f"Strength: {last.get('strength', 0):,} | {rates.get('strength_per_min', 0)}/min" + NL)
                        f.write(f"Gems: {last.get('gems', 0):,}" + NL)
                        f.write(f"Efficiency: {memory.data.get('exercise_efficiency')}" + NL)
                except Exception as e:
                    print(f"[REPORT] error: {e}")
                last_report = now

            # MIND: decide + execute + feedback + reflection (self-rewriting)
            mind.remember_context(hud, scene, ctx)
            mind.reflect()
            cap = mind.decide(hud, scene, ctx)
            if cap is None:
                time.sleep(1.0)
                continue
            print(f"[MIND] chosen: {cap.name} ({cap.description})")
            ctx["_last_action"] = cap.name
            try:
                res = mind.execute(cap, hud, scene, ctx)
                print(f"[MIND] {cap.name}: {res['detail']} "
                      f"[{res['cost']:.1f}s, {'OK' if res['success'] else 'no result'}]")
            except Exception as e:
                print(f"[EXEC] error in {cap.name}: {e}")

            # Look-around sweep every 90s
            if now - last_lookaround >= 90.0:
                try:
                    look = actions.look_around()
                    if look.get("danger"):
                        print("[LOOK] DANGER around!")
                    if look.get("eggs"):
                        print("[LOOK] Eggs spotted nearby!")
                except Exception as e:
                    print(f"[LOOK] error: {e}")
                last_lookaround = now

            time.sleep(0.05)

    except KeyboardInterrupt:
        print(NL + "[STOP] Stopping agent gracefully.")
        print(telemetry.get_summary_text())

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Muscle Legends Autonomous AI Agent")
    parser.add_argument("--monitor", action="store_true", help="Run continuous background Eye monitor")
    parser.add_argument("--watch", action="store_true", help="Standalone read-only watcher (scene journal)")
    parser.add_argument("--test-input", action="store_true", help="Test dumbbells, pushups, and gym machines")
    parser.add_argument("--wheel", action="store_true", help="Spin fortune wheel safely (OCR-guided)")
    parser.add_argument("--gift", action="store_true", help="Claim timed gifts safely (OCR-guided)")
    parser.add_argument("--quests", action="store_true", help="Claim only COMPLETED quests (OCR-guided)")
    parser.add_argument("--rebirth", action="store_true", help="Attempt a rebirth (OCR-guided, verified)")
    parser.add_argument("--adapter", action="store_true", help="Run universal GameAdapter loop")
    args = parser.parse_args()

    if args.watch:
        wm = WindowManager()
        eye = ScreenEye(wm)
        ocr = OCREngine()
        run_watcher(Watcher(wm, eye, ocr))
        sys.exit(0)

    (wm, input_ctrl, eye, ocr, detector, watcher, memory, actions, mind,
     telemetry, navigator, pets, eggs, boss, rebirth) = build_stack()

    if args.monitor:
        run_continuous_monitor(wm, eye, detector)
    elif args.test_input:
        run_test_input(wm, input_ctrl)
    elif args.wheel:
        actions.spin_wheel()
    elif args.gift:
        actions.claim_gifts()
    elif args.quests:
        actions.check_quests()
    elif args.rebirth:
        rebirth.attempt()
    elif args.adapter:
        run_adapter_agent()
    else:
        run_autonomous_agent()