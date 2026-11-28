"""Load planner: what to do when a start would take the house over its max kW.

The household sets a maximum whole-house load (FR-8). When a start -- from a
command, an app button, a booked job, the engine itself, or someone pressing
the appliance's own button -- would take the house over it, the rules prepare
every honest plan and the language model picks one:

  allow       it fits; nothing else changes
  make_room   change other running loads first (raise an AC, switch off the
              dehumidifier, pause a Styler / wash / dryer and finish it later),
              then start the requested one
  delay       do not start it now; book it for the off-peak band or later
  stop        refuse it (or, if someone started it by hand, switch it back off)
  stop_book   someone started a movable load by hand: switch it off and book it
  allow_over  it cannot wait: shed what can be shed, allow the start, and go
              over by as little as possible

The model only chooses among listed plans; the plan's steps, the resulting
load and the safety exemptions are computed here, in code. Cooling during a
heat-stress risk, the fridge, air cleaning, the range hood and anything that
is cooking are never touched. With no model, or a bad answer, a fixed order
decides (``choose_by_rules``). Every choice is recorded with its source.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import llm

ALLOW, MAKE_ROOM, DELAY, STOP, STOP_BOOK, OVER = (
    "allow", "make_room", "delay", "stop", "stop_book", "allow_over")

# Never changed to make room: food, air, cooking and the hood that serves it.
PROTECTED_TYPES = frozenset({"refrigerator", "air_purifier", "range_hood", "oven", "cooktop"})
# Always allowed to start (comfort / health); the planner may only make room for them.
NEVER_REFUSED = frozenset({"air_conditioner", "refrigerator", "air_purifier", "range_hood"})
AC_RAISE_C = 2
AC_MAX_C = 28

PLAN_SYSTEM = """You are the load planner of a home energy agent. The household set a
maximum whole-house load in kW. A start would take the house over it. The rules
have prepared the possible plans; each lists exactly what would change and the
whole-house load after it.

Choose ONE plan by its id. Unless the facts say otherwise, prefer:
- in the peak price band, moving a movable load to later;
- otherwise, keeping the household's request by making room with the smallest
  comfort change;
- refusing, or switching off, only what can wait;
- when going over cannot be avoided, the plan that goes over by the least.

Never choose a plan that is not listed. You cannot execute anything yourself.
Answer with JSON only and nothing else:
{"plan": "P1", "reason": "one short clause"}"""


# ----------------------------------------------------------------- helpers

def devices_with_state(adapter) -> list[dict]:
    """The device list with each device's current state attached."""
    out = []
    for d in adapter.list_devices():
        d = dict(d)
        if "state" not in d:
            try:
                d["state"] = adapter.get_state(d["id"]) or {}
            except Exception:
                d["state"] = {}
        out.append(d)
    return out

def _draw(dev: dict) -> float:
    from . import budget
    st = dev.get("state") or {}
    if dev.get("type") == "air_conditioner":
        return budget.power_w(dev, [{"action": "temperature", "value": st.get("target_temp", 24)}])
    return budget.power_w(dev, [{"action": "power", "value": True}])


def _running(dev: dict) -> bool:
    st = dev.get("state") or {}
    return bool(st.get("power")) or st.get("state") == "washing"


def next_slot(now: datetime, band: str, region: dict) -> datetime:
    """Off-peak start in the peak band, otherwise at least 30 min later."""
    from . import budget
    if band == "peak":
        return budget._offpeak_time(now, region)
    return budget._next_slot(now, budget.CAP_RETRY_MINUTES)


