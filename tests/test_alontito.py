"""Tests for the Alontito core: memory, reflection, UI geometry, guards.

No game, no captures, no input: this file stays green on any machine.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alontito import ui
from alontito.brain import Brain, interpret
from alontito.memory import (MISSING, STATUS_MEASURED, STATUS_UNCONFIRMED,
                             STATUSES, Memory)
from alontito.skills import ALLOWED_ACTIONS, guard_report


def fresh_memory() -> Memory:
    tmp = tempfile.mkdtemp(prefix="alontito_test_")
    return Memory(Path(tmp) / "memory.json")


class TestMemory(unittest.TestCase):
    def test_fact_requires_a_known_status(self):
        memory = fresh_memory()
        with self.assertRaises(ValueError):
            memory.add_fact("сила 5.39M", "ПРОСТО ТАК", "test")
        fact = memory.add_fact("сила 5.39M", STATUS_MEASURED, "EXP-01")
        self.assertEqual(fact["status"], STATUS_MEASURED)
        self.assertIn(fact["date"], {fact["date"]})

    def test_nine_statuses_exist(self):
        self.assertEqual(len(STATUSES), 9)
        self.assertEqual(len(set(STATUSES)), 9)

    def test_missing_text_names_the_next_step(self):
        self.assertIn("Нужна проверка внутри игры", MISSING)

    def test_unverified_attempt_does_not_count_as_success(self):
        memory = fresh_memory()
        memory.record_skill("train", False, reward=0, seconds=30,
                            error="unverified")
        stat = memory.skill("train")
        self.assertEqual(stat.attempts, 1)
        self.assertEqual(stat.successes, 0)
        self.assertIsNone(stat.per_minute)     # no reward → no rate, not 0

    def test_broken_file_is_parked_not_used(self):
        tmp = Path(tempfile.mkdtemp(prefix="alontito_broken_"))
        path = tmp / "memory.json"
        path.write_text("{not json", encoding="utf-8")
        memory = Memory(path)
        self.assertEqual(memory.facts(), [])
        self.assertTrue((tmp / "memory.broken.json").exists())


class TestReflection(unittest.TestCase):
    def setUp(self):
        self.memory = fresh_memory()
        self.brain = Brain(self.memory)

    def test_no_measurement_no_verdict(self):
        verdict = self.brain.reflect("train", None, 5_500_000, 100)
        self.assertEqual(verdict.verdict, "не подтверждено")
        self.assertFalse(verdict.rollback)
        self.assertIn("не подтверждено", verdict.text.lower())

    def test_zero_gain_is_not_an_improvement(self):
        verdict = self.brain.reflect("train", 5_000_000, 5_000_000, 100)
        self.assertEqual(verdict.verdict, "не подтверждено")

    def test_first_run_sets_baseline(self):
        verdict = self.brain.reflect("train", 5_000_000, 5_660_000, 254)
        self.assertEqual(verdict.verdict, "без изменений")
        self.assertAlmostEqual(self.brain.baseline("train"), 155_906, delta=200)

    def test_clear_improvement_stays(self):
        self.brain.reflect("train", 5_000_000, 5_660_000, 254)  # baseline
        verdict = self.brain.reflect("train", 5_000_000, 7_000_000, 254)
        self.assertEqual(verdict.verdict, "лучше")
        self.assertFalse(verdict.rollback)

    def test_clear_regression_rolls_back(self):
        self.brain.reflect("train", 5_000_000, 5_660_000, 254)  # baseline
        verdict = self.brain.reflect("train", 5_000_000, 5_200_000, 254)
        self.assertEqual(verdict.verdict, "хуже")
        self.assertTrue(verdict.rollback)


class TestIntentRouting(unittest.TestCase):
    def test_commands_pass_through(self):
        self.assertEqual(interpret("/memory"), "/memory")
        self.assertEqual(interpret("  /journal 5 "), "/journal 5")

    def test_obvious_phrases_route(self):
        self.assertEqual(interpret("что знаешь?"), "/memory")
        self.assertEqual(interpret("какие навыки"), "/skills")
        self.assertEqual(interpret("правила"), "/safety")

    def test_playing_phrases_do_not_route_to_an_action(self):
        # Игровой слой удалён: фразы про игру ни на что не отображаются.
        for phrase in ("запусти игру", "тренируйся", "качай гантели",
                       "найди роблокс", "открой игру"):
            self.assertIsNone(interpret(phrase))

    def test_unknown_phrase_is_refused(self):
        self.assertIsNone(interpret("сколько стоит луна"))
        self.assertIsNone(interpret(""))


class TestGuards(unittest.TestCase):
    def test_no_game_actions_are_allowed(self):
        # HARD RULE #2 (2026-11-24) + удаление игрового слоя (2026-11-27)
        self.assertEqual(ALLOWED_ACTIONS, set())

    def test_spending_and_trading_are_named_as_forbidden(self):
        text = " ".join(guard_report()).lower()
        for needle in ("robux", "трейд", "эксплойт"):
            self.assertIn(needle, text)

    def test_launch_is_named_as_forbidden(self):
        text = " ".join(guard_report()).lower()
        self.assertIn("запускать", text)


class TestGeometry(unittest.TestCase):
    def test_box_lines_have_one_width(self):
        block = ui.box("ПРОВЕРКА", ["коротко", "", "длинная строка " * 10,
                                    ui.paint("цвет", "green")])
        widths = {ui.width_of(line) for line in block.split("\n")}
        self.assertEqual(len(widths), 1, widths)

    def test_two_columns_fit_the_panel(self):
        pairs = [(f"/cmd{i}", "описание команды") for i in range(17)]
        for width in (60, 80, 104):
            block = ui.two_columns(pairs, width)
            for line in block.split("\n"):
                self.assertLessEqual(ui.width_of(line), width + 4)

    def test_home_screen_is_aligned_and_has_the_footer_contract(self):
        screen = ui.home_screen("2.0.0", "—", 2, 1, 1,
                                [("/help", "справка")])
        for line in screen.split("\n"):
            self.assertLessEqual(ui.width_of(line), ui.term_width() + 4)
        self.assertIn("Алонтито готов к работе", ui.ready())

    def test_every_home_line_has_exactly_the_screen_width(self):
        for cols in (72, 90, 104, 140):
            with mock.patch.object(ui, "term_width",
                                   lambda limit=112, c=cols: max(72, min(limit, c - 2))):
                screen = ui.home_screen("2.0.0", "—", 0, 0, 0,
                                        [("/help", "справка")],
                                        mcp_active=False, pet_status="3D ○ выкл")
                widths = {ui.width_of(line) for line in screen.split("\n")}
                # Blank separator lines are the only allowed exception.
                self.assertEqual(widths - {0}, {ui.term_width()}, (cols, widths))

    def test_two_panel_home_has_matching_frames(self):
        with mock.patch.object(ui, "term_width", lambda limit=112: 104):
            screen = ui.home_screen("2.0.0", "—", 0, 0, 0,
                                    [("/help", "справка")])
        self.assertEqual(screen.count("╭"), screen.count("╰"))
        self.assertEqual(screen.count("╭"), 3)      # logo + info + commands
        self.assertEqual(screen.count("╭╌"), 3)

    def test_home_screen_uses_no_wide_emoji(self):
        screen = ui.home_screen("2.0.0", "—", 0, 0, 0,
                                [("/help", "справка")])
        wide = [ch for ch in ui.ANSI_RE.sub("", screen) if ord(ch) > 0x1F000]
        self.assertEqual(wide, [], "emoji break frame geometry")

    def test_narrow_screen_stacks_instead_of_overlapping(self):
        with mock.patch.object(ui, "term_width", lambda limit=112: 72):
            screen = ui.home_screen("2.0.0", "—", 0, 0, 0,
                                    [("/help", "справка")])
        for line in screen.split("\n"):
            self.assertLessEqual(ui.width_of(line), 72)

    def test_foreign_ansi_is_stripped_when_the_console_has_none(self):
        # pet3d.badge("ansi") is coloured before the UI layer sees it; on a
        # console without VT those bytes would print as junk and break frames.
        with mock.patch.object(ui, "ENABLE_ANSI", False), \
                mock.patch.object(ui, "term_width", lambda limit=112: 104):
            screen = ui.home_screen("2.0.0", "—", 0, 0, 0,
                                    [("/help", "справка")],
                                    pet_status="\x1b[2m3D ○ выкл\x1b[0m")
        self.assertNotIn("\x1b", screen)
        widths = {ui.width_of(line) for line in screen.split("\n")}
        self.assertEqual(widths - {0}, {104})

    def test_wrapping_keeps_hand_aligned_columns(self):
        # A help list aligns its second column with spaces; wrapping must not
        # collapse those runs into single spaces.
        text = "/3d on | off        — включить / выключить персонажа на столе"
        lines = ui._wrap(text, 40)
        self.assertTrue(lines[0].startswith("/3d on | off        —"), lines)
        for line in lines:
            self.assertLessEqual(ui.width_of(line), 40)

    def test_wide_glyphs_are_counted_as_two_cells(self):
        self.assertEqual(ui.width_of("🔥"), 2)
        self.assertEqual(ui.width_of("АБ"), 2)
        self.assertEqual(ui.char_width("д"), 1)

    def test_a_wide_glyph_in_body_text_cannot_break_the_frame(self):
        block = ui.box("ШИРИНА", ["огонь 🔥 и ещё текст " * 3], width=40)
        widths = {ui.width_of(line) for line in block.split("\n")}
        self.assertEqual(len(widths), 1, widths)

    def test_prompt_is_silent_when_input_is_piped(self):
        # Without a TTY the prompt would glue itself to the next panel.
        with mock.patch("sys.stdin") as fake_stdin, \
                mock.patch("builtins.input", return_value="/help") as ask:
            fake_stdin.isatty.return_value = False
            self.assertEqual(ui.prompt(), "/help")
        ask.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
