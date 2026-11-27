"""The cognitive pipeline: routing, decomposition, critique, grounding.

No network: chat_fn is a stub. Pins:
  - effort routing is deterministic, never asked of the model
  - a malformed critique never destroys a usable draft
  - grounding points numbers at sources; personal unverified numbers warn
  - memory reaches both the prompt and the grounding check
"""
import unittest

from brain.cognition import (CognitiveCore, decompose, grounding_check,
                             route_effort, rounds_for_effort,
                             _parse_review, _split_plan_draft)


def stub_chat(responses):
    """chat_fn with queued replies; records every call, streams via on_delta."""
    calls = []

    def chat(messages, on_delta=None):
        calls.append(messages)
        text = responses[len(calls) - 1] if len(calls) <= len(responses) \
            else responses[-1]
        if on_delta:
            on_delta(text)
        return text

    chat.calls = calls
    return chat


class FakeMemory:
    def __init__(self, facts):
        self._facts = facts

    def find_facts(self, needle):
        return [f for f in self._facts if needle in (f["text"] or "").lower()]

    def facts(self, status=None):
        return self._facts


class TestRouting(unittest.TestCase):
    def test_effort_levels_are_deterministic(self):
        self.assertEqual(route_effort("напиши модуль парсера"), "max")
        self.assertEqual(route_effort("спланируй архитектуру агента"), "max")
        self.assertEqual(route_effort("объясни, почему конвейер глубже"), "high")
        self.assertEqual(route_effort("какая сегодня погода?"), "medium")
        self.assertEqual(route_effort("привет"), "low")

    def test_same_input_same_effort(self):
        self.assertEqual(route_effort("объясни теорию относительности"),
                         route_effort("объясни теорию относительности"))

    def test_rounds_scale_and_cap(self):
        self.assertEqual(rounds_for_effort("low", 3), 0)
        self.assertEqual(rounds_for_effort("medium", 2), 1)
        self.assertEqual(rounds_for_effort("high", 2), 2)
        self.assertEqual(rounds_for_effort("max", 3), 3)
        self.assertEqual(rounds_for_effort("high", 7), 3)      # cap at 3

    def test_decompose_splits_composite_request(self):
        parts = decompose("сначала сравни варианты; затем выбери лучший")
        self.assertGreaterEqual(len(parts), 2)


class TestGrounding(unittest.TestCase):
    def test_supported_number_points_at_memory(self):
        facts = [{"text": "у меня 3 проекта в работе"}]
        g = grounding_check("сколько у меня проектов?", "у тебя 3 проекта.",
                            facts, [])
        self.assertEqual(g.confidence, "grounded")

    def test_unverified_personal_number_is_flagged(self):
        g = grounding_check("сколько у меня проектов?", "у тебя 7 проектов.",
                            [], [])
        self.assertEqual(g.confidence, "unverified")
        self.assertIn("7", g.unsupported)

    def test_non_personal_unsupported_is_partial(self):
        g = grounding_check("столица Австралии?",
                            "Население Канберры 400000 человек.", [], [])
        self.assertEqual(g.confidence, "partial")

    def test_no_numbers_is_grounded(self):
        g = grounding_check("привет", "Привет! Чем помочь?", [], [])
        self.assertEqual(g.confidence, "grounded")

    def test_number_from_the_question_itself_counts_as_supported(self):
        g = grounding_check("чему равен корень из 144?",
                            "корень из 144 равен 12.", [], [])
        # 144 comes from the question (its source), 12 is the computed result
        self.assertEqual(g.numbers_total, 2)
        self.assertEqual(g.numbers_supported, 1)
        self.assertEqual(g.unsupported, ["12"])


