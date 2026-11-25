"""
Dynamic game adapter registry (Fix 02).

Before this fix the entry point hardcoded MuscleLegendsAdapter; adding a game
meant editing main.py. Now:

  * drop  games/my_game.py  with a GameAdapter subclass (set GAME_KEY), and
  * select it with env ALONTITO_GAME=my_game  (or pult -> /api/status shows all).

main.py only ever calls get_adapter_class(); it knows nothing about specific
games. Discovery is lazy and failure-isolated: a broken module never crashes
the registry — it is simply skipped and reported.
"""
from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from typing import Dict, List, Optional, Type

DEFAULT_GAME = "muscle_legends"


def _base_class() -> Optional[type]:
    try:
        from games.game_adapter import GameAdapter
        return GameAdapter
    except Exception:
        return None


def discover_adapters() -> Dict[str, Type]:
    """Scan games/ for GameAdapter subclasses. Returns {GAME_KEY: class}."""
    base = _base_class()
    found: Dict[str, Type] = {}
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    for mod_info in pkgutil.iter_modules([pkg_dir]):
        name = mod_info.name
        if name.startswith("_") or name in ("registry", "game_adapter"):
            continue
        try:
            module = importlib.import_module(f"games.{name}")
        except Exception as e:  # failure-isolated discovery
            print(f"[REGISTRY] games.{name} skipped: {e}")
            continue
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if not inspect.isclass(obj):
                continue
            if base is not None and issubclass(obj, base) and obj is not base:
                key = (getattr(obj, "GAME_KEY", None) or name).lower()
                found.setdefault(key, obj)
    return found


def available_games() -> List[str]:
    return sorted(discover_adapters())


def get_adapter_class(name: Optional[str] = None) -> Type:
    """Resolve which adapter runs. Env ALONTITO_GAME wins, then DEFAULT_GAME,
    then the only discovered adapter."""
    adapters = discover_adapters()
    key = (name or os.environ.get("ALONTITO_GAME") or DEFAULT_GAME).lower()
    if key in adapters:
        return adapters[key]
    if len(adapters) == 1:
        return next(iter(adapters.values()))
    raise KeyError(
        f"adapter '{key}' not found; available: {sorted(adapters)}. "
        f"Set ALONTITO_GAME to one of them."
    )
