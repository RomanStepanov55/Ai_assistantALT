"""Web Pult (pult/server.py): read-only surface, auth and limits.

No Roblox, no input, no network beyond loopback: the server is started on an
ephemeral 127.0.0.1 port and the agent is never bound (skills are faked).
These tests pin HARD RULE #2 for the pult: no endpoint can play, train or click.
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_TMP = tempfile.mkdtemp(prefix="pult_test_")
os.environ["ALONTITO_PULT_CONFIG"] = os.path.join(_TMP, "pult_config.json")

from pult import server as srv  # noqa: E402


class TestPultSurface(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # агент не привязывается: ни стека зрения, ни памяти пользователя
        cls._bind = mock.patch.object(srv.AGENT, "bind", lambda: srv.AGENT)
        cls._bind.start()
        cls._db = mock.patch.object(srv, "CHAT_DB", Path(_TMP) / "chat.sqlite3")
        cls._db.start()
        cls._frame = mock.patch.object(srv, "FRAME_FILE", Path(_TMP) / "no_frame.png")
        cls._frame.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.PultHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for patcher in (cls._bind, cls._db, cls._frame):
            patcher.stop()

    # ---- helpers -------------------------------------------------------
    def get(self, path, token=True):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url)
        if token:
            req.add_header("X-Pult-Token", srv.TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def post(self, path, body=None, token=True, raw=None):
        data = raw if raw is not None else json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-Pult-Token", srv.TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    # ---- auth ----------------------------------------------------------
    def test_default_host_is_loopback(self):
        cfg = srv.load_config()
        self.assertEqual(cfg["host"], "127.0.0.1")

    def test_static_shell_opens_without_token(self):
        status, body, _ = self.get("/", token=False)
        self.assertEqual(status, 200)
        self.assertIn("Алонтито не играет", body.decode("utf-8"))

    def test_data_requires_token(self):
        status, _, _ = self.get("/api/status", token=False)
        self.assertEqual(status, 401)

    def test_wrong_token_is_rejected(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/status")
        req.add_header("X-Pult-Token", "not-the-token")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

    def test_non_ascii_token_is_rejected_not_crash(self):
        # заголовок клиент не примет (latin-1), поэтому не-ASCII токен идёт в query:
        # на сервере это та же ветка compare_digest, что раньше бросала TypeError
        status, _, _ = self.get("/api/status?token=%D1%82%D0%BE%D0%BA%D0%B5%D0%BD",
                                token=False)
        self.assertEqual(status, 401)

    def test_security_headers_are_present(self):
        _, _, headers = self.get("/api/status")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_config_file_is_outside_the_repo_tree_in_tests(self):
        self.assertFalse(Path(srv.CONFIG_FILE).resolve().is_relative_to(
            Path(srv.ROOT).resolve()))

    # ---- read-only surface ------------------------------------------------
    def test_status_has_no_agent_controls(self):
        status, body, _ = self.get("/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertNotIn("paused", data)
        self.assertIn("frame_age", data)

    def test_only_read_skills_are_allowed(self):
        self.assertEqual(srv.Agent.READ_ONLY_SKILLS, ("health", "observe"))
        agent = srv.Agent()
        agent.bind = lambda: agent
        for name in ("train", "close_dialog", "rebirth", "spin_wheel", "play"):
            self.assertFalse(agent.run_skill(name)["ok"], name)

    def test_play_actions_are_refused_without_touching_the_agent(self):
        with mock.patch.object(srv.AGENT, "run_skill", return_value={"ok": True}) as skill:
            for name in ("train", "close_dialog", "resume", "anything"):
                status, body = self.post("/api/action", {"name": name})
                self.assertEqual(status, 403, name)
                self.assertIn("not allowed", body.decode("utf-8"))
            skill.assert_not_called()

    def test_agent_command_endpoint_is_gone(self):
        for cmd in ("pause", "resume", "train_once", "observe"):
            status, _ = self.post("/api/agent", {"cmd": cmd})
            self.assertEqual(status, 404, cmd)

    def test_static_has_no_automation_buttons(self):
        _, html, _ = self.get("/")
        text = html.decode("utf-8")
        for needle in ('data-action="train"', 'data-action="close_dialog"',
                       "Пауза агента", "Подход", "Потренируйся"):
            self.assertNotIn(needle, text)
        _, js, _ = self.get("/app.js")
        self.assertNotIn("/api/agent", js.decode("utf-8"))

    # ---- limits / robustness ------------------------------------------------
    def test_oversized_body_is_rejected(self):
        status, _ = self.post("/api/chat", raw=b"x" * (srv.MAX_BODY + 1))
        self.assertEqual(status, 413)

    def test_empty_chat_message_is_rejected(self):
        status, _ = self.post("/api/chat", {"message": "   "})
        self.assertEqual(status, 400)

    def test_bad_journal_count_does_not_crash(self):
        status, body, _ = self.get("/api/journal?n=abc")
        self.assertEqual(status, 200)
        self.assertIn("lines", json.loads(body.decode("utf-8")))

    def test_files_outside_static_are_not_served(self):
        for path in ("/pult_config.json", "/../pult/pult_config.json",
                     "/static/../server.py", "/server.py", "/.git/config"):
            status, _, _ = self.get(path)
            self.assertEqual(status, 404, path)

    def test_frame_without_fresh_capture_is_404(self):
        status, _, _ = self.get("/api/frame")
        self.assertEqual(status, 404)

    # ---- chat ------------------------------------------------------------------
    def test_chat_asks_to_train_but_never_trains(self):
        with mock.patch.object(srv, "ollama_chat", side_effect=OSError("offline")), \
             mock.patch.object(srv.AGENT, "run_skill", return_value={"ok": True}) as skill:
            status, body = self.post("/api/chat", {"message": "потренируйся, сделай подход"})
        self.assertEqual(status, 200)
        answer = json.loads(body.decode("utf-8"))["answer"]
        self.assertIn("не играю", answer)
        skill.assert_not_called()

    def test_chat_close_dialog_request_never_clicks(self):
        self.assertIsNone(srv.detect_intent("закрой диалог"))
        with mock.patch.object(srv.AGENT, "run_skill", return_value={"ok": True}) as skill:
            srv.offline_reply("закрой диалог")
        skill.assert_not_called()

    def test_system_prompt_forbids_playing(self):
        self.assertIn("НЕ играешь", srv.SYSTEM_PROMPT)
        for needle in ("потренировать подход", "закрыть диалог", "клавиатурой и мышью"):
            self.assertNotIn(needle, srv.SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
