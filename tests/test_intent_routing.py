"""Intent routing: what a sentence IS decides where it goes.

Three sentences can share the word "cold" and mean three different things, and
before this layer existed all three reached the same comfort rule and the same
heating order:

    "the bedroom is cold"   a room is named     -> control, by the rules
    "I am cold"             a feeling, no room  -> one question, nothing changes
    "today is cold"         the world outside   -> "I cannot see the weather"

The LLM's role here is the classifier: it names the TYPE of a sentence the
rules could not read, and nothing else. It gets no tools and no action schema,
so there is no field in its reply a device could act on; execution stays in
the rules, the whitelist, the policy checks and the budget gate.

Everything runs offline against the simulator and the scripted FakeLLM.
"""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import classifier, config, db  # noqa: E402
from app.agent import Agent  # noqa: E402
from app.devices import MockAdapter  # noqa: E402
from app.devices import mock as mock_mod  # noqa: E402
from tests.fake_llm import FakeLLM, tool_call  # noqa: E402

NOW = datetime(2026, 11, 26, 10, 0)


def offline_cfg() -> dict:
    cfg = copy.deepcopy(config.DEFAULTS)
    cfg["llm"]["enabled"] = False
    cfg["llm"]["api_key"] = ""
    cfg["chat"]["enabled"] = False
    return cfg


class RoutingBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._orig = (db.DATA_DIR, db.DB_PATH, mock_mod.STATE_PATH)
        db.DATA_DIR = self.tmp
        db.DB_PATH = self.tmp / "test.db"
        mock_mod.STATE_PATH = self.tmp / "mock_state.json"
        db.init()
        self.adapter = MockAdapter()
        self.cfg = offline_cfg()
        self.cfg["agent"] = {"default_room": "Living Room"}
        self.agent = Agent(self.adapter, self.cfg)

    def tearDown(self) -> None:
        self.adapter.stop()
        (db.DATA_DIR, db.DB_PATH, mock_mod.STATE_PATH) = self._orig
        self._tmp.cleanup()

    def heat_on(self, device_id: str) -> bool:
        state = self.adapter.get_state(device_id)
        return bool(state.get("power")) and state.get("mode") == "heat"


# ---------------------------------------------------------------- rule layer

class TemperatureStatementsTest(RoutingBase):
    """The seven sentences the benchmark keeps asking about."""

    def test_today_is_cold_names_the_missing_data(self):
        r = self.agent.handle("today is cold")

        self.assertEqual(r["tier"], "rules")
        self.assertEqual(r["actions"], [])
        self.assertIn("cannot see the weather", r["reply"])
        self.assertFalse(self.heat_on("ac_living"))
        self.assertFalse(self.heat_on("ac_bedroom"))

    def test_it_is_cold_outside_is_not_a_room_question(self):
        """It used to answer "Which room?" about a room the sentence never
        mentioned, because the comfort rule read the bare "cold" as an order."""
        r = self.agent.handle("it is cold outside")

        self.assertEqual(r["tier"], "rules")
        self.assertEqual(r["actions"], [])
        self.assertIn("cannot see the weather", r["reply"])
        self.assertFalse(self.heat_on("ac_living"))

    def test_i_am_cold_asks_one_question_and_changes_nothing(self):
        r = self.agent.handle("I am cold")

        self.assertEqual(r["intent_name"], "need_target")
        self.assertEqual(r["actions"], [])
        self.assertIn("Which room", r["reply"])
        self.assertFalse(self.heat_on("ac_living"))
        self.assertFalse(self.heat_on("ac_bedroom"))

    def test_the_answer_names_the_room_and_then_it_heats(self):
        """The multi-turn flow a feeling sentence is for: ask, then act on the
        answer through the same whitelist as any typed command."""
        self.agent.handle("I am cold")
        r = self.agent.handle("the bedroom")

        self.assertEqual(r["intent_name"], "control")
        self.assertTrue(self.heat_on("ac_bedroom"))
        self.assertFalse(self.heat_on("ac_living"))

    def test_a_named_room_is_still_a_command(self):
        """A room named with a feeling word is the target, not a mystery:
        this is the locked behaviour agent mode depends on."""
        r = self.agent.handle("the bedroom is cold")

        self.assertEqual(r["intent_name"], "control")
        self.assertTrue(self.heat_on("ac_bedroom"))

    def test_make_the_bedroom_warmer_is_a_command(self):
        """An imperative is a command even without "turn on". It used to fall
        through every tier to the help text, which reads as a miss."""
        r = self.agent.handle("make the bedroom warmer")

        self.assertEqual(r["intent_name"], "control")
        self.assertTrue(self.heat_on("ac_bedroom"))
        self.assertEqual(self.adapter.get_state("ac_bedroom")["target_temp"], 24)

    def test_set_to_heat_still_executes_directly(self):
        r = self.agent.handle("set the bedroom AC to heat")

        self.assertEqual(r["intent_name"], "control")
        self.assertEqual(self.adapter.get_state("ac_bedroom")["mode"], "heat")

    def test_a_weather_clause_next_to_a_command_is_still_a_command(self):
        r = self.agent.handle("today is cold, turn on the bedroom AC")

        self.assertEqual(r["intent_name"], "control")
        self.assertTrue(self.adapter.get_state("ac_bedroom")["power"])

    def test_the_indoor_complaints_keep_their_flow(self):
        """The fix must not sweep indoor feelings out with the weather: a
        stuffy room still asks, and the answer still cools."""
        first = self.agent.handle("it's stuffy in here")

        self.assertEqual(first["intent_name"], "need_target")
        self.assertIn("Which room", first["reply"])

        second = self.agent.handle("the bedroom")
        self.assertEqual(second["intent_name"], "control")
        self.assertEqual(self.adapter.get_state("ac_bedroom")["mode"], "cool")

    def test_no_classifier_needed_for_sentences_the_rules_read(self):
        """Every rule-level answer above holds with the LLM entirely absent:
        the classifier is for the tail the rules miss, not for these."""
        with mock.patch("app.classifier.classify") as seen:
            for text in ("today is cold", "the bedroom is cold",
                         "make the bedroom warmer"):
                self.agent.handle(text)
            self.assertEqual(seen.call_count, 0)


