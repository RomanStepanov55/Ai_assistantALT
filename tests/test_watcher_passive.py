"""Watcher is passive and honest (HARD RULE #2, 2026-11-24).

No Roblox, no screen: window, eye and OCR are fakes. What is pinned:
  * observe() never raises or drags the Roblox window;
  * a frame without HUD badge plates yields NO numbers (the window is no
    longer raised, so another window - e.g. this terminal - may be on top);
  * a frame WITH badges is read as before.
"""
import unittest
from unittest import mock

from PIL import Image

from brain import watcher as w


class FakeWM:
    def __init__(self):
        self.focus_calls = 0

    def get_client_rect(self):
        return (0, 0, 1920, 1046)

    def ensure_focused(self):
        self.focus_calls += 1
        raise AssertionError("observation must not raise the Roblox window")


class FakeEye:
    def __init__(self):
        self.captures = 0

    def capture_full_window(self):
        self.captures += 1
        return Image.new("RGB", (1920, 1040), (20, 20, 40))

    def get_roi_crop(self, name, frame):
        return None


class FakeOCR:
    def __init__(self):
        self.reads = 0

    def parse_stat_string(self, text):
        return None

    def read_text_from_image(self, crop, tag):
        self.reads += 1
        return ""

    def read_lines(self, crop, tag):
        self.reads += 1
        return []

    def looks_like_claim(self, text):
        return False


class TestPassiveWatcher(unittest.TestCase):
    def make(self, badges):
        self.wm, self.eye, self.ocr = FakeWM(), FakeEye(), FakeOCR()
        watcher = w.Watcher(self.wm, self.eye, self.ocr)
        patcher = mock.patch("vision.template_reader.locate_badges",
                             return_value=badges)
        patcher.start()
        self.addCleanup(patcher.stop)
        return watcher

    def test_covered_window_yields_no_numbers(self):
        watcher = self.make(badges=[])                 # чужое окно поверх игры
        state = watcher.observe(save=False)
        self.assertTrue(state["roblox_found"])
        self.assertFalse(state["game_visible"])
        self.assertIsNone(state["strength"])
        self.assertIsNone(state["gems"])
        self.assertEqual(state["events"], [])
        self.assertFalse(state["modal_open"])
        self.assertFalse(state["likely_dead"])
        self.assertEqual(self.ocr.reads, 0, "OCR must not read a non-game frame")

    def test_missing_frame_is_not_a_game(self):
        watcher = self.make(badges=[{"kind": "strength"}])
        self.eye.capture_full_window = lambda: None
        state = watcher.observe(save=False)
        self.assertFalse(state["game_visible"])
        self.assertIsNone(state["strength"])

    def test_game_frame_is_read(self):
        watcher = self.make(badges=[{"kind": "strength"}, {"kind": "gems"}])
        state = watcher.observe(save=False)
        self.assertTrue(state["game_visible"])
        self.assertIn("strength", state)
        self.assertGreater(self.ocr.reads, 0)

    def test_observation_never_focuses_the_window(self):
        watcher = self.make(badges=[])
        for _ in range(3):
            watcher.observe(save=False)
        self.assertEqual(self.wm.focus_calls, 0)
        self.assertEqual(self.eye.captures, 3)

    def test_covered_window_resets_the_death_streak(self):
        watcher = self.make(badges=[])
        watcher._dead_streak = 5
        state = watcher.observe(save=False)
        self.assertFalse(state["likely_dead"])
        self.assertEqual(watcher._dead_streak, 0)

    def test_journal_names_a_covered_window_honestly(self):
        watcher = self.make(badges=[])
        with mock.patch.object(watcher, "_save_state"), \
             mock.patch("builtins.open", mock.mock_open()) as opened:
            state = watcher.observe(save=True)
        self.assertFalse(state["game_visible"])
        written = "".join(c.args[0] for c in opened().write.call_args_list)
        self.assertIn("NO-GAME-HUD", written)


if __name__ == "__main__":
    unittest.main()
