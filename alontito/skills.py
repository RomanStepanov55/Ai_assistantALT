"""Skills: the things Alontito can actually DO.

Every skill returns a SkillResult whose measurable fields are either a real
number or absent; None means "not measured" and is never displayed as zero.

HARD RULE #2 (user decision, 2026-11-24) and the 2026-11-27 pivot: Alontito
does not play and has no game layer at all. The former vision binding
(AlontitoBot) and the read-only game skills died with the Roblox code. What is
left here is the result contract and the structural guard report used by
/safety, the MCP server and the pult.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .memory import STATUS_UNCONFIRMED

# Actions this app may execute in a game. Empty forever: there is no game
# layer to execute them in, and /safety and the MCP server report this.
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


def guard_report() -> List[str]:
    """What the agent is structurally forbidden to do, for /safety."""
    return [
        "играть за пользователя: тренировать, кликать, ходить, закрывать диалоги — "
        "игрового слоя больше нет, код ввода удалён из проекта (2026-11-27)",
        "запускать Roblox или любую игру за пользователя",
        "покупать за реальные деньги / Robux / игровую валюту, купленную за Robux",
        "принимать или отправлять трейды",
        "читать память процессов, внедрять DLL/Lua, использовать эксплойты "
        "и обходить анти-чит",
        "выполнять команду на ПК без явного подтверждения с устройства "
        "(pc_actions выключен по умолчанию)",
        "отправлять данные за пределы машины: только локальные модели",
    ]