def shed_options(devices: list[dict], *, exclude: str, firing: set[str],
                 user_home: bool | None, when: datetime) -> list[dict]:
    """Running loads that may be changed to make room, least comfort cost first."""
    out = []
    for d in devices:
        if d.get("id") == exclude or d.get("type") in PROTECTED_TYPES or not d.get("online", True):
            continue
        if not _running(d):
            continue
        st = d.get("state") or {}
        t = d.get("type")
        if t == "air_conditioner":
            if "heat_health" in firing:
                continue                      # never weaken cooling in heat stress
            temp = int(st.get("target_temp") or 24)
            if temp >= AC_MAX_C:
                continue
            new = min(AC_MAX_C, temp + AC_RAISE_C)
            out.append({"device_id": d["id"], "name": d["name"], "cost": 1,
                        "actions": [{"action": "temperature", "value": new}],
                        "saves_w": (new - temp) * 40.0,
                        "text": f"raise {d['name']} from {temp}° to {new}°",
                        "done": f"raised {d['name']} from {temp}° to {new}°"})
        elif t == "dehumidifier":
            if "mold" in firing:
                continue                      # it is fighting the mold risk
            out.append({"device_id": d["id"], "name": d["name"], "cost": 2,
                        "actions": [{"action": "power", "value": False}],
                        "saves_w": _draw(d), "text": f"switch off {d['name']}",
                        "done": f"switched off {d['name']}"})
        elif t in ("styler", "washer", "dryer", "dish_washer"):
            out.append({"device_id": d["id"], "name": d["name"], "cost": 3,
                        "actions": [{"action": "power", "value": False}],
                        "saves_w": _draw(d),
                        "book": when.isoformat(timespec="minutes"),
                        "text": f"pause {d['name']} and finish it at {when:%H:%M}",
                        "done": f"paused {d['name']} (it finishes at {when:%H:%M})"})
    out.sort(key=lambda o: (o["cost"], -o["saves_w"]))
    return out


# ----------------------------------------------------------------- plans

def build_plans(*, dev: dict, add_w: float, load_w: float, cap_kw: float,
                devices: list[dict], firing: set[str], user_home: bool | None,
                band: str, now: datetime, region: dict, channel: str,
                started_by_hand: bool = False) -> list[dict]:
    """Every honest plan, each with its steps and the load after it (kW)."""
    from . import budget
    cap_w = cap_kw * 1000.0
    later = next_slot(now, band, region)
    sheds = shed_options(devices, exclude=dev["id"], firing=firing,
                         user_home=user_home, when=later)
    total = load_w + (0.0 if started_by_hand else add_w)
    plans: list[dict] = []

    def kw(w: float) -> float:
        return round(w / 1000.0, 2)

    if total <= cap_w:
        return [{"kind": ALLOW, "steps": [], "after_kw": kw(total),
                 "text": f"start {dev['name']}; the house stays at {kw(total)} kW"}]

    # make_room: cheapest comfort changes first, until it fits.
    chosen, freed = [], 0.0
    for s in sheds:
        if total - freed <= cap_w:
            break
        chosen.append(s)
        freed += s["saves_w"]
    if chosen and total - freed <= cap_w:
        plans.append({"kind": MAKE_ROOM, "steps": chosen, "after_kw": kw(total - freed),
                      "text": "; ".join(s["text"] for s in chosen)
                              + (f"; keep {dev['name']} on" if started_by_hand
                                 else f"; then start {dev['name']}")})

    movable = dev.get("type") in budget.DELAYABLE
    if movable and not started_by_hand:
        plans.append({"kind": DELAY, "steps": [], "after_kw": kw(load_w),
                      "book": later.isoformat(timespec="minutes"),
                      "text": f"do not start {dev['name']} now; book it for {later:%H:%M}"})
    if movable and started_by_hand:
        plans.append({"kind": STOP_BOOK, "steps": [], "after_kw": kw(load_w - add_w),
                      "book": later.isoformat(timespec="minutes"),
                      "text": f"switch {dev['name']} off and start it again at {later:%H:%M}"})

    refusable = dev.get("type") not in NEVER_REFUSED and channel != "engine"
    if refusable and not movable:
        plans.append({"kind": STOP, "steps": [],
                      "after_kw": kw(load_w - add_w if started_by_hand else load_w),
                      "text": (f"switch {dev['name']} back off" if started_by_hand
                               else f"do not start {dev['name']}; switch something off first")})

    # Going over is only for what cannot wait or be refused (cooling, the
    # engine's own risk responses); anything else can simply not start.
    if not refusable and not (movable and not started_by_hand and dev.get("type") not in NEVER_REFUSED):
        plans.append({"kind": OVER, "steps": sheds, "after_kw": kw(total - sum(s["saves_w"] for s in sheds)),
                      "text": ("; ".join(s["text"] for s in sheds) + "; " if sheds else "")
                              + f"allow {dev['name']} and go over by "
                              f"{max(0.0, kw(total - sum(s['saves_w'] for s in sheds)) - cap_kw):.2f} kW"})

    for i, p in enumerate(plans, 1):
        p["id"] = f"P{i}"
    return plans


