"""Tests for the live notification event bus (SSE source).

No network, no browser, no third-party package.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import events  # noqa: E402


class EventBusTest(unittest.TestCase):
    def setUp(self):
        self.bus = events.EventBus(max_queue=4)

    def test_subscribe_receives_publish(self):
        q = self.bus.subscribe()
        self.bus.publish({"kind": "approval", "title": "Needs an answer"})
        self.assertEqual(q.get(timeout=1)["kind"], "approval")

    def test_multiple_subscribers(self):
        q1 = self.bus.subscribe()
        q2 = self.bus.subscribe()
        self.bus.publish({"kind": "action", "title": "Done"})
        self.assertEqual(q1.get(timeout=1)["kind"], "action")
        self.assertEqual(q2.get(timeout=1)["kind"], "action")

    def test_unsubscribe_stops_delivery(self):
        q = self.bus.subscribe()
        self.bus.unsubscribe(q)
        self.bus.publish({"kind": "reminder", "title": "Dryer"})
        self.assertTrue(q.empty())

    def test_full_queue_does_not_block(self):
        q = self.bus.subscribe()
        for i in range(20):
            self.bus.publish({"kind": "x", "n": i})
        self.assertLessEqual(q.qsize(), 4)

    def test_late_subscriber_gets_recent(self):
        self.bus.publish({"kind": "approval", "title": "Earlier"})
        q = self.bus.subscribe()
        self.assertEqual(q.get(timeout=1)["title"], "Earlier")

    def test_non_dict_ignored(self):
        q = self.bus.subscribe()
        self.bus.publish("not a dict")  # type: ignore[arg-type]
        self.assertTrue(q.empty())

    def test_sse_format(self):
        text = events.sse({"kind": "a", "title": "Hi"})
        self.assertTrue(text.startswith("data: "))
        self.assertTrue(text.endswith("\n\n"))
        self.assertIn('"title": "Hi"', text)

    def test_module_emit_never_raises(self):
        events.emit("approval", "Title", "Body", approval_ids=["x"])


if __name__ == "__main__":
    unittest.main()
