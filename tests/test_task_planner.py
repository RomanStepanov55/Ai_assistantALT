import unittest
from brain.task_planner import TaskPlanner, Task, Step


class TestPlanner(unittest.TestCase):
    def test_keyword_plan_strength(self):
        t = TaskPlanner().plan("качать силу быстрее")
        self.assertIn("train", [s.name for s in t.steps])

    def test_keyword_plan_files(self):
        t = TaskPlanner().plan("разобрать файлы в папке")
        self.assertIn("scan", [s.name for s in t.steps])

    def test_default_plan(self):
        t = TaskPlanner().plan("что-то абстрактное")
        self.assertEqual([s.name for s in t.steps], ["observe", "act", "measure", "reflect"])

    def test_reuse_lesson_params(self):
        lessons = [{"verdict": "better", "params": {"rep_delay": 0.55}, "name": "x"}]
        tp = TaskPlanner(lessons)
        t = tp.plan("новая тренировка rep_delay")
        self.assertEqual(t.steps[0].params["reused_lesson_params"], {"rep_delay": 0.55})

    def test_ignore_worse_lessons(self):
        lessons = [{"verdict": "worse", "params": {"d": 1}}]
        t = TaskPlanner(lessons).plan("x d")
        self.assertNotIn("reused_lesson_params", t.steps[0].params)

    def test_step_flow(self):
        t = TaskPlanner().plan("сила")
        s1 = tp_next = TaskPlanner().next_step(t)
        self.assertIsNotNone(s1)
        tp = TaskPlanner()
        tp.finish_step(t, True, 0.8)
        self.assertFalse(t.done)
        while not t.done:
            tp.finish_step(t, True)
        self.assertEqual(t.progress, 1.0)
        with self.assertRaises(RuntimeError):
            tp.finish_step(t, True)

    def test_summary(self):
        t = TaskPlanner().plan("сила")
        self.assertIn("Задача", TaskPlanner().summary(t))


if __name__ == "__main__":
    unittest.main()
