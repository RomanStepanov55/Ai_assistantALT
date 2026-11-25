"""Тесты NeuroLab v4: bandit, curiosity, experiments, persistence."""
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brain.neuro_lab import (NeuroLab, ThompsonArm, ThompsonBandit,
                             CuriosityEngine, ExperimentLab, NOISE_BAND)


class TestThompsonArm(unittest.TestCase):
    def test_prior_mean_is_half(self):
        self.assertAlmostEqual(ThompsonArm().mean, 0.5)

    def test_update_bounds(self):
        a = ThompsonArm()
        a.update(5.0)   # зажимается в 1.0
        self.assertLessEqual(a.mean, 1.0)
        a2 = ThompsonArm()
        a2.update(-3.0) # зажимается в 0.0
        self.assertGreaterEqual(a2.mean, 0.0)

    def test_roundtrip(self):
        a = ThompsonArm(); a.update(0.8); a.update(0.9)
        b = ThompsonArm.from_dict(a.to_dict())
        self.assertAlmostEqual(a.alpha, b.alpha)
        self.assertEqual(a.attempts, b.attempts)


class TestThompsonBandit(unittest.TestCase):
    def test_select_returns_candidate(self):
        b = ThompsonBandit()
        choice = b.select(["a", "b", "c"])
        self.assertIn(choice, ["a", "b", "c"])

    def test_bad_action_never_wins_longrun(self):
        b = ThompsonBandit()
        for _ in range(200):
            b.update("bad", 0.05)
            b.update("good", 0.95)
        wins = sum(b.select(["bad", "good"]) == "good" for _ in range(50))
        self.assertGreater(wins, 40)   # ~98% ожидается

    def test_roundtrip(self):
        b = ThompsonBandit(); b.update("x", 0.7)
        b2 = ThompsonBandit.from_dict(b.to_dict())
        self.assertIn("x", b2.arms)


class TestCuriosity(unittest.TestCase):
    def test_bonus_decreases_with_attempts(self):
        c = CuriosityEngine()
        self.assertGreater(c.bonus("a", attempts=0), c.bonus("a", attempts=10))

    def test_freshness_grows_with_time(self):
        c = CuriosityEngine()
        import time as _t
        old = c.bonus("a", attempts=2, last_used=_t.time() - 7*3600)   # 7 часов назад
        self.assertGreater(old, c.bonus("a", attempts=2, last_used=_t.time()))


class TestExperimentLab(unittest.TestCase):
    def _lab(self):
        return ExperimentLab()

    def test_judge_better(self):
        lab = self._lab()
        lab.begin("x", "h", {"d": 0.65}, {"strength_per_min": 100.0})
        lab.measure_after({"strength_per_min": 120.0})   # +20% > +10%
        self.assertEqual(lab.judge(), "better")
        self.assertIsNotNone(lab.active.locked_params)

    def test_judge_worse_and_rollback(self):
        lab = self._lab()
        lab.begin("x", "h", {"d": 0.65}, {"strength_per_min": 100.0})
        lab.measure_after({"strength_per_min": 80.0})    # -20% < -10%
        self.assertEqual(lab.judge(), "worse")
        self.assertEqual(lab.rollback(), {"d": 0.65})

    def test_judge_inconclusive_inside_noise_band(self):
        lab = self._lab()
        lab.begin("x", "h", {}, {"strength_per_min": 100.0})
        lab.measure_after({"strength_per_min": 104.0})   # внутри ±10%
        self.assertEqual(lab.judge(), "inconclusive")

    def test_judge_inconclusive_on_missing_key(self):
        lab = self._lab()
        lab.begin("x", "h", {}, {"strength_per_min": 100.0})
        lab.measure_after({"other": 1})
        self.assertEqual(lab.judge(), "inconclusive")

    def test_second_experiment_blocked_while_active(self):
        lab = self._lab()
        lab.begin("x", "h", {}, {})
        with self.assertRaises(RuntimeError):
            lab.begin("y", "h", {}, {})

    def test_guard_can_block_rollback(self):
        lab = ExperimentLab(guard=lambda a: False)
        lab.begin("x", "h", {"d": 1}, {"m": 100})
        lab.measure_after({"m": 50})
        lab.judge()
        with self.assertRaises(PermissionError):
            lab.rollback()


