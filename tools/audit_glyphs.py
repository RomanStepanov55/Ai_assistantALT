"""
Glyph store AUDIT - finds poisoned templates in data/glyph_templates.npz.

Why this exists:
    The reader learns glyph shapes from OCR-verified badge strings. The old
    version trusted the OCR label wholesale, so a single misaligned/mid-animation
    crop could store a shape that is NOT the digit it is labelled as. Measured
    example: the stored '4' had IoU 0.18 with a real '4' from the blue gems
    badge, so the live badge "14.0M" read as "1?.0M" - the eye was blind on
    the very counter the brain needs for its reward signal.

How it decides:
    It never guesses. For every saved frame it reads the badge with the current
    templates and asks WinRT OCR for a second opinion. A frame only counts as
    EVIDENCE when the two agree on every anchor (positions the template already
    recognized). The collected glyphs are then compared with the stored
    templates: a template whose best IoU against evidence of its own label is
    below --poison is reported as poisoned.

Usage:
    python tools/audit_glyphs.py                # report only
    python tools/audit_glyphs.py --quarantine   # move bad templates to .bad.npz
"""
import argparse
import json
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image

from config import DEBUG_DIR, DATA_DIR
from vision.ocr_engine import OCREngine
from vision.screen_eye import ScreenEye
from vision.template_reader import (TemplateReader, binarize_strength,
                                    binarize_gems, locate_badges,
                                    _glyph_boxes, _normalize_glyph,
                                    is_badge_number)


class _Eye(ScreenEye):
    def __init__(self, frame):
        self.frame = frame

    def capture_full_window(self):
        return self.frame

    def get_roi_crop(self, roi_name, frame=None):
        return ScreenEye.get_roi_crop(self, roi_name, frame or self.frame)


def _crop_badge(frame, kind):
    """Badge crop for the audit.

    strength: auto-located by its yellow colour (reliable, the badge is the only
              large yellow strip in the HUD band);
    gems:     calibrated ROI on purpose - the blue gems badge sits on a blue sky,
              so colour segmentation merges it with the background. The ROI is
              the honest choice here; the audit's job is glyph truth, not layout.
    """
    if kind == "gems":
        return _Eye(frame).get_roi_crop("gems_counter")
    cands = [b for b in locate_badges(frame) if b["kind"] == "strength"]
    cands.sort(key=lambda b: -b["area"])
    if cands:
        x1, y1, x2, y2 = cands[0]["box"]
        pad = max(2, int(cands[0]["h"] * 0.06))
        return frame.crop((max(0, x1 - pad), max(0, y1 - pad),
                           min(frame.width, x2 + pad), min(frame.height, y2 + pad)))
    return _Eye(frame).get_roi_crop("strength_counter")


