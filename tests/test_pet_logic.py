# -*- coding: utf-8 -*-
"""Тесты логики 3D Desktop Pet без Qt и без GPU:  python -m unittest tests.test_pet_logic -v  (из корня проекта)."""
import io
import json
import os
import random
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from alontito import desktop_pet as dp                      # noqa: E402
from alontito import pet_terminal as pt                     # noqa: E402


def types_of(cmds):
    return [c["type"] for c in cmds]


def plays(cmds):
    return [c["clip"] for c in cmds if c["type"] == "play"]


class TestClassify(unittest.TestCase):
    cfg = dp.DEFAULT_CONFIG

    def test_ide(self):
        for exe in ("Code.exe", "pycharm64.exe", "devenv.exe", "Cursor.exe", "RobloxStudioBeta.exe"):
            self.assertEqual(dp.classify(exe, "x", self.cfg), "coding", exe)

    def test_roblox(self):
        self.assertEqual(dp.classify("RobloxPlayerBeta.exe", "Roblox", self.cfg), "roblox")
        self.assertEqual(dp.classify("Windows10Universal.exe", "Roblox", self.cfg), "roblox")

    def test_browser_tab_is_not_game(self):
        self.assertEqual(dp.classify("chrome.exe", "Roblox - Google Chrome", self.cfg), "other")
        self.assertEqual(dp.classify("chrome.exe", "Roblox", self.cfg), "other")

    def test_ide_title_hint_but_not_browser(self):
        self.assertEqual(dp.classify("electron-app.exe", "main.py - Visual Studio Code", self.cfg), "coding")
        self.assertEqual(dp.classify("chrome.exe", "How to use Visual Studio Code", self.cfg), "other")

    def test_other(self):
        self.assertEqual(dp.classify("explorer.exe", "Проводник", self.cfg), "other")
        self.assertEqual(dp.classify("", "", self.cfg), "other")


