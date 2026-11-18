"""
CLI runner for single safe actions - for testing and for the AI agent operator.

Examples:
  python run_action.py --hud         # read HUD via OCR
  python run_action.py --strength    # read strength only
  python run_action.py --close-modal # close any open modal via X (never ESC)
  python run_action.py --gift
  python run_action.py --quests
  python run_action.py --wheel
  python run_action.py --rebirth
  python run_action.py --observe     # one watcher observation (prints scene JSON)
"""
import argparse
import json

from main import build_stack


def main():
    p = argparse.ArgumentParser(description="Single safe action runner (Muscle Legends)")
    p.add_argument("--hud", action="store_true")
    p.add_argument("--strength", action="store_true")
    p.add_argument("--close-modal", action="store_true", dest="close_modal")
    p.add_argument("--gift", action="store_true")
    p.add_argument("--quests", action="store_true")
    p.add_argument("--wheel", action="store_true")
    p.add_argument("--rebirth", action="store_true")
    p.add_argument("--observe", action="store_true")
    a = p.parse_args()

    stack = build_stack()
    wm, input_ctrl, eye, ocr, detector, watcher, memory, actions, brain, telemetry = stack[:10]

    if a.strength:
        print("Strength:", actions.read_strength())
    elif a.hud:
        hud = detector.extract_full_hud_state()
        print(json.dumps(hud, indent=2, ensure_ascii=False, default=str))
    elif a.close_modal:
        ok = actions.close_modal_safe()
        print("Modal closed:", ok)
    elif a.gift:
        print("Gift claimed:", actions.claim_gifts())
    elif a.quests:
        print("Quest claims:", actions.check_quests())
    elif a.wheel:
        print("Wheel spun:", actions.spin_wheel())
    elif a.rebirth:
        print("Rebirth ok:", actions.trigger_rebirth())
    elif a.observe:
        state = watcher.observe()
        print(json.dumps(state, indent=2, ensure_ascii=False))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
