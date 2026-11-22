"""Read-only link between the Alontito terminal and the Omni learning layer.

The Omni subsystem (``brain/opus_local_core.py``, ``games/universal_explorer.py``,
``dashboard/app.py``) talks to a local Ollama server and optionally uses heavy
packages. This module deliberately imports none of them: it reads the config,
counts rows in the SQLite stores and probes Ollama over plain ``urllib`` with a
short timeout. That keeps ``/omni`` instant and honest on a machine where the
subsystem is installed but not running.

Nothing here starts a process, sends input to a game, or writes to the Omni
databases. The terminal only reports what exists.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = ROOT / "config_omni.json"
MEMORY_DB = ROOT / "data" / "alontito_memory.sqlite3"
LEARNING_DB = ROOT / "data" / "alontito_learning.sqlite3"
CURATED_PATH = ROOT / "data" / "curated_training.jsonl"
LAUNCHER = ROOT / "alontito_omni_launcher.py"
BATCH = ROOT / "Alontito_Omni.bat"

CORE_FILES = (
    "brain/opus_local_core.py",
    "brain/experience_memory.py",
    "brain/learning_engine.py",
    "vision/omni_eye.py",
    "games/universal_explorer.py",
    "dashboard/app.py",
    "alontito_omni_launcher.py",
    "config_omni.json",
)

OPTIONAL = (
    ("mss", "снимок экрана"),
    ("rapidocr_onnxruntime", "OCR интерфейса"),
    ("pywinauto", "дерево окон (UIA)"),
    ("aiogram", "Telegram-мост"),
    ("faster_whisper", "распознавание речи"),
    ("torch", "обучение LoRA (опционально)"),
    ("transformers", "обучение LoRA (опционально)"),
    ("peft", "обучение LoRA (опционально)"),
)


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Config with the defaults the launcher itself uses. Never invents values."""
    p = Path(path) if path else CONFIG_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def core_files(root: Optional[Path] = None) -> Tuple[int, List[str]]:
    base = Path(root) if root else ROOT
    missing = [rel for rel in CORE_FILES if not (base / rel).exists()]
    return len(CORE_FILES) - len(missing), missing


def _count(db: Path, sql: str) -> Optional[int]:
    """Count rows without creating the file. None means "no data yet"."""
    if not db.exists():
        return None
    conn = None
    try:
        uri = "file:" + db.as_posix() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        return int(conn.execute(sql).fetchone()[0])
    except (sqlite3.Error, OSError):
        return None
    finally:
        if conn is not None:
            conn.close()


def memory_counts(path: Optional[Path] = None) -> Dict[str, Optional[int]]:
    db = Path(path) if path else MEMORY_DB
    return {
        "visual_states": _count(db, "SELECT COUNT(*) FROM visual_states"),
        "experiences": _count(db, "SELECT COUNT(*) FROM experiences"),
    }


def learning_stats(path: Optional[Path] = None) -> Dict[str, Optional[int]]:
    db = Path(path) if path else LEARNING_DB
    return {
        "examples": _count(db, "SELECT COUNT(*) FROM learning_examples"),
        "approved": _count(db, "SELECT COUNT(*) FROM learning_examples WHERE approved=1"),
        "pending": _count(db, "SELECT COUNT(*) FROM learning_examples WHERE approved=1 AND exported=0"),
        "eval_runs": _count(db, "SELECT COUNT(*) FROM eval_runs"),
        "promotions": _count(db, "SELECT COUNT(*) FROM promotions WHERE promoted=1"),
    }


def curated_count(path: Optional[Path] = None) -> Optional[int]:
    p = Path(path) if path else CURATED_PATH
    if not p.exists():
        return None
    try:
        return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return None


def recent_lessons(limit: int = 3, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Last stored lessons. Only reading: no schema change, no image data."""
    db = Path(path) if path else MEMORY_DB
    if not db.exists():
        return []
    conn = None
    try:
        uri = "file:" + db.as_posix() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        rows = conn.execute(
            "SELECT created_at, outcome, lesson, source FROM experiences "
            "ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    except (sqlite3.Error, OSError):
        return []
    finally:
        if conn is not None:
            conn.close()
    return [{"created_at": r[0], "outcome": r[1], "lesson": r[2], "source": r[3]}
            for r in rows]


def missing_packages() -> List[Tuple[str, str]]:
    out = []
    for name, purpose in OPTIONAL:
        if importlib.util.find_spec(name) is None:
            out.append((name, purpose))
    return out


def ollama_status(url: Optional[str] = None, timeout: float = 1.2) -> Dict[str, Any]:
    """Probe the local Ollama server. Unreachable is reported as such, not guessed."""
    cfg = load_config()
    base = (url or cfg.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"url": base, "state": "offline", "models": [], "detail": str(exc)}
    names = [m.get("name", "") for m in payload.get("models", []) if m.get("name")]
    wanted = {
        "deep": cfg.get("deep_model", ""),
        "fallback": cfg.get("fallback_deep_model", ""),
        "fast": cfg.get("fast_model", ""),
        "vision": cfg.get("vision_model", ""),
    }
    present = {role: any(name in n for n in names) for role, name in wanted.items() if name}
    return {"url": base, "state": "ok", "models": names, "wanted": wanted,
            "present": present}


def status(root: Optional[Path] = None) -> Dict[str, Any]:
    """One snapshot for /omni. Every absent value stays None, never 0."""
    base = Path(root) if root else ROOT
    have, missing = core_files(base)
    return {
        "core_present": have,
        "core_total": len(CORE_FILES),
        "core_missing": missing,
        "installed": have == len(CORE_FILES),
        "config": load_config(base / "config_omni.json"),
        "memory": memory_counts(base / "data" / "alontito_memory.sqlite3"),
        "learning": learning_stats(base / "data" / "alontito_learning.sqlite3"),
        "curated": curated_count(base / "data" / "curated_training.jsonl"),
        "lessons": recent_lessons(3, base / "data" / "alontito_memory.sqlite3"),
        "missing_packages": missing_packages(),
        "launcher_exists": (base / "alontito_omni_launcher.py").exists(),
        "batch_exists": (base / "Alontito_Omni.bat").exists(),
    }
