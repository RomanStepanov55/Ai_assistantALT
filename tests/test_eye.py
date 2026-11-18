"""
Offline regression tests for the EYE (structured perception layer).

No Roblox window, no synthetic input, no OCR subprocess: every test uses real
saved frames or synthetic masks. Run with:

    python -m unittest -v tests.test_eye

The tests encode the promises the brain relies on:
    * a real frame is read correctly and calibrated from the frame itself;
    * a frame that is NOT the game produces nulls, never a plausible number;
    * a value cached from another frame is never presented as this frame's;
    * the annotated PNG is produced for offline viewing;
    * border-ink removal keeps glyphs and drops badge frames.
"""
import json
import unittest

import numpy as np
from PIL import Image

from config import DATA_DIR, DEBUG_DIR
from vision.eye import Eye, READABLE_CONF, dialog_word
from vision.screen_eye import ScreenEye
from vision.template_reader import _remove_border_ink, is_badge_number


LIVE = "eye_live_01.png"          # 1920x1046 live capture: STR 2.76M, GEM 14.0M
NOT_GAME = "agent_live_capture.png"   # a music app window grabbed by mistake
FOREIGN_WINDOW = "live_v13_raw.png"   # this agent's own terminal (1920x1046)
TRAVEL_DIALOG = "diag_wheel_modal.png"  # real TRAVEL/YES/NO dialog


class StubEye(ScreenEye):
    """ScreenEye whose capture returns a fixed frame (no ImageGrab)."""

    def __init__(self, frame):
        self.frame = frame

    def capture_full_window(self):
        return self.frame

    def get_roi_crop(self, roi_name, frame=None):
        return ScreenEye.get_roi_crop(self, roi_name, frame or self.frame)


def _frame(name):
    return Image.open(DEBUG_DIR / name).convert("RGB")


def _eye(frame):
    """Eye wired to a fixed frame; OCR never runs on the fast path."""
    return Eye(screen_eye=StubEye(frame))


class TestLiveFrameReading(unittest.TestCase):
    """The reference frame the whole layer was calibrated against."""

    def setUp(self):
        self.frame = _frame(LIVE)
        self.eye = _eye(self.frame)
        self.scene = self.eye.observe(frame=self.frame, temporal=False)

    def test_reads_both_counters(self):
        self.assertEqual(self.scene["hud"]["strength"]["value"], 2_760_000)
        self.assertEqual(self.scene["hud"]["gems"]["value"], 14_000_000)
        self.assertEqual(self.scene["hud"]["strength"]["text"], "2.76M")
        self.assertEqual(self.scene["hud"]["gems"]["text"], "14.0M")

    def test_confidence_is_high_and_scene_readable(self):
        self.assertGreaterEqual(self.scene["hud"]["strength"]["confidence"],
                                READABLE_CONF)
        self.assertGreaterEqual(self.scene["hud"]["gems"]["confidence"],
                                READABLE_CONF)
        self.assertTrue(self.scene["quality"]["readable"])
        self.assertTrue(self.scene["frame"]["game_visible"])
        self.assertEqual(self.scene["quality"]["warnings"], [])

    def test_layout_is_self_calibrated_from_the_frame(self):
        layout = self.scene["layout"]
        self.assertEqual(layout["source"], "auto",
                         "badges were not located; the eye fell back to ROIs")
        sx1, sy1, sx2, sy2 = layout["strength_box"]
        # measured live: (628, 12) - (944, 108)
        self.assertLessEqual(abs(sx1 - 628), 12, f"strength x1={sx1}")
        self.assertLessEqual(abs(sx2 - 944), 12, f"strength x2={sx2}")
        self.assertLess(sy1, 40, "strength box is not in the top HUD row")
        gx1, _, gx2, _ = layout["gems_box"]
        self.assertGreater(gx1, sx2, "gems badge must sit RIGHT of strength")
        self.assertGreater(gx2 - gx1, (sx2 - sx1) * 0.95,
                           "gems crop is too narrow - the suffix would be cut")

    def test_gems_suffix_survives_the_crop(self):
        """A narrow crop reads '14.0' - a valid-looking number 1000x too small."""
        for source, box in self.eye._candidate_boxes("gems", self.frame):
            text = None
            for _, ink in self.eye._inks("gems", self.frame.crop(box)):
                text, conf = self.eye.reader.read(ink)
                break
            if text and is_badge_number(text):
                self.assertTrue(text.endswith("M"),
                                f"{source} crop lost the suffix: {text}")
                return
        self.fail("no gems candidate produced a valid badge number")


