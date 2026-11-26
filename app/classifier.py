"""Classifier-only LLM pass: what IS this sentence, never what should run.

The rules answer every common command and every common comfort phrase. What is
left is the long tail, and the question about the long tail is "what is this
sentence?" -- not "what should the devices do?". So this module asks a model
for exactly three things: a type, a room the sentence names, and a confidence.
It gets no tools, no device ids, no action schema; there is no field in the
reply a device could act on, and the caller turns every verdict into either a
question or a hand-back to the deterministic path.

    command         -> handed back to the cascade (tier 2 / tool loop), where
                       every write still goes through the whitelist, the safety
                       policy and the budget gate
    observation     -> one clarification question, nothing changes
    outside_weather -> the honest "I cannot see the weather" reply
    smalltalk       -> the text-only chat door
    clarification or low confidence -> a question, never an action

Any failure -- model off, offline, timeout, unparseable JSON, unknown type --
returns None and the caller keeps the rules' answer. Offline stays a
first-class case: with the LLM disabled the cascade is exactly what it was
before this module existed.
"""
from __future__ import annotations

import json
from . import llm

VALID_TYPES = ("command", "observation", "smalltalk",
               "outside_weather", "clarification")
VALID_EFFECTS = ("warmer", "colder", "cooler", "drier")

SYSTEM_PROMPT = """You are the intent classifier of a smart-home assistant.
Classify ONE message from the user. Output JSON only, no explanation.

{"type": "...", "room": null, "desired_effect": null, "confidence": 0.0}

type is exactly one of:
- "command": the user asks for a change -- "turn on the AC", "make the bedroom
  warmer", "set the fridge to 3".
- "observation": a statement about how a room or the speaker feels with no
  change asked for -- "I am cold", "it's stuffy in here".
- "outside_weather": a statement or question about the weather outside --
  "today is cold", "will it rain tomorrow".
- "smalltalk": a greeting, thanks, farewell, or chit-chat.
- "clarification": anything else, including a message you are not sure about.

room: the room the message names, or null. Never guess one.
desired_effect: "warmer" | "colder" | "cooler" | "drier" | null.
confidence: 0.0 to 1.0 -- how sure you are of the type.

HARD RULES:
1. You classify only. You have no tools and cannot control anything.
2. A statement about temperature is not automatically a command. "I am cold"
   names no room and asks for no change: that is "observation", not "command".
3. "today is cold" and "it is cold outside" are about the weather, not the
   house: that is "outside_weather".
4. Never invent rooms or effects that are not in the message."""

_FENCE = None  # fence stripping is inline; json payloads from fences are rare


def classify(cfg: dict, text: str) -> dict | None:
    """One JSON round trip, no tools. None on any failure -> keep the rules."""
    if not llm.available(cfg):
        return None
    try:
        content = llm.chat(
            cfg,
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            json_mode=True,
            temperature=0.0,
            max_tokens=120,
            timeout=6,
        ).get("content") or ""
        if content.startswith("```"):
            content = content.strip("`")
            content = content[content.find("{"):]
        raw = json.loads(content)
    except Exception:
        return None
    return _validate(raw)


def _validate(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in VALID_TYPES:
        return None
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    room = raw.get("room")
    if not isinstance(room, str) or not room.strip():
        room = None
    else:
        room = room.strip()
    effect = raw.get("desired_effect")
    if effect not in VALID_EFFECTS:
        effect = None
    return {"type": kind, "room": room, "desired_effect": effect,
            "confidence": confidence}
