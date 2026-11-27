"""Skill synthesis: the agent's own tools pass three gates or do not exist.

Pins:
  - the AST gate is a WHITELIST: only pure computation modules import
  - forbidden calls (open, eval, system, ...) are refused
  - run(params) entry point is mandatory
  - a synthesised tool survives a smoke test before registration
  - a hanging or crashing tool is rejected and its file removed
  - the registry carries provenance; remove() is the rollback
"""
import tempfile
import unittest
from pathlib import Path

from brain.skill_synthesis import (SafeToolValidator, SkillSynthesizer,
                                   ToolSecurityViolation, extract_code,
                                   validate_code)

GOOD = ("import math\n"
        "def run(params):\n"
        "    x = params.get('x', 16)\n"
        "    return {'sqrt': math.sqrt(x)}")

EVIL_IMPORT = "import subprocess\ndef run(p):\n    return {}"
EVIL_CALL = "def run(p):\n    return {'e': eval('1+1')}"
NO_ENTRY = "x = 1\n"


def code_block(code):
    return "```python\n" + code + "\n```"


def make_synth(tmp):
    return SkillSynthesizer(tools_dir=str(tmp / "tools"), test_timeout=2.0)


class TestValidator(unittest.TestCase):
    def test_safe_math_passes(self):
        validate_code(GOOD)

    def test_whitelist_blocks_unknown_imports(self):
        for bad in ("import subprocess", "import socket", "import os",
                    "import ctypes", "from urllib import request"):
            with self.assertRaises(ToolSecurityViolation, msg=bad):
                validate_code(bad + "\ndef run(p):\n    return {}")

    def test_forbidden_calls_are_refused(self):
        with self.assertRaises(ToolSecurityViolation):
            validate_code(EVIL_CALL)
        with self.assertRaises(ToolSecurityViolation):
            validate_code("def run(p):\n    return {'x': open('f').read()}")

    def test_entry_point_is_mandatory(self):
        with self.assertRaises(ToolSecurityViolation):
            validate_code(NO_ENTRY)


class TestExtract(unittest.TestCase):
    def test_pulls_python_block(self):
        self.assertEqual(extract_code(code_block(GOOD)), GOOD)

    def test_no_block_means_raw_text(self):
        self.assertEqual(extract_code(GOOD), GOOD)


class TestSynthesis(unittest.TestCase):
    def test_good_tool_is_registered_with_provenance(self):
        tmp = Path(tempfile.mkdtemp(prefix="synth_"))
        synth = make_synth(tmp)

        def chat(messages, on_delta=None):
            return code_block(GOOD)

        ok, msg = synth.synthesize("sqrt_helper", "квадратный корень числа",
                                   chat_fn=chat)
        self.assertTrue(ok, msg)
        self.assertIn("sqrt_helper", synth.registered)
        self.assertEqual(synth.registered["sqrt_helper"]({"x": 25}), {"sqrt": 5.0})
        listed = synth.list()
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["tested"])
        self.assertEqual(listed[0]["task"], "квадратный корень числа")
        self.assertTrue((tmp / "tools" / "manifest.json").exists())

    def test_malicious_code_is_rejected_and_removed(self):
        tmp = Path(tempfile.mkdtemp(prefix="synth_"))
        synth = make_synth(tmp)

        def chat(messages, on_delta=None):
            return code_block(EVIL_IMPORT)

        ok, msg = synth.synthesize("bad_tool", "плохой инструмент", chat_fn=chat)
        self.assertFalse(ok)
        self.assertIn("стражем AST", msg)
        self.assertEqual(synth.registered, {})
        self.assertFalse((tmp / "tools" / "bad_tool.py").exists())

    def test_without_brain_is_an_honest_refusal(self):
        synth = make_synth(Path(tempfile.mkdtemp(prefix="synth_")))
        ok, msg = synth.synthesize("tool", "что-то", chat_fn=None)
        self.assertFalse(ok)
        self.assertIn("мозг не подключён", msg)

    def test_duplicate_name_is_refused(self):
        tmp = Path(tempfile.mkdtemp(prefix="synth_"))
        synth = make_synth(tmp)
        chat = lambda messages, on_delta=None: code_block(GOOD)
        self.assertTrue(synth.synthesize("dup", "задача", chat_fn=chat)[0])
        ok, msg = synth.synthesize("dup", "другая задача", chat_fn=chat)
        self.assertFalse(ok)
        self.assertIn("уже существует", msg)

    def test_hanging_tool_is_rejected(self):
        tmp = Path(tempfile.mkdtemp(prefix="synth_"))
        synth = SkillSynthesizer(tools_dir=str(tmp / "tools"), test_timeout=0.4)
        hang = "import time\ndef run(p):\n    time.sleep(30)\n    return {}"

        def chat(messages, on_delta=None):
            return code_block(hang)

        ok, msg = synth.synthesize("hang", "виснет", chat_fn=chat)
        self.assertFalse(ok)
        self.assertIn("таймаут", msg)
        self.assertFalse((tmp / "tools" / "hang.py").exists())

    def test_remove_is_the_rollback(self):
        tmp = Path(tempfile.mkdtemp(prefix="synth_"))
        synth = make_synth(tmp)
        chat = lambda messages, on_delta=None: code_block(GOOD)
        synth.synthesize("roll", "задача", chat_fn=chat)
        self.assertTrue(synth.remove("roll"))
        self.assertNotIn("roll", synth.registered)
        self.assertEqual(synth.list(), [])
        self.assertFalse((tmp / "tools" / "roll.py").exists())
        self.assertFalse(synth.remove("roll"))


if __name__ == "__main__":
    unittest.main()