class TestNotTheGame(unittest.TestCase):
    """A screen grab of an occluded Roblox window returns whatever is on top."""

    def test_foreign_window_is_reported_as_no_game_hud(self):
        if not (DEBUG_DIR / NOT_GAME).exists():
            self.skipTest(f"{NOT_GAME} fixture missing")
        frame = _frame(NOT_GAME)
        eye = _eye(frame)
        scene = eye.observe(frame=frame, temporal=False)
        self.assertFalse(scene["frame"]["game_visible"])
        self.assertFalse(scene["quality"]["readable"])
        self.assertIsNone(scene["hud"]["strength"]["value"])
        self.assertIsNone(scene["hud"]["gems"]["value"])
        self.assertIsNone(scene["hud"]["strength"]["text"])
        self.assertTrue(any("HUD not found" in w
                            for w in scene["quality"]["warnings"]))


class TestNoCrossFrameMemory(unittest.TestCase):
    """Offline frames are independent moments; stale values must not leak."""

    def test_second_frame_does_not_show_the_first_frames_value(self):
        live = _frame(LIVE)
        eye = _eye(live)
        first = eye.observe(frame=live, temporal=False)
        self.assertEqual(first["hud"]["strength"]["value"], 2_760_000)

        if not (DEBUG_DIR / NOT_GAME).exists():
            self.skipTest(f"{NOT_GAME} fixture missing")
        other = _frame(NOT_GAME)
        second = eye.observe(frame=other, temporal=False)
        for kind in ("strength", "gems"):
            self.assertIsNone(second["hud"][kind]["value"],
                              f"{kind}: a value from a DIFFERENT frame leaked")
            self.assertFalse(second["hud"][kind]["stale"])

    def test_layout_from_another_size_is_not_reused(self):
        live = _frame(LIVE)
        eye = _eye(live)
        eye.observe(frame=live, temporal=False)
        small = live.resize((1280, 697))
        scene = eye.observe(frame=small, temporal=False)
        box = scene["hud"]["strength"]["box"]
        if box:
            self.assertLessEqual(box[2], 1280,
                                 "a 1920-wide box was reused on a 1280 frame")


class TestAnnotatedView(unittest.TestCase):
    """The 'normal eye for offline viewing' must always produce a picture."""

    def test_annotate_returns_a_same_size_rgb_image(self):
        frame = _frame(LIVE)
        eye = _eye(frame)
        scene = eye.observe(frame=frame, temporal=False)
        img = eye.annotate(scene, frame=frame)
        self.assertIsNotNone(img)
        self.assertEqual(img.size, frame.size)
        self.assertEqual(img.mode, "RGB")
        # the overlay must actually change pixels (boxes + header)
        self.assertNotEqual(np.asarray(img).tobytes(),
                            np.asarray(frame).tobytes())

    def test_save_annotated_writes_a_png(self):
        frame = _frame(LIVE)
        eye = _eye(frame)
        scene = eye.observe(frame=frame, temporal=False)
        path = eye.save_annotated(scene, name="_test_eye_annotated.png",
                                  frame=frame)
        try:
            self.assertIsNotNone(path)
            self.assertTrue(path.exists())
            self.assertEqual(Image.open(path).size, frame.size)
        finally:
            path.unlink(missing_ok=True)

    def test_not_game_frame_is_banner_marked(self):
        if not (DEBUG_DIR / NOT_GAME).exists():
            self.skipTest(f"{NOT_GAME} fixture missing")
        frame = _frame(NOT_GAME)
        eye = _eye(frame)
        scene = eye.observe(frame=frame, temporal=False)
        plain = np.asarray(frame)
        marked = np.asarray(eye.annotate(scene, frame=frame))
        banner = marked[24:46, 0:frame.size[0]]
        self.assertGreater(int((banner == np.array([64, 0, 0])).all(axis=2).sum()),
                           1000, "no red 'NO GAME HUD' banner was drawn")
        self.assertNotEqual(plain.tobytes(), marked.tobytes())


class TestBorderInkRemoval(unittest.TestCase):
    """Frame stripes must go, digits must stay."""

    def test_removes_border_component_keeps_interior(self):
        mask = np.zeros((40, 60), dtype=bool)
        mask[0, :] = True          # border-connected frame line
        mask[10:30, 20:30] = True  # an interior glyph block
        out = _remove_border_ink(mask)
        self.assertFalse(out[0, :].any(), "border ink survived")
        self.assertTrue(out[10:30, 20:30].all(), "interior glyph was erased")

    def test_empty_mask_stays_empty(self):
        mask = np.zeros((10, 10), dtype=bool)
        self.assertFalse(_remove_border_ink(mask).any())


