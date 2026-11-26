"""
MuscleLegendsAdapter — wraps the existing Muscle Legends bot as a GameAdapter.

This is the bridge: all existing code (AgentActions, ScreenEye, OCR,
Navigator, Pets, Eggs, Boss, Rebirth) is reused here, but exposed through
the universal GameAdapter interface.

Important:
- this adapter must not contain global game logic;
- global choosing logic lives in brain/adapter_policy.py or future MetaBrain;
- all Muscle Legends specifics live here.
"""

import time
from typing import Dict, List, Optional

from games.game_adapter import (
    GameAdapter,
    GameIdentity,
    GameState,
    GameAction,
    ActionResult,
)
from vision.ocr_engine import CYRILLIC_MAP
from config import LIFTS_PER_SET


class MuscleLegendsAdapter(GameAdapter):
    """Adapter wrapping the existing Muscle Legends bot infrastructure."""

    ACTION_MAP = {
        "CLOSE_MODAL":      ("manage",  "Безопасно закрыть модалку через OCR/X"),
        "TRAIN_DUMBBELLS":  ("train",   "Жим гантелей (Slot 2) — основной источник силы"),
        "TRAIN_PUSHUPS":    ("train",   "Отжимания (Slot 7)"),
        "TRAIN_SITUPS":     ("train",   "Приседания (Slot 8)"),
        "TRAIN_MACHINE":    ("train",   "Тренажёр (E): жим/дедлифт — высокий множитель"),
        "TRAIN_AGILITY":    ("train",   "Беговая дорожка (E) или спринт"),
        "CLAIM_GIFTS":      ("collect", "Собрать накопленный timed gift (бесплатно)"),
        "CHECK_QUESTS":     ("collect", "Проверить квесты и забрать выполненные"),
        "SPIN_WHEEL":       ("collect", "Бесплатный спин колеса ENCHANT PETS"),
        "DAILY_REWARDS":    ("collect", "Ежедневные награды"),
        "HATCH_EGG":        ("manage",  "Открыть яйцо (выше пола гемов)"),
        "PETS_MAINTENANCE": ("manage",  "Экипировать лучших питомцев"),
        "BOSS_FIGHT":       ("explore", "Найти и уничтожить босса"),
        "DISCOVER":         ("explore", "Разведка: искать новые тренажёры/стенды"),
        "REBIRTH":          ("manage",  "Ребёрн (бесплатная механика)"),
    }

    # Only these actions should pay the cost of reading strength/gems deltas.
    # read_strength() can be expensive when OCR/template reading retries.
    METRIC_ACTIONS = {
        "TRAIN_DUMBBELLS",
        "TRAIN_PUSHUPS",
        "TRAIN_SITUPS",
        "TRAIN_MACHINE",
        "TRAIN_AGILITY",
        "CLAIM_GIFTS",
        "CHECK_QUESTS",
        "SPIN_WHEEL",
        "DAILY_REWARDS",
        "HATCH_EGG",
        "PETS_MAINTENANCE",
        "REBIRTH",
    }

    # Actions that spend a click burst at the screen centre. A dialog covers
    # that area, so these must not run while one is open.
    CLICK_BURST_ACTIONS = {"TRAIN_DUMBBELLS", "TRAIN_PUSHUPS", "TRAIN_SITUPS"}

    # Actions that involve walking/searching and should be blocked during boss danger.
    DANGER_BLOCKED_ACTIONS = {
        "TRAIN_MACHINE",
        "TRAIN_AGILITY",
        "DISCOVER",
        "HATCH_EGG",
    }

    # Do not OCR scene more often than this unless invalidated.
    STATE_TTL = 3.0

    # Popup defense should not run every single loop tick.
    POPUP_INTERVAL = 10.0

    def __init__(
        self,
        wm,
        input_ctrl,
        eye,
        ocr,
        detector,
        watcher,
        memory,
        actions,
        navigator,
        pets,
        eggs,
        boss,
        rebirth,
    ):
        self.wm = wm
        self.input_ctrl = input_ctrl
        self.eye = eye
        self.ocr = ocr
        self.detector = detector
        self.watcher = watcher
        self.memory = memory
        self.actions = actions
        self.nav = navigator
        self.pets = pets
        self.eggs = eggs
        self.boss = boss
        self.rebirth_drv = rebirth

        self._last_scene: Dict = {}
        self._state_cache: Optional[GameState] = None
        self._state_ts = 0.0
        self._last_popup_ts = 0.0

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def identity(self) -> GameIdentity:
        return GameIdentity(
            name="Muscle Legends",
            place_id=4458455378,
            genre="simulator",
            tags=["clicker", "pets", "rebirth", "training", "gym"],
            url="https://www.roblox.com/games/4458455378/Muscle-Legends",
        )

    def detect_active(self) -> bool:
        # NOTE: this is still weak. Later we need a real visual fingerprint.
        # For now: any Roblox window is enough to try the ML adapter.
        return bool(self.wm.find_roblox_window())

    # ------------------------------------------------------------------
    # Perception
    # ------------------------------------------------------------------

    def observe(self) -> GameState:
        now = time.time()

        if self._state_cache is not None and now - self._state_ts < self.STATE_TTL:
            return self._state_cache

        scene = self.watcher.observe()
        self._last_scene = scene

        state = GameState(
            timestamp=now,
            is_alive=not scene.get("likely_dead", False),
            metrics={
                # None when the badge was not read (window closed / covered /
                # unreadable frame). Fabricating 0 here reported "сила 0 ·
                # гемы 0" for a game that was never seen - a wrong statement
                # about what the eye saw (violates E-001: not measured ≠ 0).
                "strength": scene.get("strength"),
                "gems": scene.get("gems"),
                "rebirths": self.memory.get_rebirths_known(),
            },
            events=scene.get("events", []),
            modal_open=scene.get("modal_open", False),
        )

        self._state_cache = state
        self._state_ts = now
        return state

    def _invalidate(self) -> None:
        """Force fresh observe() after any meaningful world change."""
        self._state_cache = None

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def available_actions(self, state: GameState) -> List[GameAction]:
        result: List[GameAction] = []

        # Universal action: if a modal is open, close it safely first.
        if state.modal_open:
            result.append(
                GameAction(
                    name="CLOSE_MODAL",
                    description=self.ACTION_MAP["CLOSE_MODAL"][1],
                    category="manage",
                )
            )

        boss_active = "boss" in state.events

        for name, (category, desc) in self.ACTION_MAP.items():
            if name == "CLOSE_MODAL":
                continue

            # During boss danger, avoid walking/search actions.
            if boss_active and name in self.DANGER_BLOCKED_ACTIONS:
                continue

            # Preconditions.
            if name == "REBIRTH":
                from config import FEATURE_REBIRTH, get_rebirth_requirement

                if not FEATURE_REBIRTH:
                    continue

                req = get_rebirth_requirement(state.metrics.get("rebirths", 0))
                if req <= 0 or state.metrics.get("strength", 0) < req:
                    continue

            if name == "BOSS_FIGHT":
                if not boss_active:
                    continue

            result.append(
                GameAction(
                    name=name,
                    description=desc,
                    category=category,
                )
            )

        return result

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    @staticmethod
    def _lift_outcome(gain: Optional[int]):
        """Maps smart_lift's tri-state to (success, detail).

        None  -> the badge could not be read: the reps were executed, so this is
                 not a failure, but it is NOT evidence of progress either.
        0     -> verified: the game ignored the set (trap/stuck) -> real failure.
        > 0   -> verified progress.
        """
        if gain is None:
            return True, "gain unverified (badge unreadable)"
        if gain > 0:
            return True, f"gain=+{gain:,}"
        return False, "gain=0 - set ignored (trap/stuck)"

    def execute_action(self, action: GameAction, state: GameState) -> ActionResult:
        t0 = time.time()
        name = action.name

        measure = name in self.METRIC_ACTIONS

        # HARD GATE before ANY action: a verified game HUD must be in the
        # current frame. Measured 2026-11-17: after the session ended the
        # Roblox client showed its music page and the training action still
        # fired 80 clicks into it. Reads and clicks are now gated identically.
        frame = None
        try:
            frame = self.actions.eye.capture_full_window()
        except Exception:
            frame = None
        if frame is None or not self.actions.perceiver.frame_is_game(frame):
            self._invalidate()
            return ActionResult(
                success=False, reward=0.0, cost_seconds=time.time() - t0,
                detail="game HUD not visible - action refused",
                error="not_the_game",
            )

        # A dialog swallows centre clicks. Measured 2026-11-17: the brawl
        # invite ("A brawl is starting on Desert Ring, want to join? YES/NO")
        # sits exactly on the click area, and sets 1 and 4 of EXP-02 lost their
        # first 20-rep burst into it (0 gain, ~25 s wasted each). The scene was
        # already observed by the caller, so this costs nothing; the policy
        # closes dialogs first (CLOSE_MODAL priority 1000).
        dialog_open = getattr(state, "modal_open", None)
        if name in self.CLICK_BURST_ACTIONS and dialog_open:
            self._invalidate()
            return ActionResult(
                success=False, reward=0.0, cost_seconds=time.time() - t0,
                detail="a dialog is open - training refused until it is closed",
                error="dialog_open",
            )
        if not isinstance(dialog_open, bool):
            dialog_open = None      # unknown -> let smart_lift measure it

        # Click-burst actions verify their own gain inside smart_lift. Reading
        # strength a second time around the same set produced a contradiction:
        # EXP-03 set 4 reported metrics_delta -438,100 while smart_lift had
        # verified +78,900 for that exact set. Two reads of the same event cost
        # ~2 s and can disagree; one verified number is the honest source.
        self_measured = name in self.CLICK_BURST_ACTIONS

        strength_before = (self.actions.read_strength()
                           if measure and not self_measured else None)
        gems_before = self.actions.read_gems() if measure else None

        verified_gain: Optional[int] = None

        success = False
        detail = ""
        error = ""

        try:
            if name == "CLOSE_MODAL":
                success = bool(self.actions.close_modal_safe())

            elif name == "DAILY_REWARDS":
                success = bool(self._daily_rewards())

            elif name == "TRAIN_DUMBBELLS":
                verified_gain = self.actions.smart_lift(
                    lifts=LIFTS_PER_SET, equip="dumbbells", dialog_open=dialog_open)
                success, detail = self._lift_outcome(verified_gain)

            elif name == "TRAIN_PUSHUPS":
                verified_gain = self.actions.smart_lift(
                    lifts=LIFTS_PER_SET, equip="pushups", dialog_open=dialog_open)
                success, detail = self._lift_outcome(verified_gain)

            elif name == "TRAIN_SITUPS":
                verified_gain = self.actions.smart_lift(
                    lifts=LIFTS_PER_SET, equip="situps", dialog_open=dialog_open)
                success, detail = self._lift_outcome(verified_gain)

            elif name == "TRAIN_MACHINE":
                if self.nav.find_machine(max_total=8.0, scene=self._last_scene):
                    mode = self.actions.machine_session(reps=8)
                    success = mode != "failed"
                    detail = f"machine mode={mode}"
                else:
                    success, detail = self._lift_outcome(
                        self.actions.smart_lift(lifts=4, equip="dumbbells"))

            elif name == "TRAIN_AGILITY":
                success = self._train_agility()
                detail = "agility session"

            elif name == "CLAIM_GIFTS":
                success = bool(self.actions.claim_gifts())

            elif name == "CHECK_QUESTS":
                claims = self.actions.check_quests()
                success = isinstance(claims, list) and len(claims) > 0
                detail = f"claims={len(claims) if isinstance(claims, list) else 0}"

            elif name == "SPIN_WHEEL":
                success = bool(self.actions.spin_wheel())

            elif name == "HATCH_EGG":
                success = bool(self.eggs.hatch_egg(self._last_scene))

            elif name == "PETS_MAINTENANCE":
                ok1 = self.pets.equip_best()
                ok2 = self.pets.delete_weakest(max_deletes=3)
                success = bool(ok1 or ok2)
                detail = f"equip={bool(ok1)}, delete={bool(ok2)}"

            elif name == "BOSS_FIGHT":
                if self.boss.boss_travel_pending(self._last_scene):
                    res = self.boss.fight(self._last_scene)
                    success = bool(res.get("fought"))
                    detail = f"clicks={res.get('clicks', 0)}"
                else:
                    detail = "no travel dialog"

            elif name == "DISCOVER":
                found = self.nav.discover(seconds=8.0, scene=self._last_scene)
                success = bool(found and found != "unknown")
                detail = f"found={found}"

            elif name == "REBIRTH":
                success = bool(self.rebirth_drv.attempt())

            else:
                error = f"Unknown action: {name}"

        except Exception as e:
            error = str(e)
        finally:
            self._invalidate()

        cost = time.time() - t0

        metrics_delta: Dict[str, float] = {}

        if measure:
            if self_measured:
                # One verified number instead of a second, disagreeing read.
                if verified_gain is not None:
                    metrics_delta["strength"] = verified_gain
                    if verified_gain > 0:
                        detail = f"strength +{verified_gain:,}"
            else:
                strength_after = self.actions.read_strength()
                if strength_before is not None and strength_after is not None:
                    d = strength_after - strength_before
                    metrics_delta["strength"] = d
                    if d > 0:
                        detail = f"strength +{d:,}"

            gems_after = self.actions.read_gems()

            if gems_before is not None and gems_after is not None:
                d = gems_after - gems_before
                metrics_delta["gems"] = d
                if d != 0:
                    detail += f" gems {d:+,}"

        reward = max(
            0.0,
            metrics_delta.get("strength", 0) / 1000.0
            + metrics_delta.get("gems", 0) / 10000.0,
        )

        if error:
            detail = error
        elif not detail:
            detail = "ok" if success else "no result"

        return ActionResult(
            success=success,
            reward=reward,
            cost_seconds=cost,
            detail=detail,
            error=error,
            metrics_delta=metrics_delta,
        )

    # ------------------------------------------------------------------
    # Interrupts
    # ------------------------------------------------------------------

    def handle_interrupts(self, state: GameState) -> Optional[str]:
        # Death/respawn.
        if state.is_alive is False:
            self.input_ctrl.tap_key("SPACE", 0.1)
            sx, sy = self.wm.to_screen_coords(0.5, 0.55)
            self.input_ctrl.mouse_click(sx, sy)
            self._invalidate()
            return "respawned"

        now = time.time()

        # If no modal is visible, do not OCR popups every tick.
        if not state.modal_open and now - self._last_popup_ts < self.POPUP_INTERVAL:
            return None

        self._last_popup_ts = now

        try:
            handled = self.actions.handle_popups()
            if handled:
                self._invalidate()
                return f"popup: {handled}"
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # Specific action helpers
    # ------------------------------------------------------------------

    def _train_agility(self) -> bool:
        """
        Safe fallback for TRAIN_AGILITY.

        The old code had:
            AgentActions.train_agility_run()
        but logs showed AttributeError. This method works even if that
        method is missing.
        """
        fn = getattr(self.actions, "train_agility_run", None)
        if callable(fn):
            fn(duration_sec=3.5)
            return True

        fn = getattr(self.input_ctrl, "train_agility_run", None)
        if callable(fn):
            fn(duration_sec=3.5)
            return True

        # Fallback: short forward sprint.
        # This is not perfect, but better than crashing with AttributeError.
        try:
            self.input_ctrl.key_down("W")
            time.sleep(3.0)
        finally:
            self.input_ctrl.key_up("W")

        return True

    def _daily_rewards(self) -> bool:
        """
        Opens the ITEMS panel, looks for DAILY/REWARD text and clicks CLAIM.
        Free only. Never touches purchase buttons.
        """
        if not self.wm.ensure_focused():
            return False

        try:
            self.actions.handle_popups()

            if not self.input_ctrl.click_roi("items_btn", self.wm):
                return False

            time.sleep(1.2)

            lines, frame = self.actions._modal_lines("daily_panel")
            joined = " ".join((l.get("text") or "") for l in lines)
            nj = joined.upper().translate(CYRILLIC_MAP)

            if "DAILY" not in nj and "REWARD" not in nj:
                print("[DAILY] панель не открылась/другая - закрыть.")
                self.actions.close_modal_safe()
                return False

            claimed = 0

            for l in lines:
                if self.actions.ocr.looks_like_claim(l.get("text")):
                    pt = self.actions._line_to_screen("modal_area", l, frame)
                    if pt:
                        self.actions.input.mouse_click(pt[0], pt[1])
                        claimed += 1
                        time.sleep(0.8)

            print(f"[DAILY] claimed {claimed} reward(s).")
            self.actions.close_modal_safe()
            return claimed > 0

        except Exception as e:
            print(f"[DAILY] error: {e}")
            self.actions.close_modal_safe()
            return False

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_session_start(self):
        self.memory.note_session()

        if self.wm.ensure_focused():
            self.input_ctrl.equip_dumbbells()

    def on_session_end(self, stats: Dict):
        print(f"[ML] session end: {stats}")

    def get_progress_metric(self) -> str:
        return "strength"

    def get_genre_hints(self) -> List[str]:
        return [
            "clicker_simulator: click to gain primary stat",
            "rebirth_loop: reset for multiplier when threshold is met",
            "pet_system: equip pets for stat boosts",
            "wheel_spin: free spins for bonuses",
            "quest_system: complete quests for rewards",
        ]