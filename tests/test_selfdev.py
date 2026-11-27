# -*- coding: utf-8 -*-
import unittest
from alontito import selfdev
from alontito.brain import Brain
from alontito.memory import Memory, SkillStat


class TestSelfDev(unittest.TestCase):
    def setUp(self):
        self.data = {}

    def test_bucket_initialization(self):
        box = selfdev.bucket(self.data)
        self.assertIn("version", box)
        self.assertIsNone(box["active"])
        self.assertEqual(box["closed"], [])
        self.assertEqual(box["lessons"], [])
        self.assertIn("action", box["params"])

    def test_rank_skills_ucb(self):
        s1 = SkillStat(name="train_heavy", attempts=10, successes=9, reward=5000.0, seconds=60.0)
        s2 = SkillStat(name="train_light", attempts=2, successes=2, reward=800.0, seconds=30.0)
        ranked = selfdev.rank_skills([s1, s2])
        self.assertEqual(len(ranked), 2)
        # s1 has higher per_minute (5000 vs 1600)
        self.assertEqual(ranked[0][1].name, "train_heavy")
        self.assertEqual(selfdev.next_skill_name([s1, s2]), "train_heavy")

    def test_rank_skills_empty(self):
        self.assertEqual(selfdev.rank_skills([]), [])
        self.assertIsNone(selfdev.next_skill_name([]))

    def test_rank_skills_include_untried(self):
        s_known = SkillStat(name="train_known", attempts=5, successes=4, reward=1000.0, seconds=60.0)
        s_untried = SkillStat(name="train_new", attempts=0)
        # Without untried, only known is ranked
        self.assertEqual(len(selfdev.rank_skills([s_known, s_untried], include_untried=False)), 1)
        # With untried, untried is included and given high exploratory priority
        ranked = selfdev.rank_skills([s_known, s_untried], include_untried=True)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0][1].name, "train_new")

    def test_proposal_lifecycle(self):
        p1 = {
            "problem": "low reps",
            "change": "increase reps to 25",
            "metric": "strength_per_min",
            "rollback": "revert to 20",
            "evidence": "measured",
        }
        item = selfdev.register_proposal(self.data, p1)
        self.assertEqual(item["change"], "increase reps to 25")
        self.assertEqual(item["tries"], 0)

        # Re-registering identical proposal returns active
        item2 = selfdev.register_proposal(self.data, p1)
        self.assertEqual(item2["id"], item["id"])

        # Another proposal replaces an untouched proposal
        p2 = {"change": "change to situps"}
        item3 = selfdev.register_proposal(self.data, p2)
        self.assertEqual(item3["change"], "change to situps")

        # But once tries > 0, it is protected
        item3["tries"] = 1
        p3 = {"change": "change to pushups"}
        item4 = selfdev.register_proposal(self.data, p3)
        self.assertEqual(item4["change"], "change to situps")

    def test_verdict_accepted(self):
        p = {"change": "faster click delay"}
        selfdev.register_proposal(self.data, p)
        res = selfdev.note_verdict(self.data, "лучше", "+20% speed", 6000.0)
        self.assertEqual(res["status"], "accepted")
        st = selfdev.status(self.data)
        self.assertIsNone(st["active"])
        self.assertEqual(st["accepted"], 1)
        self.assertTrue(any("оставить: faster click delay" in l for l in st["lessons"]))

    def test_verdict_rejected_with_rollback(self):
        # Configure custom parameter
        selfdev.set_active_params(self.data, {"delay": 0.02})
        self.assertEqual(selfdev.get_active_params(self.data)["delay"], 0.02)

        p = {"change": "aggressive 0.02s delay"}
        selfdev.register_proposal(self.data, p)
        res = selfdev.note_verdict(self.data, "хуже", "frame drops", 1000.0)
        self.assertEqual(res["status"], "rejected")
        st = selfdev.status(self.data)
        self.assertIsNone(st["active"])
        self.assertEqual(st["rejected"], 1)
        self.assertTrue(any("откатить: aggressive 0.02s delay" in l for l in st["lessons"]))
        # Parameter should be rolled back to default
        self.assertEqual(selfdev.get_active_params(self.data)["delay"], 0.08)

    def test_verdict_inconclusive_after_two_tries(self):
        p = {"change": "alternate keys"}
        selfdev.register_proposal(self.data, p)
        # First try: stays active
        res1 = selfdev.note_verdict(self.data, "без изменений", "noise band", 3000.0)
        self.assertEqual(res1["status"], "active")
        self.assertEqual(res1["tries"], 1)
        self.assertIsNotNone(selfdev.status(self.data)["active"])

        # Second try: closes as inconclusive
        res2 = selfdev.note_verdict(self.data, "без изменений", "noise band", 3050.0)
        self.assertEqual(res2["status"], "inconclusive")
        self.assertIsNone(selfdev.status(self.data)["active"])
        self.assertEqual(selfdev.status(self.data)["closed"], 1)

    def test_lessons_deduplicated_and_capped(self):
        for _ in range(3):
            selfdev.add_lesson(self.data, "repeat lesson")
        st = selfdev.status(self.data)
        self.assertEqual(st["lessons"].count("repeat lesson"), 1)

    def test_brain_integration(self):
        import tempfile
        from pathlib import Path
        tmp = tempfile.mkdtemp(prefix="alontito_test_selfdev_")
        mem = Memory(Path(tmp) / "memory.json")
        brain = Brain(mem)
        brain.remember_lesson("never lift during dialog")
        dev = brain.development()
        self.assertIn("never lift during dialog", dev["lessons"])

        # Since the pivot the agent has no routine of its own: propose()
        # honestly answers None instead of inventing a change (2026-11-24).
        mem.record_skill("train", False, reward=0, seconds=10.0, error="dialog open")
        self.assertIsNone(brain.propose())


if __name__ == "__main__":
    unittest.main()
