"""Korean command transliteration for the rule engine.

The soft UI already switches its *interface* to Korean (and Korean speech
recognition), and even shows a Korean example chip. The rule engine, however,
only reads English. This module closes that gap with a closed-vocabulary map:
a Korean request is rewritten into the English phrasing the rules already
understand, then runs through the exact same deterministic path.

Nothing here decides safety. Translation happens before `nlu.parse`, so tier
checks, approval, the audit trail and the temperature limits are untouched.

The words are taken from app/web/soft/app.js (ROOM_KO / TYPE_KO) so what the
user sees on screen is what the parser accepts.
"""
from __future__ import annotations

import re

# Korean syllable blocks (Hangul) plus compatibility jamo.
_HANGUL = re.compile(r"[\uac00-\ud7a3\u3130-\u318f]")

# Ordered longest-first so "켜줘" is replaced before "켜" etc. Replacements are
# plain substrings: Korean has no spaces between a noun and its particle, so
# word-boundary regexes would miss "거실에어컨켜줘".
_WORDS: tuple[tuple[str, str], ...] = (
    # --- greetings / small talk (kept first: whole-sentence replacements)
    ("안녕하세요", "hello"), ("안녕하십니까", "hello"), ("안녕", "hi"),
    ("감사합니다", "thanks"), ("고맙습니다", "thanks"), ("고마워", "thanks"),
    # --- rooms
    ("living room", "living room"),  # no-op guard for already-translated text
    ("거실", "living room"), ("침실", "bedroom"), ("안방", "bedroom"),
    ("침대방", "bedroom"), ("주방", "kitchen"), ("부엌", "kitchen"),
    ("발코니", "balcony"), ("베란다", "balcony"),
    # --- devices (match TYPE_KO in the soft UI)
    ("공기청정기", "air purifier"), ("식기세척기", "dishwasher"),
    ("로봇청소기", "robot vacuum"), ("레인지 후드", "range hood"),
    (" range hood", " range hood"), ("후드", "range hood"),
    ("에어컨", "air conditioner"), ("세탁기", "washing machine"),
    ("건조기", "tumble dryer"), ("냉장고", "refrigerator"),
    ("온수기", "water heater"), ("제습기", "dehumidifier"),
    ("스타일러", "styler"), ("오븐", "oven"), ("쿡탑", "cooktop"),
    ("청소기", "vacuum"), ("가스레인지", "cooktop"),
    # --- requests / actions, longest first
    ("켜 주세요", "turn on"), ("켜주세요", "turn on"), ("켜줘", "turn on"),
    ("켜요", "turn on"), ("켜", "turn on"), ("켜놔", "turn on"),
    ("꺼 주세요", "turn off"), ("꺼주세요", "turn off"), ("꺼줘", "turn off"),
    ("꺼요", "turn off"), ("꺼", "turn off"),
    ("끄줘", "turn off"), ("끄요", "turn off"), ("끄", "turn off"),
    ("정지해줘", "stop"), ("정지해", "stop"), ("정지", "stop"),
    ("멈춰줘", "stop"), ("멈춰", "stop"),
    ("시작해줘", "start"), ("시작해", "start"), ("시작", "start"),
    ("설정해줘", "set"), ("설정해", "set"), ("설정", "set"),
    ("맞춰줘", "set"), ("맞춰", "set"), ("해줘", "set"), ("해요", "set"),
    ("온도", "temperature"), ("높여줘", "raise"), ("올려줘", "raise"),
    ("낮춰줘", "lower"), ("내려줘", "lower"),
    ("따뜻하게", "warm"), ("시원하게", "cool"),
    ("상태", "status"), ("켜져", "on"), ("꺼져", "off"),
    ("그만", "stop"), ("아니요", "no"), ("네", "yes"),
)

# A Korean number followed by 도 ("degrees") -> English degree form.
_DEG = re.compile(r"(\d{1,3})\s*도(?:로|으로)?")


def is_korean(text: str) -> bool:
    return bool(_HANGUL.search(text or ""))


def to_english(text: str) -> str:
    """Rewrite Korean command words into English, leaving other text intact.

    Unknown Korean words are kept as-is: they will fail to parse exactly as an
    unknown English word would, which is the safe outcome.
    """
    s = text or ""
    # Mark the seams with a sentinel so Korean written without spaces
    # ("거실에어컨켜줘") still becomes separate English words. The sentinel is
    # collapsed at the end and cannot occur in natural input.
    seam = "\x00"
    for word, en in _WORDS:
        if word in s:
            s = s.replace(word, f"{seam}{en}{seam}")
    s = _DEG.sub(rf"{seam}\1 degrees{seam}", s)
    out = s.replace(seam, " ")
    return re.sub(r"\s+", " ", out).strip()
