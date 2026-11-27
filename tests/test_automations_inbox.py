"""Reminders (automations) and the inbox, on a frozen clock.

All tests pass `now` explicitly; nothing relies on the real wall time.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from alontito.automations import Automation, Automations, parse
from alontito.inbox import Inbox, InboxError


def mk_auto(tmp: Path) -> Automations:
    return Automations(tmp / "automations.json")


# 2026-11-27 is a Friday (weekday 4). 12:00 local.
NOW = time.mktime((2026, 11, 27, 12, 0, 0, 0, 0, -1))


class TestParse(unittest.TestCase):
    def test_in_minutes(self):
        a = parse("напомни через 20 минут проверить почту", NOW)
        self.assertEqual(a.kind, "once")
        self.assertAlmostEqual(a.due_ts, NOW + 1200, delta=1)
        self.assertEqual(a.text, "проверить почту")

    def test_in_hours_and_seconds(self):
        self.assertAlmostEqual(parse("напомни через 2 часа спать", NOW).due_ts,
                               NOW + 7200, delta=1)
        self.assertAlmostEqual(parse("напомни через 90 секунд чай", NOW).due_ts,
                               NOW + 90, delta=1)

    def test_tomorrow_and_today(self):
        a = parse("напомни завтра в 9:00 позвонить", NOW)
        self.assertEqual(a.kind, "once")
        want = time.mktime((2026, 11, 28, 9, 0, 0, 0, 0, -1))
        self.assertAlmostEqual(a.due_ts, want, delta=1)
        b = parse("напомни сегодня в 15:00 чай", NOW)
        self.assertAlmostEqual(b.due_ts,
                               time.mktime((2026, 11, 27, 15, 0, 0, 0, 0, -1)),
                               delta=1)

    def test_recurring_kinds(self):
        self.assertEqual(parse("напомни каждый день в 9:00 зарядка", NOW).kind, "daily")
        self.assertEqual(parse("напомни по будням в 8:30 планёрка", NOW).kind, "weekdays")
        a = parse("напомни каждый понедельник в 18:00 отчёт", NOW)
        self.assertEqual(a.kind, "weekly")
        self.assertEqual(a.weekday, 0)
        self.assertEqual(a.hour, 18)

    def test_not_a_reminder_is_none(self):
        for phrase in ("запомни: я люблю чай", "напомни", "напомни купить хлеб",
                       "напомни через 0 минут ничего", "как дела",
                       "напомни каждый вторник в 25:00 невозможное"):
            self.assertIsNone(parse(phrase, NOW), phrase)


class TestAutomationsStore(unittest.TestCase):
    def test_add_due_and_fire_once(self):
        store = mk_auto(Path(tempfile.mkdtemp()))
        store.add(parse("напомни через 20 минут чай", NOW))
        self.assertEqual(store.due(NOW + 100), [])            # not yet
        fired = store.due(NOW + 1201)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].text, "чай")
        store.mark_fired(fired[0].id, NOW + 1201)
        self.assertEqual(store.due(NOW + 1300), [])           # once fires exactly once

    def test_daily_fires_once_per_day(self):
        store = mk_auto(Path(tempfile.mkdtemp()))
        store.add(parse("напомни каждый день в 9:00 зарядка", NOW))
        # 2026-11-27 09:00 already passed (now is 12:00) → due today
        fired = store.due(NOW)
        self.assertEqual(len(fired), 1)
        store.mark_fired(fired[0].id, NOW)
        # same day later: nothing
        self.assertEqual(store.due(NOW + 3600), [])
        # next day 08:59: nothing; 09:01: due again
        d8_59 = time.mktime((2026, 11, 28, 8, 59, 0, 0, 0, -1))
        d9_01 = time.mktime((2026, 11, 28, 9, 1, 0, 0, 0, -1))
        self.assertEqual(store.due(d8_59), [])
        self.assertEqual(len(store.due(d9_01)), 1)

    def test_weekday_and_weekly_gating(self):
        store = mk_auto(Path(tempfile.mkdtemp()))
        store.add(parse("напомни по будням в 9:00 планёрка", NOW))
        saturday = time.mktime((2026, 11, 28, 9, 30, 0, 0, 0, -1))  # 28.11 is Saturday
        self.assertEqual(store.due(saturday), [])
        monday = time.mktime((2026, 11, 30, 9, 30, 0, 0, 0, -1))
        self.assertEqual(len(store.due(monday)), 1)

        weekly = mk_auto(Path(tempfile.mkdtemp()))
        weekly.add(parse("напомни каждый понедельник в 18:00 отчёт", NOW))
        self.assertEqual(weekly.due(NOW), [])                 # Friday
        monday_evening = time.mktime((2026, 11, 30, 18, 30, 0, 0, 0, -1))
        self.assertEqual(len(weekly.due(monday_evening)), 1)  # Monday 18:00 passed
        self.assertEqual(weekly.due(monday), [])              # Monday morning: not yet

    def test_disabled_and_removal(self):
        store = mk_auto(Path(tempfile.mkdtemp()))
        a = store.add(parse("напомни через 5 минут тишина", NOW))
        store.set_enabled(a.id, False)
        self.assertEqual(store.due(NOW + 301), [])
        self.assertTrue(store.remove(a.id))
        self.assertFalse(store.remove(a.id))
        self.assertEqual(store.list(include_done=False), [])


class TestInbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.inbox = Inbox(self.tmp / "inbox")

    def test_text_item_lifecycle(self):
        item = self.inbox.add("text", text="запомни: кофе по вторникам")
        self.assertEqual(item["state"], "new")
        self.assertEqual(self.inbox.list()[0]["text"], "запомни: кофе по вторникам")
        self.assertTrue(self.inbox.set_state(item["id"], "processed"))
        self.assertEqual(self.inbox.list(state="processed")[0]["id"], item["id"])

    def test_voice_item_with_transcript(self):
        item = self.inbox.add("voice", b"RIFFfake-wav", filename="note.wav")
        self.assertTrue(item["filename"].endswith(".wav"))
        self.assertEqual(self.inbox.read_file(item["id"]), b"RIFFfake-wav")
        self.inbox.set_state(item["id"], "processed", transcript="сходи в магазин")
        self.assertEqual(self.inbox.get(item["id"])["transcript"], "сходи в магазин")

    def test_image_extension_gate_and_size_cap(self):
        img = self.inbox.add("image", b"\x89PNG", filename="shot.png")
        self.assertTrue(img["filename"].endswith(".png"))
        with self.assertRaises(InboxError):
            self.inbox.add("image", b"x", filename="malware.exe")
        with self.assertRaises(InboxError):
            self.inbox.add("file", b"x" * (21 * 1024 * 1024), filename="big.zip")
        with self.assertRaises(InboxError):
            self.inbox.add("text", text="   ")
        with self.assertRaises(InboxError):
            self.inbox.add("movie", text="нет такого типа")

    def test_index_survives_and_get_latest(self):
        first = self.inbox.add("text", text="первое")
        second = self.inbox.add("text", text="второе")
        reopened = Inbox(self.tmp / "inbox")
        self.assertEqual(len(reopened.list()), 2)
        self.assertEqual(reopened.get(first["id"])["text"], "первое")
        self.assertEqual(reopened.get(second["id"])["text"], "второе")

    def test_state_whitelist(self):
        item = self.inbox.add("text", text="x")
        with self.assertRaises(InboxError):
            self.inbox.set_state(item["id"], "deleted-forever")


if __name__ == "__main__":
    unittest.main()
