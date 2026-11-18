"""
Navigator: generic self-walking intelligence for Muscle Legends.

The bot walks ITSELF around the world, discovers interactables (E-prompts with
machine/egg names), REMEMBERS where they were found (location map persisted in
data/memory.json), and can return to discovered spots. This is how the bot
adapts to game updates: a NEW machine or egg stand is simply a new discovery on
the map - no code change needed.

Danger rules (from live sessions):
  - never long-walk when a boss event banner is on screen;
  - escape pattern when the camera is inside geometry (black-fraction metric);
  - jump while walking (unstuck).
"""
import json
import time
import random
from typing import Dict, List, Optional, Tuple

from config import DATA_DIR, ROIS
from vision.ocr_engine import CYRILLIC_MAP

MAP_FILE = DATA_DIR / "location_map.json"

DIRECTIONS = ["W", "D", "S", "A"]


class Navigator:
    def __init__(self, wm, input_ctrl, eye, ocr, memory=None):
        self.wm = wm
        self.input = input_ctrl
        self.eye = eye
        self.ocr = ocr
        self.memory = memory
        self.map = self._load_map()

    # ---------- location map ----------

    def _load_map(self) -> Dict:
        try:
            if MAP_FILE.exists():
                return json.loads(MAP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"interactables": {}, "updated": 0}

    def _save_map(self):
        try:
            self.map["updated"] = time.time()
            MAP_FILE.write_text(json.dumps(self.map, indent=2, ensure_ascii=False),
                                encoding="utf-8")
        except Exception:
            pass

    def remember_interactable(self, name: str):
        """Records that an E-interactable with this name exists in the world."""
        name = (name or "").strip()
        if not name:
            return
        entry = self.map["interactables"].setdefault(
            name, {"found_count": 0, "first_seen": time.strftime("%Y-%m-%d %H:%M")})
        entry["found_count"] += 1
        entry["last_seen"] = time.strftime("%Y-%m-%d %H:%M")
        self._save_map()
        print(f"[NAV] Discovered interactable: '{name}' (x{entry['found_count']})")

    def known_interactables(self) -> List[str]:
        return sorted(self.map.get("interactables", {}).keys())

    # ---------- perception ----------

    def _e_prompt_visible(self) -> bool:
        frame = self.eye.capture_full_window()
        lines = self.ocr.read_lines(self.eye.get_roi_crop("e_prompt", frame), "nav_eprompt")
        return any((l.get("text") or "").strip().upper().translate(CYRILLIC_MAP) == "E"
                   for l in lines)

    def find_machine(self, max_total: float = 8.0,
                     scene: Optional[Dict] = None,
                     want_keywords: Tuple[str, ...] = ()) -> Optional[str]:
        """Searches for an interactable in reach (E-prompt). Returns its name
        or None. Skips entirely when boss danger is active."""
        if self._danger_active(scene):
            print("[NAV] danger - find_machine skipped")
            return None
        return self.discover(seconds=max_total, scene=scene,
                             want_keywords=want_keywords)

    def read_machine_name(self) -> str:
        """Reads the world text near the E-prompt (machine/egg/stand name)."""
        frame = self.eye.capture_full_window()
        raw = self.ocr.read_text_from_image(
            self.eye.get_roi_crop("machine_info", frame), "nav_name")
        return (raw or "").strip()

    def _camera_blocked(self) -> bool:
        """Camera inside geometry: too many near-black pixels."""
        frame = self.eye.capture_full_window()
        small = frame.convert("L").resize((96, 54))
        px = list(small.getdata())
        black = sum(1 for p in px if p < 20) / len(px)
        return black > 0.45

    def _danger_active(self, scene: Optional[Dict]) -> bool:
        return bool(scene and set(scene.get("events", [])) & {"boss"})

    # ---------- movement ----------

    def walk(self, direction: str, seconds: float, jump_every: float = 1.6):
        """Walks in a direction, jumping periodically to unstick."""
        moved = 0.0
        self.input.key_down(direction)
        while moved < seconds:
            step = min(0.4, seconds - moved)
            time.sleep(step)
            moved += step
            if jump_every > 0 and int(moved / jump_every) != int((moved - step) / jump_every):
                self.input.key_up(direction)
                self.input.tap_key("SPACE", 0.1)
                time.sleep(0.15)
                self.input.key_down(direction)
        self.input.key_up(direction)
        time.sleep(0.1)

    def step_turn(self, direction: str, degrees: float = 0):
        """A short step in a direction (degrees ignored - heading via WASD)."""
        self.walk(direction, min(0.5, max(0.2, degrees / 180.0)), jump_every=0)

    def unstuck(self, scene: Optional[Dict] = None):
        """Escape pattern for stuck camera/position. Never runs during boss danger."""
        if self._danger_active(scene):
            print("[NAV] danger - skip unstuck walking")
            return
        print("[NAV] unstuck: jump + directional walk")
        self.input.tap_key("SPACE", 0.12)
        time.sleep(0.3)
        for d in ("W", "A", "S", "D"):
            self.walk(d, 0.7, jump_every=0)
        if self._camera_blocked():
            self.input.tap_key("SPACE", 0.12)
            self.walk("W", 1.5)

    # ---------- discovery ----------

    def discover(self, seconds: float = 8.0, scene: Optional[Dict] = None,
                 want_keywords: Tuple[str, ...] = ()) -> Optional[str]:
        """
        Walks a search pattern and DISCOVERS interactables. Returns the name of
        the interactable in reach (E-prompt visible) or None.
        Every found name is recorded on the location map (self-development).
        """
        if self._danger_active(scene):
            print("[NAV] danger active - discovery skipped")
            return None

        deadline = time.time() + seconds
        dirs = ["W", "D", "S", "A"]
        i = random.Random(int(time.time())).randrange(4)  # vary start direction
        found_name = None
        while time.time() < deadline and found_name is None:
            d = dirs[i % len(dirs)]
            i += 1
            self.input.key_down(d)
            reached = False
            for _ in range(4):
                time.sleep(0.3)
                if self._e_prompt_visible():
                    reached = True
                    break
            self.input.key_up(d)
            time.sleep(0.1)
            if reached:
                name = self.read_machine_name()
                self.remember_interactable(name or "unknown")
                if want_keywords and not any(k in (name or "").lower()
                                             for k in want_keywords):
                    # wrong interactable - step aside and keep searching
                    self.walk(random.choice(("A", "D")), 0.8, jump_every=0)
                    continue
                found_name = name or "unknown"
                break
            self.input.tap_key("SPACE", 0.1)  # hop while walking
            time.sleep(0.15)
        return found_name
