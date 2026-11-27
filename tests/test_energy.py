"""FSD Home energy decisions: budget, LLM validation and autonomous control.

    python -m unittest tests.test_energy -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db, energy, llm  # noqa: E402
from app.agent import Agent  # noqa: E402
from app.proactive import ProactiveEngine  # noqa: E402
from tests.test_phase2 import FakeAdapter  # noqa: E402


def cfg(strict=True, peak=False, llm_on=False) -> dict:
    return {
        "agent": {"mode": "cascade", "default_room": "Living Room"},
        "llm": {"enabled": llm_on, "api_key": "test-key" if llm_on else "",
                "base_url": "http://127.0.0.1:9/v1", "model": "gpt-4o-mini"},
        "energy": {"enabled": True, "strict_mode": strict, "llm_decide": llm_on,
                   "monthly_budget_kwh": 100.0},
        "proactive": {},
    }


def snap(tariff="offpeak", load=1000) -> dict:
    return {"total_load_w": load, "tariff": tariff,
            "occupancy": {"user_home": True}}


class EnergyBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = (db.DATA_DIR, db.DB_PATH)
        db.DATA_DIR = Path(self._tmp.name)
        db.DB_PATH = db.DATA_DIR / "test.db"
        db.init()

    def tearDown(self):
        db.DATA_DIR, db.DB_PATH = self._orig
        self._tmp.cleanup()


class MeteringTest(EnergyBase):
    def test_one_hour_at_one_kw_adds_one_kwh(self):
        em = energy.EnergyManager(cfg(), FakeAdapter())
        em.accrue(snap(load=1000), 3600)
        self.assertAlmostEqual(em.used_kwh, 1.0, places=3)
        self.assertAlmostEqual(em.remaining_kwh, 99.0, places=3)

    def test_remaining_never_goes_negative(self):
        em = energy.EnergyManager(cfg(), FakeAdapter())
        em.set_budget(1.0)
        em.accrue(snap(load=3000), 3600)
        self.assertEqual(em.remaining_kwh, 0.0)
        self.assertEqual(em.budget_pct, 100.0)

    def test_budget_and_strict_switch_are_live(self):
        em = energy.EnergyManager(cfg(strict=False), FakeAdapter())
        self.assertFalse(em.view()["strict_mode"])
        em.set_strict(True)
        em.set_budget(42)
        self.assertTrue(em.view()["strict_mode"])
        self.assertEqual(em.budget_kwh, 42)
        em.reset()
        self.assertEqual(em.used_kwh, 0.0)


class DecisionTest(EnergyBase):
    def test_peak_washer_is_delayed_to_cheap_band(self):
        em = energy.EnergyManager(cfg(), FakeAdapter())
        d = em.decide("washer", [{"action": "power", "value": True}], snap("peak"))
        self.assertEqual(d["action"], "DELAY")
        self.assertIn("22:00", d["scheduled_time"])
        applied = em.apply_decision(d, snap("peak"))
        self.assertFalse(applied["execute"])
        self.assertTrue(applied["job"])
        self.assertIn("scheduled", applied["reply"].lower())

    def test_strict_blocks_a_request_over_the_remaining_budget(self):
        em = energy.EnergyManager(cfg(), FakeAdapter())
        em.set_budget(em.used_kwh + 1.0)
        d = em.decide("dryer", [{"action": "power", "value": True}], snap())
        self.assertEqual(d["action"], "BLOCK")
        self.assertGreater(d["estimated_kwh"], 1.0)
        self.assertFalse(em.apply_decision(d, snap())["execute"])

    def test_ac_is_optimized_into_the_comfort_band(self):
        em = energy.EnergyManager(cfg(), FakeAdapter())
        d = em.decide("ac_living", [{"action": "power", "value": True},
                                    {"action": "temperature", "value": 22}], snap("peak"))
        self.assertEqual(d["action"], "OPTIMIZE")
        self.assertIn({"action": "temperature", "value": 26}, d["actions"])
        applied = em.apply_decision(d, snap("peak"))
        self.assertTrue(applied["execute"])

    def test_rules_decide_when_the_model_is_unavailable(self):
        em = energy.EnergyManager(cfg(), FakeAdapter())
        d = em.decide("washer", [{"action": "power", "value": True}], snap("peak"))
        self.assertEqual(d["decided_by"], "rules")
        self.assertEqual(d["action"], "DELAY")

    def test_rules_override_an_llm_allow_that_breaks_the_budget(self):
        orig_avail, orig_chat = llm.available, llm.chat
        llm.available = lambda c: True
        llm.chat = lambda *a, **k: {"content": json.dumps({
            "device": "dryer", "action": "ALLOW",
            "reason": "model would allow it", "actions": [],
        })}
        try:
            em = energy.EnergyManager(cfg(llm_on=True), FakeAdapter())
            em.set_budget(1.0)
            d = em.decide("dryer", [{"action": "power", "value": True}], snap())
            self.assertEqual(d["decided_by"], "llm")
            self.assertEqual(d["action"], "BLOCK")
            self.assertTrue(d.get("overridden"))
            self.assertEqual(d.get("llm_proposed"), "ALLOW")
        finally:
            llm.available, llm.chat = orig_avail, orig_chat

    def test_llm_delay_is_accepted_and_kept_in_the_future(self):
        orig_avail, orig_chat = llm.available, llm.chat
        llm.available = lambda c: True
        llm.chat = lambda *a, **k: {"content": json.dumps({
            "device": "washer", "action": "DELAY",
            "reason": "cheap electricity later",
            "scheduled_time": (datetime.now() - timedelta(hours=1)).isoformat(),
            "actions": [{"action": "power", "value": True}],
        })}
        try:
            em = energy.EnergyManager(cfg(llm_on=True), FakeAdapter())
            d = em.decide("washer", [{"action": "power", "value": True}], snap("peak"))
            self.assertEqual(d["action"], "DELAY")
            self.assertGreater(datetime.fromisoformat(d["scheduled_time"]), datetime.now())
        finally:
            llm.available, llm.chat = orig_avail, orig_chat

    def test_decision_log_and_prompt_are_available_for_demo(self):
        em = energy.EnergyManager(cfg(), FakeAdapter())
        em.decide("washer", [{"action": "power", "value": True}], snap("peak"))
        view = em.view(snap("peak"))
        self.assertTrue(view["decisions"])
        self.assertIn("autonomous home energy management", view["prompt"])
        self.assertEqual(view["sampling"]["sensor_push_hz"], 1.0)


class AgentGateTest(EnergyBase):
    def test_chat_request_to_start_washer_is_delayed_not_executed(self):
        adapter = FakeAdapter()
        agent = Agent(adapter, cfg())
        agent.proactive = SimpleNamespace(
            _last_snapshot=snap("peak", 3000))
        result = agent.handle("turn on the washing machine")
        self.assertEqual(result.get("intent_name"), "energy_gate")
        self.assertEqual(result["energy_decision"]["action"], "DELAY")
        self.assertEqual([a for dev, acts in adapter.applied for a in acts], [])
        self.assertIsNotNone(result["energy_applied"]["job"])

    def test_block_never_reaches_the_adapter(self):
        adapter = FakeAdapter()
        agent = Agent(adapter, cfg())
        agent.energy.set_budget(agent.energy.used_kwh + 1.0)
        agent.proactive = SimpleNamespace(_last_snapshot=snap())
        result = agent.handle("turn on the dryer")
        self.assertEqual(result["energy_decision"]["action"], "BLOCK")
        self.assertEqual(adapter.applied, [])

    def test_strict_off_keeps_the_original_command_path(self):
        adapter = FakeAdapter()
        agent = Agent(adapter, cfg(strict=False))
        agent.proactive = SimpleNamespace(_last_snapshot=snap("peak", 3000))
        result = agent.handle("turn on the washer")
        self.assertNotEqual(result.get("intent_name"), "energy_gate")
        self.assertTrue(adapter.applied)


class ProactiveGateTest(EnergyBase):
    def test_energy_risk_is_handled_without_an_approval_question(self):
        adapter = FakeAdapter()
        c = cfg()
        c["energy"]["llm_decide"] = False
        agent = Agent(adapter, c)
        engine = ProactiveEngine(adapter, c, energy_manager=agent.energy)
        engine.sensors.write_files = False
        engine.sensors.apply_scenario("peak")
        result = engine.tick()
        self.assertTrue(any(e.get("type") == "energy_autonomous"
                            for e in result["events"]), result["events"])
        self.assertEqual(engine.approvals.pending(), [])
        # The AC was optimised autonomously; the wash was paused and delayed.
        temps = [a for dev, acts in adapter.applied for a in acts
                 if a.get("action") == "temperature"]
        self.assertTrue(any(t.get("value") == 26 for t in temps))
        self.assertTrue(any(e.get("kind") == "pause_washer"
                            for e in result["events"][0]["executed"]))

    def test_strict_off_keeps_the_tier2_approval(self):
        adapter = FakeAdapter()
        c = cfg(strict=False)
        agent = Agent(adapter, c)
        engine = ProactiveEngine(adapter, c, energy_manager=agent.energy)
        engine.sensors.write_files = False
        engine.sensors.apply_scenario("peak")
        engine.tick()
        self.assertEqual([a.action_kind for a in engine.approvals.pending()],
                         ["pause_washer"])


if __name__ == "__main__":
    unittest.main()
