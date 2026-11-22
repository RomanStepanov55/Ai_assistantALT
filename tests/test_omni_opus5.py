import ast
import unittest
import tempfile
from pathlib import Path
from brain.opus_local_core import SafeToolValidator, ToolSecurityViolation, OpusLocalCore, OpusConfig
from vision.omni_eye import parse_hud_number, OmniEye, ScreenElement


class TestOmniOpus5(unittest.TestCase):
    def test_ast_validator_blocks_exploits(self):
        bad_code = "import ctypes\ndef run(p): return {}"
        tree = ast.parse(bad_code)
        with self.assertRaises(ToolSecurityViolation):
            SafeToolValidator().visit(tree)

    def test_ast_validator_allows_safe_math(self):
        good_code = "import math\ndef run(p): return {'val': math.sqrt(16)}"
        tree = ast.parse(good_code)
        SafeToolValidator().visit(tree)

    def test_split_thinking(self):
        raw = "<thinking>Проверяю метрики</thinking>Готово!"
        th, ans = OpusLocalCore.split_thinking(raw)
        self.assertEqual(th, "Проверяю метрики")
        self.assertEqual(ans, "Готово!")

    def test_parse_hud_number_suffixes(self):
        self.assertAlmostEqual(parse_hud_number("142.5K"), 142500.0)
        self.assertAlmostEqual(parse_hud_number("3.5B"), 3.5e9)
        self.assertAlmostEqual(parse_hud_number("12Qa"), 1.2e16)


    def test_high_effort_runs_review_and_revision_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = OpusConfig(tools_dir=str(Path(tmp) / "tools"),
                             dataset_path=str(Path(tmp) / "dataset.jsonl"),
                             knowledge_path=str(Path(tmp) / "KNOWLEDGE.md"),
                             memory_db_path=str(Path(tmp) / "memory.sqlite3"),
                             learning_db_path=str(Path(tmp) / "learning.sqlite3"),
                             max_review_rounds=2)
            brain = OpusLocalCore(config=cfg)
            outputs = iter([
                "<thinking>План и черновик</thinking>Первый ответ",
                "REVIEW: FIXED\nЗамечание исправлено.\nFINAL: Исправленный ответ",
                "REVIEW: PASS\nПроверка пройдена."
            ])
            brain._resolve_deep_model = lambda: "mock-model"
            brain._call_ollama = lambda *args, **kwargs: next(outputs)
            result = brain.think_and_act("Сложная задача", force_effort="high")
            self.assertEqual(result["answer"], "Исправленный ответ")
            self.assertTrue(result["verified"])
            self.assertEqual([x["status"] for x in result["reviews"]], ["fixed", "passed"])

    def test_trade_reflex_auto_decline(self):
        eye = OmniEye()
        elements = [
            ScreenElement(1, "Incoming Trade Request", (100, 100, 300, 140), (200, 120), 0.95, "ocr"),
            ScreenElement(2, "Decline", (220, 200, 290, 230), (255, 215), 0.98, "ocr"),
        ]
        reflex = eye.check_safety_reflexes(elements)
        self.assertIsNotNone(reflex)
        self.assertEqual(reflex["reflex"], "DECLINE_TRADE")
        self.assertEqual(reflex["click_xy"], (255, 215))


if __name__ == "__main__":
    unittest.main()
