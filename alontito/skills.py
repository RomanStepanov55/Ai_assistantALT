"""Skills: the things Alontito can actually DO.

Every skill returns a SkillResult whose `reward` is either a verified number or
None. None means "not measured" and is never displayed as zero: the whole
verification layer exists because a fake +1 poisoned the learning data before.

The bot stack is built lazily, so opening Alontito never depends on Roblox
running or on the vision stack importing successfully.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .memory import MISSING, STATUS_MEASURED, STATUS_UNCONFIRMED

# Actions this app is allowed to execute. Everything else stays unreachable
# until the user explicitly turns it on: HATCH_EGG spends gems, PETS_MAINTENANCE
# deletes pets, REBIRTH resets progress.
ALLOWED_ACTIONS = {"TRAIN_DUMBBELLS", "CLOSE_MODAL"}


@dataclass
class SkillResult:
    name: str
    success: bool
    detail: str
    reward: Optional[float] = None
    seconds: float = 0.0
    evidence: str = STATUS_UNCONFIRMED
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    rows: List[Dict] = field(default_factory=list)


class AlontitoBot:
    """Thin, guarded binding to the existing bot stack."""

    def __init__(self):
        self.adapter = None
        self.wm = None
        self.eye = None

    # ------------------------------------------------------------------

    def build(self):
        if self.adapter is not None:
            return self.adapter, self.wm
        import contextlib
        import io
        from run_adapter import build_adapter_stack
        from vision.eye import Eye
        # build_adapter_stack narrates itself on stdout; in a full-screen shell
        # those lines would land in the middle of the panel.
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            self.adapter, self.wm = build_adapter_stack()
        self.eye = Eye(self.wm)
        return self.adapter, self.wm

    def is_attached(self) -> bool:
        try:
            from core.window import WindowManager
            return bool(WindowManager().find_roblox_window())
        except Exception:
            return False

    # ------------------------------------------------------------- skills

    def health(self) -> SkillResult:
        """Read-only: window found + does the current frame prove the game HUD?"""
        t0 = time.time()
        try:
            adapter, wm = self.build()
            if not self.is_attached():
                return SkillResult("health", False,
                                   "окно Roblox не найдено — сначала запусти игру",
                                   seconds=time.time() - t0)
            frame = self.eye.capture()
            if frame is None:
                return SkillResult("health", False, "кадр не получен",
                                   seconds=time.time() - t0)
            is_game = self.eye.frame_is_game(frame)
            strength = self.eye.read_once("strength", frame) if is_game else {}
            gems = self.eye.read_once("gems", frame) if is_game else {}
            detail = ("HUD игры подтверждён" if is_game else
                      "это не кадр игры (меню/загрузка/чужое окно)")
            return SkillResult(
                "health", bool(is_game), detail,
                seconds=time.time() - t0,
                evidence=STATUS_MEASURED if is_game else STATUS_UNCONFIRMED,
                metrics={"strength": (strength or {}).get("value"),
                         "gems": (gems or {}).get("value"),
                         "strength_conf": round(float((strength or {}).get("confidence") or 0), 3),
                         "gems_conf": round(float((gems or {}).get("confidence") or 0), 3)},
            )
        except Exception as e:
            return SkillResult("health", False, f"ошибка: {e}",
                               seconds=time.time() - t0, error=str(e))

    def observe(self) -> SkillResult:
        """One structured scene. Read-only, never clicks."""
        t0 = time.time()
        try:
            adapter, _ = self.build()
            scene = adapter.observe()
            metrics = dict(scene.metrics or {})
            detail = []
            if metrics.get("strength") is not None:
                detail.append(f"сила {metrics['strength']:,}")
            if metrics.get("gems") is not None:
                detail.append(f"гемы {metrics['gems']:,}")
            if scene.modal_open:
                detail.append("открыт диалог")
            if scene.events:
                detail.append("события: " + ", ".join(scene.events))
            return SkillResult(
                "observe", True,
                " · ".join(detail) if detail else "сцена прочитана, чисел нет",
                seconds=time.time() - t0,
                evidence=STATUS_MEASURED if metrics else STATUS_UNCONFIRMED,
                metrics={**metrics, "modal_open": scene.modal_open,
                         "events": list(scene.events or [])},
            )
        except Exception as e:
            return SkillResult("observe", False, f"ошибка: {e}",
                               seconds=time.time() - t0, error=str(e))

    def close_dialog(self) -> SkillResult:
        t0 = time.time()
        try:
            adapter, _ = self.build()
            handled = adapter.actions.handle_popups()
            closed = adapter.actions.close_modal_safe()
            return SkillResult("close_dialog", bool(handled or closed),
                               handled or ("закрыто" if closed else "диалога нет"),
                               seconds=time.time() - t0)
        except Exception as e:
            return SkillResult("close_dialog", False, f"ошибка: {e}",
                               seconds=time.time() - t0, error=str(e))

    def train(self, sets: int = 3, on_event: Optional[Callable] = None,
              action_name: str = "TRAIN_DUMBBELLS", reps: Optional[int] = None) -> SkillResult:
        """Train in verified sets. Refuses on a non-game frame or open dialog."""
        from config import LIFTS_PER_SET
        from games.game_adapter import GameAction

        lifts_count = reps if reps is not None else LIFTS_PER_SET
        t0 = time.time()
        rows: List[Dict] = []
        verified_gain = 0
        verified_sets = 0

        try:
            adapter, _ = self.build()
            actions = adapter.actions
            before = actions.read_strength()

            for i in range(1, max(1, sets) + 1):
                state = adapter.observe()
                dialogs = 0
                while state.modal_open and dialogs < 2:
                    actions.handle_popups()
                    time.sleep(0.6)
                    actions.close_modal_safe()
                    time.sleep(0.4)
                    state = adapter.observe()
                    dialogs += 1

                action = GameAction(name=action_name,
                                    description=action_name.replace("_", " ").title(),
                                    category="train")
                if action.name not in ALLOWED_ACTIONS:
                    return SkillResult(action_name.lower(), False,
                                       f"действие {action.name} запрещено политикой",
                                       seconds=time.time() - t0)
                res = adapter.execute_action(action, state)
                gain = res.metrics_delta.get("strength")
                if gain is not None and gain > 0:
                    verified_gain += int(gain)
                    verified_sets += 1
                row = {"set": i, "success": res.success, "gain": gain,
                       "detail": res.detail, "sec": round(res.cost_seconds, 1),
                       "error": res.error}
                rows.append(row)
                if on_event:
                    on_event(row)

            after = actions.read_strength()
            total = (after - before) if None not in (before, after) else None
            seconds = time.time() - t0
            skill_tag = "train" if action_name == "TRAIN_DUMBBELLS" else action_name.lower()
            if verified_sets == 0:
                return SkillResult(
                    skill_tag, False,
                    "ни один подход не подтверждён (бейдж не читался или игра "
                    "игнорировала повторы)", reward=None, seconds=seconds,
                    metrics={"before": before, "after": after},
                    rows=rows, error="unverified")
            detail = (f"{verified_sets}/{sets} подходов подтверждены, "
                      f"+{verified_gain:,} силы")
            if total is not None and total != verified_gain:
                detail += f" (граница окна: {total:+,})"
            return SkillResult(skill_tag, True, detail, reward=verified_gain,
                               seconds=seconds, evidence=STATUS_MEASURED,
                               metrics={"before": before, "after": after,
                                        "reps_per_set": lifts_count,
                                        "strength_per_min":
                                            round(verified_gain / seconds * 60)
                                            if seconds else None},
                               rows=rows)
        except Exception as e:
            skill_tag = "train" if action_name == "TRAIN_DUMBBELLS" else action_name.lower()
            return SkillResult(skill_tag, False, f"ошибка: {e}",
                               seconds=time.time() - t0, error=str(e), rows=rows)


def guard_report() -> List[str]:
    """What the agent is structurally forbidden to do, for /safety."""
    return [
        "покупать за реальные деньги / Robux / гемы, купленные за Robux",
        "принимать или отправлять трейды",
        "тратить предметы и бусты без разрешения",
        "нажимать ESC (меню Roblox) и TAB (лидерборд)",
        "кликать вне белого списка ALLOWED_CLICK_ROIS",
        "использовать эксплойты, инъекции, обход анти-чита",
        "выполнять HATCH_EGG (тратит гемы), PETS_MAINTENANCE (удаляет питомцев), "
        "REBIRTH (сбрасывает прогресс) — недоступны из этого приложения",
    ]
