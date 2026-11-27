"""PC actions: whitelist + device confirmation, OFF by default.

Even a "harmless" folder listing refuses to run while the switch is off.
"""
import json
import tempfile
import unittest
from pathlib import Path

from alontito.pc_actions import (ActionDenied, PcActions, PcActionsDisabled)

T = 1_000_000.0


def make(tmp: Path, **cfg) -> PcActions:
    p = PcActions(tmp / "pc_actions.json", clock=lambda: T)
    if cfg:
        if "enabled" in cfg:
            p.set_enabled(cfg["enabled"])
        if "roots" in cfg:
            p.set_roots(cfg["roots"])
    return p


class TestDisabledByDefault(unittest.TestCase):
    def test_fresh_config_is_off(self):
        pc = make(Path(tempfile.mkdtemp()))
        self.assertFalse(pc.enabled)
        with self.assertRaises(PcActionsDisabled):
            pc.confirm(pc.propose("list_dir", path=".")["request_id"])

    def test_enable_persists(self):
        tmp = Path(tempfile.mkdtemp())
        pc = make(tmp, enabled=True)
        reopened = PcActions(tmp / "pc_actions.json", clock=lambda: T)
        self.assertTrue(reopened.enabled)


class TestWhitelist(unittest.TestCase):
    def setUp(self):
        self.pc = make(Path(tempfile.mkdtemp()), enabled=True)

    def test_unknown_action_is_denied_even_before_confirm(self):
        with self.assertRaises(ActionDenied):
            self.pc.propose("run_command", cmd="rm -rf /")

    def test_open_url_https_only(self):
        rid = self.pc.propose("open_url", url="https://example.org")["request_id"]
        r = self.pc.confirm(rid)
        self.assertTrue(r["ok"])
        with self.assertRaises(ActionDenied):
            self.pc.confirm(  # new request: the first one is consumed
                self.pc.propose("open_url", url="file:///etc/passwd")["request_id"])

    def test_paths_outside_roots_are_denied(self):
        # propose is only a request; the gate fires at confirm time
        pc = make(Path(tempfile.mkdtemp()), enabled=True,
                  roots=[Path(tempfile.mkdtemp())])
        rid = pc.propose("read_text", path="C:/Windows/win.ini")["request_id"]
        with self.assertRaises(ActionDenied):
            pc.confirm(rid)


class TestConfirmFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pc = make(self.tmp, enabled=True,
                       roots=[self.tmp])
        self.file = self.tmp / "note.txt"
        self.file.write_text("привет из файла", encoding="utf-8")

    def test_list_and_read_inside_roots(self):
        r = self.pc.confirm(self.pc.propose("list_dir", path=str(self.tmp))["request_id"])
        self.assertTrue(r["ok"])
        self.assertIn("note.txt", r["detail"])
        r = self.pc.confirm(self.pc.propose("read_text", path=str(self.file))["request_id"])
        self.assertIn("привет из файла", r["detail"])

    def test_request_is_single_use_and_expires(self):
        rid = self.pc.propose("read_text", path=str(self.file))["request_id"]
        self.pc.confirm(rid)
        with self.assertRaises(ActionDenied):
            self.pc.confirm(rid)
        rid = self.pc.propose("read_text", path=str(self.file))["request_id"]
        late = PcActions(self.tmp / "pc_actions.json",
                         clock=lambda: T + 10_000, )
        late.allowed_roots = self.pc.allowed_roots
        late.enabled = True
        with self.assertRaises(ActionDenied):
            late.confirm(rid)

    def test_pending_lists_open_requests(self):
        self.pc.propose("read_text", path=str(self.file))
        pending = self.pc.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["action"], "read_text")

    def test_disabled_after_enable_blocks_execution(self):
        self.pc.set_enabled(False)
        rid = self.pc.propose("read_text", path=str(self.file))["request_id"]
        with self.assertRaises(PcActionsDisabled):
            self.pc.confirm(rid)


if __name__ == "__main__":
    unittest.main()
