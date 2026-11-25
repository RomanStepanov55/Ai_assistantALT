"""Skills: the things Alontito can actually DO.

Every skill returns a SkillResult whose `reward` is either a verified number or
None. None means "not measured" and is never displayed as zero: the whole
verification layer exists because a fake +1 poisoned the learning data before.

HARD RULE #2 (user decision, 2026-11-24): Alontito never plays for the user.
The only skills left are read-only (health, observe). Training and dialog
closing were removed here AND are refused one level lower, in
InputController (config.GAME_INPUT_ENABLED is False), so no caller can bring
them back by calling the stack directly.

The bot stack is built lazily, so opening Alontito never depends on Roblox
running or on the vision stack importing successfully.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .memory import MISSING, STATUS_MEASURED, STATUS_UNCONFIRMED

# Actions this app may execute in the game. Empty by decision of 2026-11-24:
# the agent observes and consults, it does not act. Kept as a name because
# /safety and the MCP server report it.
ALLOWED_ACTIONS: set = set()


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
    """Thin, guarded binding to the existing perception stack (read-only)."""

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

    def inspect(self, force_fresh: bool = True) -> dict:
        """Structured vision state for the MCP tools (read-only).

        force_fresh=False reuses the last health probe; True does a new read.
        Both paths never click and never raise the game window.
        """
        cached = dict(getattr(self, "_last_inspect", {}) or {})
        if not force_fresh and cached:
            return cached
        probe = self.health()
        result = {
            "success": probe.success,
            "detail": probe.detail,
            "evidence": probe.evidence,
            "metrics": probe.metrics,
            "seconds": round(probe.seconds, 2),
        }
        self._last_inspect = result
        return result

    def capture_frame(self):
        """One PNG frame of the Roblox window (or None). Read-only capture:
        the window is not raised, nothing is clicked."""
        try:
            self.build()
            return self.eye.capture()
        except Exception:
            return None

    def observe(self) -> SkillResult:
        """One structured scene. Read-only, never clicks, never raises the window."""
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


def guard_report() -> List[str]:
    """What the agent is structurally forbidden to do, for /safety."""
    return [
        "играть за пользователя: тренировать, кликать, ходить, закрывать диалоги — "
        "ввод в игру отключён (config.GAME_INPUT_ENABLED = False)",
        "поднимать, разворачивать и перетаскивать окно Roblox во время наблюдения",
        "покупать за реальные деньги / Robux / гемы, купленные за Robux",
        "принимать или отправлять трейды",
        "тратить предметы и бусты без разрешения",
        "нажимать ESC (меню Roblox) и TAB (лидерборд)",
        "кликать вне белого списка ALLOWED_CLICK_ROIS",
        "использовать эксплойты, инъекции, обход анти-чита",
        "выполнять HATCH_EGG (тратит гемы), PETS_MAINTENANCE (удаляет питомцев), "
        "REBIRTH (сбрасывает прогресс) — недоступны из этого приложения",
    ]
