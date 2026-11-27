"""Teacher pipeline (tools/teacher_generate.py): synthetic examples stay
pending until a human approves them. The teacher cannot approve itself.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.teacher_generate import _extract_pairs, generate, style_anchors  # noqa: E402
from brain.learning_engine import LearningEngine  # noqa: E402


class FakeTeacher:
    model = "teacher-x"

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list = []

    def complete(self, prompt: str, *, system: str = "", transport=None) -> str:
        self.prompts.append(prompt)
        self.last_system = system
        return self.reply


def engine(tmp: Path) -> LearningEngine:
    return LearningEngine(db_path=str(tmp / "learning.sqlite3"),
                          dataset_path=str(tmp / "curated_training.jsonl"))


class TestExtractPairs(unittest.TestCase):
    def test_json_array(self):
        raw = 'Вот пары: [{"prompt":"в","answer":"о","lesson":"l"}] надеюсь'
        self.assertEqual(_extract_pairs(raw)[0]["prompt"], "в")

    def test_json_lines(self):
        raw = '{"prompt":"a","answer":"b"}\n{"prompt":"c","answer":"d"}'
        self.assertEqual(len(_extract_pairs(raw)), 2)

    def test_garbage_is_empty(self):
        self.assertEqual(_extract_pairs("совсем не JSON"), [])


class TestGenerateStoresPendingOnly(unittest.TestCase):
    def test_pairs_are_pending_and_honest(self):
        tmp = Path(tempfile.mkdtemp())
        eng = engine(tmp)
        teacher = FakeTeacher(json.dumps([
            {"prompt": "Что такое честный замер?",
             "answer": "Замер с подтверждёнными числами до и после; без числа — «не подтверждено».",
             "lesson": "не смог измерить ≠ получилось"},
            {"prompt": "", "answer": "пустой вопрос"},
        ], ensure_ascii=False))
        stats = generate(teacher, eng, ["честные замеры"], per_topic=2)
        self.assertEqual(stats["stored"], 1)
        self.assertEqual(stats["skipped"], 1)
        with eng._connect() as c:
            rows = c.execute("SELECT approved, source, evidence FROM learning_examples").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["approved"], 0)               # pending: teacher cannot self-approve
        self.assertIn("teacher:teacher-x", rows[0]["source"])
        self.assertIn("НЕ ПОДТВЕРЖДЕНО", rows[0]["evidence"])
        # System prompt carries the honesty rules to the teacher
        self.assertIn("не подтверждено", teacher.last_system)

    def test_duplicate_fingerprints_are_not_stored_twice(self):
        tmp = Path(tempfile.mkdtemp())
        eng = engine(tmp)
        reply = json.dumps([{"prompt": "в", "answer": "о"}], ensure_ascii=False)
        teacher = FakeTeacher(reply)
        generate(teacher, eng, ["тема"], per_topic=1)
        stats = generate(teacher, eng, ["тема"], per_topic=1)
        self.assertEqual(stats["stored"], 0)
        self.assertEqual(stats["skipped"], 1)

    def test_approval_gate_then_export(self):
        tmp = Path(tempfile.mkdtemp())
        eng = engine(tmp)
        teacher = FakeTeacher(json.dumps(
            [{"prompt": "в1", "answer": "о1", "lesson": "урок"}], ensure_ascii=False))
        generate(teacher, eng, ["т"], per_topic=1)
        # nothing exported before approval
        self.assertEqual(eng.export_approved()["exported"], 0)
        with eng._connect() as c:
            fp = c.execute("SELECT fingerprint FROM learning_examples").fetchone()[0]
        self.assertTrue(eng.approve(fp, "human:review"))
        result = eng.export_approved()
        self.assertEqual(result["exported"], 1)
        data = (tmp / "curated_training.jsonl").read_text(encoding="utf-8").splitlines()
        item = json.loads(data[0])
        self.assertEqual(item["messages"][0]["content"], "в1")
        self.assertEqual(item["messages"][1]["content"], "о1")

    def test_style_anchors_only_from_approved(self):
        tmp = Path(tempfile.mkdtemp())
        eng = engine(tmp)
        eng.add_example("q1", "a1", source="s1")
        eng.add_example("q2", "a2", source="s2")
        eng.approve(eng._fingerprint("q2", "a2", "s2"), "human")
        anchors = style_anchors(eng, 5)
        self.assertEqual([a["prompt"] for a in anchors], ["q2"])


if __name__ == "__main__":
    unittest.main()
