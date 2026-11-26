"""Korean command transliteration tests.

The soft UI switches its interface to Korean, including speech recognition.
These tests make sure a Korean request takes the *same* deterministic path as
its English equivalent — no separate parser, no special safety rules.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ko, nlu  # noqa: E402

NOW = datetime(2026, 11, 25, 10, 0)

DEVICES = [
    {"id": "ac_living", "name": "Living Room AC", "type": "air_conditioner",
     "room": "Living Room", "online": True},
    {"id": "ac_bedroom", "name": "Bedroom AC", "type": "air_conditioner",
     "room": "Bedroom", "online": True},
    {"id": "purifier", "name": "Living Room Purifier", "type": "air_purifier",
     "room": "Living Room", "online": True},
    {"id": "washer", "name": "Balcony Washer", "type": "washer",
     "room": "Balcony", "online": True},
    {"id": "dryer", "name": "Balcony Dryer", "type": "dryer",
     "room": "Balcony", "online": True},
    {"id": "fridge", "name": "Kitchen Fridge", "type": "refrigerator",
     "room": "Kitchen", "online": True},
]


def parse(text: str) -> dict:
    return nlu.parse(text, DEVICES, NOW, "Living Room")


class DetectionTest(unittest.TestCase):
    def test_detects_hangul(self):
        self.assertTrue(ko.is_korean("에어컨 켜줘"))
        self.assertFalse(ko.is_korean("turn on the ac"))
        self.assertFalse(ko.is_korean(""))

    def test_unknown_korean_is_left_alone(self):
        self.assertIn("모르는말", ko.to_english("모르는말 켜줘"))

    def test_english_command_is_untouched(self):
        self.assertEqual(ko.to_english("turn on the ac"), "turn on the ac")


class KoreanMatchesEnglishTest(unittest.TestCase):
    """A Korean command must produce exactly what the English one produces."""

    PAIRS = (
        ("에어컨 켜줘", "turn on the ac"),
        ("거실 에어컨 꺼줘", "turn off the living room ac"),
        ("침실 에어컨 24도로 해줘", "set the bedroom ac to 24 degrees"),
        ("세탁기 켜줘", "turn on the washing machine"),
        ("건조기 꺼줘", "turn off the tumble dryer"),
    )

    def test_pairs(self):
        for kr, en in self.PAIRS:
            with self.subTest(kr=kr):
                a, b = parse(kr), parse(en)
                self.assertEqual(
                    (a["intent"], a["device_id"], a["actions"]),
                    (b["intent"], b["device_id"], b["actions"]),
                )

    def test_ui_example_chip(self):
        # This exact phrase is a one-tap example in app/web/soft/app.js.
        r = parse("거실 에어컨 꺼줘")
        self.assertEqual(r["intent"], "control")
        self.assertEqual(r["device_id"], "ac_living")
        self.assertEqual(r["actions"], [{"action": "power", "value": False}])

    def test_korean_number_with_degree_unit(self):
        r = parse("침실 에어컨 24도로 해줘")
        self.assertEqual(r["actions"], [{"action": "temperature", "value": 24}])

    def test_raw_text_keeps_korean(self):
        # The audit record must show what the user actually said.
        r = parse("에어컨 켜줘")
        self.assertEqual(r["raw_text"], "에어컨 켜줘")

    def test_greeting_matches_english(self):
        self.assertEqual(parse("안녕하세요")["intent"],
                         parse("hello")["intent"])

    def test_no_spaces_still_parses(self):
        # Korean attaches particles directly to nouns.
        r = parse("거실에어컨켜줘")
        self.assertEqual(r["device_id"], "ac_living")
        self.assertEqual(r["actions"], [{"action": "power", "value": True}])


class SafetyUnchangedTest(unittest.TestCase):
    def test_korean_does_not_bypass_temperature_limit(self):
        # The translation layer must not skip range checks. A fridge at 25 C
        # is rejected just as it is in English.
        self.assertEqual(parse("냉장고 25도로 해줘")["intent"],
                         parse("set the fridge to 25 degrees")["intent"])

    def test_english_regression(self):
        r = parse("turn on the ac")
        self.assertEqual(r["intent"], "control")
        self.assertEqual(r["device_id"], "ac_living")


if __name__ == "__main__":
    unittest.main()
