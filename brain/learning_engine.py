"""Evidence-gated continual learning for Alontito.

This module improves behaviour through durable memory and curated training data.
It never edits model weights or executes generated code. Weight fine-tuning is a
separate, explicit offline process after evaluation and dataset review.
"""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class LearningEngine:
    def __init__(self, db_path: str = "data/alontito_learning.sqlite3",
                 dataset_path: str = "data/curated_training.jsonl"):
        self.db_path = Path(db_path)
        self.dataset_path = Path(dataset_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dataset_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        c = sqlite3.connect(str(self.db_path), timeout=20)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            with c:
                yield c
        finally:
            c.close()

    def _init_db(self):
        with self._connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS learning_examples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                created_at REAL NOT NULL,
                prompt TEXT NOT NULL,
                answer TEXT NOT NULL,
                lesson TEXT NOT NULL,
                source TEXT NOT NULL,
                reward REAL NOT NULL,
                evidence TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0,
                exported INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_learning_approved
              ON learning_examples(approved, exported, created_at);
            CREATE TABLE IF NOT EXISTS eval_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                suite TEXT NOT NULL,
                score REAL NOT NULL,
                details_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_version TEXT NOT NULL,
                baseline_version TEXT NOT NULL,
                candidate_score REAL NOT NULL,
                baseline_score REAL NOT NULL,
                promoted INTEGER NOT NULL,
                created_at REAL NOT NULL
            );
            """)

    @staticmethod
    def _fingerprint(prompt: str, answer: str, source: str) -> str:
        raw = "\0".join((prompt.strip(), answer.strip(), source.strip()))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def add_example(self, prompt: str, answer: str, lesson: str = "", *,
                    source: str = "unknown", reward: float = 0.0,
                    evidence: str = "unverified", approved: bool = False) -> bool:
        """Store an example. `approved` must come from human or objective evidence."""
        if not prompt.strip() or not answer.strip():
            return False
        reward = max(-1.0, min(1.0, float(reward)))
        fp = self._fingerprint(prompt, answer, source)
        with self._connect() as c:
            cur = c.execute("""INSERT OR IGNORE INTO learning_examples
                (fingerprint,created_at,prompt,answer,lesson,source,reward,evidence,approved)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (fp, time.time(), prompt[:8000], answer[:12000], lesson[:4000],
                 source[:100], reward, evidence[:300], int(approved)))
            return cur.rowcount == 1

    def approve(self, fingerprint: str, evidence: str) -> bool:
        with self._connect() as c:
            cur = c.execute("UPDATE learning_examples SET approved=1,evidence=? WHERE fingerprint=?",
                            (evidence[:300], fingerprint))
            return cur.rowcount == 1

    def export_approved(self, limit: int = 1000) -> Dict[str, int]:
        """Append only approved, deduplicated examples to a JSONL fine-tuning corpus."""
        with self._connect() as c:
            rows = c.execute("""SELECT * FROM learning_examples
                WHERE approved=1 AND exported=0 ORDER BY id LIMIT ?""", (max(1, limit),)).fetchall()
            if not rows:
                return {"exported": 0, "pending": 0}
            with self.dataset_path.open("a", encoding="utf-8") as f:
                for r in rows:
                    item = {
                        "id": r["fingerprint"], "created_at": r["created_at"],
                        "source": r["source"], "evidence": r["evidence"],
                        "reward": r["reward"],
                        "messages": [
                            {"role": "user", "content": r["prompt"]},
                            {"role": "assistant", "content": r["answer"]}
                        ]
                    }
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                c.executemany("UPDATE learning_examples SET exported=1 WHERE id=?",
                              [(r["id"],) for r in rows])
            pending = c.execute("SELECT COUNT(*) FROM learning_examples WHERE approved=1 AND exported=0").fetchone()[0]
            return {"exported": len(rows), "pending": pending}

    def record_eval(self, version: str, suite: str, score: float,
                    details: Dict[str, Any]) -> int:
        with self._connect() as c:
            cur = c.execute("INSERT INTO eval_runs(version,suite,score,details_json,created_at) VALUES(?,?,?,?,?)",
                            (version, suite, float(score), json.dumps(details, ensure_ascii=False, default=str), time.time()))
            return int(cur.lastrowid)

    def compare_and_promote(self, candidate_version: str, baseline_version: str,
                            candidate_score: float, baseline_score: float,
                            min_gain: float = 0.02, max_regression: float = 0.03) -> bool:
        """Promotion gate: require measurable gain and no excessive regression.

        Scores are normalized 0..1. A caller must evaluate both versions on the
        same held-out suite; this method records the decision, it cannot validate
        that experimental protocol itself.
        """
        cscore, bscore = float(candidate_score), float(baseline_score)
        promoted = (0 <= cscore <= 1 and 0 <= bscore <= 1 and
                    cscore >= bscore + min_gain and cscore >= bscore - max_regression)
        with self._connect() as c:
            c.execute("""INSERT INTO promotions(candidate_version,baseline_version,
                candidate_score,baseline_score,promoted,created_at) VALUES(?,?,?,?,?,?)""",
                (candidate_version, baseline_version, cscore, bscore, int(promoted), time.time()))
        return promoted

    def stats(self) -> Dict[str, int]:
        with self._connect() as c:
            return {
                "examples": c.execute("SELECT COUNT(*) FROM learning_examples").fetchone()[0],
                "approved": c.execute("SELECT COUNT(*) FROM learning_examples WHERE approved=1").fetchone()[0],
                "pending_export": c.execute("SELECT COUNT(*) FROM learning_examples WHERE approved=1 AND exported=0").fetchone()[0],
                "eval_runs": c.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0],
                "promotions": c.execute("SELECT COUNT(*) FROM promotions WHERE promoted=1").fetchone()[0]
            }
