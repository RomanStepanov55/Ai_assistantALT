"""
eye.py - the operator's window into the bot's perception.

Three modes, one code path (the same Eye.observe() the brain uses):

    python eye.py                 live: one scene + annotated PNG
    python eye.py --deep          live + OCR (modal, event banner, CLAIM)
    python eye.py --watch 1.0     live loop, one compact line per second
    python eye.py --health        self-check: what can the eye see right now?
    python eye.py --frame X.png   one saved frame (offline, no game needed)
    python eye.py --scan          every saved frame -> table + HTML gallery

Nothing here clicks, types or moves the mouse. The eye only looks.
"""
import argparse
import html
import json
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from config import DEBUG_DIR
from vision.eye import Eye


def _compact(scene: dict) -> str:
    hud = scene.get("hud", {})
    s, g = hud.get("strength", {}), hud.get("gems", {})
    def fmt(r):
        if r.get("value") is None:
            return "?"
        return f"{r.get('text')}{'*' if r.get('stale') else ''}"
    q = scene.get("quality", {})
    game = "" if q.get("game_visible", True) else "  NO-GAME-HUD"
    return (f"{scene.get('time','')}  STR {fmt(s):>9}  GEM {fmt(g):>9}  "
            f"conf={q.get('confidence', 0):.2f}  "
            f"focused={scene.get('frame', {}).get('focused')}  "
            f"read={scene.get('eye', {}).get('read_ms', '?')}ms{game}"
            + ("  ! " + "; ".join(q.get("warnings") or []) if q.get("warnings") else ""))


def _print_scene(scene: dict, as_json: bool):
    if as_json:
        print(json.dumps(scene, ensure_ascii=False, indent=2))
    else:
        print(_compact(scene))
        print(json.dumps(scene, ensure_ascii=False, indent=2))


def run_live(args) -> int:
    eye = Eye()
    scene = eye.observe(deep=args.deep)
    _print_scene(scene, args.json)
    path = eye.save_annotated(scene, name=args.out)
    if path:
        print(f"[eye] annotated -> {path}")
    return 0 if scene.get("ok") else 2


def run_watch(args) -> int:
    eye = Eye()
    print("[eye] watching (Ctrl+C to stop). '*' = stale value.")
    try:
        while True:
            scene = eye.observe(deep=args.deep)
            print(_compact(scene), flush=True)
            if args.annotate:
                eye.save_annotated(scene, name="eye_watch.png")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\n[eye] stopped")
    return 0


def run_health(args) -> int:
    eye = Eye()
    report = eye.self_check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("verdict") == "ok" else 1


def run_frame(args) -> int:
    path = Path(args.frame)
    frame = Image.open(path).convert("RGB")
    eye = Eye()
    scene = eye.observe(frame=frame, temporal=False, deep=args.deep)
    _print_scene(scene, args.json)
    out = eye.save_annotated(scene, frame=frame,
                             name=args.out or f"eye_{path.stem}_annotated.png")
    if out:
        print(f"[eye] annotated -> {out}")
    return 0 if scene.get("ok") else 2


def _full_frames(pattern: str):
    out = []
    for p in sorted(DEBUG_DIR.glob(pattern)):
        try:
            img = Image.open(p)
        except Exception:
            continue
        if img.size[0] >= 1000 and img.size[1] >= 550:
            out.append(p)
    return out


