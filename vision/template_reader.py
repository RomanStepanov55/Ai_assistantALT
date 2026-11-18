"""
Template-based digit reader for Muscle Legends HUD badges.

WinRT OCR reads the strength badge (dark digits on yellow) but consistently
fails on the gems badge (orange digits on green), even with clean binarization.
The badge font is FIXED, so we harvest glyph templates from OCR-verified
readings and recognize future numbers by shape matching (IoU), with a hole-count
(topology) tie-breaker. The store persists to data/glyph_templates.npz and
auto-learns new glyph shapes whenever a WinRT-verified string is available.

Binary convention: ink=True (digit pixels), background=False.
"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from config import DATA_DIR

STORE_NPZ = DATA_DIR / "glyph_templates.npz"
STORE_META = DATA_DIR / "glyph_templates.json"

_BADGE_RE = re.compile(r"(\d{1,6})(?:\.(\d{1,2}))?([KMBT]?)")

GLYPH_H, GLYPH_W = 32, 24
MIN_GLYPH_H = 8
MIN_GLYPH_W = 3

# Per-glyph acceptance threshold (IoU). Measured on real captured badges:
# a true match scores 0.93..1.00, while the closest WRONG shape (round digits
# confuse each other) tops out at 0.80. Below the threshold the reader must say
# '?' instead of guessing: a missing glyph shape (e.g. '0', which cannot be
# derived from 1-9) otherwise turns "100K" into a confident but false "166K",
# and wrong numbers are far more expensive than unreadable ones.
MIN_GLYPH_SCORE = 0.85

# Common WinRT OCR confusions inside numeric badge strings
NUMERIC_CONFUSIONS = {
    "O": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2",
    "G": "6", "T": "7", "A": "4", "G": "6", "D": "0", "J": "1", "P": "?",
}


def is_badge_number(text: str) -> Optional[str]:
    """Validates a template reading as a HUD badge number.

    Segmented glyph boxes can silently DROP a digit ('91.5K' -> '91.K'), and
    parse_stat_string happily turns that into 91_000 - a wrong value that the
    sanity gate (x0.2..x5) will not catch because it looks close. A badge always
    has 1-3 digits before the suffix, so the format itself is the cheapest
    defence. Returns the suffix ('' for none) or None if the text is not a badge.
    """
    if not text:
        return None
    m = _BADGE_RE.fullmatch(text)
    if not m:
        return None
    whole, frac, suffix = m.groups()
    if suffix and len(whole) > 3:
        return None
    # The HUD never prints leading zeros ("00", "007K"): such a string is OCR
    # noise scraped from unrelated text (measured: an editor frame's FPS/memory
    # numbers produced "00" -> value 0, i.e. a plausible number nobody saw).
    if len(whole) > 1 and whole.startswith("0"):
        return None
    # A decimal point without a suffix is never a real badge ("56.6M" whose 'M'
    # glyph got cut reads as "56.6" -> 56 strength). Require the suffix.
    if frac and not suffix:
        return None
    return suffix or ""


def _remove_border_ink(ink: np.ndarray) -> np.ndarray:
    """Removes ink connected to the image border (badge frames, design stripes).

    Implemented as a run-length connected-component labelling: every row is
    reduced to its runs of ink, runs overlapping between adjacent rows are
    unioned, and only components that touch the border are erased. It is the
    same answer as a per-pixel flood fill (verified pixel-identical on live
    frames), but ~7x faster - the eye runs this three times per badge and a
    0.8 s Python BFS per mask made the perception layer unusable.
    """
    h, w = ink.shape
    if h == 0 or w == 0 or not ink.any():
        return ink
    parent: List[int] = []
    runs: List[Tuple[int, int, int, int]] = []
    prev: List[Tuple[int, int, int]] = []

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for y in range(h):
        row = ink[y]
        if not row.any():
            prev = []
            continue
        edges = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts = np.flatnonzero(edges == 1).tolist()
        ends = np.flatnonzero(edges == -1).tolist()
        cur: List[Tuple[int, int, int]] = []
        j = 0
        for x0, x1 in zip(starts, ends):
            idx = len(parent)
            parent.append(idx)
            while j < len(prev) and prev[j][1] <= x0:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] < x1:
                ra, rb = find(idx), find(prev[k][2])
                if ra != rb:
                    parent[rb] = ra
                k += 1
            cur.append((x0, x1, idx))
            runs.append((y, x0, x1, idx))
        prev = cur

    border_roots = {find(idx) for y, x0, x1, idx in runs
                    if y == 0 or y == h - 1 or x0 == 0 or x1 >= w}
    if not border_roots:
        return ink
    out = ink.copy()
    for y, x0, x1, idx in runs:
        if find(idx) in border_roots:
            out[y, x0:x1] = False
    return out

def _morph_open(ink: np.ndarray, iters: int = 2) -> np.ndarray:
    """Binary opening (erode then dilate) to break thin design-line bridges."""
    def erode(m):
        r = m.copy()
        for _ in range(iters):
            r = r & np.roll(r, 1, 0) & np.roll(r, -1, 0) & np.roll(r, 1, 1) & np.roll(r, -1, 1)
        return r

    def dilate(m):
        r = m.copy()
        for _ in range(iters):
            r = r | np.roll(r, 1, 0) | np.roll(r, -1, 0) | np.roll(r, 1, 1) | np.roll(r, -1, 1)
        return r

    return dilate(erode(ink))

def binarize_gems(image: Image.Image, variant: str = "auto") -> np.ndarray:
    """Second top badge - two variants exist:
    - GREEN candy badge: bright-orange digits (R=255) on green -> R>215 mask;
    - BLUE gems badge (brawl arenas): dark digits on light blue -> dark-ink mask.
    The badge variant is detected from the background color, but a wrong guess
    is silent and fatal (all ink disappears), so `variant` can force either
    mode - the eye uses that as an independent second reading of the same crop.
    Candy icon zone is cropped out; border-connected ink removed."""
    w, h = image.size

    # Variant detection needs pixels: a small probe of the crop is enough.
    probe = np.asarray(image.crop((int(w * 0.30), 0, int(w * 0.60), h))
                       .resize((24, 12)).convert("RGB")).astype(int)
    if variant == "auto":
        # Blue badge has a light-blue background (B > G); the green candy badge
        # has a green background (G > B).
        variant = "dark" if int(np.median(probe[:, :, 2])) > int(np.median(probe[:, :, 1])) else "orange"

    # The badge ICON lives on different sides depending on the variant: the
    # blue gems badge carries its gem on the RIGHT, the green candy badge its
    # candy on the LEFT. Cropping the wrong side silently decapitates the
    # number ("14.0M" -> "14.0"), so the window follows the variant.
    if variant == "dark":
        inner = image.crop((int(w * 0.02), 0, int(w * 0.90), h))
    else:
        inner = image.crop((int(w * 0.16), 0, int(w * 0.96), h))
    big = inner.resize((inner.width * 3, inner.height * 3), Image.Resampling.LANCZOS)
    arr = np.asarray(big.convert("RGB")).astype(int)
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    if variant == "dark":
        # blue badge with dark digits on a light background. The cut is
        # relative (Otsu) so a dimmed scene still reads.
        gray = arr.mean(axis=2)
        thr = _otsu_threshold(gray)
        ink = (gray < thr) & (B >= R)
    else:
        # green badge with bright orange digits: the orange channel (R - G)
        # separates digits from the green plate far better than raw R, and
        # survives the dimming as well.
        d = R - G
        thr = _otsu_threshold(d)
        ink = d > thr
    ink = _remove_border_ink(ink)
    return _morph_open(ink, iters=1)

def _otsu_threshold(values: np.ndarray) -> float:
    """Otsu's threshold: the cut that best separates ink from background.

    Absolute thresholds only work while the badge is fully bright. Roblox dims
    the whole scene behind an open modal (USE BOOST, wheel, quests), and then a
    fixed cut sees NO ink at all - the eye went blind exactly in the moments
    where it most needs to see. Otsu follows the actual brightness of the crop,
    so a dimmed badge is read just as well as a bright one.
    """
    vals = values.ravel()
    if vals.size == 0:
        return 128.0
    lo = int(vals.min())
    hi = int(vals.max())
    if hi <= lo:
        return float(lo)
    hist = np.bincount((vals - lo).astype(np.int64),
                       minlength=(hi - lo + 1)).astype(np.float64)
    levels = np.arange(lo, hi + 1, dtype=np.float64)
    total = vals.size
    w_b = np.cumsum(hist)
    w_f = total - w_b
    sum_b = np.cumsum(hist * levels)
    sum_total = float((hist * levels).sum())
    valid = (w_b > 0) & (w_f > 0)
    if not valid.any():
        return float((lo + hi) / 2)
    m_b = np.where(w_b > 0, sum_b / np.maximum(w_b, 1e-9), 0.0)
    m_f = np.where(w_f > 0, (sum_total - sum_b) / np.maximum(w_f, 1e-9), 0.0)
    between = np.where(valid, w_b * w_f * (m_b - m_f) ** 2, -1.0)
    idx = int(np.argmax(between))
    return float(levels[idx])


def binarize_strength(image: Image.Image, thr: Optional[int] = None) -> np.ndarray:
    """Dark digits on bright yellow badge -> ink mask (frame removed).

    `thr` is the luminance cut-off. thr=None (default) picks the cut with
    Otsu's method, which survives the scene dimming Roblox applies behind
    modals; a fixed number is still available for a second, independent read.
    """
    arr = np.asarray(image.convert("L")).astype(int)
    if thr is None:
        thr = int(_otsu_threshold(arr))
    return _remove_border_ink(arr < thr)

def binarize_generic_dark_on_light(image: Image.Image) -> np.ndarray:
    arr = np.asarray(image.convert("L")).astype(int)
    return _remove_border_ink(arr < 110)


def ink_to_image(ink: np.ndarray, size: Optional[Tuple[int, int]] = None,
                 upscale: int = 4) -> Image.Image:
    """Renders an ink mask as a CLEAN OCR target: black digits, white paper.

    Why this matters for the eye: WinRT OCR on the raw coloured badge is almost
    blind - the stylized font on a yellow/blue gradient produces garbage
    ("8108K" for "81.8K", empty for "14.0M"). The same pixels rendered as plain
    black-on-white are read correctly ("2.76M", "14.0M"), which gives the eye an
    INDEPENDENT second opinion and lets it teach its own glyph store.

    `ink` may be internally upscaled (binarize_gems does 3x): pass `size` (the
    original crop size) to bring it back before the final upscale.
    """
    arr = ink.astype("uint8") * 255
    img = Image.fromarray(arr)
    if size is not None and img.size != size:
        img = img.resize(size, Image.Resampling.NEAREST)
    if upscale > 1:
        img = img.resize((img.width * upscale, img.height * upscale),
                         Image.Resampling.LANCZOS)
    return img


# --------------------------------------------------------------------------
# BADGE AUTO-LOCATION (ROI-drift insurance)
# The calibrated ROIS in config.py assume one window size/aspect. Any change
# (DPI virtualization, different gym layout, Windows rescale) puts the badge
# OUTSIDE the ROI -> binarize sees no ink -> every read fails ("OCR unverified")
# and the agent loses its ability to verify training. Instead of trusting the
# fixed ROI we can FIND the badge by its own colors in the top HUD band.
# --------------------------------------------------------------------------

BADGE_BAND_HEIGHT = 0.18      # badge row lives in the top 18% of the client
MIN_BADGE_W_FRAC = 0.045      # a badge is at least 4.5% of the width
MIN_BADGE_H_FRAC = 0.020


def _badge_masks(arr: np.ndarray) -> Dict[str, np.ndarray]:
    """Color masks for the three HUD badge variants (RGB int16 array)."""
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    yellow = (R > 150) & (G > 110) & (B < 130) & (R >= G) & (G >= B)
    green = (G > 110) & (G > R + 10) & (G > B + 10)
    blue = (B > 120) & (B > G) & (B > R + 20)
    return {"strength": yellow, "gems": green, "gems_blue": blue}


def _components(mask: np.ndarray, step: int = 4) -> List[Tuple[int, int, int, int]]:
    """Cheap connected components on a step-downsampled mask.
    Returns boxes in ORIGINAL pixel coordinates.

    The block pooling is vectorised: the previous per-cell Python loop cost
    ~1.6 s per HUD scan, which made the eye unusable in the hot path. Pooling
    with reshape+mean is a few milliseconds, BFS then runs on a tiny grid.
    """
    h, w = mask.shape
    sh, sw = max(1, h // step), max(1, w // step)
    if sh == 0 or sw == 0:
        return []
    block = mask[:sh * step, :sw * step].reshape(sh, step, sw, step)
    small = block.mean(axis=(1, 3)) > 0.6
    seen = np.zeros_like(small, dtype=bool)
    boxes = []
    cells = np.argwhere(small)
    for y0, x0 in cells:
        if seen[y0, x0]:
            continue
        stack = [(int(y0), int(x0))]
        seen[y0, x0] = True
        xs = [int(x0)]
        ys = [int(y0)]
        while stack:
            cy, cx = stack.pop()
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < sh and 0 <= nx < sw and small[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
                    xs.append(nx)
                    ys.append(ny)
        boxes.append((min(xs) * step, min(ys) * step,
                      (max(xs) + 1) * step, (max(ys) + 1) * step))
    return boxes


def _plate_has_digits(frame: Image.Image, box) -> bool:
    """A real HUD badge carries dark digit strokes inside its plate.

    Geometry alone is NOT evidence. Measured 2026-11-17 on a real captured
    frame of the Roblox app music page (debug_frames/now_watch.png): four
    coloured album cards matched the badge geometry exactly (292x68, ratio
    4.3, inside the HUD band), so locate_badges() "found" gems_blue,
    strength and gems plates there and the eye reported a music page as the
    game - while the bot went on clicking into it. Their interiors measured
    ink 0.000 with 0 tall column runs, against ink 0.024-0.37 with >= 1 tall
    run for every real strength/gems plate in the same corpus.
    """
    x1, y1, x2, y2 = box
    crop = np.asarray(frame.convert("L").crop((x1, y1, x2, y2))).astype(np.int16)
    if crop.size == 0:
        return False
    ph, pw = crop.shape
    if ph < 8 or pw < 8:
        return False
    med = float(np.median(crop))
    dark = crop < med * 0.60          # strokes are much darker than the plate
    if float(dark.mean()) < 0.015:
        return False
    tall = dark.sum(axis=0) >= max(4, int(ph * 0.35))
    run, runs = 0, 0
    min_run = max(3, int(ph * 0.06))
    for v in tall:
        if v:
            run += 1
        else:
            if run >= min_run:
                runs += 1
            run = 0
    if run >= min_run:
        runs += 1
    return runs >= 1


def locate_badges(frame: Image.Image, band: float = BADGE_BAND_HEIGHT) -> List[Dict]:
    """
    Finds HUD badges by color in the top band of a client frame.
    Returns [{"kind", "box": (x1, y1, x2, y2), "area"}] sorted left-to-right.
    Pure PIL/numpy - no OCR subprocess, safe for the hot path.

    A colour plate must also carry digit strokes inside it (`_plate_has_digits`):
    the Roblox app's own pages contain coloured rectangles of exactly badge
    size, and accepting those made the eye vouch for a non-game screen.
    """
    w, h = frame.size
    if w < 80 or h < 80:
        return []
    band_h = max(40, int(h * band))
    arr = np.asarray(frame.convert("RGB").crop((0, 0, w, band_h))).astype(np.int16)
    out = []
    for kind, mask in _badge_masks(arr).items():
        min_w = max(20, int(w * MIN_BADGE_W_FRAC))
        min_h = max(10, int(band_h * MIN_BADGE_H_FRAC))
        for (x1, y1, x2, y2) in _components(mask):
            bw, bh = x2 - x1, y2 - y1
            if bw < min_w or bh < min_h:
                continue
            # a badge is a wide horizontal strip, not a tall slab
            if bh > 0.9 * band_h:
                continue
            if not _plate_has_digits(frame, (x1, y1, x2, y2)):
                continue
            out.append({"kind": kind, "box": (x1, y1, x2, y2),
                        "area": bw * bh, "w": bw, "h": bh})
    out.sort(key=lambda d: d["box"][0])
    return out





def _normalize_glyph(glyph: np.ndarray) -> np.ndarray:
    """Crops to ink bbox and resizes to GLYPH_H x GLYPH_W binary array."""
    ys, xs = np.where(glyph)
    if len(xs) == 0:
        return np.zeros((GLYPH_H, GLYPH_W), dtype=bool)
    g = glyph[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    img = Image.fromarray((g * 255).astype("uint8")).resize((GLYPH_W, GLYPH_H))
    return np.asarray(img) > 127


def _glyph_boxes(ink: np.ndarray) -> List[Tuple[int, int, int, int]]:
    """Segments glyph bounding boxes by column projection.
    Any zero-ink column splits; boxes much taller than the median are design
    artifacts and get dropped."""
    h, w = ink.shape
    col = ink.sum(axis=0)
    raw_boxes = []
    x = 0
    while x < w:
        if col[x] > 0:
            x0 = x
            while x < w and col[x] > 0:
                x += 1
            x1 = x - 1
            sub = ink[:, x0:x1 + 1]
            rows = np.where(sub.sum(axis=1) > 0)[0]
            if len(rows) == 0:
                continue
            y0, y1 = rows.min(), rows.max()
            if (y1 - y0 + 1) >= MIN_GLYPH_H and (x1 - x0 + 1) >= MIN_GLYPH_W:
                raw_boxes.append((x0, x1, y0, y1))
        else:
            x += 1

    if not raw_boxes:
        return raw_boxes
    heights = sorted(b[3] - b[2] + 1 for b in raw_boxes)
    median_h = heights[len(heights) // 2]
    boxes = [b for b in raw_boxes if (b[3] - b[2] + 1) <= median_h * 1.6]

    # Split merged glyphs (touching digits): a box wider than ~1.25x its height
    # is cut at the weakest-ink column in its middle zone.
    final = []
    for (x0, x1, y0, y1) in boxes:
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        if bw > bh * 1.25 and bw > MIN_GLYPH_W * 3:
            col = ink[y0:y1 + 1, x0:x1 + 1].sum(axis=0)
            edge = max(2, bw // 6)
            mid = col[edge:bw - edge]
            cut = int(np.argmin(mid)) + edge
            for (a, b) in ((x0, x0 + cut - 1), (x0 + cut, x1)):
                sub = ink[y0:y1 + 1, a:b + 1]
                rows = np.where(sub.sum(axis=1) > 0)[0]
                if len(rows) and (b - a + 1) >= MIN_GLYPH_W:
                    final.append((a, b, y0 + rows.min(), y0 + rows.max()))
        else:
            final.append((x0, x1, y0, y1))
    return final


def _hole_count(glyph: np.ndarray) -> int:
    """Counts enclosed background regions (topology feature: 8 has 2, 0/6/9 have 1...)."""
    h, w = glyph.shape
    bg = ~glyph
    visited = np.zeros_like(bg, dtype=bool)
    stack = []
    # flood background from all border pixels
    for x in range(w):
        stack.extend([(0, x), (h - 1, x)])
    for y in range(h):
        stack.extend([(y, 0), (y, w - 1)])
    while stack:
        y, x = stack.pop()
        if y < 0 or y >= h or x < 0 or x >= w or visited[y, x] or not bg[y, x]:
            continue
        visited[y, x] = True
        stack.extend([(y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)])
    holes = 0
    seen_hole = np.zeros_like(bg, dtype=bool)
    for y in range(h):
        for x in range(w):
            if bg[y, x] and not visited[y, x] and not seen_hole[y, x]:
                holes += 1
                q = [(y, x)]
                seen_hole[y, x] = True
                while q:
                    cy, cx = q.pop()
                    for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                        if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not seen_hole[ny, nx]:
                            seen_hole[ny, nx] = True
                            q.append((ny, nx))
    return holes


class TemplateReader:
    def __init__(self):
        self.templates: Dict[str, List[np.ndarray]] = {}
        self._holes_cache: Dict[int, int] = {}
        self._load()

    # ---------- persistence ----------

    def _load(self):
        try:
            if STORE_NPZ.exists():
                data = np.load(STORE_NPZ)
                for key in data.files:
                    char = key[1:]  # keys like "a8" (char may be digits -> prefixed)
                    self.templates[char] = [data[key][i] for i in range(data[key].shape[0])]
        except Exception:
            self.templates = {}
        self._holes_cache = {}

    def _save(self):
        try:
            arrays = {}
            for char, tpls in self.templates.items():
                arrays["a" + char] = np.stack(tpls) if tpls else np.zeros((0, GLYPH_H, GLYPH_W), bool)
            np.savez_compressed(STORE_NPZ, **arrays)
            STORE_META.write_text(json.dumps({c: len(t) for c, t in self.templates.items()}), encoding="utf-8")
        except Exception:
            pass

    # ---------- recognition ----------

    def _score(self, a: np.ndarray, b: np.ndarray) -> float:
        inter = np.logical_and(a, b).sum()
        union = np.logical_or(a, b).sum()
        return inter / union if union else 0.0

    def _template_holes(self, tpl: np.ndarray) -> int:
        """Hole count of a template, cached (computed on the normalized glyph,
        the same space in which classification happens)."""
        key = id(tpl)
        holes = self._holes_cache.get(key)
        if holes is None:
            holes = _hole_count(tpl)
            self._holes_cache[key] = holes
        return holes

    def _classify(self, glyph_norm: np.ndarray, holes: int,
                  min_score: float = MIN_GLYPH_SCORE) -> Tuple[str, float]:
        """Best IoU match, with topology (hole count) as a tie-breaker.

        Why: '0' is the one digit whose template may be missing (it cannot be
        inferred from 1-9), and without a topology check a '0' collapses into
        whichever of 6/8/9 scores highest - producing a plausible but WRONG
        number ("100K" read as "166K"), which is worse than "?".
        """
        best_char, best_score = "?", 0.0
        topo_char, topo_score = "?", 0.0
        for char, tpls in self.templates.items():
            for tpl in tpls:
                s = self._score(glyph_norm, tpl)
                if s > best_score:
                    best_char, best_score = char, s
                if s > topo_score and self._template_holes(tpl) == holes:
                    topo_char, topo_score = char, s
        # Topology wins only when it is competitive: a glyph whose holes match a
        # template must not be overridden by a hole-mismatched one, unless that
        # one is clearly better (protects against resize-closed small holes).
        if topo_char != "?" and topo_score >= best_score - 0.12:
            best_char, best_score = topo_char, topo_score
        if best_score < min_score:
            return "?", best_score
        return best_char, best_score

    def read(self, ink: np.ndarray,
             min_confidence: float = MIN_GLYPH_SCORE) -> Tuple[str, float]:
        """Reads a segmented digit string; returns (text, avg_confidence).

        `min_confidence` is the per-glyph acceptance threshold: glyphs below it
        become '?' so callers can tell "unknown" from "wrong".
        """
        text, avg, _boxes, _chars = self.read_detailed(ink, min_confidence)
        return text, avg

    def _segments(self, ink: np.ndarray, min_confidence: float = MIN_GLYPH_SCORE):
        """Classifies every blob of a badge and drops the plate ICON.

        A badge plate carries a picture next to the number (a dumbbell on the
        strength badge, a gem on the gems badge). That picture is ink, so it
        used to occupy a glyph slot and turned every reading into '?81.8K'.
        An icon is recognisable: the reader cannot classify it ('?') AND it is
        at the very edge of the plate AND it is clearly taller/wider than the
        digits beside it (digits share the font's cap height, so a real
        unknown digit never trips this test).

        One segmentation for reading AND learning - otherwise the icon would
        shift the glyph/label alignment and teach the wrong shape.
        """
        boxes = _glyph_boxes(ink)
        if not boxes:
            return [], [], []
        chars, scores = [], []
        for (x0, x1, y0, y1) in boxes:
            glyph_norm = _normalize_glyph(ink[y0:y1 + 1, x0:x1 + 1])
            holes = _hole_count(glyph_norm)  # same space as the templates
            ch, sc = self._classify(glyph_norm, holes, min_confidence)
            chars.append(ch)
            scores.append(sc)
        drop = self._icon_indices(boxes, chars)
        if drop:
            keep = [i for i in range(len(boxes)) if i not in drop]
            boxes = [boxes[i] for i in keep]
            chars = [chars[i] for i in keep]
            scores = [scores[i] for i in keep]
        return boxes, chars, scores

    def _icon_indices(self, boxes, chars) -> set:
        """Indices of the plate icon: unrecognized, at the edge, not digit-shaped."""
        if len(boxes) < 3:
            return set()
        widths = [b[1] - b[0] + 1 for b in boxes]
        heights = [b[3] - b[2] + 1 for b in boxes]
        known = [i for i, c in enumerate(chars) if c != "?"]
        if not known:
            return set()
        med_w = sorted(widths[i] for i in known)[len(known) // 2]
        med_h = sorted(heights[i] for i in known)[len(known) // 2]
        drop = set()
        for i in (0, len(boxes) - 1):
            if chars[i] != "?":
                continue                       # a recognized glyph is data
            bw, bh = widths[i], heights[i]
            too_tall = bh > med_h * 1.25
            too_wide = bw > med_w * 1.25
            not_digit_shaped = (bw / max(bh, 1)) > 0.85
            if (too_tall or too_wide or not_digit_shaped) and bh <= med_h * 1.6:
                drop.add(i)
        return drop

    def read_detailed(self, ink: np.ndarray,
                      min_confidence: float = MIN_GLYPH_SCORE
                      ) -> Tuple[str, float, List[Tuple[int, int, int, int]], List[str]]:
        """read() + the glyph boxes and per-glyph characters (icon excluded)."""
        boxes, chars, scores = self._segments(ink, min_confidence)
        if not boxes:
            return "", 0.0, [], []
        text = "".join(chars)
        avg = sum(scores) / len(scores) if scores else 0.0
        return text, avg, boxes, chars

    # ---------- learning ----------

    def learn(self, ink: np.ndarray, label: str) -> bool:
        """Stores glyphs for an OCR-verified string. Glyph count must match."""
        boxes, _chars, _scores = self._segments(ink)
        if len(boxes) != len(label):
            return False
        learned = 0
        for (x0, x1, y0, y1), ch in zip(boxes, label):
            if ch in "? ":
                continue
            glyph_norm = _normalize_glyph(ink[y0:y1 + 1, x0:x1 + 1])
            lst = self.templates.setdefault(ch, [])
            # avoid exact duplicates
            if not any(self._score(glyph_norm, t) > 0.92 for t in lst):
                lst.append(glyph_norm)
                learned += 1
        if learned:
            self._save()
        return True

    def known_chars(self) -> List[str]:
        return sorted(self.templates.keys())

    def missing_chars(self) -> List[str]:
        """Characters the badge font can show but the store cannot read yet."""
        return [c for c in "0123456789KMBT." if c not in self.templates]

    def learn_partial(self, ink: np.ndarray, template_text: str,
                      label: str) -> int:
        """Learns ONLY the glyphs the reader could not classify ('?').

        Sources of truth: `label` (WinRT OCR, normalized) and the template read.
        The two strings must have the same length - a length mismatch means the
        OCR line is not the badge (scene text, merged digits) and learning from
        it would poison the store. Positions where the template already has a
        confident character are NEVER overwritten.

        Returns the number of glyphs learned.
        """
        if not label or not template_text or len(label) != len(template_text):
            return 0
        boxes, _chars, _scores = self._segments(ink)
        if len(boxes) != len(label):
            return 0
        # ANCHORS: every position the template already read confidently must
        # agree with the OCR label. Without this, a shifted/misaligned OCR line
        # would teach the wrong shape for the '?' slots.
        anchors = [(t, l) for t, l in zip(template_text, label) if t != "?"]
        if len(anchors) < max(1, len(label) // 2) or any(t != l for t, l in anchors):
            return 0
        learned = 0
        for (x0, x1, y0, y1), t_ch, l_ch in zip(boxes, template_text, label):
            if t_ch != "?" or l_ch in "? .":
                continue
            glyph_norm = _normalize_glyph(ink[y0:y1 + 1, x0:x1 + 1])
            lst = self.templates.setdefault(l_ch, [])
            if any(self._score(glyph_norm, t) > 0.92 for t in lst):
                continue
            lst.append(glyph_norm)
            learned += 1
        if learned:
            self._holes_cache = {}
            self._save()
        return learned
