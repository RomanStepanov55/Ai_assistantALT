"""The agent (alontito/agent.py): skills, recall, own-brain chat, learning loop.

No network: the gateway is faked; the offline path pins the honesty rule.
"""
import tempfile
import time
import unittest
from pathlib import Path

from alontito.agent import Agent
from alontito.automations import Automations
from alontito.memory import STATUS_OFFICIAL, Memory
from brain.learning_engine import LearningEngine


class FakeGateway:
    model = "fake-brain"

    def __init__(self, text: str = "Отвечаю по памяти."):
        self.text = text
        self.calls: list = []

    def chat(self, messages, *, role="own", on_delta=None):
        self.calls.append({"messages": messages, "role": role})
        return {"text": self.text, "provider": "own", "model": self.model}

    def own_status(self):
        return {"enabled": True, "reachable": True, "model": self.model,
                "detail": ""}


def make_agent(tmp: Path, gateway=None) -> Agent:
    return Agent(
        memory=Memory(tmp / "memory.json"),
        gateway=gateway,
        automations=Automations(tmp / "automations.json"),
        learning=LearningEngine(db_path=str(tmp / "learn.sqlite3"),
                                dataset_path=str(tmp / "curated.jsonl")),
    )


class TestSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent_test_"))
        self.agent = make_agent(self.tmp)

    def test_remember_is_an_immediate_fact(self):
        r = self.agent.answer("запомни: у меня встреча в пятницу")
        self.assertEqual(r["source"], "skill")
        facts = self.agent.memory.facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["status"], STATUS_OFFICIAL)
        self.assertEqual(facts[0]["source"], "пользователь")
        self.assertIn("встреча", facts[0]["text"])

    def test_remind_creates_an_automation(self):
        r = self.agent.answer("напомни через 5 минут выключить чайник")
        self.assertEqual(r["source"], "skill")
        items = self.agent.automations.list()
        self.assertEqual(len(items), 1)
        self.assertIn("чайник", items[0].text)

    def test_bad_reminder_is_not_a_guess(self):
        r = self.agent.answer("напомни как-нибудь потом")
        self.assertIn("не понял время", r["text"])
        self.assertEqual(self.agent.automations.list(), [])

    def test_recall_reads_memory_not_imagination(self):
        self.agent.answer("запомни: пароль от роутера в органайзере")
        r = self.agent.answer("что мы решали про роутер")
        self.assertEqual(r["source"], "skill")
        self.assertIn("органайзере", r["text"])

    def test_recall_of_unknown_is_honest(self):
        r = self.agent.answer("вспомни про кводно-планконовские единицы")
        self.assertIn("ничего нет", r["text"])

    def test_status_reports_real_counts(self):
        self.agent.answer("запомни: факт для статуса")
        r = self.agent.answer("статус")
        self.assertIn("фактов 1", r["text"])


class TestOwnBrainPath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent_own_"))
        self.gateway = FakeGateway("Ответ собственного мозга.")
        self.agent = make_agent(self.tmp, gateway=self.gateway)

    def test_memory_is_injected_into_the_prompt(self):
        self.agent.answer("запомни: сессия стартует в мае")
        self.agent.answer("когда начинается сессия?")
        msgs = self.gateway.calls[-1]["messages"]
        self.assertTrue(any("сессия стартует в мае" in m["content"]
                            for m in msgs if m["role"] == "system"))

    def test_answer_is_recorded_as_pending_training_data(self):
        self.agent.answer("расскажи что-нибудь полезное")
        pending = self.agent.pending_examples()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["prompt"], "расскажи что-нибудь полезное")
        self.assertEqual(pending[0]["answer"], "Ответ собственного мозга.")
        self.assertIn("chat:pc", pending[0]["source"])
        # approved=0 until a device confirms
        self.assertEqual(self.agent._learning().stats()["approved"], 0)

    def test_device_approval_then_export(self):
        self.agent.answer("вопрос один")
        pending = self.agent.pending_examples()
        self.assertTrue(self.agent.approve_example(pending[0]["fingerprint"]))
        self.assertEqual(self.agent._learning().stats()["approved"], 1)
        exported = self.agent._learning().export_approved()
        self.assertEqual(exported["exported"], 1)

    def test_device_dismiss_deletes_the_candidate(self):
        self.agent.answer("вопрос два")
        pending = self.agent.pending_examples()
        self.assertTrue(self.agent.dismiss_example(pending[0]["fingerprint"]))
        self.assertEqual(self.agent.pending_examples(), [])

    def test_skill_answers_are_not_training_data(self):
        self.agent.answer("запомни: это навык, не мозг")
        self.assertEqual(self.agent.pending_examples(), [])


class TestOfflinePath(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent_off_"))
        self.agent = make_agent(self.tmp, gateway=None)

    def test_no_brain_means_honest_offline_reply(self):
        r = self.agent.answer("объясни теорию относительности")
        self.assertEqual(r["source"], "offline")
        self.assertIn("не подключён", r["text"])
        self.assertIn("запомни", r["text"])          # names what it CAN do

    def test_offline_replies_are_never_training_data(self):
        self.agent.answer("случайный вопрос без мозга")
        self.assertEqual(self.agent.pending_examples(), [])
        self.assertEqual(self.agent._learning().stats()["examples"], 0)

    def test_skills_still_work_without_the_brain(self):
        r = self.agent.answer("запомни: кофе по вторникам")
        self.assertEqual(r["source"], "skill")
        self.assertEqual(len(self.agent.memory.facts()), 1)

    def test_status_names_the_brain_state(self):
        st = self.agent.status()
        self.assertFalse(st["brain"]["enabled"])
        self.assertIn("не подключён", self.agent.answer("статус")["text"])


if __name__ == "__main__":
    unittest.main()