# ---------------------------------------------------------------- classifier

class ClassifierModuleTest(unittest.TestCase):
    def _cfg(self, fake: FakeLLM) -> dict:
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["llm"] = {"enabled": True, "api_key": "k", "model": "fake",
                      "base_url": fake.base_url, "timeout": 5}
        return cfg

    def test_one_json_round_trip_with_no_tools(self):
        with FakeLLM() as fake:
            fake.prose_reply = ('{"type": "observation", "room": "Bedroom", '
                                '"desired_effect": "warmer", "confidence": 0.9}')
            verdict = classifier.classify(self._cfg(fake), "the bedroom is cold")

            self.assertEqual(verdict["type"], "observation")
            self.assertEqual(verdict["room"], "Bedroom")
            self.assertEqual(verdict["desired_effect"], "warmer")
            body = fake.requests[0]
            self.assertNotIn("tools", body)          # nothing to act with
            self.assertNotIn("tool_choice", body)
            self.assertIn("response_format", body)   # JSON verdict only

    def test_unparseable_reply_is_none(self):
        with FakeLLM() as fake:
            fake.prose_reply = "sorry, no"
            self.assertIsNone(classifier.classify(self._cfg(fake), "hello"))

    def test_an_unknown_type_is_rejected(self):
        with FakeLLM() as fake:
            fake.prose_reply = '{"type": "device_action", "confidence": 0.99}'
            self.assertIsNone(classifier.classify(self._cfg(fake), "hello"))

    def test_confidence_is_clamped_and_effects_are_whitelisted(self):
        with FakeLLM() as fake:
            fake.prose_reply = ('{"type": "observation", "room": 7, '
                                '"desired_effect": "turn on the oven", '
                                '"confidence": 5}')
            verdict = classifier.classify(self._cfg(fake), "hello")

            self.assertEqual(verdict["confidence"], 1.0)
            self.assertIsNone(verdict["room"])
            self.assertIsNone(verdict["desired_effect"])

    def test_offline_is_none_not_an_error(self):
        cfg = offline_cfg()
        self.assertIsNone(classifier.classify(cfg, "hello"))


