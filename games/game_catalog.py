"""
GameCatalog and Launcher for multi-game Roblox autonomous agent.
Maintains known games, handles game selection policies (UCB across games),
and manages game switches when progression stagnates.
"""
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from config import DATA_DIR
from games.game_adapter import GameAdapter

CATALOG_FILE = DATA_DIR / "game_catalog.json"


class GameCatalog:
    """Manages multi-game registry and cross-game learning statistics."""

    def __init__(self):
        self.games: Dict[str, GameAdapter] = {}
        self.stats: Dict[str, Dict] = self._load_stats()

    def _load_stats(self) -> Dict[str, Dict]:
        if CATALOG_FILE.exists():
            try:
                return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def save_stats(self):
        try:
            CATALOG_FILE.write_text(json.dumps(self.stats, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def register(self, adapter: GameAdapter):
        ident = adapter.identity()
        self.games[ident.name] = adapter
        if ident.name not in self.stats:
            self.stats[ident.name] = {
                "place_id": ident.place_id,
                "genre": ident.genre,
                "total_playtime_sec": 0.0,
                "sessions_count": 0,
                "stagnation_streak": 0,
                "last_played": 0.0,
                "best_growth_rate": 0.0,
                "insights": [],
            }
        self.save_stats()

    def record_session_result(self, game_name: str, duration_sec: float, growth_rate: float, stagnated: bool):
        st = self.stats.setdefault(game_name, {})
        st["total_playtime_sec"] = st.get("total_playtime_sec", 0.0) + duration_sec
        st["sessions_count"] = st.get("sessions_count", 0) + 1
        st["last_played"] = time.time()
        if growth_rate > st.get("best_growth_rate", 0.0):
            st["best_growth_rate"] = growth_rate

        if stagnated:
            st["stagnation_streak"] = st.get("stagnation_streak", 0) + 1
        else:
            st["stagnation_streak"] = 0
        self.save_stats()

    def should_switch_game(self, game_name: str) -> bool:
        """Rule: if 3 consecutive sessions fail to yield progress (>10%), suggest switching games."""
        st = self.stats.get(game_name, {})
        return st.get("stagnation_streak", 0) >= 3

    def get_game(self, name: str) -> Optional[GameAdapter]:
        return self.games.get(name)

    def list_games(self) -> List[str]:
        return list(self.games.keys())
