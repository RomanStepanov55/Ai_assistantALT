from typing import Any, Dict
from brain.opus_local_core import OpusLocalCore
from games.universal_explorer import UniversalExplorer


class OmniMCPBridge:
    """Связывает базовый MCP-сервер Ai_assistantALT с когнитивным ядром Opus 5."""

    def __init__(self, brain: OpusLocalCore, explorer: UniversalExplorer):
        self.brain = brain
        self.explorer = explorer

    def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "alontito_status":
            return {
                "explorer_running": self.explorer.running,
                "total_ucb_attempts": self.explorer.total_attempts,
                "registered_auto_tools": list(self.brain.registered_tools.keys())
            }
        if tool_name == "alontito_observe":
            obs = self.explorer.eye.observe_verified(delay_sec=0.15)
            return {"metrics": obs["metrics"], "elements_count": len(obs["elements"])}
        if tool_name == "alontito_train":
            return self.explorer.run_single_cycle(duration_sec=float(arguments.get("duration", 2.0)))
        if tool_name == "alontito_propose":
            query = arguments.get("query", "Проанализируй текущую стратегию")
            res = self.brain.think_and_act(query, force_effort="high")
            return {"thinking": res["thinking"], "answer": res["answer"],
                    "reviews": res.get("reviews", []), "verified": res.get("verified", False)}
        if tool_name == "alontito_reflect":
            last = self.brain.last_interaction or {}
            if not last:
                return {"status": "no_interaction_to_review"}
            res = self.brain.think_and_act(
                "Проведи повторный аудит предыдущего результата. Найди ошибки и предложи исправление. "
                f"Запрос: {last.get('prompt', '')}\nОтвет: {last.get('response', '')}",
                force_effort="max"
            )
            return {"status": "reviewed", "answer": res["answer"],
                    "reviews": res.get("reviews", []), "verified": res.get("verified", False)}
        if tool_name == "alontito_memory":
            return self.brain.memory.counts()
        return {"error": f"Неизвестный MCP инструмент: {tool_name}"}
