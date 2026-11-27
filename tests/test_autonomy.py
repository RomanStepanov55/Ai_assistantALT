"""Autonomy engine: routines, proposals, verdicts - measured, never felt.

Pins the self-development discipline:
  - proposals exist only for measured weaknesses, one at a time
  - the proposal's change is APPLIED (that is the experiment)
  - a verdict comes no sooner than one full interval after opening
  - "хуже" rolls parameters back, "лучше" locks them in as a lesson
  - two "без изменений" close the experiment as inconclusive
  - routines are paced (governor) and respect their own interval
"""
import tempfile
import unittest
from pathlib import Path

from alontito import selfdev
from alontito.memory import STATUS_OFFICIAL, Memory
from alontito.selfdev import bucket
from brain.autonomy import AutonomyEngine
from brain.learning_engine import LearningEngine


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_learning(tmp: Path, examples: int = 4, approved: int = 0):
    le = LearningEngine(db_path=str(tmp / "learn.sqlite3"),
                        dataset_path=str(tmp / "curated.jsonl"))
    for i in range(examples):
        le.add_example(f"вопрос {i}", f"ответ {i}", source="chat:test")
    with le._connect() as c:
        fps = [r["fingerprint"] for r in c.execute(
            "SELECT fingerprint FROM learning_examples ORDER BY id").fetchall()]
    for fp in fps[:max(0, approved)]:
        le.approve(fp, "test")
    return le


def make_engine(clock, learning=None):
    tmp = Path(tempfile.mkdtemp(prefix="autonomy_"))
    memory = Memory(tmp / "memory.json")
    eng = AutonomyEngine(memory=memory, learning=learning, clock=clock)
    return eng, memory


class TestMeasure(unittest.TestCase):
    def test_no_data_means_none_not_zero(self):
        eng, _ = make_engine(FakeClock())
        m = eng.measure()
        self.assertIsNone(m["memory_health"])
        self.assertIsNone(m["helpfulness"])
        self.assertIsNone(m["weakest"])

    def test_helpfulness_from_real_learning_stats(self):
        tmp = Path(tempfile.mkdtemp(prefix="autonomy_m_"))
        le = make_learning(tmp, examples=4, approved=2)
        eng, _ = make_engine(FakeClock(), learning=le)
        m = eng.measure()
        self.assertEqual(m["examples"], 4)
        self.assertEqual(m["helpfulness"], 0.5)


class TestProposals(unittest.TestCase):
    def test_low_helpfulness_proposes_deeper_review(self):
        tmp = Path(tempfile.mkdtemp(prefix="autonomy_p_"))
        le = make_learning(tmp, examples=4, approved=0)
        eng, memory = make_engine(FakeClock(), learning=le)
        events = eng.tick()
        box = bucket(memory.data)
        self.assertIsNotNone(box["active"])
        self.assertEqual(box["active"]["metric"], "helpfulness")
        # the change IS applied: review_rounds moved into params
        self.assertEqual(box["params"]["review_rounds"],
                         selfdev.DEFAULT_PARAMS["review_rounds"] + 1)
        self.assertTrue(any(e.get("type") == "experiment" for e in events))

    def test_no_weakness_no_proposal(self):
        tmp = Path(tempfile.mkdtemp(prefix="autonomy_p2_"))
        le = make_learning(tmp, examples=4, approved=4)
        eng, memory = make_engine(FakeClock(), learning=le)
        eng.tick()
        box = bucket(memory.data)
        self.assertIsNone(box["active"])