def choose_by_rules(plans: list[dict], band: str) -> dict:
    """The fixed order used without a model (or after a bad answer)."""
    by = {p["kind"]: p for p in plans}
    order = ([DELAY, MAKE_ROOM] if band == "peak" else [MAKE_ROOM, DELAY]) + [STOP_BOOK, OVER, STOP, ALLOW]
    for k in order:
        if k in by:
            return by[k]
    return plans[0]


def choose(cfg: dict | None, plans: list[dict], facts: str, band: str) -> tuple[dict, str, dict]:
    """Ask the model to pick a plan; fall back to the rules. Returns
    (plan, source, trace) where trace holds the real prompt, input and output."""
    from . import budget
    listing = "\n".join(f"- {p['id']}: {p['text']} (house after: {p['after_kw']} kW)" for p in plans)
    user = facts + "\n\nPlans:\n" + listing
    trace = {"prompt": PLAN_SYSTEM, "model_input": user, "model_output": None}
    if len(plans) == 1 or not (cfg and llm.available(cfg)):
        return choose_by_rules(plans, band), budget.SOURCE_RULES, trace
    try:
        msg = llm.chat(cfg, [{"role": "system", "content": PLAN_SYSTEM},
                             {"role": "user", "content": user}],
                       temperature=0, max_tokens=120, json_mode=True, timeout=8)
        text = (msg.get("content") if isinstance(msg, dict) else str(msg)) or ""
        trace["model_output"] = text
        clean = text.strip().strip("`")
        clean = clean[clean.find("{"):clean.rfind("}") + 1] if "{" in clean else clean
        pick = json.loads(clean).get("plan")
        plan = next((p for p in plans if p["id"] == pick), None)
        if plan is None:
            return choose_by_rules(plans, band), budget.SOURCE_OVERRIDE, trace
        return plan, budget.SOURCE_LLM, trace
    except Exception as exc:
        trace["model_output"] = f"(model unavailable: {type(exc).__name__})"
        return choose_by_rules(plans, band), budget.SOURCE_FALLBACK, trace


def facts_text(*, dev: dict, add_w: float, load_w: float, cap_kw: float, band: str,
               user_home: bool | None, firing: set[str], who: str) -> str:
    return (f"Limit: {cap_kw:g} kW. House now: {load_w / 1000:.2f} kW.\n"
            f"Request: {dev['name']} ({dev.get('type')}), {add_w / 1000:.2f} kW, started by {who}.\n"
            f"Price band: {band}. Someone home: {user_home}. "
            f"Risks above 50: {', '.join(sorted(firing)) or 'none'}.")


def _joined(steps: list[dict]) -> str:
    done = [s.get("done", s["text"]) for s in steps]
    return done[0] if len(done) == 1 else ", ".join(done[:-1]) + " and " + done[-1]


def describe(plan: dict, cap_kw: float) -> str:
    """One plain sentence for the household."""
    k = plan["kind"]
    if k == MAKE_ROOM:
        return (f"To stay under your {cap_kw:g} kW limit I {_joined(plan['steps'])} "
                f"(house at {plan['after_kw']} kW).")
    if k == OVER:
        shed = f"I {_joined(plan['steps'])}, but " if plan["steps"] else ""
        over = max(0.0, plan["after_kw"] - cap_kw)
        return (f"This could not wait: {shed}the house is at {plan['after_kw']} kW, "
                f"{over:.2f} kW over your {cap_kw:g} kW limit -- as low as I could keep it.")
    if k == STOP_BOOK:
        return f"That would have taken the house over your {cap_kw:g} kW limit, so I will {plan['text']}."
    if k == STOP:
        return f"That would have taken the house over your {cap_kw:g} kW limit, so I will {plan['text']}."
    if k == DELAY:
        return f"Not now: {plan['text']} (limit {cap_kw:g} kW)."
    return plan["text"]


def run_steps(adapter, steps: list[dict], *, source: str, channel: str = "planner") -> list[str]:
    """Carry out make-room steps through the audit gate; book paused loads."""
    from . import audit, db
    done = []
    for s in steps:
        res = audit.execute(adapter, s["device_id"], s["actions"],
                            context={"channel": channel, "decision_source": source,
                                     "action_kind": "make_room", "tier": "energy"})
        if not res.get("ok"):
            continue
        done.append(s["text"])
        if s.get("book"):
            db.add_job(device_id=s["device_id"], device_name=s["name"],
                       actions=[{"action": "power", "value": True}],
                       rule={"type": "once", "reason": "energy budget DELAY",
                             "decision_source": source},
                       next_run_at=s["book"], source_text="paused to make room under the kW limit")
    return done
