"""FR-7: savings estimated from the audit trail.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, savings  # noqa: E402
from app.proactive import ProactiveEngine  # noqa: E402
from tests.test_phase2 import FakeAdapter, _cfg  # noqa: E402

T0 = datetime(2026, 11, 19, 18, 0, 0)


def row(event, kind=None, device=None, minutes=0):
    return {"event_type": event, "action_kind": kind, "device_id": device,
            "ts": (T0 + timedelta(minutes=minutes)).isoformat()}


class SummarizeTest(unittest.TestCase):
    def test_nothing_done_saves_nothing(self):
        s = savings.summarize([], T0)
        self.assertEqual((s["kwh_saved"], s["kwh_shifted"], s["krw_saved"], s["co2_kg"]), (0, 0, 0, 0))
        self.assertTrue(s["estimate"])

    def test_only_executed_actions_count(self):
        rows = [row("approval.proposed", "pause_washer"), row("approval.declined", "pause_washer"),
                row("action.failed", "eco_ac", "ac_living"), row("action.blocked", "pause_washer")]
        self.assertEqual(savings.summarize(rows, T0)["actions"], [])

    def test_eco_ac_counts_until_the_same_ac_changes(self):
        rows = [row("action.executed", "eco_ac", "ac_living", 0),
                row("action.executed", None, "ac_living", 60)]            # changed by hand after 1 h
        s = savings.summarize(rows, T0 + timedelta(hours=5))
        self.assertAlmostEqual(s["kwh_saved"], savings.ECO_SAVED_W / 1000 * 1.0, places=2)

    def test_eco_ac_is_capped(self):
        s = savings.summarize([row("action.executed", "eco_ac", "ac_living", 0)], T0 + timedelta(hours=24))
        self.assertAlmostEqual(s["kwh_saved"], savings.ECO_SAVED_W / 1000 * savings.ECO_CAP_H, places=2)

    def test_shifted_load_saves_money_but_no_co2(self):
        s = savings.summarize([row("action.executed", "pause_washer", "washer", 0)], T0)
        self.assertEqual(s["kwh_saved"], 0)
        self.assertEqual(s["co2_kg"], 0)
        self.assertGreater(s["kwh_shifted"], 0)
        self.assertEqual(s["krw_saved"], round(savings.WASH_SHIFT_KWH * (savings.PRICE_PEAK_KRW - savings.PRICE_OFFPEAK_KRW)))

    def test_mold_prevention_and_co2(self):
        s = savings.summarize([row("action.executed", "ventilate_washer", "washer", 0)], T0)
        self.assertEqual(s["mold_prevented"], 1)
        self.assertAlmostEqual(s["co2_kg"], round(savings.TUB_CLEAN_KWH * savings.CO2_KG_PER_KWH, 2))

    def test_week_starts_on_monday(self):
        thu = datetime(2026, 11, 19, 15, 0)
        self.assertEqual(savings.period_start("week", thu), datetime(2026, 11, 16))
        self.assertEqual(savings.period_start("today", thu), datetime(2026, 11, 19))


class ReportFromEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (db.DATA_DIR, db.DB_PATH)
        db.DATA_DIR = Path(self._tmp.name)
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.init()

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH = self._orig
        self._tmp.cleanup()

    def test_peak_scenario_with_yes_shows_up_in_the_report(self):
        e = ProactiveEngine(FakeAdapter(), _cfg())
        e.sensors.write_files = False
        e.sensors.apply_scenario("peak")
        e.tick()
        e.confirm(e.approvals.pending()[0].id)
        r = savings.report("today")
        kinds = {a["kind"] for a in r["actions"]}
        self.assertIn("eco_ac", kinds)
        self.assertIn("pause_washer", kinds)
        self.assertGreater(r["krw_saved"], 0)


if __name__ == "__main__":
    unittest.main()
