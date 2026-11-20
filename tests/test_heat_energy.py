"""Heat stress (HSI) and power load (PLI): the two indices added after MRI/TOI.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, policy, risk  # noqa: E402
from app.proactive import ProactiveEngine  # noqa: E402
from tests.test_phase2 import FakeAdapter, _cfg  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (db.DATA_DIR, db.DB_PATH)
        db.DATA_DIR = Path(self._tmp.name)
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.init()
        self.adapter = FakeAdapter()

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH = self._orig
        self._tmp.cleanup()

    def engine(self, scenario):
        e = ProactiveEngine(self.adapter, _cfg())
        e.sensors.write_files = False
        e.sensors.apply_scenario(scenario)
        return e

    def risks(self, e):
        return {r.kind: r for r in risk.evaluate(e.sensors.snapshot(), e.sensors.history)}

    def applied(self, device):
        return [a for dev, acts in self.adapter.applied if dev == device for a in acts]


class ApparentTempTest(unittest.TestCase):
    def test_humidity_makes_it_feel_hotter(self):
        self.assertGreater(risk.apparent_temp(31.5, 70), risk.apparent_temp(31.5, 40))

    def test_known_value(self):
        # 31.5 °C at 70 % relative humidity feels like about 38 °C.
        self.assertAlmostEqual(risk.apparent_temp(31.5, 70), 38.1, delta=0.3)


class HeatHealthTest(Base):
    def test_hot_humid_bedroom_fires(self):
        r = self.risks(self.engine("heatwave"))["heat_health"]
        self.assertGreaterEqual(r.score, r.threshold)
        self.assertEqual(r.drivers["room"], "Bedroom")

    def test_silent_when_nobody_is_home(self):
        e = self.engine("heatwave")
        e.sensors.occupancy["user_home"] = False
        self.assertNotIn("heat_health", self.risks(e))

    def test_normal_rooms_stay_below_the_threshold(self):
        for sc in ("idle", "oven", "mold"):
            r = self.risks(self.engine(sc)).get("heat_health")
            self.assertTrue(r is None or r.score < r.threshold, sc)

    def test_house_cools_the_room_gently_without_asking(self):
        e = self.engine("heatwave")
        e.tick()
        acts = self.applied("ac_bedroom")
        self.assertIn({"action": "temperature", "value": policy.GENTLE_COOL_C}, acts)
        self.assertIn({"action": "mode", "value": "cool"}, acts)
        self.assertEqual(policy.tier_of("cool_room"), policy.TIER1)
        self.assertEqual(e.approvals.pending(), [])

    def test_no_cooling_proposed_when_the_room_is_already_cooled(self):
        e = self.engine("heatwave")
        e.sensors.note_action("ac_bedroom", [{"action": "power", "value": True},
                                             {"action": "mode", "value": "cool"},
                                             {"action": "temperature", "value": 26}])
        snap = e.sensors.snapshot()
        r = self.risks(e)["heat_health"]
        self.assertNotIn("cool_room", [c.kind for c in policy.candidates(r, snap)])


class EnergyTest(Base):
    def test_peak_band_with_cold_acs_and_a_wash_fires(self):
        r = self.risks(self.engine("peak"))["energy"]
        self.assertGreaterEqual(r.score, r.threshold)
        self.assertEqual(r.drivers["tariff"], "peak")

    def test_same_load_off_peak_is_fine(self):
        e = self.engine("peak")
        e.sensors.tariff = "offpeak"
        r = self.risks(e)["energy"]
        self.assertLess(r.score, r.threshold)

    def test_existing_scenarios_do_not_trip_the_energy_index(self):
        for sc in ("idle", "mold", "oven", "oven_running", "heatwave"):
            r = self.risks(self.engine(sc)).get("energy")
            self.assertTrue(r is None or r.score < r.threshold, sc)

    def test_warmer_setpoint_draws_less(self):
        from app.sensors import SensorSimulator
        self.assertGreater(SensorSimulator.ac_draw_w(22), SensorSimulator.ac_draw_w(24))

    def test_eco_runs_on_its_own_and_the_wash_is_asked_about(self):
        e = self.engine("peak")
        e.tick()
        self.assertTrue(any(a["action"] == "temperature" and a["value"] == 24
                            for dev in ("ac_living", "ac_bedroom") for a in self.applied(dev)))
        pending = e.approvals.pending()
        self.assertEqual([a.action_kind for a in pending], ["pause_washer"])
        self.assertEqual(self.applied("washer"), [])   # Level 2 never runs by itself

    def test_yes_pauses_the_wash_and_brings_the_load_down(self):
        e = self.engine("peak")
        e.tick()
        ap = e.approvals.pending()[0]
        before = e.sensors.snapshot()["total_load_w"]
        res = e.confirm(ap.id)
        self.assertTrue(res["ok"], res)
        self.assertIn({"action": "power", "value": False}, self.applied("washer"))
        snap = e.sensors.snapshot()
        self.assertEqual(snap["appliances"]["washer"]["cycle"], "paused")
        self.assertLess(snap["total_load_w"], before)
        # A paused (not finished) wash must not start the mold index.
        self.assertNotIn("mold", self.risks(e))

    def test_no_pause_offered_off_peak(self):
        e = self.engine("peak")
        e.sensors.tariff = "offpeak"
        e.sensors.ac_targets["Kitchen"] = {"power": True, "mode": "cool", "target_temp": 18}
        snap = e.sensors.snapshot()
        r = risk.energy_risk(snap, e.sensors.history)
        self.assertNotIn("pause_washer", [c.kind for c in policy.candidates(r, snap)])


class TrendHistoryTest(Base):
    def test_history_is_recorded_and_drives_the_prediction(self):
        from datetime import datetime, timedelta
        e = self.engine("oven")
        t0 = datetime.now()
        for i in range(10):
            with e.sensors._lock:
                e.sensors.rooms["Kitchen"]["temp"] = 30.0 + 0.1 * i
            e.sensors.push_history(e.sensors.snapshot(), now=t0 + timedelta(seconds=i))
        self.assertEqual(len(e.sensors.history["Kitchen.temp"]), 10)
        r = risk.thermal_risk(e.sensors.snapshot(), e.sensors.history, t0)
        self.assertGreater(r.predicted, r.current)   # rising room -> warmer in 30 min

    def test_same_tick_is_not_recorded_twice(self):
        from datetime import datetime
        e = self.engine("idle")
        now = datetime.now()
        snap = e.sensors.snapshot()
        e.sensors.push_history(snap, now=now)
        e.sensors.push_history(snap, now=now)
        self.assertEqual(len(e.sensors.history["home.load_w"]), 1)

    def test_scenario_change_clears_the_trend(self):
        e = self.engine("oven")
        e.tick()
        self.assertTrue(e.sensors.history["Kitchen.temp"])
        e.sensors.apply_scenario("idle")
        self.assertFalse(e.sensors.history["Kitchen.temp"])


if __name__ == "__main__":
    unittest.main()
