"""HARD RULE #2 regression: the agent must not be able to send input to the game.

User decision 2026-11-24: the agent observes and consults; it never plays,
trains or clicks. Every real input path must raise GameInputDisabled while
config.GAME_INPUT_ENABLED is False.
"""
import unittest
from unittest import mock

import config
from core.input_controller import GameInputDisabled, InputController


class TestGameInputDisabled(unittest.TestCase):
    def setUp(self):
        self.ic = InputController()
        self.assertFalse(config.GAME_INPUT_ENABLED,
                         "по решению пользователя 2026-11-24 ввод должен быть выключен")

    def test_every_input_path_raises(self):
        paths = [
            ("tap_key", lambda: self.ic.tap_key("2")),
            ("key_down", lambda: self.ic.key_down("W")),
            ("key_up", lambda: self.ic.key_up("W")),
            ("mouse_click", lambda: self.ic.mouse_click(10, 10)),
            ("move_cursor_to", lambda: self.ic.move_cursor_to(10, 10)),
            ("center_cursor", lambda: self.ic.center_cursor(None)),
            ("camera_zoom", lambda: self.ic.camera_zoom()),
            ("camera_rotate", lambda: self.ic.camera_rotate(seconds=0.05)),
            ("smooth_lift", lambda: self.ic.smooth_lift(lifts=1)),
            ("train_machine_routine", lambda: self.ic.train_machine_routine(reps=1)),
            ("train_agility_run", lambda: self.ic.train_agility_run(duration_sec=0.01)),
            ("select_slot", lambda: self.ic.select_slot(2)),
            ("click_roi_whitelisted", lambda: self.ic.click_roi("wheel_btn", None)),
            ("click_roi_forbidden", lambda: self.ic.click_roi("store_btn", None)),
            ("close_modal", lambda: self.ic.close_modal(None)),
        ]
        for name, call in paths:
            with self.subTest(path=name):
                with self.assertRaises(GameInputDisabled):
                    call()

    def test_send_input_itself_is_guarded(self):
        from core.input_controller import INPUT
        inp = INPUT()
        inp.type = 1
        with self.assertRaises(GameInputDisabled):
            self.ic._send_input(inp)

    def test_flag_is_read_at_call_time_not_import_time(self):
        # флаг читается при вызове: даже если кто-то включит его после импорта,
        # выключение снова работает без перезапуска
        with mock.patch.object(config, "GAME_INPUT_ENABLED", True):
            self.ic.move_cursor_to(10, 10)          # не бросает
        with self.assertRaises(GameInputDisabled):
            self.ic.move_cursor_to(10, 10)          # снова запрещено

    def test_error_message_names_the_rule(self):
        with self.assertRaises(GameInputDisabled) as ctx:
            self.ic.tap_key("2")
        self.assertIn("не играет", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
