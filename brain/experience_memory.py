"""Compact persistent memory for visual states and verified agent experiences.

Only hashes and structured metadata are persisted. Raw screenshots are never written.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class ExperienceMemory:
    def __init__(self, db_path: str = "data/alontito_memory.sqlite3"):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path), timeout=15)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS visual_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state_hash TEXT NOT NULL UNIQUE,
                image_hash TEXT NOT NULL,
                observed_at REAL NOT NULL,
                elements_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                windows_json TEXT NOT NULL,
                occurrences INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_visual_observed ON visual_states(observed_at);
            CREATE TABLE IF NOT EXISTS experiences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                prompt TEXT NOT NULL,
                outcome TEXT NOT NULL,
                lesson TEXT NOT NULL,
                reward REAL NOT NULL,
                source TEXT NOT NULL,
                state_hash TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_exp_created ON experiences(created_at);
            """)

    @staticmethod
    def _normalise_elements(elements: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for e in elements:
            text = re.sub(r"\s+", " ", str(e.get("text", ""))).strip().lower()
            center = e.get("center", (0, 0))
            out.append({"text": text, "center": [int(center[0]), int(center[1])],
                        "conf": round(float(e.get("conf", 0)), 2)})
        return out

    def remember_visual(self, image_hash: str, elements: List[Dict[str, Any]],
                        metrics: Dict[str, Any], windows: List[Dict[str, Any]]) -> Dict[str, Any]:
        norm = self._normalise_elements(elements)
        # Semantic state hash is independent of pixel-level rendering noise.
        canonical = json.dumps({"elements": norm, "metrics": metrics, "windows": windows},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        state_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock, self._connect() as c:
            row = c.execute("SELECT id, occurrences FROM visual_states WHERE state_hash=?", (state_hash,)).fetchone()
            if row:
                c.execute("UPDATE visual_states SET occurrences=occurrences+1, observed_at=?, image_hash=? WHERE id=?",
                          (now, image_hash, row["id"]))
                return {"state_id": row["id"], "state_hash": state_hash, "known_state": True,
                        "occurrences": row["occurrences"] + 1}
            cur = c.execute("""INSERT INTO visual_states
                (state_hash,image_hash,observed_at,elements_json,metrics_json,windows_json)
                VALUES(?,?,?,?,?,?)""",
                (state_hash, image_hash, now, json.dumps(norm, ensure_ascii=False),
                 json.dumps(metrics, ensure_ascii=False), json.dumps(windows, ensure_ascii=False)))
            return {"state_id": cur.lastrowid, "state_hash": state_hash,
                    "known_state": False, "occurrences": 1}

    def add_experience(self, prompt: str, outcome: str, lesson: str,
                       reward: float, source: str, state_hash: Optional[str] = None) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT INTO experiences(created_at,prompt,outcome,lesson,reward,source,state_hash) VALUES(?,?,?,?,?,?,?)",
                      (time.time(), prompt[:4000], outcome[:4000], lesson[:2000],
                       float(reward), source[:80], state_hash))

    @staticmethod
    def _tokens(text: str) -> set:
        return {t for t in re.findall(r"[a-zа-яё0-9_]{3,}", text.lower())}

    def recall(self, query: str, limit: int = 4) -> List[Dict[str, Any]]:
        q = self._tokens(query)
        if not q:
            return []
        with self._connect() as c:
            rows = c.execute("SELECT prompt,outcome,lesson,reward,source,created_at FROM experiences ORDER BY id DESC LIMIT 500").fetchall()
        scored = []
        for row in rows:
            hay = " ".join((row["prompt"], row["outcome"], row["lesson"]))
            terms = self._tokens(hay)
            overlap = len(q & terms) / max(1, len(q))
            if overlap > 0:
                scored.append((overlap + max(-0.2, min(0.2, float(row["reward"]) * 0.05)), dict(row)))
        scored.sort(key=lambda x: (x[0], x[1]["created_at"]), reverse=True)
        return [item for _, item in scored[:limit]]


    def similar_visual_states(self, elements: List[Dict[str, Any]], limit: int = 3,
                              image_hash: Optional[str] = None) -> List[Dict[str, Any]]:
        """Find prior states using OCR-token overlap and compact 64-bit dHash.

        The image hash is a 16-character hexadecimal descriptor, not image data.
        """
        tokens = self._tokens(" ".join(str(e.get("text", "")) for e in elements))
        try:
            current_hash = int(image_hash, 16) if image_hash else None
        except (TypeError, ValueError):
            current_hash = None
        if not tokens and current_hash is None:
            return []
        with self._connect() as c:
            rows = c.execute("SELECT id,image_hash,elements_json,metrics_json,occurrences,observed_at FROM visual_states ORDER BY observed_at DESC LIMIT 1000").fetchall()
        matches = []
        for row in rows:
            saved = json.loads(row["elements_json"])
            saved_tokens = self._tokens(" ".join(x.get("text", "") for x in saved))
            text_score = (len(tokens & saved_tokens) / max(1, len(tokens | saved_tokens))) if tokens else 0.0
            hash_score = 0.0
            if current_hash is not None:
                try:
                    old_hash = int(row["image_hash"], 16)
                    distance = (current_hash ^ old_hash).bit_count()
                    hash_score = 1.0 - distance / 64.0
                except (TypeError, ValueError):
                    pass
            score = max(text_score, hash_score * 0.85)
            if score >= 0.25:
                matches.append((score, {"state_id": row["id"], "similarity": round(score, 3),
                                        "text_similarity": round(text_score, 3),
                                        "image_similarity": round(hash_score, 3),
                                        "metrics": json.loads(row["metrics_json"]),
                                        "occurrences": row["occurrences"]}))
        matches.sort(key=lambda x: x[0], reverse=True)
        return [x[1] for x in matches[:limit]]

    def counts(self) -> Dict[str, int]:
        with self._connect() as c:
            return {"visual_states": c.execute("SELECT COUNT(*) FROM visual_states").fetchone()[0],
                    "experiences": c.execute("SELECT COUNT(*) FROM experiences").fetchone()[0]}
