"""Memory consolidation: dedupe, contradictions, decay, promotion nominees.

Pins the honesty rules:
  - «в 9:00» and «в 12:00» never merge (digits must match for a dedupe)
  - user-sourced facts are never deleted, only merged exact/near twins
  - contradictions get the status and an open question, not a deletion
  - consolidation NOMINATES hypotheses for promotion, never promotes
"""
import tempfile
import time
import unittest
from pathlib import Path

from alontito.memory import (STATUS_HYPOTHESIS, STATUS_MEASURED,
                             STATUS_OFFICIAL, STATUS_OUTDATED,
                             STATUS_SECONDARY, Memory)
from brain.consolidation import consolidate


def make_memory():
    tmp = Path(tempfile.mkdtemp(prefix="consolidation_"))
    return Memory(tmp / "memory.json")


class TestDedupe(unittest.TestCase):
    def test_exact_duplicate_collapses(self):
        memory = make_memory()
        memory.add_fact("пароль от роутера в органайзере", STATUS_OFFICIAL, "пользователь")
        memory.add_fact("пароль от роутера в органайзере", STATUS_OFFICIAL, "пользователь")
        report = consolidate(memory)
        self.assertEqual(report["deduped"], 1)
        self.assertEqual(len(memory.facts()), 1)

    def test_same_words_different_numbers_never_merge(self):
        memory = make_memory()
        memory.add_fact("встреча в пятницу в 15:00", STATUS_OFFICIAL, "пользователь")
        memory.add_fact("встреча в пятницу в 16:00", STATUS_OFFICIAL, "пользователь")
        report = consolidate(memory)
        self.assertEqual(report["deduped"], 0)
        self.assertEqual(len(memory.facts()), 2)

    def test_merge_keeps_the_strongest_copy(self):
        memory = make_memory()
        memory.add_fact("пинограф на кухне второй справа", STATUS_SECONDARY, "друг")
        memory.add_fact("пинограф на кухне второй справа", STATUS_OFFICIAL, "пользователь")
        consolidate(memory)
        facts = memory.facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["status"], STATUS_OFFICIAL)
        self.assertEqual(facts[0]["source"], "пользователь")


class TestContradictions(unittest.TestCase):
    def test_opposite_claims_get_marked_and_questioned(self):
        memory = make_memory()
        memory.add_fact("серпентарий открывается в 9:00", STATUS_OFFICIAL, "пользователь")
        memory.add_fact("серпентарий открывается в 12:00", STATUS_OFFICIAL, "пользователь")
        report = consolidate(memory)
        self.assertEqual(report["contradictions"], 2)
        statuses = {f["status"] for f in memory.facts()}
        self.assertIn("ПРОТИВОРЕЧИЕ", statuses)
        questions = memory.open_questions()
        self.assertTrue(any("противоречие" in q["question"] for q in questions))

    def test_two_clean_facts_are_untouched(self):
        memory = make_memory()
        memory.add_fact("ключи лежат в ящике", STATUS_OFFICIAL, "пользователь")
        memory.add_fact("паспорт лежит в сейфе", STATUS_OFFICIAL, "пользователь")
        report = consolidate(memory)
        self.assertEqual(report["contradictions"], 0)
        self.assertEqual(len(memory.open_questions()), 0)


class TestDecay(unittest.TestCase):
    def test_old_secondary_facts_become_outdated(self):
        memory = make_memory()
        memory.add_fact("какая-то новость со слов знакомого", STATUS_SECONDARY, "друг")
        memory.data["facts"][0]["added"] = time.time() - 61 * 86400
        report = consolidate(memory, now=time.time())
        self.assertEqual(report["decayed"], 1)
        self.assertEqual(memory.facts()[0]["status"], STATUS_OUTDATED)

    def test_fresh_secondary_facts_stay(self):
        memory = make_memory()
        memory.add_fact("свежая новость со слов знакомого", STATUS_SECONDARY, "друг")
        report = consolidate(memory, now=time.time())
        self.assertEqual(report["decayed"], 0)
        self.assertEqual(memory.facts()[0]["status"], STATUS_SECONDARY)


class TestPromotion(unittest.TestCase):
    def test_hypotheses_are_nominated_never_promoted(self):
        memory = make_memory()
        memory.add_fact("молния быстрее замера дедлайна на замер", STATUS_HYPOTHESIS, "гипотеза")
        memory.add_fact("у кофе произвольный срок годности", STATUS_HYPOTHESIS, "гипотеза")
        report = consolidate(memory)
        self.assertEqual(len(report["promotable"]), 1)     # only the measurable one
        statuses = {f["status"] for f in memory.facts()}
        self.assertIn(STATUS_HYPOTHESIS, statuses)         # nobody was promoted
        self.assertNotIn(STATUS_MEASURED, statuses)

    def test_official_facts_survive_consolidation(self):
        memory = make_memory()
        memory.add_fact("день рождения жены 14 марта", STATUS_OFFICIAL, "пользователь")
        consolidate(memory)
        self.assertEqual(len(memory.facts()), 1)
        self.assertEqual(memory.facts()[0]["status"], STATUS_OFFICIAL)


if __name__ == "__main__":
    unittest.main()
