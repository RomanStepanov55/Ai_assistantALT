import tempfile
import unittest
from pathlib import Path
from brain.learning_engine import LearningEngine
from evaluation.regression_suite import evaluate_responses

class TestLearningEngine(unittest.TestCase):
    def test_unapproved_examples_never_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'learning.sqlite3'
            ds = Path(tmp) / 'approved.jsonl'
            engine = LearningEngine(str(db), str(ds))
            engine.add_example('question', 'answer', 'lesson', source='model', approved=False)
            self.assertEqual(engine.export_approved()['exported'], 0)
            self.assertFalse(ds.exists())

    def test_approved_examples_export_once_and_deduplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LearningEngine(str(Path(tmp)/'db.sqlite3'), str(Path(tmp)/'data.jsonl'))
            self.assertTrue(engine.add_example('q', 'a', source='human', approved=True, evidence='user'))
            self.assertFalse(engine.add_example('q', 'a', source='human', approved=True, evidence='user'))
            self.assertEqual(engine.export_approved()['exported'], 1)
            self.assertEqual(engine.export_approved()['exported'], 0)

    def test_promotion_requires_real_gain(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = LearningEngine(str(Path(tmp)/'db.sqlite3'), str(Path(tmp)/'data.jsonl'))
            self.assertTrue(engine.compare_and_promote('candidate', 'base', .90, .85))
            self.assertFalse(engine.compare_and_promote('bad', 'base', .80, .85))

    def test_regression_suite_scores_contracts(self):
        report = evaluate_responses({
            'honesty_unknown': 'Нет данных, не могу определить.',
            'uncertainty': 'Недостаточно данных, не могу подтвердить.',
            'memory_not_fact': 'Нет, память — это подсказка, не факт.'
        })
        self.assertEqual(report['score'], 1.0)

if __name__ == '__main__':
    unittest.main()
