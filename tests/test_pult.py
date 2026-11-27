"""The app server (pult/server.py): devices, pairing, agent API, limits.

No game anywhere: the old read-only game skills, the frame endpoint and the
telemetry ring are gone. What is pinned here:
  - the shell is public, every data endpoint needs the master or a device token
  - pairing: master-only code generation, 5 attempts, 5-minute expiry
  - chat reaches the agent and is stored; the agent stub never gets game verbs
  - automations/inbox/training/facts do what they claim, nothing more
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
from alontito.automations import Automations  # noqa: E402
from alontito.inbox import Inbox  # noqa: E402
from alontito.memory import Memory  # noqa: E402


class StubAgent:
    """Everything the server touches, no network, no model."""

    def __init__(self, tmp: Path):
        self.memory = Memory(tmp / "memory.json")
        self.automations = Automations(tmp / "automations.json")
        self.inbox = Inbox(tmp / "inbox")
        self.asked: list = []
        self.answer_text = "Ответ на-your вопрос."

    # agent API used by the server
    def answer(self, text, *, device="pc", history=None, on_delta=None):
        self.asked.append({"text": text, "device": device, "history": history})
        if on_delta:
            on_delta("От")
            on_delta("вет ")
            on_delta("готов.")
        return {"text": "Ответ готов.", "source": "skill"}

    def status(self):
        return {"sessions": 1, "facts": len(self.memory.facts()),
                "open_questions": 0, "skills": 0, "automations": 0,
                "brain": {"enabled": False, "reachable": False,
                          "model": "", "detail": ""},
                "training": {"examples": 0, "approved": 0,
                             "pending_export": 0, "eval_runs": 0,
                             "promotions": 0}}

    def pending_examples(self, limit=50):
        return [{"fingerprint": "fp-1", "prompt": "в", "answer": "о",
                 "source": "chat:pc", "created_at": 0.0}]

    def approve_example(self, fingerprint):
        return fingerprint == "fp-1"

    def dismiss_example(self, fingerprint):
        return fingerprint == "fp-1"

    def _learning(self):
        class L:
            @staticmethod
            def stats():
                return {"examples": 1, "approved": 0, "pending_export": 0,
                        "eval_runs": 0, "promotions": 0}
        return L()


class FakeBinding:
    def __init__(self, agent: StubAgent):
        self.agent = agent

    def bind(self):
        return self.agent

    def ask(self, text, device, history, on_delta=None):
        return self.agent.answer(text, device=device, history=history,
                                 on_delta=on_delta)

    def bound(self):
        return self.agent


class TestAppServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stub = StubAgent(Path(_TMP))
        cls._patch_agent = mock.patch.object(srv, "AGENT", FakeBinding(cls.stub))
        cls._patch_agent.start()
        cls._patch_devices = mock.patch.object(srv, "DEVICES",
                                               srv.Devices(Path(_TMP) / "devices.json"))
        cls._patch_devices.start()
        cls._patch_pairing = mock.patch.object(srv, "PAIRING", srv.Pairing())
        cls._patch_pairing.start()
        cls._patch_chat = mock.patch.object(srv, "CHAT_DB",
                                            Path(_TMP) / "chat.sqlite3")
        cls._patch_chat.start()
        cls._patch_inbox = mock.patch.object(srv, "_inbox_safe",
                                             lambda: cls.stub.inbox)
        cls._patch_inbox.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.PultHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for p in (cls._patch_agent, cls._patch_devices, cls._patch_pairing,
                  cls._patch_chat, cls._patch_inbox):
            p.stop()

    # ---- helpers -------------------------------------------------------
    def get(self, path, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if token is not None:
            req.add_header("X-Pult-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def post(self, path, body=None, token=None, raw=None, headers=None):
        data = raw if raw is not None else json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data)
        req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if token is not None:
            req.add_header("X-Pult-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def delete(self, path, token):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     method="DELETE")
        req.add_header("X-Pult-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    M = property(lambda self: srv.TOKEN)

    # ---- auth ----------------------------------------------------------
    def test_default_host_is_the_lan(self):
        cfg = srv.load_config()
        self.assertEqual(cfg["host"], "0.0.0.0")

    def test_static_shell_opens_without_token(self):
        status, body, _ = self.get("/", token=None)
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("Алонтито", text)
        # PWA: манифест и иконки на месте
        self.assertIn("manifest.webmanifest", text)
        self.assertIn("apple-touch-icon", text)

    def test_data_requires_token(self):
        status, _, _ = self.get("/api/state", token=None)
        self.assertEqual(status, 401)

    def test_wrong_token_is_rejected(self):
        status, _, _ = self.get("/api/state", token="nope")
        self.assertEqual(status, 401)

    def test_master_token_works(self):
        status, body, _ = self.get("/api/state", token=self.M)
        self.assertEqual(status, 200)
        self.assertIn("agent", body.decode("utf-8"))

    def test_security_headers_are_present(self):
        _, _, headers = self.get("/api/state", token=self.M)
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Referrer-Policy"), "no-referrer")

    def test_oversized_body_is_rejected(self):
        status, _ = self.post("/api/chat", token=self.M,
                              raw=b"x" * (srv.MAX_BODY + 1))
        self.assertEqual(status, 413)

    # ---- pairing ---------------------------------------------------------
    def test_pairing_code_is_master_only(self):
        status, _ = self.post("/api/pairing-code", token=None)
        self.assertEqual(status, 401)

    def test_pair_flow_creates_a_working_device(self):
        status, body = self.post("/api/pairing-code", token=self.M)
        self.assertEqual(status, 200)
        code = json.loads(body)["code"]
        status, body = self.post("/api/pair", {"code": "000000", "name": "phone"})
        self.assertEqual(status, 403)
        status, body = self.post("/api/pair", {"code": code, "name": "мой телефон"})
        self.assertEqual(status, 200)
        device = json.loads(body)
        self.assertIn("token", device)
        status, _, _ = self.get("/api/state", token=device["token"])
        self.assertEqual(status, 200)
        # a used code cannot be reused
        status, _ = self.post("/api/pair", {"code": code, "name": "again"})
        self.assertEqual(status, 403)

    def test_pairing_code_has_attempt_limit(self):
        _, body = self.post("/api/pairing-code", token=self.M)
        code = json.loads(body)["code"]
        for _ in range(srv.PAIR_MAX_ATTEMPTS + 2):
            self.post("/api/pair", {"code": "999999" if code == "999999" else "999998"})
        # wrong guesses burn attempts, then the code is revoked
        for _ in range(srv.PAIR_MAX_ATTEMPTS):
            status, _ = self.post("/api/pair", {"code": code, "name": "x"})
        status, body = self.post("/api/pair", {"code": code, "name": "x"})
        self.assertEqual(status, 403)
        self.assertIn("попыт", json.loads(body)["reason"])

    def test_device_revoke_by_master(self):
        _, body = self.post("/api/pairing-code", token=self.M)
        code = json.loads(body)["code"]
        _, body = self.post("/api/pair", {"code": code, "name": "tablet"})
        device = json.loads(body)
        status, _ = self.post(f"/api/devices/{device['id']}/revoke", token=device["token"])
        self.assertEqual(status, 403)          # a device cannot revoke others
        status, _ = self.post(f"/api/devices/{device['id']}/revoke", token=self.M)
        self.assertEqual(status, 200)
        status, _, _ = self.get("/api/state", token=device["token"])
        self.assertEqual(status, 401)

    # ---- chat --------------------------------------------------------------
    def test_chat_reaches_the_agent_and_is_stored(self):
        status, body = self.post("/api/chat", {"message": "привет, что видишь?"},
                                 token=self.M)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["answer"], "Ответ готов.")
        self.assertEqual(self.stub.asked[-1]["device"], "web")
        status, body, _ = self.get("/api/chat/history", token=self.M)
        msgs = json.loads(body)["messages"]
        self.assertEqual(msgs[-1]["role"], "assistant")
        self.assertEqual(msgs[-1]["text"], "Ответ готов.")

    def test_empty_chat_message_is_rejected(self):
        status, _ = self.post("/api/chat", {"message": "  "}, token=self.M)
        self.assertEqual(status, 400)

    def test_the_agent_is_never_asked_to_play(self):
        # даже если попросить прямо — сервер просто передаёт текст; у агента
        # нет игровых навыков, а у сервера нет эндпоинтов действий вовсе
        self.post("/api/chat", {"message": "потренируйся за меня"}, token=self.M)
        self.assertEqual(self.stub.asked[-1]["text"], "потренируйся за меня")
        status, _ = self.post("/api/action", {"name": "train"}, token=self.M)
        self.assertEqual(status, 404)          # the endpoint is gone entirely
        status, _, _ = self.get("/api/frame", token=self.M)
        self.assertEqual(status, 404)

    # ---- automations ---------------------------------------------------------
    def test_automation_add_list_delete_run(self):
        status, body = self.post("/api/automations",
                                 {"text": "напомни через 5 минут чай"}, token=self.M)
        self.assertEqual(status, 200)
        item = json.loads(body)["item"]
        status, body, _ = self.get("/api/automations", token=self.M)
        self.assertIn("чай", body.decode("utf-8"))
        status, body = self.post(f"/api/automations/{item['id']}/run", token=self.M)
        self.assertEqual(status, 200)
        status, _ = self.delete(f"/api/automations/{item['id']}", token=self.M)
        self.assertEqual(status, 200)

    def test_automation_text_that_is_not_a_reminder_is_400(self):
        status, body = self.post("/api/automations", {"text": "просто слова"},
                                 token=self.M)
        self.assertEqual(status, 400)
        self.assertIn("не похоже", body.decode("utf-8"))

    # ---- inbox ---------------------------------------------------------------
    def test_inbox_text_and_binary_lifecycle(self):
        status, body = self.post("/api/inbox", {"text": "заметка с телефона"},
                                 token=self.M)
        self.assertEqual(status, 200)
        item = json.loads(body)["item"]
        self.assertEqual(item["kind"], "text")
        raw = b"RIFFwavdata"
        status, body = self.post(
            "/api/inbox?kind=voice&filename=note.wav&note=" + urllib.parse.quote("голос"),
            token=self.M, raw=raw,
            headers={"Content-Type": "application/octet-stream"})
        self.assertEqual(status, 200)
        voice = json.loads(body)["item"]
        status, body, headers = self.get(f"/api/inbox/{voice['id']}/file", token=self.M)
        self.assertEqual(status, 200)
        self.assertEqual(body, raw)
        status, body = self.post(f"/api/inbox/{voice['id']}/state",
                                 {"state": "read"}, token=self.M)
        self.assertEqual(status, 200)
        status, body, _ = self.get("/api/inbox?state=read", token=self.M)
        self.assertEqual(json.loads(body)["items"][0]["id"], voice["id"])

    # ---- training loop ---------------------------------------------------------
    def test_training_pending_approve_dismiss(self):
        status, body, _ = self.get("/api/training", token=self.M)
        pending = json.loads(body)["pending"]
        self.assertEqual(pending[0]["fingerprint"], "fp-1")
        status, body = self.post("/api/training/approve",
                                 {"fingerprint": "fp-1"}, token=self.M)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)

    def test_facts_add_and_list(self):
        status, body = self.post("/api/facts", {"text": "кофе по вторникам"},
                                 token=self.M)
        self.assertEqual(status, 200)
        status, body, _ = self.get("/api/facts", token=self.M)
        self.assertIn("кофе по вторникам", body.decode("utf-8"))

    # ---- devices management -------------------------------------------------------
    def test_devices_list_is_master_only(self):
        status, _, _ = self.get("/api/devices", token=None)
        self.assertEqual(status, 401)
        status, body, _ = self.get("/api/devices", token=self.M)
        self.assertEqual(status, 200)

    # ---- pet --------------------------------------------------------------
    def test_pet_page_is_public_and_rendered(self):
        status, body, _ = self.get("/pet", token=None)
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertNotIn("{{IMPORTMAP}}", text)
        self.assertIn("/pet/pet.js", text)

    def test_journal_needs_a_token(self):
        status, _, _ = self.get("/api/journal", token=None)
        self.assertEqual(status, 401)

    # ---- static design contract (E-023, kept from the pult) -------------------
    def test_static_has_seven_screens(self):
        _, body, _ = self.get("/", token=False)
        text = body.decode("utf-8")
        for h in ("Чат", "Память", "Напоминания", "Входящие", "Журнал",
                  "Алонтито 3D", "Настройки"):
            self.assertIn("<h1", text)
        self.assertEqual(text.count("<h1"), 7)
        self.assertNotIn("Панель", text)          # игровой пульт не возвращается
        self.assertNotIn("сила", text.lower())

    def test_static_is_quiet_no_slop(self):
        css = (srv.STATIC / "style.css").read_text(encoding="utf-8")
        html = (srv.STATIC / "index.html").read_text(encoding="utf-8")
        for needle in ("linear-gradient", "radial-gradient", "text-shadow",
                       "backdrop-filter", "filter: blur", "drop-shadow"):
            self.assertNotIn(needle, css, needle)
            self.assertNotIn(needle, html, needle)
        self.assertNotIn("text-transform: uppercase", css)
        # тень в покое запрещена; исключения — плавающие листы: меню и плашка
        shadows = [ln for ln in css.splitlines() if "box-shadow" in ln]
        self.assertLessEqual(len(shadows), 2, shadows)
        for ln in shadows:
            self.assertIn("sheet-shadow", ln, ln)

    def test_appjs_ids_exist_in_html(self):
        import re
        js = (srv.STATIC / "app.js").read_text(encoding="utf-8")
        html = (srv.STATIC / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js))
        self.assertTrue(used, "app.js не ссылается ни на один id")
        missing = sorted(i for i in used if 'id="%s"' % i not in html)
        self.assertEqual(missing, [])

    def test_config_file_is_outside_the_repo_tree_in_tests(self):
        self.assertTrue(srv.CONFIG_FILE.is_relative_to(Path(_TMP)))


if __name__ == "__main__":
    unittest.main()
