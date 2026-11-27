"""The structural guarantee of the 2026-11-27 pivot: no game layer exists.

Since the game code was physically deleted, the guarantee is the ABSENCE of
code: this test re-checks the tree for any mechanism that could launch the
game, send input to it, or read its HUD. Historical mentions in prose
(docstrings, CHANGELOG, BUILDER_LOG) are allowed and expected; capability
tokens are not.

This file is the successor of tests/test_input_guard.py: the kill switch
(GAME_INPUT_ENABLED / InputController) is gone together with the input code,
so what is pinned here is that nothing like it ever comes back quietly.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Directories with production code and assets (tests and prose docs excluded).
SCAN_DIRS = ("alontito", "brain", "core", "pult", "tools", "evaluation", "training")
SCAN_SUFFIXES = (".py", ".js", ".html", ".css", ".json", ".bat", ".ps1")
SCAN_EXTRA_FILES = (
    "config.py", "config_omni.json", "alontito.py",
    "requirements.txt", "requirements-training.txt",
    "Alontito.bat", "Alontito_3D_Pet.bat", "Alontito_MCP.bat", "Pult.bat",
)
EXCLUDE_PARTS = ("pet3d" + "/" + "vendor",)  # vendored three.js is not our code

# Tokens that only make sense if a game layer exists again: launching,
# input injection, HUD reading or the old kill switch.
FORBIDDEN_TOKENS = (
    "roblox://",
    "placeId",
    "placeid:",
    "3623096087",            # Muscle Legends place id
    "1268927906",            # universe id
    "sendinput",
    "game_input_enabled",
    "ensure_focused",
    "allowed_click_rois",
    "scan_codes",
    "roblox_watch_back",
    "roblox_exes",
    "robloxplayerbeta",
    "os.startfile",          # launch mechanism: pc_actions uses subprocess-free approved paths
)


def _iter_files():
    for rel in SCAN_EXTRA_FILES:
        p = ROOT / rel
        if p.exists():
            yield p
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_SUFFIXES:
                continue
            if "vendor" in p.parts:
                continue
            yield p


class TestNoGameLayer(unittest.TestCase):
    def test_no_capability_tokens_in_production_files(self):
        offenders = []
        for path in _iter_files():
            try:
                text = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            for token in FORBIDDEN_TOKENS:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(offenders, [],
                         "game-layer mechanism returned to the tree: "
                         + "; ".join(offenders))

    def test_deleted_modules_stay_deleted(self):
        gone = [
            "games", "vision", "dashboard", "knowledge", "alontito-app",
            "agent_actions.py", "run_adapter.py", "run_action.py",
            "main.py", "eye.py", "alontito/games_catalog.py",
            "core/input_controller.py", "core/window.py",
            "brain/mind.py", "brain/watcher.py", "brain/skill_autonomy.py",
            "tools/harvest_glyphs.py",
        ]
        still_there = [rel for rel in gone if (ROOT / rel).exists()]
        self.assertEqual(still_there, [],
                         "deleted game modules reappeared")

    def test_actions_allowlist_is_empty(self):
        from alontito.skills import ALLOWED_ACTIONS
        self.assertEqual(ALLOWED_ACTIONS, set())

    def test_memory_has_no_game_selection(self):
        from alontito.memory import Memory
        import tempfile
        memory = Memory(Path(tempfile.mkdtemp()) / "memory.json")
        self.assertNotIn("autostart", memory.data)
        self.assertNotIn("selected_game", memory.data)


if __name__ == "__main__":
    unittest.main()