def _anchors_agree(template_text, label):
    """True when the OCR label matches the template on every known position."""
    if not label or len(label) != len(template_text):
        return False
    pairs = [(t, l) for t, l in zip(template_text, label) if t != "?"]
    if len(pairs) < max(1, len(label) // 2):
        return False
    return all(t == l for t, l in pairs)


def collect_evidence(reader, ocr, frames, verbose=True):
    """char -> list of normalized glyphs validated by template+OCR agreement."""
    evidence = defaultdict(list)
    stats = {"frames": 0, "read": 0, "agreed": 0, "learned": 0}
    for p in frames:
        stats["frames"] += 1
        try:
            frame = Image.open(p).convert("RGB")
        except Exception:
            continue
        for kind, binz in (("strength", binarize_strength), ("gems", binarize_gems)):
            crop = _crop_badge(frame, kind)
            if crop is None:
                continue
            try:
                ink = binz(crop)
            except Exception:
                continue
            if int(ink.sum()) < 200:
                continue
            text, conf = reader.read(ink)
            if not text or conf < 0.7:
                continue
            stats["read"] += 1
            label = ocr.read_badge_number(crop, ink, "audit")
            if not label or len(label) != len(text):
                continue
            pairs = list(zip(text, label))
            anchors = [(t, l) for t, l in pairs if t != "?"]
            # Every position the template already recognized must agree with
            # OCR. Only then is the OCR label allowed to vouch for the shapes
            # the template could not read.
            if len(anchors) < max(1, len(label) // 2):
                continue
            if any(t != l for t, l in anchors):
                continue
            stats["agreed"] += 1
            boxes = _glyph_boxes(ink)
            if len(boxes) != len(label):
                continue
            for (x0, x1, y0, y1), t_ch, l_ch in zip(boxes, text, label):
                if t_ch != l_ch:
                    continue
                evidence[l_ch].append(_normalize_glyph(ink[y0:y1 + 1, x0:x1 + 1]))
            # Close the learning loop right here: the '?' slots are taught from
            # an OCR label that agreed on all anchors (additive, poison-proof).
            if "?" in text:
                stats["learned"] += reader.learn_partial(ink, text, label)
        if verbose and stats["frames"] % 10 == 0:
            print(f"  ...{stats['frames']} frames, evidence chars={len(evidence)}")
    return evidence, stats


def audit(reader, evidence, poison_threshold=0.55):
    report = {}
    for char, tpls in sorted(reader.templates.items()):
        good = evidence.get(char, [])
        rows = []
        for idx, tpl in enumerate(tpls):
            if good:
                own = max(reader._score(tpl, g) for g in good)
            else:
                own = None
            # cross-check: is this template closer to another char's evidence?
            rival_char, rival = "?", 0.0
            for other, glyphs in evidence.items():
                if other == char or not glyphs:
                    continue
                s = max(reader._score(tpl, g) for g in glyphs)
                if s > rival:
                    rival_char, rival = other, s
            rows.append({"index": idx, "own_iou": None if own is None else round(float(own), 3),
                         "rival": rival_char, "rival_iou": round(float(rival), 3),
                         "poisoned": bool(own is not None and own < poison_threshold),
                         "confused": bool(rival > (own or 0) + 0.15)})
        report[char] = {"templates": len(tpls), "evidence": len(good), "rows": rows}
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="*.png")
    ap.add_argument("--poison", type=float, default=0.55,
                    help="own-label IoU below this => template is poisoned")
    ap.add_argument("--quarantine", action="store_true",
                    help="remove poisoned templates (backup kept)")
    args = ap.parse_args()

    frames = []
    for p in sorted(DEBUG_DIR.glob(args.glob)):
        try:
            img = Image.open(p)
        except Exception:
            continue
        if img.size[0] >= 1200 and img.size[1] >= 600:
            frames.append(p)
    if not frames:
        print("[audit] no frames found in", DEBUG_DIR)
        return

    reader = TemplateReader()
    ocr = OCREngine()
    print(f"[audit] frames={len(frames)}")
    evidence, stats = collect_evidence(reader, ocr, frames)
    print(f"[audit] readable={stats['read']} ocr-agreed={stats['agreed']} "
          f"chars_with_evidence={sorted(evidence)}")

    report = audit(reader, evidence, args.poison)
    bad = []
    for char, info in report.items():
        for row in info["rows"]:
            own = "?" if row["own_iou"] is None else f"{row['own_iou']:.2f}"
            flag = "POISON" if row["poisoned"] else ("confused" if row["confused"] else "ok")
            if row["poisoned"]:
                bad.append((char, row["index"]))
            print(f"  '{char}' #{row['index']}: own={own} "
                  f"rival={row['rival']}({row['rival_iou']:.2f}) "
                  f"evidence={info['evidence']} -> {flag}")

    out = DATA_DIR / "glyph_audit.json"
    out.write_text(json.dumps({"stats": stats,
                               "report": {c: {"templates": v["templates"],
                                              "evidence": v["evidence"],
                                              "rows": v["rows"]}
                                          for c, v in report.items()}},
                              indent=2), encoding="utf-8")
    print(f"[audit] report -> {out}")

    if not args.quarantine:
        if bad:
            print(f"[audit] {len(bad)} poisoned template(s); re-run with --quarantine to drop them")
        return

    if not bad:
        print("[audit] nothing to quarantine")
        return

    from vision.template_reader import STORE_NPZ, STORE_META
    backup = STORE_NPZ.with_suffix(".npz.bak")
    if STORE_NPZ.exists() and not backup.exists():
        shutil.copy2(STORE_NPZ, backup)
    by_char = defaultdict(set)
    for char, idx in bad:
        by_char[char].add(idx)
    for char, idxs in by_char.items():
        keep = [t for i, t in enumerate(reader.templates[char]) if i not in idxs]
        if keep:
            reader.templates[char] = keep
        else:
            reader.templates.pop(char, None)
        print(f"  quarantined '{char}' #{sorted(idxs)}")

    # Re-seed every char that has OCR-verified evidence but no trustworthy
    # template left. The evidence glyph IS the truth: it comes from a badge
    # where the template and OCR agreed on all anchors.
    seeded = 0
    for char, glyphs in evidence.items():
        if not glyphs:
            continue
        existing = reader.templates.get(char, [])
        if existing and max((max(reader._score(t, g) for g in glyphs) for t in existing),
                            default=0.0) >= args.poison:
            continue
        reader.templates[char] = [glyphs[0]]
        seeded += 1
        print(f"  seeded '{char}' from verified evidence ({len(glyphs)} sample(s))")
    if seeded:
        reader._holes_cache = {}
    reader._save()
    print(f"[audit] store rewritten; backup at {backup.name}; "
          f"missing now: {reader.missing_chars()}")


if __name__ == "__main__":
    main()
