"""Load planner: plans for a start that would go over the max kW.

The model may only pick a listed plan; the plans, the resulting load and the
safety exemptions are computed in code and are tested here without a model.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import budget, db, loadplan, regions  # noqa: E402

ON = [{"action": "power", "value": True}]


class StateAdapter:
    """Devices with state, recording what was applied."""

    backend_name = "fake"

    def __init__(self, states: dict):
        types = {"ac_living": "air_conditioner", "ac_bedroom": "air_conditioner",
                 "dehumidifier": "dehumidifier", "dryer": "dryer", "styler": "styler",
                 "fridge": "refrigerator", "purifier": "air_purifier", "range_hood": "range_hood",
                 "washer": "washer"}
        self.devices = [{"id": k, "name": k.replace("_", " ").title(), "type": t, "online": True}
                        for k, t in types.items()]
        self.states = {k: dict(states.get(k, {"power": False})) for k in types}
        self.applied = []

    def list_devices(self):
        return [dict(d) for d in self.devices]

    def get_device(self, device_id):
        d = next((x for x in self.devices if x["id"] == device_id), None)
        return {**d, "state": dict(self.states[device_id])} if d else None

    def get_state(self, device_id):
        return dict(self.states.get(device_id, {}))

    def apply(self, device_id, actions):
        self.applied.append((device_id, [dict(a) for a in actions]))
        for a in actions:
            key = "target_temp" if a["action"] == "temperature" else a["action"]
            self.states[device_id][key] = a["value"]
        return {"ok": True, "applied": actions, "error": None}


class LoadPlanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (db.DATA_DIR, db.DB_PATH, budget._last_load_w, dict(budget._house))
        db.DATA_DIR = Path(self._tmp.name)
        db.DB_PATH = db.DATA_DIR / "t.db"
        db.init()
        budget.set_budget(1000)
        budget.set_max_kw(3.0)
        budget.observe(set(), True)
        self.offpeak = datetime(2026, 11, 26, 23, 10)
        self.peak = datetime(2026, 11, 26, 17, 0)

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH, budget._last_load_w = self._orig[:3]
        budget._house.clear(); budget._house.update(self._orig[3])
        self._tmp.cleanup()

    def busy(self):
        return StateAdapter({"ac_living": {"power": True, "target_temp": 22},
                             "dehumidifier": {"power": True},
                             "fridge": {"power": True}, "purifier": {"power": True}})

    # ------------------------------------------------ plans

    def test_makes_room_by_changing_other_loads_and_says_so(self):
        a = self.busy()
        budget._last_load_w = 1800.0      # the house now, as the 1 Hz loop saw it
        v = budget.gate(a, "dryer", ON, now=self.offpeak, channel="chat")
        # 1.8 + 1.4 = 3.2 kW: the AC raise (smallest comfort change) comes
        # first, then the dehumidifier, until the dryer fits under 3 kW.
        self.assertEqual(v["decision"], budget.ALLOW)
        self.assertEqual(v["label"], "MAKE_ROOM")
        changed = {d for d, _ in a.applied}
        self.assertTrue(changed & {"ac_living", "dehumidifier"})
        self.assertIn("To stay under your 3 kW limit", v["note"])
        self.assertLessEqual(v["plan_after_kw"], 3.0)

    def test_peak_band_prefers_moving_a_movable_load(self):
        plans = [{"kind": loadplan.MAKE_ROOM, "id": "P1"}, {"kind": loadplan.DELAY, "id": "P2"}]
        self.assertEqual(loadplan.choose_by_rules(plans, "peak")["kind"], loadplan.DELAY)
        self.assertEqual(loadplan.choose_by_rules(plans, "offpeak")["kind"], loadplan.MAKE_ROOM)

    def test_cooling_is_kept_during_heat_stress(self):
        sheds = loadplan.shed_options(self.busy().list_devices() and
                                      [self.busy().get_device("ac_living")],
                                      exclude="dryer", firing={"heat_health"},
                                      user_home=True, when=self.offpeak)
        self.assertEqual(sheds, [])

    def test_dehumidifier_kept_while_fighting_mold(self):
        a = self.busy()
        sheds = loadplan.shed_options(loadplan.devices_with_state(a), exclude="dryer",
                                      firing={"mold"}, user_home=True, when=self.offpeak)
        self.assertNotIn("dehumidifier", {s["device_id"] for s in sheds})

    def test_food_air_and_cooking_are_never_changed(self):
        a = self.busy()
        sheds = loadplan.shed_options(loadplan.devices_with_state(a), exclude="dryer",
                                      firing=set(), user_home=True, when=self.offpeak)
        self.assertFalse({s["device_id"] for s in sheds} & {"fridge", "purifier", "range_hood"})

    def test_cooling_goes_over_as_little_as_possible_instead_of_being_refused(self):
        a = StateAdapter({"dryer": {"power": True}, "dehumidifier": {"power": True}})
        v = budget.can_start(a, "ac_living", ON, now=self.offpeak, load_w=2800, channel="chat")
        self.assertEqual(v["decision"], budget.ALLOW)
        self.assertIn(v["plan"], (loadplan.MAKE_ROOM, loadplan.OVER))

    def test_by_hand_start_of_a_movable_load_can_be_switched_off_and_booked(self):
        a = StateAdapter({"styler": {"power": True}})
        dev = a.get_device("styler")
        plans = loadplan.build_plans(dev=dev, add_w=1000, load_w=3400, cap_kw=3.0,
                                     devices=loadplan.devices_with_state(a), firing=set(),
                                     user_home=True, band="offpeak", now=self.offpeak,
                                     region=regions.get("KR"), channel="by-hand",
                                     started_by_hand=True)
        kinds = [p["kind"] for p in plans]
        self.assertIn(loadplan.STOP_BOOK, kinds)
        self.assertEqual(loadplan.choose_by_rules(plans, "offpeak")["kind"], loadplan.STOP_BOOK)

    # ------------------------------------------------ the model's choice

    def test_model_choice_is_used_when_it_names_a_listed_plan(self):
        plans = [{"kind": loadplan.MAKE_ROOM, "id": "P1", "text": "a", "after_kw": 2.9},
                 {"kind": loadplan.DELAY, "id": "P2", "text": "b", "after_kw": 1.8}]
        with mock.patch.object(loadplan.llm, "available", return_value=True), \
             mock.patch.object(loadplan.llm, "chat", return_value={"content": '{"plan": "P2", "reason": "peak"}'}):
            plan, source, trace = loadplan.choose({"llm": {}}, plans, "facts", "offpeak")
        self.assertEqual(plan["id"], "P2")
        self.assertEqual(source, budget.SOURCE_LLM)
        self.assertIn("P2", trace["model_output"])

    def test_unlisted_model_choice_is_overridden_by_the_rules(self):
        plans = [{"kind": loadplan.MAKE_ROOM, "id": "P1", "text": "a", "after_kw": 2.9},
                 {"kind": loadplan.DELAY, "id": "P2", "text": "b", "after_kw": 1.8}]
        with mock.patch.object(loadplan.llm, "available", return_value=True), \
             mock.patch.object(loadplan.llm, "chat", return_value={"content": '{"plan": "P9"}'}):
            plan, source, _ = loadplan.choose({"llm": {}}, plans, "facts", "offpeak")
        self.assertEqual(plan["kind"], loadplan.MAKE_ROOM)
        self.assertEqual(source, budget.SOURCE_OVERRIDE)

    def test_model_failure_falls_back_to_the_rules(self):
        plans = [{"kind": loadplan.STOP, "id": "P1", "text": "a", "after_kw": 2.9},
                 {"kind": loadplan.DELAY, "id": "P2", "text": "b", "after_kw": 1.8}]
        with mock.patch.object(loadplan.llm, "available", return_value=True), \
             mock.patch.object(loadplan.llm, "chat", side_effect=TimeoutError()):
            plan, source, _ = loadplan.choose({"llm": {}}, plans, "facts", "offpeak")
        self.assertEqual(plan["kind"], loadplan.DELAY)
        self.assertEqual(source, budget.SOURCE_FALLBACK)


if __name__ == "__main__":
    unittest.main()
