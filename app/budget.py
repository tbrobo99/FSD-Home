"""Monthly energy budget — one gate for every path that can start a device.

FR-8. Three quantities are deliberately kept separate, because mixing them is
how an energy demo becomes a fiction:

* **watts / kilowatts** — instantaneous draw, from `sensors.snapshot()['total_load_w']`.
* **kilowatt-hours** — energy actually used, the time integral of watts. This
  is what a monthly budget is measured against.
* **money** — kWh multiplied by the region tariff (`regions.py`). A price is
  never used as a proxy for energy and energy is never called a price.

`can_start()` is the only place that decides ALLOW / BLOCK / DELAY. It is called
by the proactive loop, the chat command path, the raw control API and the
scheduler. The scheduler calls it again *at execution time*, because a job
queued under a budget that still had room must not run after the budget is
spent.

The timer itself is `scheduler.py`; this module never sleeps and never owns a
thread. A DELAY is a decision plus a job, not a model promise.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import db, regions

DEFAULT_BUDGET_KWH = 100.0

# Loads that can sensibly be moved to another time. A device already running
# is never assumed to support pause/resume — `can_start` refuses to DELAY it.
DELAYABLE = frozenset({"washer", "dryer", "dish_washer", "styler"})

# Safe tier: these may never be started autonomously, whatever the budget says.
# This is the hard constraint the pre-authorisation does not remove.
AUTONOMY_FORBIDDEN = frozenset({"cooktop", "oven"})

# Nominal run time used only for an *estimate*. It is labelled as an estimate
# everywhere it is shown; it is not the measured consumption of a real cycle.
NOMINAL_HOURS = {
    "washer": 1.0, "dryer": 1.0, "dish_washer": 1.0,
    "air_conditioner": 1.0, "oven": 0.5, "cooktop": 0.5,
    "refrigerator": 1.0, "dehumidifier": 1.0,
    "air_purifier": 1.0, "range_hood": 0.25, "styler": 1.0,
}

# Max kW cap (instantaneous draw, not kWh). The cap moves chores; it never
# touches cooling, food or air, and it never blocks the proactive engine's
# own risk responses. Off until the user sets a limit.
CAP_EXEMPT = frozenset({"refrigerator", "air_conditioner", "air_purifier", "range_hood"})
CAP_RETRY_MINUTES = 30
CAP_MAX_RETRIES = 12          # 12 × 30 min = give up after 6 hours
MAX_KW_RANGE = (0.5, 50.0)

# Latest whole-house draw seen by accrue(), in watts. The 1 Hz loop updates it,
# so a start can be checked against the load the house has right now.
_last_load_w: float | None = None

ALLOW, BLOCK, DELAY = "ALLOW", "BLOCK", "DELAY"

# Decision provenance. Only an actual model reply gets SOURCE_LLM.
SOURCE_LLM = "llm"
SOURCE_FALLBACK = "rules_fallback"
SOURCE_OVERRIDE = "rules_override"
SOURCE_RULES = "rules"
SOURCES = (SOURCE_LLM, SOURCE_FALLBACK, SOURCE_OVERRIDE, SOURCE_RULES)


def period_key(now: datetime | None = None) -> str:
    """Monthly billing key. Changing this string resets the budget cleanly."""
    return (now or datetime.now()).strftime("%Y-%m")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def configured(cfg: dict | None) -> tuple[float, bool]:
    e = (cfg or {}).get("energy") or {}
    try:
        limit = float(e.get("budget_kwh", DEFAULT_BUDGET_KWH))
    except (TypeError, ValueError):
        limit = DEFAULT_BUDGET_KWH
    return max(0.0, limit), bool(e.get("strict_mode", True))


def _ensure_period(now: datetime, cfg: dict | None) -> dict:
    """Create the row for this month if it is missing; never touch an old one."""
    key = period_key(now)
    limit, strict = configured(cfg)
    with db.conn() as c:
        row = c.execute("SELECT * FROM energy_state WHERE period_key=?", (key,)).fetchone()
        if row is None:
            c.execute(
                "INSERT INTO energy_state (period_key, used_kwh, budget_kwh, strict_mode,"
                " last_accrual_ts, updated_at) VALUES (?,?,?,?,NULL,?)",
                (key, 0.0, limit, int(strict), now.isoformat(timespec="seconds")),
            )
            row = c.execute("SELECT * FROM energy_state WHERE period_key=?", (key,)).fetchone()
        return dict(row)


def accrue(total_load_w: float, now: datetime | None = None,
           cfg: dict | None = None) -> None:
    """Add energy since the last sample: kWh = watts × elapsed_seconds / 3.6e6.

    Called from the 1 Hz loop. Sampling by elapsed time rather than by tick
    count means a paused or throttled process does not under-count, and a call
    made twice in the same second adds nothing.
    """
    global _last_load_w
    now = now or datetime.now()
    _last_load_w = max(0.0, float(total_load_w or 0.0))
    key = period_key(now)
    row = _ensure_period(now, cfg)
    last = _parse(row.get("last_accrual_ts"))
    # A clock jump backwards, or a sample from a previous month, is ignored.
    if last is None or last > now or period_key(last) != key:
        with db.conn() as c:
            c.execute("UPDATE energy_state SET last_accrual_ts=?, updated_at=? WHERE period_key=?",
                      (now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), key))
        return
    seconds = max(0.0, (now - last).total_seconds())
    kwh = max(0.0, float(total_load_w or 0.0)) * seconds / 3_600_000.0
    if kwh <= 0:
        return
    with db.conn() as c:
        c.execute(
            "UPDATE energy_state SET used_kwh=used_kwh+?, last_accrual_ts=?, updated_at=?"
            " WHERE period_key=?",
            (kwh, now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), key),
        )


def state(now: datetime | None = None, cfg: dict | None = None,
          region: str | None = None) -> dict:
    """Snapshot for the dashboard. `power_kw` and `used_kwh` are different units."""
    now = now or datetime.now()
    row = _ensure_period(now, cfg)
    limit = float(row["budget_kwh"])
    used = float(row["used_kwh"])
    remaining = max(0.0, limit - used)
    R = regions.get(region)
    return {
        "period": row["period_key"],
        "budget_kwh": round(limit, 2),
        "used_kwh": round(used, 3),
        "remaining_kwh": round(remaining, 3),
        "used_pct": round(100.0 * used / limit, 1) if limit > 0 else 0.0,
        "strict_mode": bool(row["strict_mode"]),
        "price_per_kwh": R["price"],
        "currency": R["currency"], "symbol": R["symbol"], "decimals": R["decimals"],
        "estimated_cost": round(used * R["price"], R["decimals"]),
        "updated_at": row.get("updated_at"),
        "max_kw": max_kw(cfg),
        "load_kw": None if _last_load_w is None else round(_last_load_w / 1000.0, 2),
    }


def max_kw(cfg: dict | None = None) -> float | None:
    """The household's instantaneous cap in kW, or None when it is off.

    A value the user set (stored in SQLite) wins over config.json.
    """
    raw = None
    try:
        with db.conn() as c:
            row = c.execute("SELECT value FROM energy_settings WHERE key='max_kw'").fetchone()
        if row is not None:
            raw = row["value"]
        else:
            raw = ((cfg or {}).get("energy") or {}).get("max_kw")
    except Exception:
        raw = ((cfg or {}).get("energy") or {}).get("max_kw")
    try:
        v = float(raw) if raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def set_max_kw(kw: float | None, now: datetime | None = None) -> float | None:
    """Set (or clear, with None / 0) the max kW cap. Returns the stored value."""
    now = now or datetime.now()
    value = None
    if kw:
        value = float(kw)
        lo, hi = MAX_KW_RANGE
        if not lo <= value <= hi:
            raise ValueError(f"max_kw must be between {lo:g} and {hi:g}")
    with db.conn() as c:
        c.execute(
            "INSERT INTO energy_settings (key, value, updated_at) VALUES ('max_kw', ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            ("" if value is None else str(value), now.isoformat(timespec="seconds")),
        )
    return value


def set_budget(kwh: float, strict_mode: bool | None = None,
               now: datetime | None = None) -> dict:
    """User pre-authorisation: setting the budget is the authorisation act."""
    now = now or datetime.now()
    _ensure_period(now, {"energy": {"budget_kwh": kwh,
                                    "strict_mode": strict_mode if strict_mode is not None else True}})
    with db.conn() as c:
        if strict_mode is None:
            c.execute("UPDATE energy_state SET budget_kwh=?, updated_at=? WHERE period_key=?",
                      (float(kwh), now.isoformat(timespec="seconds"), period_key(now)))
        else:
            c.execute("UPDATE energy_state SET budget_kwh=?, strict_mode=?, updated_at=?"
                      " WHERE period_key=?",
                      (float(kwh), int(strict_mode), now.isoformat(timespec="seconds"), period_key(now)))
    return state(now)


def power_w(device: dict | None, actions: list[dict]) -> float:
    """Instantaneous draw change implied by a proposed action, in watts."""
    if not device:
        return 0.0
    dtype = device.get("type", "")
    caps = set()
    for a in actions:
        caps.add(a.get("action"))
    if dtype == "air_conditioner":
        base = 845.0
        temp = next((a.get("value") for a in actions if a.get("action") == "temperature"), None)
        if temp is not None:
            base = max(127.0, 845.0 - (int(temp) - 22) * 40.0)
        return base if ("power" not in caps or any(
            a.get("action") == "power" and a.get("value") for a in actions)) else 0.0
    if dtype == "refrigerator":
        return 100.0
    if dtype == "washer":
        return 500.0
    if dtype == "dryer":
        return 1400.0
    if dtype == "oven":
        return 2200.0
    if dtype == "cooktop":
        return 2000.0
    if dtype == "dish_washer":
        return 1600.0
    if dtype == "dehumidifier":
        return 300.0
    if dtype == "range_hood":
        return 150.0
    if dtype == "air_purifier":
        return 40.0
    if dtype == "styler":
        return 1000.0   # nominal (see sensors.BASE_LOAD_W)
    return 0.0


def estimate_kwh(device: dict | None, actions: list[dict]) -> float:
    """Estimated energy of one proposed start. An estimate, never a measurement."""
    if not device:
        return 0.0
    hours = NOMINAL_HOURS.get(device.get("type", ""), 0.5)
    return round(power_w(device, actions) * hours / 1000.0, 4)


def _offpeak_time(now: datetime, region: dict) -> datetime:
    """Next start of the off-peak band. Falls back to 22:00 local."""
    hours = region.get("peak_hours") or "16-22"
    try:
        end = int(str(hours).split("-")[1])
    except (IndexError, ValueError):
        end = 22
    candidate = now.replace(hour=end, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def can_start(adapter, device_id: str, actions: list[dict], *,
              now: datetime | None = None, cfg: dict | None = None,
              region: str | None = None, load_w: float | None = None,
              channel: str = "") -> dict:
    """The single ALLOW / BLOCK / DELAY decision used by all four paths.

    `autonomous=True` means no human is in the loop; the hard safety set still
    applies. DELAY is offered only for a device that is *not yet running* —
    we do not assume a real washer can be paused and resumed.
    """
    now = now or datetime.now()
    dev = adapter.get_device(device_id)
    s = state(now, cfg, region)
    est = estimate_kwh(dev, actions or [])
    R = regions.get(region)
    band = "peak" if _in_peak(now, R) else "offpeak"

    base = {
        "device_id": device_id,
        "device": (dev or {}).get("name") or device_id,
        "power_kw": round(power_w(dev, actions or []) / 1000.0, 3),
        "estimated_kwh": est,
        "remaining_kwh": s["remaining_kwh"],
        "budget_kwh": s["budget_kwh"],
        "used_kwh": s["used_kwh"],
        "band": band,
        "strict_mode": s["strict_mode"],
    }
    if not dev:
        # Device existence is the adapter's concern, not the budget's. A fake
        # adapter in a unit test, or a new backend that only implements apply(),
        # must not be turned into a budget BLOCK. `audit.execute` still rejects
        # a genuinely missing device.
        return {**base, "decision": ALLOW,
                "reason": "budget cannot identify device; deferred to adapter"}
    if not dev.get("online", True):
        return {**base, "decision": ALLOW,
                "reason": "offline; deferred to adapter"}

    starts = any(a.get("action") == "power" and a.get("value") for a in (actions or []))
    if not starts and not any(a.get("action") == "temperature" for a in (actions or [])):
        return {**base, "decision": ALLOW, "reason": "not a starting action"}

    # Hard safety set: pre-authorisation never extends to heating appliances.
    if dev.get("type") in AUTONOMY_FORBIDDEN:
        return {**base, "decision": BLOCK,
                "reason": f"{dev['type']} is outside autonomous authority"}

    if s["strict_mode"] and est > 0 and est > s["remaining_kwh"]:
        return {**base, "decision": BLOCK,
                "reason": (f"estimated {est:.2f} kWh exceeds "
                           f"{s['remaining_kwh']:.2f} kWh remaining this month")}

    # Max kW cap: would this start push the whole house over the user's limit?
    # The load planner (app/loadplan.py) prepares every honest plan -- make
    # room, delay, refuse, or go over as little as possible -- and the model,
    # or the fixed rule order without one, picks one.
    cap = max_kw(cfg)
    load = _last_load_w if load_w is None else float(load_w)
    if (s["strict_mode"] and cap and starts and load is not None
            and not _is_running(dev)):
        add = power_w(dev, actions or [])
        after_kw = (load + add) / 1000.0
        if after_kw > cap:
            return _plan_over_cap(adapter, dev, add, load, cap, band, now, R,
                                  channel, cfg, {**base, "limit": "max_kw", "max_kw": cap,
                                                 "load_kw": round(load / 1000.0, 2),
                                                 "after_kw": round(after_kw, 2)})

    # DELAY only for a not-yet-running, movable load during the peak band.
    if (band == "peak" and dev.get("type") in DELAYABLE and starts
            and not _is_running(dev)):
        when = _offpeak_time(now, R)
        return {**base, "decision": DELAY,
                "reason": f"peak band; moved to off-peak at {when.strftime('%H:%M')}",
                "scheduled_at": when.isoformat(timespec="minutes")}

    return {**base, "decision": ALLOW, "reason": "within budget"}


# What the engine last saw: risks above threshold and presence. The planner
# uses it to keep cooling during heat stress and the dehumidifier during mold.
_house = {"firing": set(), "user_home": None}


def adjust_load(delta_w: float) -> None:
    """Keep the latest load honest between ticks when the planner switches
    something on or off, so a second start in the same second sees it."""
    global _last_load_w
    if _last_load_w is not None:
        _last_load_w = max(0.0, _last_load_w + float(delta_w))


def observe(firing: set[str], user_home: bool | None) -> None:
    _house["firing"] = set(firing)
    _house["user_home"] = user_home


def _plan_over_cap(adapter, dev, add, load, cap, band, now, region, channel, cfg, over):
    from . import loadplan
    try:
        devices = loadplan.devices_with_state(adapter)
    except Exception:
        devices = []
    plans = loadplan.build_plans(dev=dev, add_w=add, load_w=load, cap_kw=cap,
                                 devices=devices, firing=_house["firing"],
                                 user_home=_house["user_home"], band=band, now=now,
                                 region=region, channel=channel)
    facts = loadplan.facts_text(dev=dev, add_w=add, load_w=load, cap_kw=cap, band=band,
                                user_home=_house["user_home"], firing=_house["firing"],
                                who={"engine": "the house itself", "scheduler": "a booked job",
                                     "chat": "the household (command)", "tool": "the household (command)",
                                     "manual": "the household (app control)"}.get(channel, channel))
    plan, source, trace = loadplan.choose(cfg, plans, facts, band)
    why = (f"starting it would take the house to {over['after_kw']:.1f} kW, "
           f"over your {cap:g} kW limit")
    extra = {"plan": plan["kind"], "plan_text": plan["text"], "source": source, "trace": trace,
             "plan_after_kw": plan["after_kw"]}
    k = plan["kind"]
    if k == loadplan.DELAY:
        return {**over, **extra, "decision": DELAY, "label": "DELAY", "reason": why,
                "scheduled_at": plan["book"]}
    if k == loadplan.STOP:
        return {**over, **extra, "decision": BLOCK, "label": "BLOCK",
                "reason": why + "; switch something off first or raise the limit"}
    note = loadplan.describe(plan, cap)
    if k == loadplan.MAKE_ROOM:
        return {**over, **extra, "decision": ALLOW, "label": "MAKE_ROOM",
                "reason": note, "pre_steps": plan["steps"], "note": note}
    if k == loadplan.OVER:
        return {**over, **extra, "decision": ALLOW, "label": "ALLOW_OVER",
                "reason": note, "pre_steps": plan["steps"], "note": note}
    return {**over, **extra, "decision": ALLOW, "reason": plan["text"]}


def _next_slot(now: datetime, minutes: int) -> datetime:
    """`minutes` from now, rounded up to the next half hour."""
    t = now + timedelta(minutes=minutes)
    t = t.replace(second=0, microsecond=0)
    if t.minute not in (0, 30):
        t += timedelta(minutes=(30 - t.minute % 30))
    return t


def _in_peak(now: datetime, region: dict) -> bool:
    hours = region.get("peak_hours")
    if not hours:
        return False
    try:
        a, b = (int(x) for x in str(hours).split("-")[:2])
    except (ValueError, IndexError):
        return False
    h = now.hour
    return (a <= h < b) if a < b else (h >= a or h < b)


def _is_running(dev: dict) -> bool:
    return bool(dev.get("state", {}).get("power") or
                dev.get("state", {}).get("cycle") == "washing")


def gate(adapter, device_id: str, actions: list[dict], *,
         now: datetime | None = None, cfg: dict | None = None,
         region: str | None = None, channel: str = "device",
         judge: str = SOURCE_RULES, context: dict | None = None,
         prompt: str | None = None, model_input: str | None = None,
         model_output: str | None = None) -> dict:
    """Check the budget, append the decision, then return the verdict.

    A BLOCK here happens *before* `audit.execute`, so `write-before-act` still
    holds for everything that actually reaches an adapter.
    """
    now = now or datetime.now()
    v = can_start(adapter, device_id, actions, now=now, cfg=cfg, region=region,
                  channel=channel)
    trace = v.get("trace") or {}
    record_decision(
        v.get("label", v["decision"]), v.get("source", judge), device_id, v["reason"],
        estimated_kwh=v.get("estimated_kwh"), remaining_kwh=v.get("remaining_kwh"),
        channel=channel, prompt=prompt or trace.get("prompt"),
        model_input=model_input or trace.get("model_input"),
        model_output=model_output or trace.get("model_output"),
    )
    # Make room before the caller starts the requested load. Every change goes
    # through the audit gate; a paused load is booked to finish later.
    if v["decision"] == ALLOW and v.get("pre_steps"):
        from . import loadplan
        v["made_room"] = loadplan.run_steps(adapter, v["pre_steps"],
                                            source=v.get("source", judge))
        adjust_load(-sum(s["saves_w"] for s in v["pre_steps"]))
    if v["decision"] == ALLOW and v.get("limit") == "max_kw":
        adjust_load(v.get("power_kw", 0.0) * 1000.0)
    return v


def record_decision(decision: str, source: str, device_id: str, reason: str, *,
                    estimated_kwh: float | None = None,
                    remaining_kwh: float | None = None,
                    channel: str = "device", prompt: str | None = None,
                    model_input: str | None = None, model_output: str | None = None,
                    now: datetime | None = None) -> None:
    """Append provenance to both the append-only audit log and the decision table."""
    now = now or datetime.now()
    ts = now.astimezone().isoformat(timespec="milliseconds")
    if source not in SOURCES:
        source = SOURCE_RULES
    with db.conn() as c:
        c.execute(
            "INSERT INTO energy_decisions (ts, device_id, decision, source, reason,"
            " prompt, model_input, model_output, estimated_kwh, remaining_kwh, channel)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, device_id, decision, source, reason, prompt, model_input, model_output,
             estimated_kwh, remaining_kwh, channel),
        )
    try:
        db.audit_write(
            "energy.decision",
            decision_source=source,
            channel=channel,
            device_id=device_id,
            execution_status=decision.lower(),
            detail=(f"{decision} {device_id}: {reason}"
                    + (f" [prompt={_short(prompt)} input={_short(model_input)}"
                       f" output={_short(model_output)}]" if prompt or model_input or model_output else "")),
        )
    except Exception:
        # The decision table is already durable; a failed secondary write must
        # not abort the housekeeping tick.
        pass


def _short(text: str | None, limit: int = 400) -> str:
    if not text:
        return ""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "…"


def decisions(limit: int = 50) -> list[dict]:
    with db.conn() as c:
        rows = c.execute(
            "SELECT * FROM energy_decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def tariff_cost(kwh: float, region: str | None = None,
                now: datetime | None = None) -> float:
    """Money is derived from kWh, never the other way round."""
    R = regions.get(region)
    price = R.get("peak") if (_in_peak(now or datetime.now(), R) and regions.has_tou(R)) \
        else R["price"]
    return round(float(kwh or 0.0) * price, R["decimals"])