class TestNeuroLab(unittest.TestCase):
    def test_feed_and_select(self):
        lab = NeuroLab()
        lab.feed("TRAIN_DUMBBELLS", 120, 60, True)
        lab.feed("TRAIN_PUSHUPS", 30, 60, True)
        choice = lab.select(["TRAIN_DUMBBELLS", "TRAIN_PUSHUPS"])
        self.assertIn(choice, ["TRAIN_DUMBBELLS", "TRAIN_PUSHUPS"])
        self.assertGreater(lab.ema_rates["TRAIN_DUMBBELLS"], lab.ema_rates["TRAIN_PUSHUPS"])

    def test_fail_sets_cooldown(self):
        lab = NeuroLab()
        lab.feed("TRAIN_MACHINE", 0, 10, False, cooldown_on_fail=60)
        self.assertGreater(lab.cooldowns["TRAIN_MACHINE"], 0)

    def test_cooldown_excludes_action(self):
        lab = NeuroLab()
        lab.feed("a", 0, 1, False, cooldown_on_fail=9999)
        lab.feed("b", 10, 1, True)
        lab.feed("b", 10, 1, True)
        for _ in range(20):
            self.assertEqual(lab.select(["a", "b"]), "b")

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nl.json"
            lab = NeuroLab(p)
            lab.feed("a", 50, 60, True)
            lab.experiments.begin("e", "h", {"x": 1}, {"strength_per_min": 10})
            lab.experiments.measure_after({"strength_per_min": 15})
            lab.experiments.judge()
            lab.save()
            lab2 = NeuroLab.load(p)
            self.assertIn("a", lab2.bandit.arms)
            self.assertAlmostEqual(lab2.ema_rates["a"], lab.ema_rates["a"])
            self.assertEqual(lab2.experiments.active.verdict, "better")
            self.assertTrue((Path(td) / "nl.json").exists())

    def test_report_contains_lines(self):
        lab = NeuroLab()
        lab.feed("a", 10, 10, True)
        self.assertIn("a", lab.report())


class TestRewardScale(unittest.TestCase):
    """Темп силы в игре - 16 358...155 998/мин (knowledge/agent_state.json).
    Абсолютная шкала 0.5 + 0.5*tanh(темп/50) даёт всем успешным действиям 1.0."""

    def test_reward_is_scale_free(self):
        small, big = NeuroLab(), NeuroLab()
        small.feed("a", 120, 60, True)
        r_small = small.feed("b", 30, 60, True)
        big.feed("a", 120000, 60, True)
        r_big = big.feed("b", 30000, 60, True)
        self.assertAlmostEqual(r_small, r_big)
        self.assertAlmostEqual(r_small, 0.625)

    def test_measured_rates_are_distinguished(self):
        lab = NeuroLab()
        fast = lab.feed("dumbbells", 155998, 60, True)
        slow = lab.feed("other", 16358, 60, True)
        self.assertEqual(fast, 1.0)
        self.assertLess(slow, 0.7)

    def test_failure_quarters_the_reward(self):
        lab = NeuroLab()
        self.assertEqual(lab.feed("a", 100, 60, False), 0.25)     # четверть от 1.0
        self.assertEqual(lab.feed("b", 0, 60, False), 0.125)      # четверть от 0.5
        # успех и неудача не пересекаются при любом темпе
        ok_rewards, fail_rewards = [], []
        for delta, ok in ((0, True), (100000, True), (0, False), (100000, False)):
            (ok_rewards if ok else fail_rewards).append(lab.feed("c", delta, 60, ok))
        self.assertGreaterEqual(min(ok_rewards), 0.5)
        self.assertLessEqual(max(fail_rewards), 0.25)

    def test_success_without_gain_is_half(self):
        lab = NeuroLab()
        lab.feed("a", 100, 60, True)
        self.assertEqual(lab.feed("b", 0, 60, True), 0.5)

    def test_ema_smooths_a_single_empty_set(self):
        lab = NeuroLab()
        lab.feed("a", 120, 60, True)
        lab.feed("a", 0, 60, True)            # 0 из-за округления HUD
        self.assertAlmostEqual(lab.ema_rates["a"], 90.0)
        self.assertGreater(lab.pace_share("a"), 0.99)

    def test_unmeasured_sample_is_neutral_and_skips_ema(self):
        # E-001: подход с нечитаемым бейджем не может тянуть оценку темпа вниз
        lab = NeuroLab()
        lab.feed("a", 120, 60, True)
        before = lab.ema_rates["a"]
        reward = lab.feed("a", 0, 60, True, measured=False)
        self.assertEqual(reward, 0.5)
        self.assertEqual(lab.ema_rates["a"], before)