class TestBrain(unittest.TestCase):
    def setUp(self):
        self.cfg = json.loads(json.dumps(dp.DEFAULT_CONFIG))
        self.b = dp.PetBrain(self.cfg, rng=random.Random(7), now=0.0)
        self.ctx = {"activity": "other", "user_idle": 0, "x": 500, "xmin": 100, "xmax": 1500, "park_x": 1400, "portal": True}

    def boot(self):
        out = self.b.show(0.0)
        self.assertEqual(types_of(out), ["show_window", "dissolve"])
        self.assertEqual(self.b.state, "materializing")
        out = self.b.on_event({"ev": "dissolve_done", "dir": "in"}, 1.0)
        self.assertEqual(self.b.state, "idle")
        self.assertIn("idle", plays(out))

    def test_boot_and_hidden_ignores_ticks(self):
        self.assertEqual(self.b.tick(1.0, self.ctx), [])           # hidden
        self.boot()

    def test_coding_walks_to_park_then_codes_then_leaves(self):
        self.boot()
        self.ctx["activity"] = "coding"
        self.assertEqual([c for c in self.b.tick(2.0, self.ctx) if c["type"] == "walk_to"], [])   # дебаунс
        out = self.b.tick(5.0, self.ctx)
        walk = [c for c in out if c["type"] == "walk_to"]
        self.assertEqual(len(walk), 1)
        self.assertEqual(walk[0]["x"], 1400)
        self.assertIn(90, [c["deg"] for c in out if c["type"] == "face"])         # идёт вправо
        self.assertEqual(self.b.state, "walk")
        out = self.b.on_event({"ev": "walk_done"}, 12.0)
        self.assertEqual(self.b.state, "coding")
        self.assertIn("activity_coding", plays(out))
        self.ctx.update(activity="other", x=1400)
        self.b.tick(13.0, self.ctx)
        self.assertEqual(self.b.state, "coding")                                   # ещё не ушёл: <5 c
        self.b.tick(19.0, self.ctx)
        self.assertEqual(self.b.state, "idle")

    def test_coding_when_already_parked_does_not_walk(self):
        self.boot()
        self.ctx.update(activity="coding", x=1390)
        self.b.tick(2.0, self.ctx)
        out = self.b.tick(5.0, self.ctx)
        self.assertEqual(self.b.state, "coding")
        self.assertNotIn("walk_to", types_of(out))

    def test_roblox_turns_back_and_uses_portal(self):
        self.boot()
        self.ctx.update(activity="roblox", x=1400)
        self.b.tick(2.0, self.ctx)
        out = self.b.tick(5.0, self.ctx)
        self.assertEqual(self.b.state, "roblox")
        self.assertIn("roblox_watch_back", plays(out))
        self.assertIn(180, [c["deg"] for c in out if c["type"] == "face"])         # спиной к пользователю
        portal_cmds = []
        t = 5.0
        while t < 60 and not portal_cmds:                                          # первый заход ≤ 30 c
            t += 1.0
            portal_cmds = [c for c in self.b.tick(t, self.ctx) if c["type"] == "portal"]
        self.assertTrue(portal_cmds, "портал не запустился за 55 c")
        self.assertEqual(self.b.state, "portal")
        self.assertEqual(portal_cmds[0]["action"], "run")
        out = self.b.on_event({"ev": "portal_done", "ok": True}, t + 8)
        self.assertEqual(self.b.state, "roblox")                                   # вернулся смотреть игру
        self.assertIn("roblox_watch_back", plays(out))
        self.assertIn(180, [c["deg"] for c in out if c["type"] == "face"])

    def test_drag_fall_land_cycle_and_portal_cancel(self):
        self.boot()
        self.b._start_portal(2.0, [])
        out = self.b.on_event({"ev": "grab"}, 3.0)
        self.assertEqual(self.b.state, "drag")
        self.assertIn({"type": "portal", "action": "cancel"}, out)
        self.assertIn("drag_panic", plays(out))
        self.assertEqual(self.b.tick(3.5, self.ctx), [])                           # во время drag мозг молчит
        self.b.on_event({"ev": "release"}, 4.0)
        self.assertEqual(self.b.state, "fall")
        out = self.b.on_event({"ev": "landed"}, 4.6)
        self.assertEqual(self.b.state, "land")
        landed = [c for c in out if c["type"] == "play"]
        self.assertEqual(landed[0]["clip"], "drag_drop")
        self.assertFalse(landed[0]["loop"])
        self.b.on_event({"ev": "clip_end", "name": "drag_drop"}, 5.6)
        self.assertEqual(self.b.state, "idle")

    def test_land_safety_timeout(self):
        self.boot()
        self.b.on_event({"ev": "grab"}, 2.0)
        self.b.on_event({"ev": "release"}, 2.5)
        self.b.on_event({"ev": "landed"}, 3.0)
        self.b.tick(5.5, self.ctx)
        self.assertEqual(self.b.state, "idle")

    def test_hide_and_show(self):
        self.boot()
        out = self.b.hide(2.0)
        self.assertEqual(self.b.state, "dissolving")
        self.assertIn({"type": "dissolve", "dir": "out"}, out)
        self.assertEqual(self.b.hide(2.1), [])                                     # повторно — ничего
        out = self.b.on_event({"ev": "dissolve_done", "dir": "out"}, 4.0)
        self.assertEqual(self.b.state, "hidden")
        self.assertIn("hide_window", types_of(out))
        self.assertEqual(self.b.on_event({"ev": "grab"}, 5.0), [])                 # скрытого не схватить
        self.b.show(6.0)
        self.assertEqual(self.b.state, "materializing")

    def test_still_mode_never_walks_or_uses_portal(self):
        self.boot()
        self.b.set_mode("still", 1.0)
        self.ctx.update(user_idle=300)
        seen = set()
        t = 1.0
        for _ in range(4000):
            t += 0.5
            for c in self.b.tick(t, self.ctx):
                seen.add(c["type"])
        self.assertNotIn("walk_to", seen)
        self.assertNotIn("portal", seen)

    def test_free_mode_ignores_windows_and_roams(self):
        self.boot()
        self.b.set_mode("free", 1.0)
        self.ctx.update(activity="coding")
        seen_states, seen_cmds = set(), set()
        t = 1.0
        for _ in range(6000):
            t += 0.5
            for c in self.b.tick(t, self.ctx):
                seen_cmds.add(c["type"])
            seen_states.add(self.b.state)
            if self.b.state == "walk":
                self.b.on_event({"ev": "walk_done"}, t)
            if self.b.state == "portal":
                self.b.on_event({"ev": "portal_done"}, t)
        self.assertNotIn("coding", seen_states)
        self.assertTrue({"walk", "sit", "rubik"} <= seen_states, seen_states)
        self.assertIn("portal", seen_cmds)

    def test_mode_switch_leaves_activity_state(self):
        self.boot()
        self.ctx.update(activity="roblox", x=1400)
        self.b.tick(2.0, self.ctx)
        self.b.tick(5.0, self.ctx)
        self.assertEqual(self.b.state, "roblox")
        self.b.set_mode("free", 6.0)
        self.assertEqual(self.b.state, "idle")

    def test_manual_play_and_back(self):
        self.boot()
        out = self.b.play_clip("activity_rubik", 2.0, dur=3)
        self.assertEqual(self.b.state, "manual")
        self.assertIn("activity_rubik", plays(out))
        self.b.tick(6.0, self.ctx)
        self.assertEqual(self.b.state, "idle")
        out = self.b.play_clip("portal_reach_in", 7.0)
        self.assertEqual(self.b.state, "portal")
        self.assertIn("portal", types_of(out))
        out = self.b.play_clip("disintegrate", 30.0)                               # из portal нельзя «мимо» — но hide допустим
        self.assertEqual(self.b.state, "dissolving")

    def test_portal_timeout_recovers(self):
        self.boot()
        self.b._start_portal(2.0, [])
        out = self.b.tick(17.0, self.ctx)
        self.assertIn({"type": "portal", "action": "cancel"}, out)
        self.assertEqual(self.b.state, "idle")

    def test_fps_hint(self):
        self.boot()
        out = self.b.tick(1.1, self.ctx)
        self.assertTrue(any(c["type"] == "fps" and c["value"] == 30 for c in out) or self.b._fps == 30)
        out = self.b.on_event({"ev": "grab"}, 2.0)
        self.assertTrue(any(c["type"] == "fps" and c["value"] == 60 for c in out))


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.glb = Path(cls.tmp.name) / "model.glb"
        cls.glb.write_bytes(b"glTF" + b"\0" * 20)
        cls.srv = dp.PetServer(dp.WEB_DIR, {"character": cls.glb, "portal": None}, use_vendor=True)
        cls.port = cls.srv.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()
        cls.tmp.cleanup()

    def get(self, path, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_index_has_importmap_and_no_placeholder(self):
        st, h, body = self.get("/")
        self.assertEqual(st, 200)
        t = body.decode()
        self.assertNotIn("{{IMPORTMAP}}", t)
        self.assertIn("/vendor/three/three.module.js", t)

    def test_js_mime_is_javascript(self):
        st, h, _ = self.get("/pet.js")
        self.assertEqual(st, 200)
        self.assertTrue(h["Content-Type"].startswith("text/javascript"))

    def test_assets(self):
        self.assertEqual(self.get("/asset/character.glb")[0], 200)
        self.assertEqual(self.get("/asset/portal.glb")[0], 404)
        self.assertEqual(self.get("/asset/other.glb")[0], 404)

    def test_traversal_and_host_check(self):
        self.assertEqual(self.get("/..%2f..%2fdesktop_pet.py")[0], 403)
        self.assertEqual(self.get("/%2e%2e/desktop_pet.py")[0] in (403, 404), True)
        self.assertEqual(self.get("/", host="evil.example.com")[0], 403)

    def test_animations_json_valid(self):
        st, _, body = self.get("/animations.json")
        data = json.loads(body)
        self.assertEqual(set(data["clips"]), set(dp.ALL_CLIPS))


class TestAnimationsData(unittest.TestCase):
    def test_all_clips_well_formed(self):
        data = json.loads((dp.WEB_DIR / "animations.json").read_text(encoding="utf-8"))
        driven = set(data["driven"])
        for name, c in data["clips"].items():
            ts = [k["t"] for k in c["keys"]]
            self.assertEqual(ts, sorted(ts), name)
            self.assertAlmostEqual(ts[-1], c["duration"], places=3, msg=name)
            for k in c["keys"]:
                self.assertTrue(set(k["b"]) <= driven, (name, set(k["b"]) - driven))
            if c["loop"]:                                   # петля бесшовна: первый и последний ключ совпадают
                self.assertEqual(c["keys"][0]["b"], c["keys"][-1]["b"], name)


class TestConfig(unittest.TestCase):
    def test_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            cfg = dp.load_config(p)
            self.assertFalse(cfg["enabled"])
            cfg["enabled"], cfg["mode"] = True, "free"
            dp.save_config(cfg, p)
            again = dp.load_config(p)
            self.assertTrue(again["enabled"])
            self.assertEqual(again["mode"], "free")
            self.assertIn("ide_exes", again)

    def test_bad_mode_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text('{"mode": "nope"}', encoding="utf-8")
            self.assertEqual(dp.load_config(p)["mode"], "auto")
            p.write_text("{broken", encoding="utf-8")
            self.assertEqual(dp.load_config(p)["mode"], "auto")

    def test_find_asset_by_hashed_name(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "3ed061d0-hooded_neon_character_rigged.glb").write_bytes(b"x")
            (Path(d) / "b3ee-mini_portal_slim.glb").write_bytes(b"x")
            old = dp.ASSETS_DIR
            dp.ASSETS_DIR = Path(d)
            try:
                self.assertIn("rigged", dp.find_asset("character", {}).name)
                self.assertIn("portal", dp.find_asset("portal", {}).name)
            finally:
                dp.ASSETS_DIR = old


FAKE_CHILD = r'''
import sys, json
P = "@@PETEV@@ "
def ev(**k): sys.stdout.write(P + json.dumps(k) + "\n"); sys.stdout.flush()
ev(ev="info", msg="hello")
ev(ev="ready", clips={}, portal=True)
ev(ev="status", state="idle", activity="other", mode="auto")
for line in sys.stdin:
    m = json.loads(line)
    if m["cmd"] == "mode": ev(ev="status", state="idle", activity="other", mode=m["value"])
    if m["cmd"] == "quit": break
'''


class TestControllerAndTerminal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.child = Path(self.tmp.name) / "fake_child.py"
        self.child.write_text(FAKE_CHILD, encoding="utf-8")
        self.saved = []
        self._orig_save = pt.save_config
        pt.save_config = lambda cfg, path=None: self.saved.append(dict(cfg)) or Path("x")
        self._orig_q = pt.qt_available
        pt.qt_available = lambda: True
        self._orig_find = pt.find_asset
        pt.find_asset = lambda kind, cfg: Path("fake.glb")
        self.said = []
        self.t = pt.PetTerminal(say=self.said.append, ask=lambda q: "")
        # The real config on disk already has asked=True; the test must not
        # depend on the user's machine state.
        self.t.cfg.update(asked=False, enabled=False)
        self.t.ctl = dp.PetController(self.t.cfg, child_argv=[sys.executable, str(self.child)])

    def tearDown(self):
        self.t.ctl.stop(timeout=3)
        pt.save_config, pt.qt_available, pt.find_asset = self._orig_save, self._orig_q, self._orig_find
        self.tmp.cleanup()

    def test_badge_states(self):
        self.assertIn("выкл", self.t.badge())
        self.assertTrue(self.t.badge("ansi").startswith("\033["))
        self.assertTrue(self.t.badge("rich").startswith("[") and self.t.badge("rich").endswith("]"))

    def test_on_mode_off_flow(self):
        self.assertTrue(self.t.handle_command("/3d on"))
        self.assertEqual(self.t.ctl.status, "on")
        self.assertIn("вкл", self.t.badge())
        self.assertTrue(self.saved[-1]["enabled"])
        self.t.handle_command("/3d mode free")
        deadline = time.time() + 5
        while time.time() < deadline and self.t.ctl.mode != "free":
            time.sleep(0.05)
        self.assertEqual(self.t.ctl.mode, "free")
        self.assertIn("free", self.t.badge())
        self.t.handle_command("/3d off")
        self.assertEqual(self.t.ctl.status, "off")
        self.assertFalse(self.saved[-1]["enabled"])
        self.assertIn("выкл", self.t.badge())

    def test_not_a_3d_command(self):
        self.assertFalse(self.t.handle_command("/help"))
        self.assertFalse(self.t.handle_command("привет"))
        self.assertTrue(self.t.handle_command("/3d bogus"))

    def test_commands_without_running_pet(self):
        self.t.handle_command("/3d portal")
        self.assertTrue(any("/3d on" in s for s in self.said))
        self.t.handle_command("/3d scale 9")
        self.assertTrue(any("0.4" in s for s in self.said))

    def test_help_is_framed_like_the_rest_of_the_app(self):
        self.assertTrue(self.t.handle_command("/3d"))
        block = "\n".join(self.said)
        self.assertIn("3D-АССИСТЕНТ", block)
        self.assertIn("╭╌", block)
        for line in block.split("\n"):
            self.assertEqual(line.count("┆"), 0 if "╭" in line or "╰" in line else 2)

    def test_first_run_yes_default_and_no(self):
        t = self.t
        asked = []
        t.ask = lambda q: asked.append(q) or ""
        t.first_run_prompt()
        self.assertIn("Хочешь включить живого 3D-ассистента на рабочем столе? [Y/n]", asked[0])
        self.assertTrue(t.cfg["asked"] and t.cfg["enabled"])
        self.assertEqual(t.ctl.status, "on")
        t.first_run_prompt()                                   # второй раз не спрашивает
        self.assertEqual(len(asked), 1)
        t.ctl.stop()
        t2 = pt.PetTerminal(say=self.said.append, ask=lambda q: "n")
        t2.cfg.update(asked=False, enabled=True)
        t2.first_run_prompt()
        self.assertTrue(t2.cfg["asked"])
        self.assertFalse(t2.cfg["enabled"])

    def test_missing_qt_gives_hint_and_error_badge(self):
        pt.qt_available = lambda: False
        self.assertFalse(self.t.start())
        self.assertTrue(any("pip install PyQt6" in s for s in self.said))
        self.assertIn("ошибка", self.t.badge())

    def test_child_crash_reports_error(self):
        bad = Path(self.tmp.name) / "bad.py"
        bad.write_text("import sys; sys.stdout.write('@@PETEV@@ {\"ev\":\"error\",\"msg\":\"boom\"}\\n'); sys.stdout.flush(); sys.exit(2)", encoding="utf-8")
        self.t.ctl = dp.PetController(self.t.cfg, child_argv=[sys.executable, str(bad)])
        self.assertFalse(self.t.start(timeout=5))
        self.assertEqual(self.t.ctl.status, "error")
        self.assertIn("boom", self.t.ctl.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
