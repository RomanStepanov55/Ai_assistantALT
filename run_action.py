"""
Read-only HUD runner - for testing perception and for the AI agent operator.

  python run_action.py --hud         # read HUD via OCR
  python run_action.py --strength    # read strength only
  python run_action.py --observe     # one watcher observation (prints scene JSON)

HARD RULE #2 (user decision, 2026-11-24): the agent never plays for the user,
so the former action flags (--close-modal/--gift/--quests/--wheel/--rebirth)
are removed. Input to the game is blocked at the InputController level.
"""
import argparse
import json

from main import build_stack


def main():
    p = argparse.ArgumentParser(description="Read-only HUD runner (Muscle Legends)")
    p.add_argument("--hud", action="store_true")
    p.add_argument("--strength", action="store_true")
    p.add_argument("--observe", action="store_true")
    a = p.parse_args()

    wm, eye, ocr, detector, watcher = build_stack()

    if a.strength:
        hud = detector.extract_full_hud_state()
        print("Strength:", hud.get("strength"))
    elif a.hud:
        hud = detector.extract_full_hud_state()
        print(json.dumps(hud, indent=2, ensure_ascii=False, default=str))
    elif a.observe:
        state = watcher.observe()
        print(json.dumps(state, indent=2, ensure_ascii=False))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