class TestLessonParams(unittest.TestCase):
    """Урок хранит параметры, которые ПРОВЕРЯЛИ, а не прежний baseline."""

    def _run(self, after):
        lab = ExperimentLab()
        lab.begin("exp", "h", baseline_params={"rep_delay": 0.65},
                  baseline_metrics={"strength_per_min": 100.0},
                  trial_params={"rep_delay": 0.55})
        lab.measure_after({"strength_per_min": after})
        return lab, lab.judge()

    def test_better_locks_the_tested_params(self):
        lab, verdict = self._run(120.0)
        self.assertEqual(verdict, "better")
        self.assertEqual(lab.active.locked_params, {"rep_delay": 0.55})
        self.assertEqual(lab.lessons[-1]["params"], {"rep_delay": 0.55})
        self.assertEqual(lab.lessons[-1]["baseline_params"], {"rep_delay": 0.65})

    def test_worse_lesson_still_names_what_was_tested(self):
        lab, verdict = self._run(80.0)
        self.assertEqual(verdict, "worse")
        self.assertEqual(lab.lessons[-1]["params"], {"rep_delay": 0.55})
        self.assertEqual(lab.rollback(), {"rep_delay": 0.65})

    def test_without_trial_params_the_baseline_is_what_was_tested(self):
        lab = ExperimentLab()
        lab.begin("x", "h", {"d": 0.65}, {"strength_per_min": 100.0})
        lab.measure_after({"strength_per_min": 120.0})
        lab.judge()
        self.assertEqual(lab.active.locked_params, {"d": 0.65})

    def test_planner_reuses_the_winning_params(self):
        from brain.task_planner import TaskPlanner
        lab = ExperimentLab()
        lab.begin("exp", "h", {"rep_delay": 0.65}, {"strength_per_min": 100.0},
                  trial_params={"rep_delay": 0.55})
        lab.measure_after({"strength_per_min": 120.0})
        lab.judge()
        task = TaskPlanner(lab.lessons).plan("новая тренировка rep_delay")
        self.assertEqual(task.steps[0].params["reused_lesson_params"],
                         {"rep_delay": 0.55})

    def test_trial_params_survive_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nl.json"
            lab = NeuroLab(p)
            lab.experiments.begin("e", "h", {"rep_delay": 0.65},
                                  {"strength_per_min": 100.0},
                                  trial_params={"rep_delay": 0.55})
            lab.save()
            self.assertEqual(NeuroLab.load(p).experiments.active.trial_params,
                             {"rep_delay": 0.55})


class TestStateFile(unittest.TestCase):
    def test_non_positive_beta_parameters_are_reset(self):
        arm = ThompsonArm.from_dict({"alpha": 0, "beta": -1})
        self.assertEqual((arm.alpha, arm.beta), (1.0, 1.0))
        bandit = ThompsonBandit.from_dict({"x": {"alpha": float("nan"), "beta": 1.0}})
        self.assertEqual(bandit.select(["x"]), "x")

    def test_broken_json_raises_for_the_caller_to_handle(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nl.json"
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                NeuroLab.load(p)

    def test_save_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nl.json"
            NeuroLab(p).save()
            self.assertEqual([q.name for q in Path(td).iterdir()], ["nl.json"])


if __name__ == "__main__":
    unittest.main()