class TestVerdicts(unittest.TestCase):
    def test_improvement_locks_the_change_as_lesson(self):
        tmp = Path(tempfile.mkdtemp(prefix="autonomy_v1_"))
        le = make_learning(tmp, examples=4, approved=0)
        clock = FakeClock()
        eng, memory = make_engine(clock, learning=le)
        eng.tick()                                   # proposal, baseline 0.0
        for fp in self._all_fingerprints(le):
            le.approve(fp, "test")                   # helpfulness 0.0 -> 1.0
        clock.advance(3600)                          # past the judging interval
        events = eng.tick()
        box = bucket(memory.data)
        self.assertTrue(any(e.get("kind") == "лучше" for e in events))
        self.assertTrue(any("оставить" in l for l in box["lessons"]))

    def test_regression_rolls_parameters_back(self):
        tmp = Path(tempfile.mkdtemp(prefix="autonomy_v2_"))
        memory = Memory(tmp / "memory.json")
        clock = FakeClock(100000.0)                  # routines may run
        eng = AutonomyEngine(memory=memory, clock=clock)
        memory.add_fact("серпентарий открывается в 9:00", STATUS_OFFICIAL, "тест")
        memory.add_fact("серпентарий открывается в 12:00", STATUS_OFFICIAL, "тест")
        memory.add_fact("ключи лежат в ящике", STATUS_OFFICIAL, "тест")
        memory.add_fact("паспорт лежит в сейфе", STATUS_OFFICIAL, "тест")
        eng.tick()
        box = bucket(memory.data)
        self.assertIsNotNone(box["active"])          # memory weakness -> proposal
        self.assertEqual(box["active"]["metric"], "memory_health")
        self.assertEqual(box["active"]["metric_before"], 0.5)
        # make the metric worse: another contradicting pair
        memory.add_fact("ключи лежат в сумке", STATUS_OFFICIAL, "тест")
        memory.add_fact("ключи лежат в кармане", STATUS_OFFICIAL, "тест")
        clock.advance(3600)
        events = eng.tick()                          # consolidate + judge
        box = bucket(memory.data)
        self.assertTrue(any(e.get("kind") == "хуже" for e in events))
        self.assertTrue(any(c["status"] == "rejected" for c in box["closed"]))
        # the rollback happened: prev_params carries the pre-experiment value
        self.assertEqual(box["prev_params"].get("interval_min"), 45)

    def test_two_no_changes_close_as_inconclusive(self):
        tmp = Path(tempfile.mkdtemp(prefix="autonomy_v3_"))
        le = make_learning(tmp, examples=4, approved=0)
        clock = FakeClock()
        eng, memory = make_engine(clock, learning=le)
        eng.tick()                                   # proposal
        clock.advance(3600)
        eng.tick()                                   # без изменений, try 1
        clock.advance(3600)
        eng.tick()                                   # без изменений, try 2
        box = bucket(memory.data)
        self.assertTrue(any(c["status"] == "inconclusive" for c in box["closed"]))
        self.assertTrue(any("не меняет темп" in l for l in box["lessons"]))

    def test_verdict_waits_a_full_interval(self):
        tmp = Path(tempfile.mkdtemp(prefix="autonomy_v4_"))
        le = make_learning(tmp, examples=4, approved=0)
        clock = FakeClock()
        eng, memory = make_engine(clock, learning=le)
        eng.tick()                                   # proposal, baseline 0.0
        for fp in self._all_fingerprints(le):
            le.approve(fp, "test")                   # improvement already there
        clock.advance(60)                            # NOT a full interval
        events = eng.tick()
        box = bucket(memory.data)
        self.assertIsNotNone(box["active"])          # not judged too early
        self.assertFalse(any(e.get("kind") == "лучше" for e in events))

    @staticmethod
    def _all_fingerprints(le):
        with le._connect() as c:
            return [r["fingerprint"] for r in c.execute(
                "SELECT fingerprint FROM learning_examples").fetchall()]


class TestRoutines(unittest.TestCase):
    def test_routine_respects_governor_and_interval(self):
        clock = FakeClock(100000.0)                  # far past the first interval
        eng, _ = make_engine(clock)
        first = eng.run_routines()
        self.assertTrue(first)                       # consolidation ran
        second = eng.run_routines()                  # immediately: no token
        self.assertEqual(second, [])
        clock.advance(120)                           # token back, interval not
        third = eng.run_routines()
        self.assertEqual(third, [])

    def test_journal_records_the_routine(self):
        clock = FakeClock(100000.0)
        eng, memory = make_engine(clock)
        eng.run_routines()
        self.assertTrue((memory.path.parent / "journal.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
