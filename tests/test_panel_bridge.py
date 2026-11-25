import tempfile
import unittest
from pathlib import Path
from brain.panel_bridge import PanelBridge


class TestBridge(unittest.TestCase):
    def test_command_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            b = PanelBridge(Path(td))
            b.send_command("stop")
            b.send_command("experiment", "rep_delay 0.55")
            cmds = b.poll_commands()
            self.assertEqual(cmds, [("stop", ""), ("experiment", "rep_delay 0.55")])
            self.assertEqual(b.poll_commands(), [])      # очередь очищена

    def test_status_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            b = PanelBridge(Path(td))
            b.push_status({"mode": "train", "strength": 12345})
            st = b.read_status()
            self.assertEqual(st["mode"], "train")
            self.assertEqual(st["strength"], 12345)
            self.assertIn("ts", st)

    def test_empty_status(self):
        with tempfile.TemporaryDirectory() as td:
            b = PanelBridge(Path(td))
            self.assertEqual(b.read_status(), {})

    def test_queue_integrity_after_many_sends(self):
        with tempfile.TemporaryDirectory() as td:
            b = PanelBridge(Path(td))
            for i in range(7):
                b.send_command("cmd%d" % i, str(i))
            cmds = b.poll_commands()
            self.assertEqual([c for c, _ in cmds], ["cmd%d" % i for i in range(7)])
            self.assertEqual(b.poll_commands(), [])

    def test_new_bridge_instance_sees_queued_commands(self):
        # пульт и агент - разные процессы и разные экземпляры класса
        with tempfile.TemporaryDirectory() as td:
            sender = PanelBridge(Path(td))
            agent = PanelBridge(Path(td))
            sender.send_command("watch")
            self.assertEqual(agent.poll_commands(), [("watch", "")])

    def test_broken_command_file_recovers(self):
        with tempfile.TemporaryDirectory() as td:
            b = PanelBridge(Path(td))
            Path(b.cmd_file).write_text("{not json", encoding="utf-8")
            b.send_command("stop")
            self.assertEqual(b.poll_commands(), [("stop", "")])


if __name__ == "__main__":
    unittest.main()
