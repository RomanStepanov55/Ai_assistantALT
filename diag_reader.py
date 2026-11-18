"""
Offline diagnostic: run the template digit reader over saved strength-badge
crops, WITHOUT touching the live game (no input, no window focus).

Usage:
    python diag_reader.py                 # scan the known debug crops
    python diag_reader.py path\\to\\img.png # scan a specific image
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from config import DEBUG_DIR
from vision.template_reader import TemplateReader, binarize_strength, binarize_gems, _glyph_boxes


def show(path: Path):
    reader = TemplateReader()
    img = Image.open(path)
    print(f"\n=== {path.name} | size={img.size} ===")
    for name, binz in (("strength", binarize_strength), ("gems", binarize_gems)):
        ink = binz(img)
        boxes = _glyph_boxes(ink)
        text, conf = reader.read(ink)
        print(f"  [{name:8}] ink={int(ink.sum()):6d} glyphs={len(boxes):2d} "
              f"text={text!r} conf={conf:.2f}")
        if boxes:
            hs = [b[3] - b[2] + 1 for b in boxes]
            ws = [b[1] - b[0] + 1 for b in boxes]
            print(f"             glyph h={min(hs)}..{max(hs)} w={min(ws)}..{max(ws)}")


def main():
    if len(sys.argv) > 1:
        for p in sys.argv[1:]:
            show(Path(p))
        return
    candidates = [
        DEBUG_DIR / "crops" / "w_strength_prep.png",
        DEBUG_DIR / "crops" / "win_str_crop.png",
        DEBUG_DIR / "diag_str_crop.png",
        DEBUG_DIR / "crops" / "w_gems_prep.png",
    ]
    for p in candidates:
        if p.exists():
            show(p)
        else:
            print(f"[skip] {p} not found")
    r = TemplateReader()
    print("\nKnown glyph templates:", {c: len(t) for c, t in sorted(r.templates.items())})


if __name__ == "__main__":
    main()