class TestGlyphHealth(unittest.TestCase):
    def test_health_lists_known_chars_and_missing_ones(self):
        frame = _frame(LIVE)
        health = _eye(frame).glyph_health()
        self.assertIn("0", health["chars"])
        self.assertIn("M", health["chars"])
        self.assertIsInstance(health["missing"], list)

    def test_audit_report_is_consumed_when_present(self):
        audit_file = DATA_DIR / "glyph_audit.json"
        if not audit_file.exists():
            self.skipTest("no glyph audit yet (run tools/audit_glyphs.py)")
        data = json.loads(audit_file.read_text(encoding="utf-8"))
        self.assertIn("report", data)


class TestReadBudget(unittest.TestCase):
    """The live eye must stay fast enough to sit in the decision loop."""

    def test_observe_on_a_saved_frame_is_under_two_seconds(self):
        frame = _frame(LIVE)
        eye = _eye(frame)
        eye.observe(frame=frame, temporal=False)      # warm the caches
        scene = eye.observe(frame=frame, temporal=False)
        self.assertLess(scene["eye"]["read_ms"], 2000,
                        f"observe took {scene['eye']['read_ms']} ms")
        self.assertTrue(scene["quality"]["readable"])


class TestDialogEvidence(unittest.TestCase):
    """`modal_open` is an URGENT claim: the policy clicks a blind close button.

    Measured before the fix: a line-count rule flagged 36 of 46 saved frames,
    including this agent's own editor window. A dialog must prove itself with a
    control word AND with the game HUD in the same frame.
    """

    def test_banner_and_hud_text_is_not_a_dialog(self):
        lines = [{"text": "НЕ Boss SPAWNS lN: 11124 -0 AIiM'"},
                 {"text": "Сут ореп"},
                 {"text": "enght required lCh Press"}]
        self.assertIsNone(dialog_word(lines))

    def test_brawl_banner_words_alone_do_not_prove_a_dialog(self):
        for text in ("Brawl starting in 90000", "NEXT Boss SPAWNS IN: 11:15",
                     "x2 Strength 15m 09s", "Omega Emperor Sold out 0/500"):
            self.assertIsNone(dialog_word([{"text": text}]), text)

    def test_a_real_dialog_word_is_found(self):
        lines = [{"text": "NEXT SPAWNS"}, {"text": "TRAVEL"},
                 {"text": "Travel to Hallowed Gym?"}, {"text": "YES"},
                 {"text": "NO"}]
        self.assertIsNotNone(dialog_word(lines))

    def test_permanent_world_and_hud_text_is_not_a_dialog(self):
        """Words visible in EVERY frame cannot prove a dialog is open.

        Measured 2026-11-17: the world sign "Join the game community in our
        Groups to have +10% more cash" and the permanent TRADE button made
        training frames report modal_open=true (debug_frames/fix_set4.png),
        which would make the policy chase a dialog that does not exist.
        """
        for text in ("Join the game community in our Groups to have +10% more cash",
                     "TRADE", "ITEMS", "INVITE"):
            self.assertIsNone(dialog_word([{"text": text}]), text)

    def test_foreign_window_is_never_a_dialog(self):
        if not (DEBUG_DIR / FOREIGN_WINDOW).exists():
            self.skipTest(f"{FOREIGN_WINDOW} fixture missing")
        frame = _frame(FOREIGN_WINDOW)
        eye = _eye(frame)
        flags = eye._ui_flags(frame, deep=True)
        self.assertFalse(eye.frame_is_game(frame))
        self.assertFalse(flags["modal_open"],
                         f"an editor window was reported as a dialog: "
                         f"{flags.get('modal_word')!r}")

    def test_a_real_travel_dialog_is_still_detected(self):
        if not (DEBUG_DIR / TRAVEL_DIALOG).exists():
            self.skipTest(f"{TRAVEL_DIALOG} fixture missing")
        frame = _frame(TRAVEL_DIALOG)
        eye = _eye(frame)
        flags = eye._ui_flags(frame, deep=True)
        self.assertTrue(flags["modal_open"],
                        f"a TRAVEL/YES dialog was missed: {flags.get('modal_text')!r}")


if __name__ == "__main__":
    unittest.main()