class TestParsing(unittest.TestCase):
    def test_plan_and_draft_markers(self):
        plan, draft = _split_plan_draft("PLAN: шаг1\nшаг2\nDRAFT: ответ")
        self.assertEqual(plan, ["шаг1", "шаг2"])
        self.assertEqual(draft, "ответ")

    def test_missing_markers_whole_text_is_draft(self):
        plan, draft = _split_plan_draft("просто ответ без маркеров")
        self.assertEqual(plan, [])
        self.assertEqual(draft, "просто ответ без маркеров")

    def test_review_fixed_replaces_answer(self):
        v = _parse_review("REVIEW: FIXED\nцифра не подтверждена\nFINAL: Исправленный ответ")
        self.assertEqual(v["status"], "fixed")
        self.assertEqual(v["answer"], "Исправленный ответ")

    def test_review_pass(self):
        self.assertEqual(_parse_review("REVIEW: PASS\nчисто")["status"], "passed")

    def test_malformed_review_is_invalid(self):
        self.assertEqual(_parse_review("выглядит нормально")["status"],
                         "invalid_review_format")


class TestPipeline(unittest.TestCase):
    def test_low_effort_is_one_call_and_streams(self):
        chat = stub_chat(["Готово."])
        core = CognitiveCore(chat_fn=chat)
        got = []
        result = core.answer("привет", on_delta=got.append)
        self.assertEqual(result.answer, "Готово.")
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(result.effort, "low")
        self.assertEqual(result.reviews, [])
        self.assertTrue(got)                       # streaming passed through

    def test_high_effort_critique_fixes_the_draft(self):
        chat = stub_chat([
            "PLAN: сверить с памятью\nDRAFT: Черновик с ошибкой",
            "REVIEW: FIXED\nцифра не подтверждена\nFINAL: Исправленный ответ",
            "REVIEW: PASS\nчисто",
        ])
        core = CognitiveCore(chat_fn=chat, max_review_rounds=2)
        result = core.answer("объясни подробно, как устроен конвейер")
        self.assertEqual(result.answer, "Исправленный ответ")
        self.assertEqual([r["status"] for r in result.reviews], ["fixed", "passed"])
        self.assertEqual(result.plan, ["сверить с памятью"])

    def test_malformed_critique_keeps_the_draft(self):
        chat = stub_chat(["Черновик", "Критика без маркеров"])
        core = CognitiveCore(chat_fn=chat, max_review_rounds=2)
        result = core.answer("объясни подробно тему")
        self.assertEqual(result.answer, "Черновик")
        self.assertEqual(result.reviews[0]["status"], "invalid_review_format")

    def test_unverified_personal_numbers_get_warning(self):
        chat = stub_chat(["У тебя 5 проектов в списке."])
        core = CognitiveCore(chat_fn=chat)
        result = core.answer("сколько у меня проектов?")
        self.assertIn("⚠", result.answer)
        self.assertEqual(result.confidence, "unverified")

    def test_supported_answer_stays_clean(self):
        memory = FakeMemory([{"text": "у меня 5 проектов",
                              "status": "ПОДТВЕРЖДЕНО ОФИЦИАЛЬНЫМ ИСТОЧНИКОМ"}])
        chat = stub_chat(["У тебя 5 проектов в списке."])
        core = CognitiveCore(chat_fn=chat, memory=memory)
        result = core.answer("сколько у меня проектов?")
        self.assertNotIn("⚠", result.answer)
        self.assertEqual(result.confidence, "grounded")

    def test_memory_reaches_the_prompt(self):
        memory = FakeMemory([{"text": "сессия стартует в мае",
                              "status": "ПОДТВЕРЖДЕНО ОФИЦИАЛЬНЫМ ИСТОЧНИКОМ"}])
        chat = stub_chat(["Ответ."])
        core = CognitiveCore(chat_fn=chat, memory=memory)
        core.answer("когда начинается сессия?")
        system_msgs = [m for m in chat.calls[0] if m["role"] == "system"]
        self.assertTrue(any("сессия стартует в мае" in m["content"]
                            for m in system_msgs))

    def test_meta_carries_the_metrics(self):
        chat = stub_chat(["Ответ."])
        core = CognitiveCore(chat_fn=chat)
        meta = core.answer("привет").meta()
        self.assertEqual(meta["effort"], "low")
        self.assertEqual(meta["rounds"], 0)
        self.assertIn("confidence", meta["grounding"])


if __name__ == "__main__":
    unittest.main()
