"""Tests for optional preference learning.

The key invariant: learning is OFF by default, and even when on it only records
answers -- it never changes an action's tier or skips an approval.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class PreferenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"DATA_DIR": self.tmp.name})
        self.env.start()
        # Import after DATA_DIR is set so db paths point at the temp dir.
        import importlib
        import app.db as db
        import app.preference as pref
        importlib.reload(db)
        importlib.reload(pref)
        self.db = db
        self.pref = pref
        pref.init()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_disabled_by_default(self):
        self.assertFalse(self.pref.learning_enabled({}))
        self.assertFalse(self.pref.learning_enabled({"preference": {}}))
        self.assertTrue(self.pref.learning_enabled(
            {"preference": {"learn_enabled": True}}))

    def test_streak_becomes_trusted_after_threshold(self):
        self.assertFalse(self.pref.is_trusted("thermal", "stop_dryer"))
        self.assertFalse(self.pref.record_decision("thermal", "stop_dryer", True, threshold=3))
        self.assertFalse(self.pref.record_decision("thermal", "stop_dryer", True, threshold=3))
        self.assertTrue(self.pref.record_decision("thermal", "stop_dryer", True, threshold=3))
        self.assertTrue(self.pref.is_trusted("thermal", "stop_dryer"))

    def test_single_decline_revokes(self):
        for _ in range(3):
            self.pref.record_decision("thermal", "stop_dryer", True, threshold=3)
        self.assertTrue(self.pref.is_trusted("thermal", "stop_dryer"))
        self.pref.record_decision("thermal", "stop_dryer", False, threshold=3)
        self.assertFalse(self.pref.is_trusted("thermal", "stop_dryer"))
        self.assertEqual(self.pref.all_preferences()[0]["streak"], 0)

    def test_pairs_are_independent(self):
        self.pref.record_decision("thermal", "stop_dryer", True, threshold=1)
        self.assertFalse(self.pref.is_trusted("mold", "ventilate_washer"))
        self.assertTrue(self.pref.is_trusted("thermal", "stop_dryer"))

    def test_revoke(self):
        self.pref.record_decision("thermal", "preheat_cooktop", True, threshold=1)
        self.pref.revoke("thermal", "preheat_cooktop")
        self.assertFalse(self.pref.is_trusted("thermal", "preheat_cooktop"))

    def test_tier_is_unchanged(self):
        """The safety contract: learning never rewrites the action whitelist."""
        from app import policy
        before = dict(policy.ACTION_TIERS)
        self.pref.record_decision("thermal", "stop_dryer", True, threshold=1)
        self.assertEqual(policy.ACTION_TIERS, before)
        self.assertEqual(policy.tier_of("stop_dryer"), policy.TIER2)


if __name__ == "__main__":
    unittest.main()
