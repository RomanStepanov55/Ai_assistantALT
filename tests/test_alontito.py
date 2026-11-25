"""Tests for the Alontito app layer: memory, brain, UI geometry, guards.

No Roblox, no captures, no input: this file must stay green on a closed laptop.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from alontito import games_catalog, ui
from alontito.brain import Brain, interpret
from alontito.memory import (MISSING, STATUS_MEASURED, STATUS_UNCONFIRMED,
                             STATUSES, Memory)
from alontito.skills import ALLOWED_ACTIONS, AlontitoBot, guard_report


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


class TestPlanning(unittest.TestCase):
    def setUp(self):
        self.memory = fresh_memory()
        self.brain = Brain(self.memory)

    def test_no_window_means_launch(self):
        self.assertEqual(self.brain.plan({}, attached=False).action, "launch")

    def test_non_game_frame_means_wait(self):
        decision = self.brain.plan({"is_game": False}, attached=True)
        self.assertEqual(decision.action, "wait")

    def test_dialog_means_wait_not_clicking(self):
        # HARD RULE #2: Алонтито не кликает — диалог закрывает пользователь
        decision = self.brain.plan({"is_game": True, "modal_open": True,
                                    "strength": 5_000_000}, attached=True)
        self.assertEqual(decision.action, "wait")
        self.assertIn("сам", decision.reason)

    def test_unreadable_strength_means_observe_not_train(self):
        decision = self.brain.plan({"is_game": True, "modal_open": False,
                                    "strength": None}, attached=True)
        self.assertEqual(decision.action, "observe")

    def test_healthy_scene_means_watch_never_train(self):
        decision = self.brain.plan({"is_game": True, "modal_open": False,
                                    "strength": 5_000_000}, attached=True)
        self.assertEqual(decision.action, "watch")
        self.assertNotIn(decision.action, ("train", "close_dialog"))

    def test_no_proposal_without_measurements(self):
        self.assertIsNone(self.brain.propose())


class TestIntentRouting(unittest.TestCase):
    def test_commands_pass_through(self):
        self.assertEqual(interpret("/status"), "/status")
        self.assertEqual(interpret("  /train 3 "), "/train 3")

    def test_obvious_phrases_route(self):
        self.assertEqual(interpret("запусти игру"), "/play")
        self.assertEqual(interpret("что видишь?"), "/watch")
        self.assertEqual(interpret("как дела"), "/status")

    def test_playing_phrases_do_not_route_to_an_action(self):
        # "тренируйся" и подобные больше ни на что не отображаются: игра вернулась
        # пользователю. Интерпретатор честно отвечает None.
        for phrase in ("тренируйся", "качай гантели", "сделай подход"):
            self.assertNotEqual(interpret(phrase), "/train")

    def test_unknown_phrase_is_refused(self):
        self.assertIsNone(interpret("сколько стоит луна"))
        self.assertIsNone(interpret(""))


class TestGuards(unittest.TestCase):
    def test_no_game_actions_are_allowed(self):
        # HARD RULE #2 (2026-11-24): наблюдение вместо игры
        self.assertEqual(ALLOWED_ACTIONS, set())

    def test_spending_and_trading_are_named_as_forbidden(self):
        text = " ".join(guard_report()).lower()
        for needle in ("robux", "трейд", "esc", "tab", "эксплойт"):
            self.assertIn(needle, text)

    def test_app_never_exposes_spending_actions(self):
        bot = AlontitoBot()
        public = [n for n in dir(bot) if not n.startswith("_")]
        self.assertNotIn("hatch_egg", public)
        self.assertNotIn("rebirth", public)
        self.assertNotIn("pets_maintenance", public)

    def test_catalog_identifies_by_place_id(self):
        game = games_catalog.find("muscle")
        self.assertIsNotNone(game)
        self.assertEqual(game.place_id, 3623096087)
        self.assertEqual(games_catalog.find("3623096087").place_id, 3623096087)
        self.assertIsNone(games_catalog.find("несуществующая игра"))


class TestPlaceIdentity(unittest.TestCase):
    def _logs(self, text: str, age_sec: float = 10.0, name: str = "session.log"):
        tmp = Path(tempfile.mkdtemp(prefix="roblox_logs_"))
        path = tmp / name
        path.write_text(text, encoding="utf-8")
        stamp = time.time() - age_sec
        os.utime(path, (stamp, stamp))
        return tmp

    def test_place_is_read_from_the_client_log(self):
        logs = self._logs("session start\nplaceid:3623096087\n"
                          "universeid:1268927906\njoining game")
        found = games_catalog.current_place(logs_dir=logs)
        self.assertTrue(found["found"])
        self.assertEqual(found["place_id"], 3623096087)
        self.assertEqual(found["universe_id"], 1268927906)
        self.assertIsNotNone(found["game"])
        self.assertEqual(found["game"].place_id, 3623096087)

    def test_yesterday_log_cannot_identify_today(self):
        logs = self._logs("placeid:3623096087", age_sec=9 * 3600)
        found = games_catalog.current_place(logs_dir=logs)
        self.assertFalse(found["found"])
        self.assertIn("не подтверждён", found["detail"])

    def test_unknown_place_is_reported_as_unknown_not_guessed(self):
        logs = self._logs("placeid : 123456789")
        found = games_catalog.current_place(logs_dir=logs)
        self.assertTrue(found["found"])
        self.assertEqual(found["place_id"], 123456789)
        self.assertIsNone(found["game"])

    def test_missing_logs_folder_is_not_an_exception(self):
        found = games_catalog.current_place(logs_dir=Path("Z:/definitely/not/here"))
        self.assertFalse(found["found"])

    def test_autostart_launches_when_nothing_is_running(self):
        with mock.patch.object(games_catalog, "window_state",
                               return_value={"found": False}), \
             mock.patch.object(games_catalog, "current_place",
                               side_effect=[{"found": False},
                                            {"found": True, "place_id": 3623096087,
                                             "game": games_catalog.find("muscle"),
                                             "log": "x.log", "age_sec": 5}]), \
             mock.patch.object(games_catalog, "launch",
                               return_value=(True, "roblox://placeId=3623096087")) as launch, \
             mock.patch.object(games_catalog, "wait_for_game",
                               return_value=(True, "HUD игры подтверждён")):
            result = games_catalog.autostart()
        self.assertTrue(result["launched"])
        self.assertTrue(result["attached"])
        launch.assert_called_once()
        self.assertIn("подтверждено", result["identity"])

    def test_unknown_running_place_is_not_called_the_preferred_game(self):
        with mock.patch.object(games_catalog, "window_state",
                               return_value={"found": True}), \
             mock.patch.object(games_catalog, "current_place",
                               return_value={"found": True, "place_id": 123456789,
                                             "game": None, "log": "x.log",
                                             "age_sec": 5}), \
             mock.patch.object(games_catalog, "_hud_visible",
                               return_value=(True, "HUD")):
            result = games_catalog.autostart()
        self.assertTrue(result["attached"])
        self.assertIsNone(result["game"])          # no name, no guess
        self.assertEqual(result["place"]["place_id"], 123456789)
        self.assertIn("подтверждено", result["identity"])

    def test_autostart_does_not_relaunch_a_running_game(self):
        with mock.patch.object(games_catalog, "window_state",
                               return_value={"found": True}), \
             mock.patch.object(games_catalog, "current_place",
                               return_value={"found": True, "place_id": 3623096087,
                                             "game": games_catalog.find("muscle"),
                                             "log": "x.log", "age_sec": 5}), \
             mock.patch.object(games_catalog, "_hud_visible",
                               return_value=(True, "HUD игры виден")), \
             mock.patch.object(games_catalog, "launch") as launch:
            result = games_catalog.autostart()
        self.assertTrue(result["attached"])
        self.assertFalse(result["launched"])
        launch.assert_not_called()

    def test_open_place_wins_over_the_preferred_one(self):
        other = games_catalog.RobloxGame(key="other", name="Other", place_id=999,
                                         universe_id=None, note="", evidence="")
        with mock.patch.object(games_catalog, "window_state",
                               return_value={"found": True}), \
             mock.patch.object(games_catalog, "current_place",
                               return_value={"found": True, "place_id": 3623096087,
                                             "game": games_catalog.find("muscle"),
                                             "log": "x.log", "age_sec": 5}), \
             mock.patch.object(games_catalog, "_hud_visible",
                               return_value=(True, "HUD")):
            result = games_catalog.autostart(preferred=other)
        self.assertEqual(result["game"].place_id, 3623096087)


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
        screen = ui.home_screen("1.0.0", "Muscle Legends", 2, 1, 1,
                                [("/help", "справка")])
        for line in screen.split("\n"):
            self.assertLessEqual(ui.width_of(line), ui.term_width() + 4)
        self.assertIn("Алонтито готов к работе", ui.ready())

    def test_every_home_line_has_exactly_the_screen_width(self):
        for cols in (72, 90, 104, 140):
            with mock.patch.object(ui, "term_width",
                                   lambda limit=112, c=cols: max(72, min(limit, c - 2))):
                screen = ui.home_screen("1.4.0", "Muscle Legends", 0, 0, 0,
                                        [("/help", "справка")],
                                        mcp_active=False, pet_status="3D ○ выкл")
                widths = {ui.width_of(line) for line in screen.split("\n")}
                # Blank separator lines are the only allowed exception.
                self.assertEqual(widths - {0}, {ui.term_width()}, (cols, widths))

    def test_two_panel_home_has_matching_frames(self):
        with mock.patch.object(ui, "term_width", lambda limit=112: 104):
            screen = ui.home_screen("1.4.0", "Muscle Legends", 0, 0, 0,
                                    [("/help", "справка")])
        self.assertEqual(screen.count("╭"), screen.count("╰"))
        self.assertEqual(screen.count("╭"), 3)      # logo + info + commands
        self.assertEqual(screen.count("╭╌"), 3)

    def test_home_screen_uses_no_wide_emoji(self):
        screen = ui.home_screen("1.4.0", "Muscle Legends", 0, 0, 0,
                                [("/help", "справка")])
        wide = [ch for ch in ui.ANSI_RE.sub("", screen) if ord(ch) > 0x1F000]
        self.assertEqual(wide, [], "emoji break frame geometry")

    def test_narrow_screen_stacks_instead_of_overlapping(self):
        with mock.patch.object(ui, "term_width", lambda limit=112: 72):
            screen = ui.home_screen("1.4.0", "Muscle Legends", 0, 0, 0,
                                    [("/help", "справка")])
        for line in screen.split("\n"):
            self.assertLessEqual(ui.width_of(line), 72)

    def test_foreign_ansi_is_stripped_when_the_console_has_none(self):
        # pet3d.badge("ansi") is coloured before the UI layer sees it; on a
        # console without VT those bytes would print as junk and break frames.
        with mock.patch.object(ui, "ENABLE_ANSI", False), \
                mock.patch.object(ui, "term_width", lambda limit=112: 104):
            screen = ui.home_screen("1.4.0", "Muscle Legends", 0, 0, 0,
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


class TestSkillsSafety(unittest.TestCase):
    def test_train_refuses_without_a_visible_game(self):
        bot = AlontitoBot()
        fake = mock.Mock()
        fake.success = False
        fake.detail = "окно Roblox не найдено"
        fake.metrics = {"strength": None, "gems": None}
        with mock.patch.object(bot, "health", return_value=fake):
            res = bot.health()
        self.assertFalse(res.success)

    def test_health_never_invents_numbers(self):
        bot = AlontitoBot()
        with mock.patch.object(bot, "build", side_effect=RuntimeError("нет стека")):
            res = bot.health()
        self.assertFalse(res.success)
        self.assertIn("ошибка", res.detail)
        self.assertNotIn(0, res.metrics.values())


if __name__ == "__main__":
    unittest.main()
