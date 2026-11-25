import json
import os
import tempfile
import unittest
from pathlib import Path

from brain.skill_autonomy import SkillAutonomy


class TestRotation(unittest.TestCase):
    def test_bandit_choice_rotated_after_cap(self):
        auto = SkillAutonomy(max_consecutive=3)
        auto.record("A", 0.6); auto.record("A", 0.6)
        auto.record("A", 0.6); auto.record("B", 0.5)   # A не доминирует (margin 0.25)
        # 2 подряд A -> 3-й выбор A должен быть сменён на менее свежий B
        auto.choose(["A", "B"], "A")
        auto.choose(["A", "B"], "A")
        pick = auto.choose(["A", "B"], "A")
        self.assertEqual(pick, "B")

    def test_dominant_action_allowed_to_repeat(self):
        auto = SkillAutonomy(max_consecutive=3, dominance_margin=0.1)
        for _ in range(10): auto.record("A", 1.0)
        for _ in range(10): auto.record("B", 0.1)      # A доминирует
        for _ in range(3):
            pick = auto.choose(["A", "B"], "A")
        self.assertEqual(pick, "A")

    def test_single_candidate_always_returned(self):
        auto = SkillAutonomy()
        for _ in range(10):
            self.assertEqual(auto.choose(["only"], "only"), "only")


class TestStagnation(unittest.TestCase):
    def test_flat_rewards_give_high_stagnation(self):
        auto = SkillAutonomy(stagnation_window=10)
        for _ in range(10):
            auto.record("A", 0.5)
        self.assertGreater(auto.stagnation_level(), 0.8)
        self.assertGreater(auto.exploration_boost, 1.5)

    def test_growing_rewards_low_stagnation(self):
        auto = SkillAutonomy(stagnation_window=10)
        for i in range(10):
            auto.record("A", 0.1 + i * 0.09)          # растёт
        self.assertLess(auto.stagnation_level(), 0.5)

    def test_short_history_no_stagnation(self):
        auto = SkillAutonomy(stagnation_window=10)
        auto.record("A", 0.5)
        self.assertEqual(auto.stagnation_level(), 0.0)


class TestCurriculum(unittest.TestCase):
    def test_unlock_requires_mastery(self):
        auto = SkillAutonomy(min_attempts_to_unlock=5, mastery_threshold=0.7)
        for _ in range(4): auto.record("TRAIN_DUMBBELLS", 1.0)
        self.assertEqual(auto.maybe_unlock(), [])      # мало попыток
        auto.record("TRAIN_DUMBBELLS", 1.0)
        news = auto.maybe_unlock()
        self.assertIn("TRAIN_PUSHUPS", news)
        self.assertIn("TRAIN_SITUPS", auto.allowed_actions())

    def test_unlock_chain_continues(self):
        auto = SkillAutonomy(min_attempts_to_unlock=2, mastery_threshold=0.5)
        for _ in range(2): auto.record("TRAIN_DUMBBELLS", 0.9)
        auto.maybe_unlock()
        for a in ["TRAIN_PUSHUPS", "TRAIN_SITUPS"]:
            for _ in range(2): auto.record(a, 0.9)
        news = auto.maybe_unlock()
        self.assertIn("TRAIN_MACHINE", news)
        # второй уровень проекта: TRAIN_MACHINE, TRAIN_AGILITY, SPIN_WHEEL,
        # CLAIM_GIFTS - осваиваются все, прежде чем откроется третий
        for a in ["TRAIN_MACHINE", "TRAIN_AGILITY", "SPIN_WHEEL", "CLAIM_GIFTS"]:
            for _ in range(2): auto.record(a, 0.9)
        news = auto.maybe_unlock()                    # третий уровень
        self.assertIn("REBIRTH", news)
        self.assertIn("REBIRTH", auto.allowed_actions())

    def test_no_unlock_beyond_last_level(self):
        auto = SkillAutonomy(min_attempts_to_unlock=1, mastery_threshold=0.0)
        for lvl in range(10):
            for a in auto.allowed_actions():
                auto.record(a, 1.0)
            auto.maybe_unlock()
        self.assertEqual(auto.maybe_unlock(), [])


class TestPersistence(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "sa.json")
            auto = SkillAutonomy(path=p)
            auto.record("A", 0.9)
            auto.maybe_unlock()
            auto.save()
            auto2 = SkillAutonomy.load(p)
            self.assertEqual(auto2.allowed_actions(), auto.allowed_actions())
            self.assertAlmostEqual(auto2._stats["A"]["mean"], auto._stats["A"]["mean"])

    def test_broken_json_raises_for_the_caller(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "sa.json")
            Path(p).write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                SkillAutonomy.load(p)

    def test_default_path_is_in_project_data(self):
        from brain.skill_autonomy import DEFAULT_PATH
        self.assertTrue(DEFAULT_PATH.is_absolute())
        self.assertEqual(DEFAULT_PATH.parent.name, "data")

    def test_curriculum_uses_project_capability_names(self):
        # контракт со словарём brain/mind.py: в реестре есть ровно эти имена
        known = {"CLAIM_GIFTS", "BOSS_FIGHT", "CHECK_QUESTS", "SPIN_WHEEL",
                 "PETS_MAINTENANCE", "HATCH_EGG", "TRAIN_DUMBBELLS",
                 "TRAIN_PUSHUPS", "TRAIN_SITUPS", "TRAIN_MACHINE",
                 "TRAIN_AGILITY", "DISCOVER", "DAILY_REWARDS", "REBIRTH"}
        auto = SkillAutonomy(min_attempts_to_unlock=1, mastery_threshold=0.0)
        for _ in range(4):
            for a in auto.allowed_actions():
                auto.record(a, 1.0)
            auto.maybe_unlock()
        unknown = set(auto.allowed_actions()) - known
        self.assertEqual(unknown, set())

    def test_save_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "sa.json")
            SkillAutonomy(path=p).save()
            self.assertEqual([q.name for q in Path(td).iterdir()], ["sa.json"])


if __name__ == "__main__":
    unittest.main()
