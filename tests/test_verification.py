"""
Offline regression tests for the bot's VERIFICATION layer.

No Roblox window, no synthetic input, no OCR subprocess needed: real saved
frames + fakes. Run with:

    python -m unittest -v tests.test_verification
"""
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from config import DEBUG_DIR, ROIS
from vision.screen_eye import ScreenEye
from vision.template_reader import (TemplateReader, locate_badges,
                                   binarize_strength)


def _not_game_frame():
    """The Roblox app music page, reproduced from a real capture.

    Four album cards match the badge geometry exactly (292x68 at y=120,
    pitch 328) but contain artwork, not digit strokes. Real frame:
    debug_frames/now_watch.png, 2026-11-17.
    """
    img = Image.new("RGB", (1920, 1046), (255, 255, 255))
    d = ImageDraw.Draw(img)
    colors = [(91, 141, 239), (217, 165, 33), (52, 199, 123), (240, 124, 208)]
    for i, color in enumerate(colors):
        x = 372 + i * 328
        d.rectangle([x, 120, x + 292, 188], fill=color)
        d.ellipse([x + 110, 130, x + 170, 178], fill=(255, 255, 255))
    return img


class StubEye(ScreenEye):
    """ScreenEye whose capture returns a fixed frame (no ImageGrab)."""

    def __init__(self, frame):
        self.frame = frame

    def capture_full_window(self):
        return self.frame

    def get_roi_crop(self, roi_name, frame=None):
        return ScreenEye.get_roi_crop(self, roi_name, frame or self.frame)


class StubWM:
    def __init__(self, frame):
        self.frame = frame

    def ensure_focused(self):
        return True

    def get_client_rect(self):
        return (0, 0, self.frame.size[0], self.frame.size[1])


class StubInput:
    calls = None

    def __init__(self):
        self.calls = []

    def tap_key(self, key, hold=0.05):
        self.calls.append(("tap", key))

    def key_down(self, key):
        self.calls.append(("down", key))

    def key_up(self, key):
        self.calls.append(("up", key))

    def equip_dumbbells(self):
        self.calls.append(("equip", "dumbbells"))

    def equip_pushups(self):
        self.calls.append(("equip", "pushups"))

    def equip_situps(self):
        self.calls.append(("equip", "situps"))

    def smooth_lift(self, lifts=6, animation_delay=0.65, wm=None):
        self.calls.append(("lift", lifts))


def _frame():
    """A real captured frame with the strength badge in its calibrated place."""
    return Image.open(DEBUG_DIR / "diag_live_now.png").convert("RGB")


FRAME_STRENGTH = 56_600_000  # what the badge in diag_live_now.png says


class TestBadgeLocator(unittest.TestCase):
    def test_finds_strength_badge(self):
        frame = _frame()
        badges = [b for b in locate_badges(frame) if b["kind"] == "strength"]
        self.assertTrue(badges, "yellow strength badge not located")
        x1, y1, x2, y2 = badges[0]["box"]
        # calibrated ROI is x 614..960, y 15..120 for a 1920px-wide frame
        self.assertTrue(600 <= x1 <= 660, f"unexpected badge x1={x1}")
        self.assertTrue(920 <= x2 <= 990, f"unexpected badge x2={x2}")


class TestReadStrengthRelocation(unittest.TestCase):
    """The fix for 'badge unreadable after a window/DPI change'."""

    @staticmethod
    def _drifted_frame():
        """Same frame, badge moved OUT of the calibrated ROI (HUD drift)."""
        frame = _frame()
        badge = max((b for b in locate_badges(frame) if b["kind"] == "strength"),
                    key=lambda b: b["area"])
        x1, y1, x2, y2 = badge["box"]
        patch = frame.crop((x1, y1, x2, y2))
        bg = frame.getpixel((x1 - 6, y1 + 4))
        frame.paste(bg, (x1, y1, x2, y2))            # erase the original badge
        frame.paste(patch, (1330, 12))               # put it far to the right
        return frame

    def _make_actions(self, frame):
        from agent_actions import AgentActions
        from vision.ocr_engine import OCREngine
        eye = StubEye(frame)
        return AgentActions(StubWM(frame), StubInput(), eye, OCREngine())

    def test_calibrated_roi_reads_the_badge(self):
        actions = self._make_actions(_frame())
        with mock.patch("agent_actions.time.sleep"):
            self.assertEqual(actions.read_strength(), FRAME_STRENGTH)

    def test_fixed_roi_alone_loses_the_badge_after_drift(self):
        """Demonstrates the bug: with the badge moved, the ROI path is blind."""
        frame = self._drifted_frame()
        actions = self._make_actions(frame)
        crop = actions.eye.get_roi_crop("strength_counter", frame)
        self.assertLess(int(binarize_strength(crop).sum()), 200,
                        "test frame is wrong: the ROI still sees the badge")

    def test_relocation_recovers_the_value_after_drift(self):
        frame = self._drifted_frame()
        actions = self._make_actions(frame)
        with mock.patch("agent_actions.time.sleep"):
            self.assertEqual(actions.read_strength(), FRAME_STRENGTH,
                             "auto-located badge did not restore the reading")


