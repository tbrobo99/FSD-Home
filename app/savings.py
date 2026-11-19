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
  money    the energy saving at the average price plus the shifted peak
           energy at the peak/off-peak price difference

CO2 is counted on energy saved only; shifting load changes cost and peak
demand, not the total consumed.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# ---- assumptions (change here; the paper's FR-7 lists the same values) ----
# Prices are demo values in the range of Korean residential tariffs, not a
# specific KEPCO schedule. The emission factor is the national grid factor
# commonly used in Korean greenhouse-gas inventories (kg CO2-eq per kWh).
PRICE_AVG_KRW = 150.0
PRICE_PEAK_KRW = 250.0
PRICE_OFFPEAK_KRW = 110.0
CO2_KG_PER_KWH = 0.4594

ECO_SAVED_W = 200.0          # eco_ac raises a setpoint 2 °C; 100 W per degree (sensors.py)
ECO_CAP_H = 3.0              # count at most three hours per adjustment
COOKTOP_AVOIDED_KWH = 0.45   # 1.8 kW cooktop that would have kept heating ~15 min
TUB_CLEAN_KWH = 1.0          # one hot-water tub-clean cycle avoided per mold episode
WASH_SHIFT_KWH = 0.5         # 0.5 kW wash moved out of the peak band for ~1 h
DRYER_SHIFT_KWH = 0.9        # 1.8 kW dryer kept out of a kitchen overload for ~30 min

KINDS = ("eco_ac", "cooktop_auto_off", "ventilate_washer", "pause_washer", "stop_dryer")


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    return t.replace(tzinfo=None) if t.tzinfo else t


def period_start(period: str, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday()) if period == "week" else day


def summarize(rows: list[dict], now: datetime | None = None) -> dict:
    """Turn audit rows (oldest first) into a savings summary."""
    now = now or datetime.now()
    by: dict[str, dict] = {k: {"kind": k, "count": 0, "kwh_saved": 0.0, "kwh_shifted": 0.0, "krw": 0.0} for k in KINDS}
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
            b["krw"] += kwh * PRICE_AVG_KRW
        elif kind == "cooktop_auto_off":
            b["kwh_saved"] += COOKTOP_AVOIDED_KWH
            b["krw"] += COOKTOP_AVOIDED_KWH * PRICE_AVG_KRW
        elif kind == "ventilate_washer":
            # One avoided tub-clean per wet episode, however many airing passes.
            mold_episodes += 1
            b["kwh_saved"] += TUB_CLEAN_KWH
            b["krw"] += TUB_CLEAN_KWH * PRICE_AVG_KRW
        elif kind == "pause_washer":
            b["kwh_shifted"] += WASH_SHIFT_KWH
            b["krw"] += WASH_SHIFT_KWH * (PRICE_PEAK_KRW - PRICE_OFFPEAK_KRW)
        elif kind == "stop_dryer":
            b["kwh_shifted"] += DRYER_SHIFT_KWH
    kwh_saved = sum(b["kwh_saved"] for b in by.values())
    kwh_shifted = sum(b["kwh_shifted"] for b in by.values())
    krw = sum(b["krw"] for b in by.values())
    actions = [dict(b, kwh_saved=round(b["kwh_saved"], 3), kwh_shifted=round(b["kwh_shifted"], 3), krw=round(b["krw"]))
               for b in by.values() if b["count"]]
    actions.sort(key=lambda b: (b["kwh_saved"] + b["kwh_shifted"]), reverse=True)
    return {
        "kwh_saved": round(kwh_saved, 2),
        "kwh_shifted": round(kwh_shifted, 2),
        "krw_saved": int(round(krw)),
        "co2_kg": round(kwh_saved * CO2_KG_PER_KWH, 2),
        "mold_prevented": mold_episodes,
        "actions": actions,
        "assumptions": {
            "price_avg_krw": PRICE_AVG_KRW, "price_peak_krw": PRICE_PEAK_KRW,
            "price_offpeak_krw": PRICE_OFFPEAK_KRW, "co2_kg_per_kwh": CO2_KG_PER_KWH,
        },
        "estimate": True,
    }


def report(period: str = "week", now: datetime | None = None) -> dict:
    from . import db
    now = now or datetime.now()
    start = period_start(period, now)
    # audit timestamps are timezone-aware ISO strings; compare in local time
    since = start.astimezone().isoformat(timespec="milliseconds")
    out = summarize(db.audit_since(since), now)
    out["period"] = period
    out["since"] = start.isoformat(timespec="minutes")
    return out
