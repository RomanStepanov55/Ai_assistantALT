import tempfile
import unittest
from pathlib import Path
from brain.experience_memory import ExperienceMemory


class TestExperienceMemory(unittest.TestCase):
    def test_visual_state_is_compact_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = ExperienceMemory(str(Path(tmp) / "memory.sqlite3"))
            elements = [{"id": 1, "text": "Strength 120K", "center": (20, 30), "conf": 0.98}]
            first = mem.remember_visual("dhash-1", elements, {"strength": 120000}, [])
            second = mem.remember_visual("dhash-2", elements, {"strength": 120000}, [])
            self.assertFalse(first["known_state"])
            self.assertTrue(second["known_state"])
            self.assertEqual(second["occurrences"], 2)
            self.assertEqual(mem.counts()["visual_states"], 1)
            raw = Path(tmp) / "memory.sqlite3"
            self.assertTrue(raw.exists())

    def test_experience_recall_uses_new_examples(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = ExperienceMemory(str(Path(tmp) / "memory.sqlite3"))
            mem.add_experience("HUD strength training", "strength increased", "repeat verified training", 1, "test")
            found = mem.recall("training strength", limit=2)
            self.assertEqual(len(found), 1)
            self.assertIn("verified", found[0]["lesson"])

    def test_visual_similarity_uses_compact_image_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem = ExperienceMemory(str(Path(tmp) / "memory.sqlite3"))
            mem.remember_visual("ffffffffffffffff", [], {"strength": 1}, [])
            matches = mem.similar_visual_states([], limit=2, image_hash="fffffffffffffffe")
            self.assertEqual(len(matches), 1)
            self.assertGreater(matches[0]["image_similarity"], 0.9)


if __name__ == "__main__":
    unittest.main()