class TestSmartLiftVerification(unittest.TestCase):
    """smart_lift must distinguish gain / no-gain / unreadable."""

    def setUp(self):
        from agent_actions import AgentActions
        from vision.ocr_engine import OCREngine
        self.actions = AgentActions(StubWM(_frame()), StubInput(),
                                    StubEye(_frame()), OCREngine())

    def _lift(self, readings):
        seq = iter(readings)

        def fake_read(*a, **kw):
            try:
                return next(seq)
            except StopIteration:
                return readings[-1]

        with mock.patch.object(self.actions, "read_strength", side_effect=fake_read), \
                mock.patch("agent_actions.time.sleep"):
            return self.actions.smart_lift(lifts=6, equip="dumbbells",
                                           dialog_open=False)

    def test_gain_is_measured(self):
        self.assertEqual(self._lift([1000, 1500]), 500)

    def test_no_gain_is_zero_not_success(self):
        self.assertEqual(self._lift([1000, 1000]), 0)

    def test_unreadable_badge_is_none_not_fake_gain(self):
        """Regression: the old code returned +1 here, poisoning learning."""
        result = self._lift([None, None])
        self.assertIsNone(result, "unverified set must not be reported as a gain")
        self.assertNotEqual(result, 1)

    def test_trap_recovery_still_runs_when_blind(self):
        self._lift([None, None])
        keys = [c for c in self.actions.input.calls if c[0] in ("tap", "down")]
        self.assertTrue(any(c == ("tap", "SPACE") for c in keys),
                        "trap prevention (SPACE dismount) must run regardless")
        self.assertTrue(any(c == ("down", "S") for c in keys),
                        "walk-back must run regardless")


class TestGemsAgreement(unittest.TestCase):
    """A dropped leading digit must never pass as a gems reading.

    Live failure 2026-11-16: badge showed 16.1M, one read returned 6,100,000
    at confidence 0.97 and was consumed as a +5,000,000 gems gain.
    """

    def setUp(self):
        from agent_actions import AgentActions
        from vision.ocr_engine import OCREngine
        self.actions = AgentActions(StubWM(_frame()), StubInput(),
                                    StubEye(_frame()), OCREngine())

    def _read(self, values):
        seq = iter(values)

        class P:
            def frame_is_game(self, frame):
                return True

            def read_once(self, kind, frame, rescue=True):
                try:
                    v = next(seq)
                except StopIteration:
                    v = None
                return {"value": v,
                        "confidence": 0.97 if v is not None else 0.0}

        with mock.patch.object(type(self.actions), "perceiver",
                               new_callable=mock.PropertyMock,
                               return_value=P()), \
                mock.patch("agent_actions.time.sleep"):
            return self.actions.read_gems()

    def test_disagreeing_readings_yield_unknown(self):
        self.assertIsNone(
            self._read([6100000, 16100000, 6100000, 16100000]),
            "alternating reads (lost leading digit) must not become a value")

    def test_agreeing_pair_is_accepted(self):
        self.assertEqual(self._read([16100000, 16100000]), 16100000)

    def test_single_glitch_then_agreement_is_accepted(self):
        self.assertEqual(self._read([6100000, 16100000, 16100000]), 16100000)

    def test_never_readable_is_unknown(self):
        self.assertIsNone(self._read([None, None, None, None]))


class TestEquipToggle(unittest.TestCase):
    """The hotbar slot key is a toggle - verify, never re-assert blindly.

    Live 2026-11-16: with dumbbells held, pressing slot 2 took them OFF
    (20 reps -> 0 gain) and the next press put them back (+390,000).
    """

    def setUp(self):
        from agent_actions import AgentActions
        from vision.ocr_engine import OCREngine
        self.actions = AgentActions(StubWM(_frame()), StubInput(),
                                    StubEye(_frame()), OCREngine())

    def _lift(self, readings, **kw):
        seq = iter(readings)

        def fake_read(*a, **k):
            try:
                return next(seq)
            except StopIteration:
                return readings[-1]

        with mock.patch.object(self.actions, "read_strength", side_effect=fake_read), \
                mock.patch("agent_actions.time.sleep"):
            return self.actions.smart_lift(lifts=20, equip="dumbbells",
                                           dialog_open=False, **kw)

    def test_second_set_does_not_toggle_the_item(self):
        self.assertEqual(self._lift([1000, 1500]), 500)
        self.actions.input.calls.clear()
        self.assertEqual(self._lift([1500, 2000]), 500)
        presses = [c for c in self.actions.input.calls
                   if c == ("equip", "dumbbells")]
        self.assertEqual(presses, [],
                         "a verified gain proves the item is held; pressing the "
                         "toggle again would take it off")

    def test_press_that_gains_nothing_is_read_as_unequip(self):
        # press -> no gain -> press again -> gain
        self.assertEqual(self._lift([1000, 1000, 1000, 1500]), 500)
        presses = [c for c in self.actions.input.calls
                   if c == ("equip", "dumbbells")]
        self.assertEqual(len(presses), 2)

    def test_first_burst_runs_without_walk_back_prefix(self):
        self._lift([1000, 1500])
        keys = [c for c in self.actions.input.calls if c[0] in ("tap", "down")]
        self.assertEqual(keys, [],
                         "the trap prefix must not run before a first burst "
                         "(it walked the character off the platform)")


