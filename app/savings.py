"""Energy and environment savings, estimated from the audit trail (FR-7).

Every number here is derived from actions that the append-only audit_log
records as *executed* — nothing is counted for a question the user declined
or an action that failed. What would have happened otherwise is not
observable, so each figure is an estimate under the stated assumptions below,
and the interface labels it as such.

Three kinds of saving are kept apart, because they are not the same thing:

  energy   kWh not used at all            (a warmer AC setpoint, a cooktop
                                           switched off, a hot tub-clean
                                           cycle that mold prevention avoided)
  shifted  kWh moved out of the peak band  (a wash paused until the peak ends,
           or out of a kitchen overload     a dryer stopped during cooking) —
                                           the same energy, used later
  money    the energy saving at the household's marginal price plus the shifted peak
           energy at the peak/off-peak price difference

CO2 is counted on energy saved only; shifting load changes cost and peak
demand, not the total consumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import regions

# ---- assumptions (change here; the paper's Table "Assumptions of the
# Savings Estimate" lists the same values and their sources) ----------------
#
# Prices and the grid emission factor depend on the household's country and
# live in regions.py (Korea: KEPCO tariff, 2023 national grid factor; United
# States: EIA average price, PG&E time-of-use, EPA eGRID2023). The energy
# figures below do not depend on the country.

# Appliance energy, from LG Electronics Korea product specifications:
AC_RATED_W = 845.0           # LG Whisen wall AC SQ09MDKWMS, rated cooling draw
AC_SAVING_PER_C = 0.047      # Korea Energy Agency: -4.7 % per +1 degC setpoint
ECO_STEP_C = 2               # eco_ac raises the setpoint 2 degC (policy.py)
ECO_SAVED_W = AC_RATED_W * AC_SAVING_PER_C * ECO_STEP_C   # ~79 W; the simulator uses 40 W/degC (sensors.py)
ECO_CAP_H = 3.0              # count at most three hours per adjustment
COOKTOP_AVOIDED_KWH = 2.0 * 0.25   # LG DIOS induction BEI3ASS4E large zone 2,000 W, ~15 min
TUB_CLEAN_KWH = 2.2 * 0.5          # LG TROMM F24VDLPR 2,200 W heater, ~30 min heating in a tub-clean course
WASH_SHIFT_KWH = 0.5 * 1.0         # wash without heating, 500 W (simulator), ~1 h moved out of the peak
DRYER_SHIFT_KWH = 1.4 * 0.5        # LG TROMM heat-pump dryer RD25GS 1,400 W, ~30 min kept out of cooking

KINDS = ("eco_ac", "cooktop_auto_off", "ventilate_washer", "pause_washer", "stop_dryer")


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    return t.replace(tzinfo=None) if t.tzinfo else t


def period_start(period: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday()) if period == "week" else day


def summarize(rows: list[dict], now: datetime | None = None, region: str | None = None) -> dict:
    """Turn audit rows (oldest first) into a savings summary, priced for a
    country (regions.py)."""
    now = now or datetime.now()
    R = regions.get(region)
    price, peak, offpeak = R["price"], R["peak"], R["offpeak"]
    by: dict[str, dict] = {k: {"kind": k, "count": 0, "kwh_saved": 0.0, "kwh_shifted": 0.0, "money": 0.0} for k in KINDS}
    mold_episodes = 0
    executed = [r for r in rows if r.get("event_type") == "action.executed"]
    for i, r in enumerate(executed):
        kind = r.get("action_kind")
        if kind not in by:
            continue
        b = by[kind]
        b["count"] += 1
        if kind == "eco_ac":
            # Until the same AC is changed again, the demo is reset, or now.
            start = _parse(r["ts"])
            end = now
            for later in rows:
                if _parse(later["ts"]) <= start:
                    continue
                if later.get("event_type") == "scenario.reset" or (
                        later.get("event_type") == "action.executed" and later.get("device_id") == r.get("device_id")):
                    end = _parse(later["ts"])
                    break
            hours = max(0.0, min(ECO_CAP_H, (end - start).total_seconds() / 3600.0))
            kwh = ECO_SAVED_W / 1000.0 * hours
            b["kwh_saved"] += kwh
            b["money"] += kwh * price
        elif kind == "cooktop_auto_off":
            b["kwh_saved"] += COOKTOP_AVOIDED_KWH
            b["money"] += COOKTOP_AVOIDED_KWH * price
        elif kind == "ventilate_washer":
            # One avoided tub-clean per wet episode, however many airing passes.
            mold_episodes += 1
            b["kwh_saved"] += TUB_CLEAN_KWH
            b["money"] += TUB_CLEAN_KWH * price
        elif kind == "pause_washer":
            b["kwh_shifted"] += WASH_SHIFT_KWH
            b["money"] += WASH_SHIFT_KWH * (peak - offpeak)
        elif kind == "stop_dryer":
            b["kwh_shifted"] += DRYER_SHIFT_KWH
    kwh_saved = sum(b["kwh_saved"] for b in by.values())
    kwh_shifted = sum(b["kwh_shifted"] for b in by.values())
    money = sum(b["money"] for b in by.values())
    actions = [dict(b, kwh_saved=round(b["kwh_saved"], 3), kwh_shifted=round(b["kwh_shifted"], 3), money=round(b["money"], R["decimals"]))
               for b in by.values() if b["count"]]
    actions.sort(key=lambda b: (b["kwh_saved"] + b["kwh_shifted"]), reverse=True)
    return {
        "kwh_saved": round(kwh_saved, 2),
        "kwh_shifted": round(kwh_shifted, 2),
        "money_saved": round(money, R["decimals"]),
        "currency": R["currency"], "symbol": R["symbol"], "decimals": R["decimals"],
        "region": R["code"],
        "co2_kg": round(kwh_saved * R["co2_kg_per_kwh"], 2),
        "mold_prevented": mold_episodes,
        "actions": actions,
        "assumptions": {
            "price": price, "price_peak": peak, "price_offpeak": offpeak,
            "peak_hours": R["peak_hours"], "co2_kg_per_kwh": R["co2_kg_per_kwh"],
            "tariff_source": R["tariff_source"], "co2_source": R["co2_source"],
        },
        "estimate": True,
    }


def report(period: str = "week", now: datetime | None = None, region: str | None = None) -> dict:
    from . import db
    now = now or datetime.now()
    start = period_start(period, now)
    # audit timestamps are timezone-aware ISO strings; compare in local time
    since = start.astimezone().isoformat(timespec="milliseconds")
    out = summarize(db.audit_since(since), now, region)
    out["period"] = period
    out["since"] = start.isoformat(timespec="minutes")
    return out
