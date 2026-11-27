"""Max kW cap: the strict "not now, later" rule for instantaneous draw.

The monthly budget (kWh) and the cap (kW) are different quantities. These
tests pin the cap's boundaries: what it moves, what it refuses, what it never
touches, and that a booked job is checked again when it comes due.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import budget, db  # noqa: E402
from tests.test_phase2 import FakeAdapter  # noqa: E402

ON = [{"action": "power", "value": True}]


class MaxKwTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (db.DATA_DIR, db.DB_PATH, budget._last_load_w)
        db.DATA_DIR = Path(self._tmp.name)
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.init()
        self.adapter = FakeAdapter()
        self.adapter.devices.append(
            {"id": "ac", "name": "Bedroom AC", "type": "air_conditioner",
             "room": "Bedroom", "online": True, "capabilities": ["power"]})
        self.peak = datetime(2026, 11, 26, 17, 0)      # KR peak band 16-22
        self.offpeak = datetime(2026, 11, 26, 23, 10)
        budget.set_budget(1000, now=self.peak)
        budget.set_max_kw(3.0, now=self.peak)

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH, budget._last_load_w = self._orig
        self._tmp.cleanup()

    def check(self, device, load_w, now, channel="chat"):
        return budget.can_start(self.adapter, device, ON, now=now,
                                load_w=load_w, channel=channel)

    # ------------------------------------------------ the rule itself

    def test_off_by_default(self):
        budget.set_max_kw(None)
        self.assertIsNone(budget.max_kw())
        v = self.check("dryer", 9000, self.offpeak)
        self.assertEqual(v["decision"], budget.ALLOW)

    def test_under_the_cap_is_allowed(self):
        v = self.check("dryer", 1000, self.offpeak)      # 1.0 + 1.4 = 2.4 kW
        self.assertEqual(v["decision"], budget.ALLOW)

    def test_chore_over_the_cap_offpeak_is_booked_at_least_30_minutes_later(self):
        v = self.check("dryer", 2000, self.offpeak)      # 2.0 + 1.4 = 3.4 kW
        self.assertEqual(v["decision"], budget.DELAY)
        self.assertEqual(v["limit"], "max_kw")
        self.assertEqual(v["after_kw"], 3.4)
        self.assertEqual(v["scheduled_at"], "2026-11-27T00:00")
        self.assertIn("3.4 kW", v["reason"])
        self.assertIn("3 kW limit", v["reason"])

    def test_chore_over_the_cap_at_peak_waits_for_offpeak(self):
        # The professor's example: 17:00 → booked five hours later.
        v = self.check("washer", 2800, self.peak)
        self.assertEqual(v["decision"], budget.DELAY)
        self.assertEqual(v["scheduled_at"], "2026-11-26T22:00")

    def test_non_movable_load_over_the_cap_is_refused(self):
        v = self.check("dehumidifier", 2900, self.offpeak)
        self.assertEqual(v["decision"], budget.BLOCK)
        self.assertIn("switch something off", v["reason"])

    def test_cooling_is_never_limited(self):
        v = self.check("ac", 2900, self.offpeak)
        self.assertNotEqual(v["decision"], budget.BLOCK)
        self.assertNotEqual(v.get("limit"), "max_kw")

    def test_engine_risk_response_is_not_refused_by_the_cap(self):
        v = self.check("dehumidifier", 2900, self.offpeak, channel="engine")
        self.assertNotEqual(v["decision"], budget.BLOCK)

    def test_running_machine_is_not_moved(self):
        self.adapter.get_device("dryer")["state"] = {"power": True}
        v = self.check("dryer", 2900, self.offpeak)
        self.assertNotEqual(v.get("limit"), "max_kw")

    def test_cap_only_applies_in_strict_mode(self):
        budget.set_budget(1000, strict_mode=False, now=self.offpeak)
        v = self.check("dryer", 2900, self.offpeak)
        self.assertNotEqual(v.get("limit"), "max_kw")

    def test_load_comes_from_the_1hz_accrual_when_not_given(self):
        budget.accrue(2500, now=self.offpeak)
        v = budget.can_start(self.adapter, "dryer", ON, now=self.offpeak)
        self.assertEqual(v["decision"], budget.DELAY)
        self.assertEqual(v["load_kw"], 2.5)

    def test_out_of_range_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            budget.set_max_kw(0.1)
        with self.assertRaises(ValueError):
            budget.set_max_kw(500)

    def test_state_reports_cap_and_load_separately_from_kwh(self):
        budget.accrue(1200, now=self.offpeak)
        s = budget.state(self.offpeak)
        self.assertEqual(s["max_kw"], 3.0)
        self.assertEqual(s["load_kw"], 1.2)
        self.assertIn("budget_kwh", s)

    # ------------------------------------------------ the booked job

    def _due_job(self):
        from app.scheduler import Scheduler
        job_id = db.add_job("dryer", "Balcony Dryer", ON, {"type": "once"},
                            self.offpeak.isoformat(timespec="seconds"))
        return job_id, Scheduler(self.adapter)

    def test_due_job_still_over_the_cap_is_moved_again_not_run(self):
        job_id, sched = self._due_job()
        budget.accrue(2500, now=self.offpeak)
        sched._run_job(db.get_job(job_id), self.offpeak)
        self.assertEqual(self.adapter.applied, [])
        job = db.get_job(job_id)
        self.assertTrue(job["enabled"])
        self.assertEqual(job["next_run_at"], "2026-11-27T00:00")
        self.assertEqual(job["rule"]["cap_retries"], 1)
        self.assertTrue(job["last_result"].startswith("Waiting:"))

    def test_due_job_runs_once_the_house_has_room(self):
        job_id, sched = self._due_job()
        budget.accrue(800, now=self.offpeak)
        sched._run_job(db.get_job(job_id), self.offpeak)
        self.assertEqual(self.adapter.applied, [("dryer", ON)])
        self.assertFalse(db.get_job(job_id)["enabled"])

    def test_due_job_gives_up_after_six_hours(self):
        job_id, sched = self._due_job()
        budget.accrue(2500, now=self.offpeak)
        for _ in range(budget.CAP_MAX_RETRIES + 1):
            sched._run_job(db.get_job(job_id), self.offpeak)
        job = db.get_job(job_id)
        self.assertFalse(job["enabled"])
        self.assertTrue(job["last_result"].startswith("Not started:"))
        self.assertEqual(self.adapter.applied, [])


if __name__ == "__main__":
    unittest.main()


class DuplicateBookingTest(unittest.TestCase):
    """Asking twice for a start that the gate deferred books it once."""

    def setUp(self):
        from app import config
        from app.agent import Agent
        from app.devices import MockAdapter
        from app.devices import mock as mock_mod
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig = (db.DATA_DIR, db.DB_PATH, mock_mod.STATE_PATH, budget._last_load_w)
        self._mock = mock_mod
        db.DATA_DIR, db.DB_PATH, mock_mod.STATE_PATH = tmp, tmp / "t.db", tmp / "mock.json"
        db.init()
        self.adapter = MockAdapter()
        cfg = dict(config.DEFAULTS)
        cfg["agent"] = {"default_room": "Living Room"}
        self.agent = Agent(self.adapter, cfg)
        budget.set_budget(1000)
        budget.set_max_kw(1.0)
        budget._last_load_w = 500.0

    def tearDown(self):
        self.adapter.stop()
        db.DATA_DIR, db.DB_PATH, self._mock.STATE_PATH, budget._last_load_w = self._orig
        self._tmp.cleanup()

    def test_second_request_reuses_the_booking(self):
        first = self.agent.handle("turn on the dryer")
        second = self.agent.handle("turn on the dryer")
        jobs = [j for j in db.list_jobs(only_enabled=True) if j["device_id"] == "dryer"]
        self.assertEqual(len(jobs), 1, (first.get("reply"), second.get("reply")))
        self.assertIn("already booked", second.get("reply", ""))
