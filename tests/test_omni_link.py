import sqlite3
import tempfile
import unittest
from pathlib import Path

from alontito import omni_link


class TestOmniLink(unittest.TestCase):
    def test_missing_databases_are_none_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "nope.sqlite3"
            mem = omni_link.memory_counts(db)
            learn = omni_link.learning_stats(db)
            self.assertIsNone(mem["experiences"])
            self.assertIsNone(mem["visual_states"])
            self.assertIsNone(learn["examples"])
            self.assertEqual(omni_link.recent_lessons(path=db), [])
            self.assertIsNone(omni_link.curated_count(Path(tmp) / "none.jsonl"))

    def test_counts_are_read_from_the_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem_db = Path(tmp) / "memory.sqlite3"
            conn = sqlite3.connect(mem_db)
            try:
                with conn:
                    conn.executescript("""
                    CREATE TABLE visual_states (id INTEGER PRIMARY KEY, state_hash TEXT);
                    CREATE TABLE experiences (id INTEGER PRIMARY KEY, created_at REAL,
                        prompt TEXT, outcome TEXT, lesson TEXT, reward REAL,
                        source TEXT, state_hash TEXT);
                    INSERT INTO visual_states (state_hash) VALUES ('a'), ('b');
                    INSERT INTO experiences VALUES
                        (1, 1.0, 'p1', 'o1', 'первый урок', 1, 'test', NULL),
                        (2, 2.0, 'p2', 'o2', 'второй урок', 1, 'test', NULL);
                    """)
            finally:
                conn.close()
            self.assertEqual(omni_link.memory_counts(mem_db)["visual_states"], 2)
            self.assertEqual(omni_link.memory_counts(mem_db)["experiences"], 2)
            recent = omni_link.recent_lessons(limit=1, path=mem_db)
            self.assertEqual(recent[0]["lesson"], "второй урок")

            learn_db = Path(tmp) / "learning.sqlite3"
            conn = sqlite3.connect(learn_db)
            try:
                with conn:
                    conn.executescript("""
                    CREATE TABLE learning_examples (id INTEGER PRIMARY KEY,
                        fingerprint TEXT, created_at REAL, prompt TEXT, answer TEXT,
                        lesson TEXT, source TEXT, reward REAL, evidence TEXT,
                        approved INTEGER DEFAULT 0, exported INTEGER DEFAULT 0);
                    CREATE TABLE eval_runs (id INTEGER PRIMARY KEY, version TEXT, suite TEXT,
                        score REAL, details_json TEXT, created_at REAL);
                    CREATE TABLE promotions (id INTEGER PRIMARY KEY, candidate_version TEXT,
                        baseline_version TEXT, candidate_score REAL, baseline_score REAL,
                        promoted INTEGER, created_at REAL);
                    INSERT INTO learning_examples (prompt, approved, exported)
                        VALUES ('q', 1, 0), ('q2', 0, 0);
                    INSERT INTO eval_runs (version, suite, score, details_json, created_at)
                        VALUES ('v1', 's', 1.0, '{}', 0);
                    """)
            finally:
                conn.close()
            stats = omni_link.learning_stats(learn_db)
            self.assertEqual(stats["examples"], 2)
            self.assertEqual(stats["approved"], 1)
            self.assertEqual(stats["pending"], 1)
            self.assertEqual(stats["eval_runs"], 1)
            self.assertEqual(stats["promotions"], 0)

    def test_core_files_report_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            have, missing = omni_link.core_files(Path(tmp))
            self.assertEqual(have, 0)
            self.assertEqual(len(missing), len(omni_link.CORE_FILES))
            for rel in omni_link.CORE_FILES:
                p = Path(tmp) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("", encoding="utf-8")
            have, missing = omni_link.core_files(Path(tmp))
            self.assertEqual(have, len(omni_link.CORE_FILES))
            self.assertEqual(missing, [])

    def test_unreachable_ollama_is_reported_as_offline(self):
        probe = omni_link.ollama_status("http://127.0.0.1:9", timeout=0.4)
        self.assertEqual(probe["state"], "offline")
        self.assertEqual(probe["models"], [])

    def test_missing_packages_are_real_names(self):
        for name, purpose in omni_link.missing_packages():
            self.assertIsInstance(name, str)
            self.assertTrue(purpose)

    def test_status_survives_an_empty_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            st = omni_link.status(Path(tmp))
            self.assertFalse(st["installed"])
            self.assertEqual(st["core_present"], 0)
            self.assertIsNone(st["memory"]["experiences"])
            self.assertIsNone(st["learning"]["approved"])
            self.assertEqual(st["lessons"], [])


if __name__ == "__main__":
    unittest.main()