def run_scan(args) -> int:
    """Offline eye: the whole saved corpus, one table + one HTML gallery."""
    frames = _full_frames(args.scan)
    if not frames:
        print(f"[eye] no full-size frames in {DEBUG_DIR}/{args.scan}")
        return 2
    eye = Eye()
    out_dir = DEBUG_DIR / "eye_scan"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    t0 = time.time()
    for i, path in enumerate(frames, 1):
        frame = Image.open(path).convert("RGB")
        scene = eye.observe(frame=frame, temporal=False)
        png = out_dir / f"{path.stem}.png"
        img = eye.annotate(scene, frame=frame)
        if img is not None:
            img.save(png)
        hud = scene.get("hud", {})
        rows.append({
            "frame": path.name,
            "image": f"eye_scan/{png.name}",
            "game": scene.get("quality", {}).get("game_visible"),
            "strength": hud.get("strength", {}).get("text"),
            "gems": hud.get("gems", {}).get("text"),
            "strength_conf": hud.get("strength", {}).get("confidence", 0),
            "gems_conf": hud.get("gems", {}).get("confidence", 0),
            "readable": scene.get("quality", {}).get("readable"),
            "warnings": "; ".join(scene.get("quality", {}).get("warnings") or []),
        })
        if args.verbose:
            print(f"  [{i:2}/{len(frames)}] {path.name:34} "
                  f"STR={rows[-1]['strength'] or '?':>9} GEM={rows[-1]['gems'] or '?':>9} "
                  f"{'OK' if rows[-1]['readable'] else ('NO-GAME' if not rows[-1]['game'] else 'partial')}")

    total = len(rows)
    both = sum(1 for r in rows if r["readable"])
    nogame = sum(1 for r in rows if not r["game"])
    one = sum(1 for r in rows
              if r["game"] and (r["strength"] or r["gems"]) and not r["readable"])
    none = total - both - one - nogame
    report = {"scanned": total, "both_readable": both, "partial": one,
              "unreadable": none, "no_game_hud": nogame,
              "elapsed_sec": round(time.time() - t0, 1),
              "rows": rows}
    report_path = DEBUG_DIR / "eye_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    _write_gallery(report, out_dir)
    print(f"\n[eye] frames={total} both={both} partial={one} "
          f"unreadable={none} no_game_hud={nogame} in {report['elapsed_sec']}s")
    print(f"[eye] annotated -> {out_dir}")
    print(f"[eye] report    -> {report_path}")
    print(f"[eye] gallery   -> {DEBUG_DIR / 'eye_gallery.html'}")
    return 0


def _write_gallery(report: dict, out_dir: Path):
    rows = report
    html_path = DEBUG_DIR / "eye_gallery.html"
    cards = []
    for r in rows["rows"]:
        warn = (f'<div class="warn">{html.escape(r["warnings"])}</div>'
                if r["warnings"] else "")
        if not r["game"]:
            status, cls = "no game HUD", "bad"
        elif r["readable"]:
            status, cls = "readable", "ok"
        else:
            status, cls = "partial", "warnc"
        cards.append(f"""
    <div class="card">
      <a href="{r['image']}" target="_blank"><img src="{r['image']}" loading="lazy"></a>
      <div class="meta"><b>{html.escape(r['frame'])}</b>
        <span>STR {html.escape(str(r['strength']))} ({r['strength_conf']:.2f})</span>
        <span>GEM {html.escape(str(r['gems']))} ({r['gems_conf']:.2f})</span>
        <span class="{cls}">{status}</span></div>
      {warn}
    </div>""")
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Eye - offline scan</title>
<style>
body{{background:#101216;color:#e6e6e6;font:14px/1.4 Segoe UI,system-ui,sans-serif;margin:24px}}
h1{{font-size:20px;margin:0 0 6px}} .sum{{color:#9aa4b2;margin-bottom:18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px}}
.card{{background:#171b21;border:1px solid #242a33;border-radius:8px;overflow:hidden}}
.card img{{width:100%;display:block}} .meta{{display:flex;gap:12px;flex-wrap:wrap;padding:8px 10px;font-size:12px}}
.meta span{{color:#9aa4b2}} .ok{{color:#31d07a}} .bad{{color:#ff5d5d}} .warnc{{color:#ffb300}}
.warn{{padding:0 10px 8px;color:#ffb300;font-size:12px}}
</style></head><body>
<h1>Eye - offline scan</h1>
<div class="sum">{rows['scanned']} frames &middot; both readable <b>{rows['both_readable']}</b>
 &middot; partial <b>{rows['partial']}</b> &middot; unreadable <b>{rows['unreadable']}</b>
 &middot; no game HUD <b>{rows.get('no_game_hud', 0)}</b>
 &middot; {rows['elapsed_sec']}s</div>
<div class="grid">{''.join(cards)}</div></body></html>"""
    html_path.write_text(doc, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Structured eye for the Roblox agent")
    ap.add_argument("--deep", action="store_true", help="add OCR fields (modal/event/CLAIM)")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    ap.add_argument("--out", default=None, help="annotated PNG filename")
    ap.add_argument("--frame", default=None, help="offline: one saved frame")
    ap.add_argument("--scan", nargs="?", const="*.png", default=None,
                    help="offline: scan saved frames (default *.png)")
    ap.add_argument("--watch", type=float, default=None, metavar="SEC",
                    help="live loop every SEC seconds")
    ap.add_argument("--annotate", action="store_true", help="save PNG each watch tick")
    ap.add_argument("--health", action="store_true", help="self-check report")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.health:
        return run_health(args)
    if args.scan is not None:
        return run_scan(args)
    if args.frame:
        return run_frame(args)
    if args.watch is not None:
        return run_watch(args)
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
