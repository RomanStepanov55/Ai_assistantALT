import unittest
from unittest import mock

from brain.voice_chat import VoiceInterface, SttEngine


class TestVoiceTextRouting(unittest.TestCase):
    def setUp(self):
        self.vi = VoiceInterface(speak=False, stt_enable=False)

    def test_command_status(self):
        self.assertIn("Статус", self.vi.handle_text("статус"))

    def test_report_needs_a_handler(self):
        self.assertIn("недоступен", self.vi.handle_text("отчёт"))

    def test_custom_handler_wins(self):
        vi = VoiceInterface(on_command=lambda c, a: f"GOT:{c}:{a}",
                            speak=False, stt_enable=False)
        self.assertEqual(vi.handle_text("статус сила"), "GOT:статус:сила")

    def test_free_chat_fallback_no_ollama(self):
        r = self.vi.handle_text("расскажи анекдот про нейросети")
        self.assertTrue(r)                      # не пусто, без исключений

    def test_heuristic_greeting(self):
        self.assertIn("Alontito", self.vi.handle_text("привет"))


class TestNoGameCommands(unittest.TestCase):
    """HARD RULE #2: голос не может запустить тренировку, паузу или клик."""

    def setUp(self):
        self.vi = VoiceInterface(speak=False, stt_enable=False)

    def test_command_list_has_only_read_only_phrases(self):
        self.assertEqual(VoiceInterface.COMMANDS, ("статус", "отчёт"))

    def test_game_phrases_do_not_match_any_command(self):
        for phrase in ("стоп", "старт", "наблюдатель", "тренировка",
                       "эксперимент быстрее повторения", "помоги с задачей"):
            with mock.patch.object(self.vi, "on_command",
                                   lambda c, a: f"EXECUTED:{c}"):
                reply = self.vi.handle_text(phrase)
            self.assertNotIn("EXECUTED", reply, phrase)

    def test_handler_error_is_reported_not_raised(self):
        def bad(cmd, arg):
            raise RuntimeError("boom")
        vi = VoiceInterface(on_command=bad, speak=False, stt_enable=False)
        r = vi.handle_text("статус")
        self.assertIn("Ошибка", r)


class TestOfflineOnly(unittest.TestCase):
    """Правило проекта: только локальные сервисы. Аудио не уходит в облако."""

    def test_no_subprocess_in_module(self):
        import brain.voice_chat as vc
        self.assertFalse(hasattr(vc, "subprocess"),
                         "subprocess в voice_chat не используется и не нужен")

    def test_stt_has_no_cloud_recognizer(self):
        self.assertFalse(hasattr(SttEngine, "recognize_google"))
        self.assertFalse(hasattr(SttEngine, "_listen_sr"))

    def test_ollama_host_is_local_by_default(self):
        from brain.voice_chat import OllamaClient
        self.assertEqual(OllamaClient().host, "http://127.0.0.1:11434")

    def test_stt_without_vosk_honestly_refuses(self):
        stt = SttEngine()                       # vosk на машине отсутствует
        if not stt.ok:                          # если когда-то поставят vosk с моделью - тест безвреден
            self.assertIsNone(stt.listen())


if __name__ == "__main__":
    unittest.main()
