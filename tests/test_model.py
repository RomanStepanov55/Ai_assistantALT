"""Model gateway (alontito/model.py): own brain only, teacher never in runtime.

The decision of 2026-11-27: no third-party model answers the user. The
downloaded teacher exists only to generate training examples. These tests pin
that separation and the local-endpoint-only rule.
"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from alontito.model import (BRAIN_CONFIG, BrainConfig, ModelGateway,
                            ModelUnavailable, Teacher, TeacherNotForRuntime,
                            offline_reply)


def cfg(tmp: Path, data: dict) -> BrainConfig:
    p = tmp / "brain.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return BrainConfig.load(p)


class TestBrainConfig(unittest.TestCase):
    def test_missing_file_means_everything_off(self):
        c = BrainConfig.load(Path(tempfile.mkdtemp()) / "absent.json")
        self.assertFalse(c.own_enabled)
        self.assertFalse(c.teacher_enabled)
        c.validated()  # loopback defaults pass

    def test_cloud_endpoint_is_refused_before_any_request(self):
        c = cfg(Path(tempfile.mkdtemp()), {
            "own": {"enabled": True, "endpoint": "https://api.example.com"}})
        with self.assertRaises(ModelUnavailable):
            c.validated()

    def test_lan_endpoint_is_allowed(self):
        c = cfg(Path(tempfile.mkdtemp()), {
            "own": {"enabled": True, "endpoint": "http://192.168.1.10:11434"}})
        c.validated()  # private LAN address: allowed

    def test_teacher_cloud_endpoint_is_refused(self):
        c = cfg(Path(tempfile.mkdtemp()), {
            "teacher": {"enabled": True,
                        "endpoint": "http://8.8.8.8:11434",
                        "model": "x"}})
        with self.assertRaises(ModelUnavailable):
            c.validated()


class FakeReply:
    """Context-manager bytes stream standing in for urllib response."""

    def __init__(self, lines):
        self.buf = io.BytesIO(b"".join(
            (l + "\n").encode("utf-8") for l in lines))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.buf.getvalue().splitlines())

    def read(self):
        return self.buf.getvalue()


class TestChatOwnOnly(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.c = cfg(self.tmp, {"own": {"enabled": True,
                                        "endpoint": "http://127.0.0.1:11434",
                                        "model": "my-brain"}})
        self.gw = ModelGateway(self.c)

    def test_disabled_brain_raises(self):
        off = ModelGateway(BrainConfig({}))
        with self.assertRaises(ModelUnavailable):
            off.chat([{"role": "user", "content": "привет"}])

    def test_teacher_is_not_reachable_from_chat(self):
        with self.assertRaises(TeacherNotForRuntime):
            self.gw.chat([{"role": "user", "content": "привет"}], role="teacher")
        with self.assertRaises(ValueError):
            self.gw.chat([{"role": "user", "content": "привет"}], role="mystery")

    def test_chat_streams_and_assembles_text(self):
        seen = {}

        def transport(url, body, headers):
            seen["url"] = url
            seen["payload"] = json.loads(body.decode("utf-8"))
            self.assertTrue(headers["Content-Type"].startswith("application/json"))
            return FakeReply([
                json.dumps({"message": {"content": "Привет!"}}),
                json.dumps({"message": {"content": " Я Алонтито."}}),
                json.dumps({"done": True}),
            ])

        deltas = []
        r = self.gw.chat([{"role": "user", "content": "привет"}],
                         on_delta=deltas.append, transport=transport)
        self.assertEqual(r["text"], "Привет! Я Алонтито.")
        self.assertEqual(deltas, ["Привет!", " Я Алонтито."])
        self.assertEqual(r["provider"], "own")
        self.assertEqual(r["model"], "my-brain")
        self.assertTrue(seen["url"].endswith("/api/chat"))
        self.assertTrue(seen["payload"]["stream"])

    def test_openai_compatible_stream(self):
        c = cfg(self.tmp, {"own": {"enabled": True,
                                   "endpoint": "http://127.0.0.1:1234",
                                   "api": "openai",
                                   "model": "my-brain",
                                   "api_key": "local-token"}})
        gw = ModelGateway(c)

        def transport(url, body, headers):
            self.assertTrue(url.endswith("/v1/chat/completions"))
            self.assertEqual(headers.get("Authorization"), "Bearer local-token")
            return FakeReply([
                'data: ' + json.dumps({"choices": [{"delta": {"content": "Ок"}}]}),
                "data: [DONE]",
            ])

        r = gw.chat([{"role": "user", "content": "x"}], transport=transport)
        self.assertEqual(r["text"], "Ок")


class TestTeacher(unittest.TestCase):
    def test_unconfigured_teacher_raises(self):
        with self.assertRaises(ModelUnavailable):
            Teacher(BrainConfig({}))

    def test_teacher_completes_blocking(self):
        c = cfg(Path(tempfile.mkdtemp()), {
            "teacher": {"enabled": True, "endpoint": "http://127.0.0.1:11434",
                        "model": "teacher-x"}})
        t = Teacher(c)

        def transport(url, body, headers):
            self.assertIn("/api/chat", url)
            payload = json.loads(body.decode("utf-8"))
            self.assertEqual(payload["model"], "teacher-x")
            self.assertFalse(payload["stream"])
            return FakeReply([json.dumps({"message": {"content": "пример"}})])

        self.assertEqual(t.complete("сделай пример", transport=transport), "пример")


class TestOffline(unittest.TestCase):
    def test_offline_never_invents_numbers(self):
        import re
        for phrase in ("расскажи про квантовую физику", "кто победит завтра?",
                       "напиши стихи"):
            reply = offline_reply(phrase)
            self.assertIn("мозг", reply.lower())
            # No made-up numbers: digits only allowed in years/phone-like
            # boilerplate; these replies contain none at all.
            self.assertIsNone(re.search(r"\d", reply), reply)

    def test_offline_reports_real_status(self):
        reply = offline_reply("статус", {"facts": 12, "sessions": 7,
                                         "own": {"enabled": False}})
        self.assertIn("12", reply)
        self.assertIn("7", reply)
        self.assertIn("не подключён", reply)

    def test_offline_greetings_name_skills(self):
        reply = offline_reply("привет")
        self.assertIn("запомни", reply)
        self.assertIn("напомни", reply)


class TestConfigNotCommitted(unittest.TestCase):
    def test_real_brain_config_is_gitignored(self):
        ignored = (Path(__file__).resolve().parent.parent / ".gitignore"
                   ).read_text(encoding="utf-8")
        self.assertIn("config/alontito_brain.json", ignored)


if __name__ == "__main__":
    unittest.main()