class TestVerifiedFeedback(unittest.TestCase):
    def test_unverified_samples_do_not_inflate_success_rate(self):
        import importlib
        import brain.capabilities as caps_mod
        with mock.patch.object(caps_mod, "CAPS_FILE", DEBUG_DIR / "_test_caps.json"):
            reg = caps_mod.CapabilityRegistry()
            cap = caps_mod.Capability("T", "test", lambda *a: True, lambda *a: True)
            reg.register(cap)
            reg.feedback("T", True, 0.0, 1.0, "unverified", verified=False)
            self.assertEqual(reg.success_rate(cap), 0.5)  # no evidence yet
            reg.feedback("T", False, 0.0, 1.0, "trap", verified=True)
            self.assertEqual(reg.success_rate(cap), 0.0)  # one real failure
            reg.feedback("T", True, 2.0, 1.0, "gain", verified=True)
            self.assertAlmostEqual(reg.success_rate(cap), 0.5)


class TestLearnPartial(unittest.TestCase):
    """Autonomous glyph learning must be additive and poison-proof."""

    def setUp(self):
        self.frame = Image.open(DEBUG_DIR / "diag_now.png").convert("RGB")
        eye = StubEye(self.frame)
        self.ink = binarize_strength(eye.get_roi_crop("strength_counter"))
        self.reader = TemplateReader()
        self.reader._save = lambda: None  # tests must never touch the real store

    def test_no_learning_when_label_length_differs(self):
        text, _ = self.reader.read(self.ink)
        self.assertEqual(self.reader.learn_partial(self.ink, text, "123"), 0)

    def test_no_learning_when_anchors_disagree(self):
        text, _ = self.reader.read(self.ink)          # e.g. '81.8K'
        wrong = "9" + text[1:]
        self.assertEqual(self.reader.learn_partial(self.ink, text, wrong), 0)

    def test_partial_learning_repairs_a_missing_glyph(self):
        text, conf = self.reader.read(self.ink)       # '81.8K'
        self.assertNotIn("?", text)
        target = text[0]                              # drop the first glyph
        self.reader.templates.pop(target, None)
        self.reader._holes_cache = {}
        broken, _ = self.reader.read(self.ink)
        self.assertTrue(broken.startswith("?"), f"expected '?' at slot 0, got {broken!r}")
        learned = self.reader.learn_partial(self.ink, broken, text)
        self.assertGreater(learned, 0, "the '?' slot was not learned")
        restored, _ = self.reader.read(self.ink)
        self.assertEqual(restored, text)


class TestPlateDigitProof(unittest.TestCase):
    """Geometry is not proof: plate-shaped album cards must be rejected.

    Live 2026-11-17: the Roblox app showed its music page, four coloured cards
    matched badge geometry, and the eye called it the game while the bot
    clicked into it. Strokes inside the plate are the discriminator.
    """

    def test_coloured_cards_without_strokes_are_not_badges(self):
        self.assertEqual(locate_badges(_not_game_frame()), [],
                         "badge-shaped colour cards without digit strokes "
                         "were accepted as HUD plates")

    def test_real_badge_plates_still_found(self):
        kinds = {b["kind"] for b in locate_badges(_frame())}
        self.assertIn("strength", kinds)


