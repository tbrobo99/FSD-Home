"""FR-8: the energy budget is one gate, with one unit per quantity.

These tests are deliberately about the *boundaries*: kW vs kWh vs money,
month rollover, what a DELAY is allowed to assume about a real appliance, and
the provenance of a decision. The safety tests in test_fr456.py are untouched.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import budget, config, db  # noqa: E402
from app.agent import Agent  # noqa: E402
from app.devices import MockAdapter  # noqa: E402
from app.devices import mock as mock_mod  # noqa: E402
from tests.test_phase2 import FakeAdapter  # noqa: E402


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (db.DATA_DIR, db.DB_PATH)
        db.DATA_DIR = Path(self._tmp.name)
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.init()
        self.adapter = FakeAdapter()
        self.peak = datetime(2026, 11, 26, 19, 0)
        self.offpeak = datetime(2026, 11, 26, 23, 0)

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH = self._orig
        self._tmp.cleanup()

    # -------------------------------------------------- units

    def test_kw_kwh_and_money_are_separate_fields(self):
        s = budget.state(self.peak)
        self.assertIn("budget_kwh", s)
        self.assertIn("used_kwh", s)
        self.assertIn("estimated_cost", s)
        self.assertIn("price_per_kwh", s)
        self.assertNotIn("budget_kw", s)
        self.assertEqual(s["currency"], "KRW")

    def test_accrual_is_time_weighted_not_per_tick(self):
        budget.accrue(3600, now=self.peak, cfg={})
        budget.accrue(3600, now=self.peak + timedelta(seconds=1800), cfg={})
        self.assertAlmostEqual(budget.state(self.peak)["used_kwh"], 1.8, places=2)

    def test_duplicate_or_backwards_timestamps_add_nothing(self):
        budget.accrue(5000, now=self.peak, cfg={})
        budget.accrue(5000, now=self.peak, cfg={})
        budget.accrue(5000, now=self.peak - timedelta(seconds=30), cfg={})
        self.assertAlmostEqual(budget.state(self.peak)["used_kwh"], 0.0, places=4)

    # -------------------------------------------------- monthly rollover

    def test_month_rollover_starts_a_fresh_period(self):
        budget.set_budget(10, now=datetime(2026, 10, 31, 22, 0))
        budget.accrue(3600, now=datetime(2026, 10, 31, 23, 0), cfg={})
        budget.accrue(3600, now=datetime(2026, 11, 1, 0, 0, 1), cfg={})
        nov = budget.state(datetime(2026, 11, 1))
        self.assertEqual(nov["period"], "2026-11")
        self.assertEqual(nov["used_kwh"], 0.0)
        self.assertEqual(budget.state(datetime(2026, 10, 31))["period"], "2026-10")

    # -------------------------------------------------- decisions

    def test_peak_movable_not_running_is_delayed(self):
        v = budget.can_start(self.adapter, "washer",
                             [{"action": "power", "value": True}], now=self.peak)
        self.assertEqual(v["decision"], budget.DELAY)
        self.assertIn("scheduled_at", v)

    def test_offpeak_same_load_is_allowed(self):
        v = budget.can_start(self.adapter, "washer",
                             [{"action": "power", "value": True}], now=self.offpeak)
        self.assertEqual(v["decision"], budget.ALLOW)

    def test_already_running_load_is_never_delayed(self):
        # A real washing machine may not support pause/resume; the simulator
        # must not teach the habit of assuming it does.
        d = self.adapter.get_device("washer")
        d["state"] = {"power": True, "cycle": "washing"}
        v = budget.can_start(self.adapter, "washer",
                             [{"action": "power", "value": True}], now=self.peak)
        self.assertNotEqual(v["decision"], budget.DELAY)

    def test_heating_appliances_are_outside_autonomous_authority(self):
        # FakeAdapter has no cooktop; use the real 11-device mock for the type
        # check. The safety set is defined by device type, not by test fixture.
        from app.devices.mock import MockAdapter
        adapter = MockAdapter()
        for dev in ("cooktop", "oven"):
            v = budget.can_start(adapter, dev,
                                 [{"action": "power", "value": True}], now=self.offpeak)
            self.assertEqual(v["decision"], budget.BLOCK, dev)

    def test_spent_budget_blocks_even_offpeak(self):
        budget.set_budget(0.01, now=self.offpeak)
        v = budget.can_start(self.adapter, "dryer",
                             [{"action": "power", "value": True}], now=self.offpeak)
        self.assertEqual(v["decision"], budget.BLOCK)
        self.assertIn("remaining", v["reason"])

    def test_estimate_and_actual_are_not_confused(self):
        v = budget.can_start(self.adapter, "washer",
                             [{"action": "power", "value": True}], now=self.offpeak)
        self.assertGreater(v["estimated_kwh"], 0)
        # An estimate must not be booked as consumption.
        self.assertEqual(budget.state(self.offpeak)["used_kwh"], 0.0)

    # -------------------------------------------------- provenance

    def test_three_provenance_sources_are_distinct(self):
        for src in (budget.SOURCE_LLM, budget.SOURCE_FALLBACK,
                    budget.SOURCE_OVERRIDE, budget.SOURCE_RULES):
            budget.record_decision("ALLOW", src, "washer", "test", channel="test")
        with db.conn() as c:
            rows = c.execute(
                "SELECT DISTINCT source FROM energy_decisions ORDER BY source"
            ).fetchall()
        self.assertEqual({r["source"] for r in rows}, set(budget.SOURCES))

    def test_real_prompt_input_output_are_persisted(self):
        budget.record_decision("DECIDED", budget.SOURCE_LLM, "washer", "reason",
                               channel="engine", prompt="SYSTEM PROMPT TEXT",
                               model_input="USER INPUT TEXT",
                               model_output='{"chosen":[]}')
        d = budget.decisions(1)[0]
        self.assertEqual(d["prompt"], "SYSTEM PROMPT TEXT")
        self.assertEqual(d["model_input"], "USER INPUT TEXT")
        self.assertEqual(d["model_output"], '{"chosen":[]}')

    def test_budget_decisions_are_also_in_append_only_audit(self):
        budget.record_decision(budget.BLOCK, budget.SOURCE_LLM, "washer", "reason")
        rows = db.recent_audit(10, "energy.decision")
        self.assertTrue(rows)
        self.assertIn("BLOCK", rows[0]["detail"])

    def test_energy_decisions_table_rejects_update_and_delete(self):
        # A comment saying "append-only" is not a guarantee. The database must
        # reject the edit even if application code has a bug or someone opens
        # the file by hand.
        budget.record_decision(budget.ALLOW, budget.SOURCE_RULES,
                               "washer", "original reason")
        with db.conn() as c:
            row = c.execute("SELECT id FROM energy_decisions LIMIT 1").fetchone()
        decision_id = row["id"]

        with self.assertRaises(sqlite3.IntegrityError):
            with db.conn() as c:
                c.execute(
                    "UPDATE energy_decisions SET reason='edited' WHERE id=?",
                    (decision_id,),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with db.conn() as c:
                c.execute("DELETE FROM energy_decisions WHERE id=?", (decision_id,))

        with db.conn() as c:
            row = c.execute(
                "SELECT reason FROM energy_decisions WHERE id=?", (decision_id,)
            ).fetchone()
        self.assertEqual(row["reason"], "original reason")

    def test_due_job_rechecks_budget_instead_of_using_old_verdict(self):
        from app.scheduler import Scheduler

        budget.set_budget(10, now=self.offpeak)
        actions = [{"action": "power", "value": True}]
        self.assertEqual(
            budget.can_start(self.adapter, "dryer", actions,
                             now=self.offpeak)["decision"],
            budget.ALLOW,
        )
        job_id = db.add_job(
            "dryer", "Balcony Dryer", actions, {"type": "once"},
            self.offpeak.isoformat(timespec="seconds"),
            source_text="queued while budget was available",
        )

        # The household spends the budget before the appointment comes due.
        budget.set_budget(0.01, now=self.offpeak)
        job = db.get_job(job_id)
        Scheduler(self.adapter)._run_job(job, self.offpeak)

        self.assertEqual(self.adapter.applied, [])
        updated = db.get_job(job_id)
        self.assertTrue(updated["last_result"].startswith("Blocked by budget:"))
        self.assertFalse(updated["enabled"])

        decisions = budget.decisions(10)
        self.assertTrue(any(
            d["decision"] == budget.BLOCK and d["channel"] == "scheduler"
            and d["device_id"] == "dryer" for d in decisions
        ))


class GateFailureTest(unittest.TestCase):
    """A budget that cannot be read is not a budget with room left.

    SQLite locked, the energy row corrupt, the module itself broken: none of
    those mean the household has kWh to spare. The hard limit fails CLOSED for
    anything that starts or raises a load, and stays open for the safe
    direction — switching something off — because refusing that would be the
    more dangerous of the two failures.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._orig = (db.DATA_DIR, db.DB_PATH, mock_mod.STATE_PATH)
        db.DATA_DIR = self.tmp
        db.DB_PATH = self.tmp / "test.db"
        mock_mod.STATE_PATH = self.tmp / "mock_state.json"
        db.init()
        self.adapter = MockAdapter()
        cfg = dict(config.DEFAULTS)
        cfg["agent"] = {"default_room": "Living Room"}
        self.agent = Agent(self.adapter, cfg)

    def tearDown(self):
        self.adapter.stop()
        (db.DATA_DIR, db.DB_PATH, mock_mod.STATE_PATH) = self._orig
        self._tmp.cleanup()

    def _gate_failed_events(self):
        return db.recent_audit(limit=50, event_type="budget.gate_failed")

    def test_a_broken_gate_never_starts_anything(self):
        with mock.patch("app.budget.gate",
                        side_effect=RuntimeError("database is locked")), \
                mock.patch.object(self.adapter, "apply") as apply:
            r = self.agent.handle("turn on the living room AC")

        self.assertFalse(r["ok"])
        self.assertIn("could not be checked", r["reply"])
        apply.assert_not_called()
        self.assertFalse(self.adapter.get_state("ac_living")["power"])

        events = self._gate_failed_events()
        self.assertEqual(len(events), 1)
        self.assertIn("database is locked", events[0]["detail"])
        self.assertEqual(events[0]["device_id"], "ac_living")
        self.assertEqual(events[0]["execution_status"], "blocked")

    def test_a_temperature_change_is_a_load_change(self):
        """No power action at all is still a start: a setpoint only lands on a
        machine that will run to reach it."""
        before = self.adapter.get_state("ac_bedroom")["target_temp"]
        with mock.patch("app.budget.gate",
                        side_effect=sqlite3.OperationalError("no such table")):
            r = self.agent.handle("set the bedroom AC to 22 degrees")

        self.assertFalse(r["ok"])
        self.assertEqual(self.adapter.get_state("ac_bedroom")["target_temp"], before)
        self.assertEqual(len(self._gate_failed_events()), 1)

    def test_switching_something_off_still_works(self):
        """The safe direction stays open: a budget you cannot read is no reason
        to leave an appliance running."""
        self.adapter.apply("ac_living", [{"action": "power", "value": True}])
        with mock.patch("app.budget.gate",
                        side_effect=RuntimeError("database is locked")):
            r = self.agent.handle("turn off the living room AC")

        self.assertTrue(r["ok"])
        self.assertFalse(self.adapter.get_state("ac_living")["power"])
        # the failure is still on the record
        self.assertEqual(len(self._gate_failed_events()), 1)

    def test_a_whole_home_start_is_refused_per_device(self):
        with mock.patch("app.budget.gate",
                        side_effect=RuntimeError("database is locked")):
            r = self.agent.handle("turn on everything")

        self.assertFalse(r["ok"])
        for did in ("ac_living", "ac_bedroom", "purifier", "washer"):
            self.assertFalse(self.adapter.get_state(did).get("power"))


if __name__ == "__main__":
    unittest.main()
