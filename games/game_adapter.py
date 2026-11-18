"""
GameAdapter — abstract interface for any Roblox game the agent learns to play.

Every game provides:
  - identity (name, genre, Roblox place ID)
  - perception (what can the agent see on screen right now?)
  - actions (what can the agent do in this game?)
  - rewards (how well is the agent doing?)
  - session lifecycle (launch, detect active, exit)

The MetaBrain selects a game, the adapter translates between
the universal agent and the game-specific world.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import time


@dataclass
class GameIdentity:
    """Static info about a game."""
    name: str                       # Human-readable name
    place_id: int                   # Roblox Place ID
    genre: str                      # 'simulator', 'obby', 'tycoon', 'fighting', 'rpg', etc.
    tags: List[str] = field(default_factory=list)   # e.g. ['clicker', 'pets', 'rebirth']
    url: str = ""                   # roblox.com/games/... URL (optional)


@dataclass
class GameState:
    """Snapshot of what the agent sees right now in the game."""
    timestamp: float = 0.0
    is_alive: bool = True
    metrics: Dict[str, float] = field(default_factory=dict)  # e.g. {'strength': 56700000, 'gems': 3910000}
    events: List[str] = field(default_factory=list)           # e.g. ['boss', 'brawl']
    modal_open: bool = False
    raw_screen: Any = None          # PIL Image or None


@dataclass
class GameAction:
    """A single thing the agent can do in this game."""
    name: str                       # e.g. 'TRAIN_DUMBBELLS'
    description: str                # Human-readable
    category: str = "unknown"       # 'train', 'collect', 'navigate', 'manage', 'explore'
    cooldown: float = 0.0
    last_used: float = 0.0

    def is_ready(self) -> bool:
        return time.time() - self.last_used >= self.cooldown


@dataclass
class ActionResult:
    """Outcome of executing a GameAction."""
    success: bool = False
    reward: float = 0.0             # Numeric reward (strength gained, items collected, etc.)
    cost_seconds: float = 0.0       # How long it took
    detail: str = ""                # Human-readable description
    error: str = ""                 # Error message if failed
    metrics_delta: Dict[str, float] = field(default_factory=dict)  # e.g. {'strength': +17000}


class GameAdapter(ABC):
    """
    Abstract interface every game must implement.

    The Mind/MetaBrain never knows game-specific details — it only talks
    through this interface. To add a new game, subclass GameAdapter and
    implement the abstract methods.
    """

    @abstractmethod
    def identity(self) -> GameIdentity:
        """Return static game info (name, genre, place ID)."""
        ...

    @abstractmethod
    def detect_active(self) -> bool:
        """Is this game currently running and visible on screen?"""
        ...

    @abstractmethod
    def observe(self) -> GameState:
        """Capture current game state from the screen."""
        ...

    @abstractmethod
    def available_actions(self, state: GameState) -> List[GameAction]:
        """Return the list of actions currently possible given the state."""
        ...

    @abstractmethod
    def execute_action(self, action: GameAction, state: GameState) -> ActionResult:
        """Execute an action and return the measured result."""
        ...

    @abstractmethod
    def handle_interrupts(self, state: GameState) -> Optional[str]:
        """Handle popups, trade requests, death, etc. Return description or None."""
        ...

    # --- Optional hooks (override if needed) ---

    def on_session_start(self):
        """Called when the agent starts a session in this game."""
        pass

    def on_session_end(self, stats: Dict):
        """Called when the agent stops playing this game."""
        pass

    def get_progress_metric(self) -> str:
        """Name of the primary metric to track progress (e.g. 'strength')."""
        return "score"

    def get_genre_hints(self) -> List[str]:
        """Return hints for the MetaBrain about what strategies might work."""
        return []
