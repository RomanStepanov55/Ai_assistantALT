"""
Glyph harvester - teaches the template reader the shapes it cannot read yet.

Runs OFFLINE over saved frames (debug_frames/*.png) and never touches the game:
for every frame it crops the strength badge, reads it with the templates, asks
WinRT OCR for a second opinion, and - only when the two AGREE on every glyph the
template already recognized - learns the shapes that came back as '?'.

This is how a missing glyph such as '0' (which cannot be derived from 1-9) gets
filled in without guessing.

Usage:
    python tools/harvest_glyphs.py            # report only, no writes
    python tools/harvest_glyphs.py --write    # store the learned glyphs
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from config import DEBUG_DIR
from vision.ocr_engine import OCREngine
from vision.screen_eye import ScreenEye
from vision.template_reader import TemplateReader, binarize_strength


class _Eye(ScreenEye):
    def __init__(self, frame):
        self.frame = frame

    def capture_full_window(self):
        return self.frame

    def get_roi_crop(self, roi_name, frame=None):
        return ScreenEye.get_roi_crop(self, roi_name, frame or self.frame)


def readable_count(frames, reader) -> int:
    n = 0
    for fr in frames:
        ink = binarize_strength(_Eye(fr).get_roi_crop("strength_counter"))
        if int(ink.sum()) < 200:
            continue
        text, conf = reader.read(ink)
        if text and "?" not in text and conf >= 0.7:
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="persist learned glyphs (default: dry run)")
    ap.add_argument("--glob", default="diag_*.png")
    args = ap.parse_args()

    frames = []
    for p in sorted(DEBUG_DIR.glob(args.glob)):
        try:
            img = Image.open(p).convert("RGB")
        except Exception:
            continue
        if img.size[0] >= 1200:      # full client captures only
            frames.append(img)
    if not frames:
        print("[harvest] no usable frames in", DEBUG_DIR)
        return

    reader = TemplateReader()
    if not args.write:
        reader._save = lambda: None      # dry run must not touch the store
    ocr = OCREngine()

    before = readable_count(frames, reader)
    print(f"[harvest] frames={len(frames)} readable_before={before} "
          f"missing={reader.missing_chars()}")

    learned_total = 0
    for i, fr in enumerate(frames, 1):
        ex = _Eye(fr)
        ink = binarize_strength(ex.get_roi_crop("strength_counter"))
        if int(ink.sum()) < 200:
            continue
        text, conf = reader.read(ink)
        if "?" not in text:          # nothing to learn from this frame
            continue
        label = ocr.normalize_ocr_number(
            ocr.read_text_from_image(ex.get_roi_crop("strength_counter"), "harvest"))
        if not label:
            continue
        n = reader.learn_partial(ink, text, label)
        if n:
            learned_total += n
            print(f"  [{i:2}/{len(frames)}] {text} <- ocr {label}: learned {n} glyph(s)")

    after = readable_count(frames, reader)
    print(f"[harvest] learned={learned_total} readable_after={after} "
          f"(+{after - before}) missing={reader.missing_chars()}")
    if not args.write:
        print("[harvest] dry run - nothing saved. Re-run with --write to keep it.")


if __name__ == "__main__":
    main()