class ClassifierRoutingTest(RoutingBase):
    """The agent turns a verdict into a reply. Never into an action."""

    def setUp(self) -> None:
        super().setUp()
        # the LLM is on so the routing branch is reachable; the verdicts are
        # scripted, which is what the tier under test consumes
        self.cfg["llm"] = {"enabled": True, "api_key": "k", "model": "fake",
                           "base_url": "http://127.0.0.1:9/v1", "timeout": 1}

    def _sentence(self) -> str:
        """Unknown to the rules, not conversational, no value to set: the
        exact shape the classifier exists for."""
        return "the garage feels like a walk in freezer"

    def test_smalltalk_goes_to_the_text_door(self):
        verdict = {"type": "smalltalk", "room": None,
                   "desired_effect": None, "confidence": 0.9}
        with mock.patch("app.classifier.classify", return_value=verdict):
            r = self.agent.handle("well that is a fine thing to say")

        self.assertEqual(r["tier"], "classifier")
        self.assertEqual(r["intent_name"], "chat")
        self.assertEqual(r["actions"], [])
        self.assertFalse(self.heat_on("ac_living"))

    def test_outside_weather_gets_the_honest_reply(self):
        verdict = {"type": "outside_weather", "room": None,
                   "desired_effect": None, "confidence": 0.9}
        with mock.patch("app.classifier.classify", return_value=verdict):
            r = self.agent.handle(self._sentence())

        self.assertEqual(r["tier"], "classifier")
        self.assertIn("cannot see the weather", r["reply"])
        self.assertEqual(r["actions"], [])

    def test_an_observation_only_buys_a_question(self):
        verdict = {"type": "observation", "room": "Bedroom",
                   "desired_effect": "warmer", "confidence": 0.9}
        with mock.patch("app.classifier.classify", return_value=verdict):
            r = self.agent.handle(self._sentence())

        self.assertEqual(r["tier"], "classifier")
        self.assertEqual(r["actions"], [])
        self.assertIn("Bedroom", r["reply"])
        self.assertIn("make the Bedroom warmer", r["reply"])
        # nothing changed anywhere the agent could have acted
        for did in ("ac_living", "ac_bedroom", "purifier", "washer"):
            self.assertFalse(self.adapter.get_state(did).get("power"))

    def test_low_confidence_is_a_question_never_an_action(self):
        verdict = {"type": "observation", "room": "Bedroom",
                   "desired_effect": "warmer", "confidence": 0.3}
        with mock.patch("app.classifier.classify", return_value=verdict):
            r = self.agent.handle(self._sentence())

        self.assertEqual(r["tier"], "classifier")
        self.assertEqual(r["actions"], [])
        self.assertNotIn("heating", r["reply"].lower())
        self.assertFalse(self.heat_on("ac_bedroom"))

    def test_a_confident_command_is_handed_back_to_the_cascade(self):
        """The classifier says "command" and stops: the loop (which acts only
        through validated tools) takes it from there."""
        verdict = {"type": "command", "room": None,
                   "desired_effect": None, "confidence": 0.9}
        with FakeLLM() as fake:
            fake.script = [tool_call("finish", reply="The garage is not mine to fix.")]
            self.cfg["llm"]["base_url"] = fake.base_url
            with mock.patch("app.classifier.classify", return_value=verdict):
                r = self.agent.handle(self._sentence())

        self.assertEqual(r["tier"], "loop")
        self.assertEqual(r["reply"], "The garage is not mine to fix.")
        self.assertEqual(r["actions"], [])

    def test_a_failed_call_leaves_the_cascade_exactly_as_it_was(self):
        """Offline, timeout, nonsense JSON: the rules' answer is the floor."""
        with mock.patch("app.classifier.classify", return_value=None):
            r = self.agent.handle(self._sentence())

        self.assertEqual(r["tier"], "rules")
        self.assertIn("I can turn devices", r["reply"])

    def test_the_tier_two_shape_skips_the_classifier(self):
        """A miss that carries a value to set ("hi, living room Ac 25") goes
        straight to tier 2 -- the classifier would only buy a round trip."""
        verdict = {"type": "smalltalk", "room": None,
                   "desired_effect": None, "confidence": 0.9}
        plan = {"intent": "control", "device_id": "ac_living",
                "device_name": "Living Room AC",
                "targets": [self.adapter.get_device("ac_living")],
                "actions": [{"action": "temperature", "value": 25}],
                "rule": None}
        with mock.patch("app.classifier.classify") as seen, \
                mock.patch("app.llm.plan", return_value=plan):
            r = self.agent.handle("hi, living room Ac 25")

        self.assertEqual(seen.call_count, 0)
        self.assertEqual(r["tier"], "llm")
        self.assertEqual(self.adapter.get_state("ac_living")["target_temp"], 25)


class LoopPromptTest(unittest.TestCase):
    def test_the_loop_is_told_a_temperature_statement_is_not_a_command(self):
        from app.agent_loop import SYSTEM_PROMPT

        self.assertIn("not automatically a command", SYSTEM_PROMPT)
        self.assertIn("Which room feels cold?", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