class TestActionGate(unittest.TestCase):
    """No clicks unless the current frame is proven to be the game."""

    def test_smart_lift_refuses_on_a_non_game_frame(self):
        from agent_actions import AgentActions
        from vision.ocr_engine import OCREngine
        frame = _not_game_frame()
        inp = StubInput()
        actions = AgentActions(StubWM(frame), inp, StubEye(frame), OCREngine())
        with mock.patch("agent_actions.time.sleep"):
            self.assertIsNone(actions.smart_lift(lifts=20, equip="dumbbells"))
        self.assertEqual([c for c in inp.calls if c[0] == "lift"], [],
                         "clicks were delivered into a non-game window")

    def test_smart_lift_refuses_while_a_dialog_is_open(self):
        """Measured 2026-11-17: the brawl invite covers the click area, and a
        20-rep burst went into its buttons (0 gain, 25 s lost)."""
        from agent_actions import AgentActions
        from vision.ocr_engine import OCREngine
        inp = StubInput()
        actions = AgentActions(StubWM(_frame()), inp, StubEye(_frame()),
                               OCREngine())
        with mock.patch.object(type(actions.perceiver), "_ui_flags",
                               return_value={"modal_open": True,
                                             "modal_word": "yes"}), \
                mock.patch("agent_actions.time.sleep"):
            self.assertIsNone(actions.smart_lift(lifts=20, equip="dumbbells"))
        self.assertEqual([c for c in inp.calls if c[0] == "lift"], [],
                         "clicks were delivered into an open dialog")

    def test_smart_lift_still_clicks_on_a_game_frame(self):
        from agent_actions import AgentActions
        from vision.ocr_engine import OCREngine
        inp = StubInput()
        actions = AgentActions(StubWM(_frame()), inp, StubEye(_frame()),
                               OCREngine())
        with mock.patch.object(actions, "read_strength",
                               side_effect=[1000, 1500]), \
                mock.patch("agent_actions.time.sleep"):
            self.assertEqual(actions.smart_lift(lifts=20, equip="dumbbells",
                                                dialog_open=False), 500)
        self.assertTrue([c for c in inp.calls if c[0] == "lift"],
                        "the gate must not block a verified game frame")


class TestVerifiedGainIsTheOnlyNumber(unittest.TestCase):
    """EXP-03 set 4 reported metrics_delta -438,100 while smart_lift had
    verified +78,900 for that exact set: two reads of one event disagreed.
    The verified number is now the only source, and the extra read is gone."""

    def _adapter(self):
        from agent_actions import AgentActions
        from games.muscle_legends import MuscleLegendsAdapter
        from vision.ocr_engine import OCREngine
        frame = _frame()
        ocr = OCREngine()
        actions = AgentActions(StubWM(frame), StubInput(), StubEye(frame), ocr)
        adapter = MuscleLegendsAdapter(
            StubWM(frame), StubInput(), StubEye(frame), ocr,
            None, None, None, actions, None, None, None, None, None)
        return adapter, actions

    def test_training_reports_the_verified_gain(self):
        from games.game_adapter import GameAction, GameState
        adapter, actions = self._adapter()
        with mock.patch.object(actions, "smart_lift", return_value=78_900), \
                mock.patch.object(actions, "read_strength",
                                  side_effect=[5_300_000, 4_861_900]) as rs, \
                mock.patch.object(actions, "read_gems", return_value=16_100_000):
            res = adapter.execute_action(
                GameAction(name="TRAIN_DUMBBELLS", description="Hand Weights",
                           category="train"),
                GameState(modal_open=False))
        self.assertEqual(res.metrics_delta.get("strength"), 78_900)
        self.assertTrue(res.success)
        self.assertEqual(rs.call_count, 0,
                         "the verified gain must not be re-measured around the set")

    def test_dialog_refuses_training_without_clicking(self):
        from games.game_adapter import GameAction, GameState
        adapter, actions = self._adapter()
        with mock.patch.object(actions, "smart_lift", return_value=0) as lift:
            res = adapter.execute_action(
                GameAction(name="TRAIN_DUMBBELLS", description="Hand Weights",
                           category="train"),
                GameState(modal_open=True))
        self.assertFalse(res.success)
        self.assertEqual(res.error, "dialog_open")
        lift.assert_not_called()


class TestBadgeTextValidation(unittest.TestCase):
    """A dropped digit must be rejected, not turned into a plausible value."""

    def test_accepts_real_badge_formats(self):
        from vision.template_reader import is_badge_number
        for text in ("56.6M", "2.23M", "81.8K", "330.0K", "62K", "458"):
            self.assertIsNotNone(is_badge_number(text), text)

    def test_rejects_truncated_or_garbled(self):
        from vision.template_reader import is_badge_number
        for text in ("91.K", "1...M", "??3??.9K", "", "1234567", "56.6"):
            self.assertIsNone(is_badge_number(text), text)


class TestConfidenceThreshold(unittest.TestCase):
    def test_unknown_shape_becomes_question_mark(self):
        """Without a template a glyph must be '?', never a confident guess."""
        frame = Image.open(DEBUG_DIR / "diag_now.png").convert("RGB")
        ink = binarize_strength(StubEye(frame).get_roi_crop("strength_counter"))
        reader = TemplateReader()
        text, _ = reader.read(ink)
        reader.templates.pop(text[0], None)       # e.g. the missing '0' case
        reader._holes_cache = {}
        broken, _ = reader.read(ink)
        self.assertIn("?", broken, f"missing glyph was guessed as {broken!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
