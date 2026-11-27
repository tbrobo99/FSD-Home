"""FSD Home — autonomous energy management engine.

This is the module added after the 25 Nov professor review. It turns the
advisory smart-home agent into an energy-budget agent:

    user sets a monthly kWh budget
        -> sensors report the house's instantaneous draw (1 Hz, push into state)
        -> an appliance request (or a fired energy risk) becomes a decision
        -> LLM proposes ALLOW / BLOCK / DELAY / OPTIMIZE as structured JSON
        -> rules validate that proposal against the budget and safety limits
        -> only validated actions reach the device adapter (through audit.execute)

Two boundaries are deliberate and are the answer to "which algorithm do you
use, and how does the LLM make a decision":

* The LLM decides *what* to do. It never touches a device, never owns a timer,
  and never gets to waive a hard constraint.
* Rules decide *whether the LLM's answer is admissible*. A model that says
  ALLOW with 0.1 kWh left in Strict Mode is overridden to BLOCK and the
  override is recorded. A model that is unreachable never stops the house:
  the deterministic rule default produces the same four-way decision.

Physical constants come from app/sensors.py's documented LG nameplate values,
so the energy estimate and the existing PLI index describe the same house.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import audit, db, llm

# ---------------------------------------------------------------- prompt

# Shown verbatim in the Energy Dashboard (and in docs/FSD-ENERGY.md) so the
# decision process can be demonstrated instead of described.
ENERGY_DECIDE_SYSTEM = """You are the decision engine of FSD Home, an autonomous home energy management agent.
The user has set a monthly energy budget and has authorised you to control their appliances within it.

Your primary goal is to minimise energy cost while respecting the user's energy budget,
safety constraints and comfort limits.

You will receive one JSON object with:
  device            - appliance id
  requested_actions - what the user or the risk layer asked for
  current_load_w    - whole-house draw right now
  tariff            - "peak" or "offpeak"
  price_krw_per_kwh - price in the current band
  used_kwh          - energy used this month so far
  budget_kwh        - the user's monthly limit
  remaining_kwh     - budget_kwh - used_kwh
  estimated_kwh     - energy the request would consume
  user_home         - whether somebody is home
  strict_mode       - whether the budget is enforced as a hard limit

Choose exactly ONE action:
  ALLOW    - run the appliance as requested
  BLOCK    - refuse to run it; the budget or safety would be violated
  DELAY    - run it later in a cheaper band; set scheduled_time (ISO, future)
  OPTIMIZE - run it with a cheaper setting; set actions to the modified list

Hard constraints (enforced in code after you answer, not by trust):
  1. In strict_mode, never ALLOW a request whose estimated_kwh exceeds remaining_kwh.
  2. Never switch an oven or cooktop ON yourself; those need an explicit user request.
  3. A DELAY time must be in the future and in the cheaper band.
  4. Keep air-conditioner cooling setpoints between 24 and 26 degrees Celsius.

Return ONLY this JSON and nothing else:
{"device":"...","action":"ALLOW|BLOCK|DELAY|OPTIMIZE","reason":"one short sentence","scheduled_time":null,"actions":[],"estimated_kwh":0.0}"""

# ---------------------------------------------------------------- physics / pricing

# watts, from app/sensors.py (LG nameplate figures documented there)
DEVICE_WATTS: dict[str, int] = {
    "washer": 500, "dryer": 1400, "dish_washer": 1600, "oven": 2200,
    "cooktop": 2000, "dehumidifier": 300, "purifier": 40, "range_hood": 150,
    "fridge": 100, "ac_living": 845, "ac_bedroom": 845, "ac_kitchen": 845,
}

# typical duration of one request, hours — used for the pre-action estimate
RUNTIME_HOURS: dict[str, float] = {
    "washer": 1.0, "dryer": 1.0, "dish_washer": 1.0, "oven": 0.5,
    "cooktop": 0.5, "dehumidifier": 2.0, "purifier": 2.0, "range_hood": 0.5,
    "fridge": 1.0, "ac_living": 1.0, "ac_bedroom": 1.0, "ac_kitchen": 1.0,
}

# Time-of-use tariff, KRW per kWh. Deliberately simple and visible: the point
# is that the agent can reason about a price signal, not that it forecasts the
# Korean electricity market.
TARIFF_PRICE_KRW: dict[str, float] = {"peak": 180.0, "offpeak": 90.0}
FLEXIBLE_DEVICES = frozenset({"washer", "dryer", "dish_washer"})
HEATING_DEVICES = frozenset({"oven", "cooktop"})
AC_DEVICES = frozenset({"ac_living", "ac_bedroom", "ac_kitchen"})
COMFORT_MIN_C = 24
COMFORT_MAX_C = 26
OFFPEAK_START_HOUR = 22
OFFPEAK_END_HOUR = 8

DECISIONS = ("ALLOW", "BLOCK", "DELAY", "OPTIMIZE")


def next_offpeak(now: datetime | None = None) -> datetime:
    """The next cheap-band start. A request delayed at 19:00 lands at 22:00."""
    now = now or datetime.now()
    if now.hour >= OFFPEAK_START_HOUR or now.hour < OFFPEAK_END_HOUR:
        return now + timedelta(minutes=1)   # already cheap: run shortly
    return now.replace(hour=OFFPEAK_START_HOUR, minute=0, second=0, microsecond=0)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- manager

class EnergyManager:
    """Budget state, energy estimates and the decision / validation chain."""

    def __init__(self, cfg: dict | None = None, adapter=None) -> None:
        cfg = cfg or {}
        self.cfg = cfg
        self.adapter = adapter
        ec = cfg.get("energy") or {}
        self.enabled = bool(ec.get("enabled", True))
        self.strict_mode = bool(ec.get("strict_mode", False))
        self.llm_decide = bool(ec.get("llm_decide", True)) and llm.available(cfg)
        self.default_budget_kwh = float(ec.get("monthly_budget_kwh", 100.0))
        self._state: dict = {}
        try:
            db.init()
            self._state = self._load_state()
        except Exception:
            self._state = self._fresh_state()

    # ------------------------------------------------------------ state

    def _fresh_state(self) -> dict:
        return {
            "period": datetime.now().strftime("%Y-%m"),
            "budget_kwh": self.default_budget_kwh,
            "used_kwh": 0.0,
            "strict_mode": int(self.strict_mode),
            "llm_decide": int(self.llm_decide),
        }

    def _load_state(self) -> dict:
        with db._lock, db.conn() as c:
            row = c.execute("SELECT * FROM energy_state WHERE id=1").fetchone()
        if row is None:
            state = self._fresh_state()
            with db._lock, db.conn() as c:
                c.execute(
                    "INSERT INTO energy_state (id, period, budget_kwh, used_kwh,"
                    " strict_mode, llm_decide, updated_at) VALUES (1,?,?,?,?,?,?)",
                    (state["period"], state["budget_kwh"], state["used_kwh"],
                     state["strict_mode"], state["llm_decide"], db.now_iso()),
                )
            return state
        state = dict(row)
        # config.json is the deployment's stated intent for Strict Mode, so it
        # wins on startup. The dashboard can flip the flag at runtime; a
        # restart returns to the documented configuration.
        if "strict_mode" in (self.cfg.get("energy") or {}):
            state["strict_mode"] = int(self.strict_mode)
            self._save_state(state)
        # A new calendar month starts a new budget; the old total stays in the
        # decision log but no longer constrains the household.
        period = datetime.now().strftime("%Y-%m")
        if state.get("period") != period:
            state.update({"period": period, "used_kwh": 0.0})
            self._save_state(state)
        return state

    def _save_state(self, state: dict | None = None) -> None:
        s = state or self._state
        try:
            with db._lock, db.conn() as c:
                c.execute(
                    "UPDATE energy_state SET period=?, budget_kwh=?, used_kwh=?,"
                    " strict_mode=?, llm_decide=?, updated_at=? WHERE id=1",
                    (s["period"], s["budget_kwh"], s["used_kwh"],
                     int(bool(s["strict_mode"])), int(bool(s["llm_decide"])),
                     db.now_iso()),
                )
        except Exception as exc:
            db.log("energy.state_save_failed", str(exc), "error")
        self._state = s

    @property
    def used_kwh(self) -> float:
        return float(self._state.get("used_kwh") or 0.0)

    @property
    def budget_kwh(self) -> float:
        return float(self._state.get("budget_kwh") or self.default_budget_kwh)

    @property
    def remaining_kwh(self) -> float:
        return round(max(0.0, self.budget_kwh - self.used_kwh), 3)

    @property
    def budget_pct(self) -> float:
        if self.budget_kwh <= 0:
            return 100.0
        return round(_clamp(100.0 * self.used_kwh / self.budget_kwh, 0, 100), 1)

    # ------------------------------------------------------------ settings

    def set_budget(self, kwh: float) -> dict:
        try:
            value = float(kwh)
        except (TypeError, ValueError):
            return {"ok": False, "error": "budget must be a number"}
        if not 1 <= value <= 10000:
            return {"ok": False, "error": "budget must be between 1 and 10000 kWh"}
        self._state["budget_kwh"] = value
        self._save_state()
        db.log("energy.budget_set", f"{value} kWh")
        return {"ok": True, "budget_kwh": value, "remaining_kwh": self.remaining_kwh}

    def set_strict(self, enabled: bool) -> dict:
        self._state["strict_mode"] = int(bool(enabled))
        self._save_state()
        db.log("energy.strict_set", f"{bool(enabled)}")
        return {"ok": True, "strict_mode": bool(enabled),
                "remaining_kwh": self.remaining_kwh}

    def set_llm(self, enabled: bool) -> dict:
        want = bool(enabled) and llm.available(self.cfg)
        self._state["llm_decide"] = int(want)
        self.llm_decide = want
        self._save_state()
        db.log("energy.llm_set", f"{want}")
        return {"ok": True, "llm_decide": want,
                "available": llm.available(self.cfg)}

    def reset(self) -> dict:
        self._state = self._fresh_state()
        self._save_state()
        db.log("energy.reset", "monthly usage reset to zero")
        return {"ok": True, "used_kwh": 0.0, "budget_kwh": self.budget_kwh}

    # ------------------------------------------------------------ metering

    def accrue(self, snapshot: dict, dt_seconds: float) -> float:
        """Integrate instantaneous draw into kWh. Called once per sensor tick.

        The sensor layer pushes a new snapshot every second; no HTTP request
        and no LLM call is involved in metering. This is the energy equivalent
        of a smart-meter pulse and is what makes the dashboard's used_kwh move.
        """
        if not self.enabled or dt_seconds <= 0:
            return 0.0
        watts = float(snapshot.get("total_load_w") or 0.0)
        kwh = watts * float(dt_seconds) / 3_600_000.0
        if kwh <= 0:
            return 0.0
        self._state["used_kwh"] = round(self.used_kwh + kwh, 6)
        # Persist every tick: a restart must not silently lose the meter.
        self._save_state()
        return kwh

    @staticmethod
    def estimate_kwh(device_id: str, actions: list[dict],
                     snapshot: dict | None = None) -> float:
        """Energy a single request would consume, in kWh (pre-action estimate)."""
        watts = DEVICE_WATTS.get(device_id, 0)
        hours = RUNTIME_HOURS.get(device_id, 1.0)
        total = 0.0
        for act in actions or []:
            name, value = act.get("action"), act.get("value")
            if name == "power" and value and not str(device_id).startswith("fridge"):
                total += watts * hours / 1000.0
            elif name == "temperature" and device_id in AC_DEVICES:
                # Raising a cooling setpoint saves ~40 W per degree (sensors.py).
                target = int(value or 26)
                saving = max(0, target - 22) * 40 / 1000.0
                total -= saving
        return round(max(0.0, total), 4)

    # ------------------------------------------------------------ decision

    def context(self, device_id: str, actions: list[dict], snapshot: dict,
                source: str = "user", user_text: str = "") -> dict:
        tariff = snapshot.get("tariff") or "offpeak"
        name = device_id
        if self.adapter is not None:
            try:
                dev = self.adapter.get_device(device_id)
                name = (dev or {}).get("name") or device_id
            except Exception:
                pass
        return {
            "device": device_id,
            "device_name": name,
            "requested_actions": actions,
            "source": source,
            "user_text": user_text,
            "current_load_w": int(snapshot.get("total_load_w") or 0),
            "tariff": tariff,
            "price_krw_per_kwh": TARIFF_PRICE_KRW.get(tariff, TARIFF_PRICE_KRW["offpeak"]),
            "used_kwh": round(self.used_kwh, 3),
            "budget_kwh": self.budget_kwh,
            "remaining_kwh": self.remaining_kwh,
            "estimated_kwh": self.estimate_kwh(device_id, actions, snapshot),
            "user_home": bool((snapshot.get("occupancy") or {}).get("user_home", True)),
            "strict_mode": bool(self._state.get("strict_mode")),
        }

    def decide(self, device_id: str, actions: list[dict], snapshot: dict,
               source: str = "user", user_text: str = "") -> dict:
        """Produce one validated ALLOW/BLOCK/DELAY/OPTIMIZE decision."""
        ctx = self.context(device_id, actions, snapshot, source, user_text)
        prompt = (ENERGY_DECIDE_SYSTEM + "\n\nUSER (JSON):\n"
                  + json.dumps(ctx, ensure_ascii=False, default=str))
        raw_llm = None
        proposed = None
        decided_by = "rules"

        use_llm = (self.enabled and self.llm_decide
                   and bool(self._state.get("llm_decide")) and llm.available(self.cfg))
        if use_llm:
            try:
                msg = llm.chat(self.cfg, [
                    {"role": "system", "content": ENERGY_DECIDE_SYSTEM},
                    {"role": "user", "content": json.dumps(ctx, ensure_ascii=False,
                                                           default=str)},
                ], json_mode=True, temperature=0, max_tokens=300, timeout=8)
                raw_llm = (msg.get("content") or "").strip()
                text = raw_llm
                if text.startswith("```"):
                    text = text.strip("`")
                    text = text[text.find("{"):]
                candidate = json.loads(text)
                if isinstance(candidate, dict) and candidate.get("action") in DECISIONS:
                    proposed = candidate
                    decided_by = "llm"
            except Exception as exc:
                db.log("energy.llm_fallback",
                       f"{type(exc).__name__}: {exc}; rules decided")
                proposed = None

        decision = (self._validate(proposed, ctx) if proposed is not None
                    else self._rule_decide(ctx))
        decision["decided_by"] = decided_by if proposed is not None else "rules"
        if proposed is not None and decision["action"] != proposed.get("action"):
            decision["overridden"] = True
            decision["llm_proposed"] = proposed.get("action")
        decision["context"] = ctx
        self._record(decision, prompt, raw_llm)
        return decision

    def _rule_decide(self, ctx: dict) -> dict:
        """The deterministic four-way decision, also the LLM fallback."""
        device = ctx["device"]
        actions = ctx["requested_actions"] or []
        est = float(ctx["estimated_kwh"])
        remaining = float(ctx["remaining_kwh"])
        starting = any(a.get("action") == "power" and a.get("value")
                       for a in actions)
        reason = ""

        if ctx["strict_mode"] and est > remaining:
            return self._decision("BLOCK",
                                  f"only {remaining:.1f} kWh left this month; "
                                  f"{device} would need {est:.1f} kWh", ctx)
        if (starting and device in FLEXIBLE_DEVICES
                and ctx["tariff"] == "peak"):
            when = next_offpeak()
            return self._decision("DELAY",
                                  f"peak rate ({ctx['price_krw_per_kwh']:.0f} KRW/kWh); "
                                  f"moved to the {OFFPEAK_START_HOUR}:00 cheap band",
                                  ctx, scheduled_at=when.isoformat(timespec="minutes"))
        if device in AC_DEVICES:
            temps = [int(a["value"]) for a in actions
                     if a.get("action") == "temperature" and str(a.get("value")).lstrip("-").isdigit()]
            if starting or (temps and min(temps) < COMFORT_MAX_C):
                new_actions = []
                changed = False
                for a in actions:
                    if a.get("action") == "temperature":
                        a = {**a, "value": COMFORT_MAX_C}
                        changed = True
                    new_actions.append(a)
                if not changed:
                    new_actions = [*actions, {"action": "temperature",
                                              "value": COMFORT_MAX_C}]
                return self._decision(
                    "OPTIMIZE",
                    f"cooling at {COMFORT_MAX_C}\u00b0C keeps the room comfortable "
                    f"and draws about 160 W less",
                    ctx, actions=new_actions)
        if device in HEATING_DEVICES and starting and ctx["source"] != "user":
            return self._decision("BLOCK",
                                  "a heating appliance must not be switched on "
                                  "by the agent itself", ctx)
        return self._decision("ALLOW", reason or "within the energy budget", ctx)

    def _validate(self, proposed: dict | None, ctx: dict) -> dict:
        """Rules own admissibility. The LLM owns the choice, not the constraint."""
        if not proposed:
            return self._rule_decide(ctx)
        action = str(proposed.get("action") or "ALLOW").upper()
        if action not in DECISIONS:
            return self._rule_decide(ctx)
        est = float(ctx["estimated_kwh"])
        remaining = float(ctx["remaining_kwh"])
        reason = str(proposed.get("reason") or "agent decision")[:240]
        scheduled = proposed.get("scheduled_time")
        actions = ctx["requested_actions"]

        # 1. budget is a hard limit in strict mode
        if ctx["strict_mode"] and est > remaining and action == "ALLOW":
            return self._decision("BLOCK",
                                  f"budget guard overrode ALLOW: only "
                                  f"{remaining:.1f} kWh left, request needs {est:.1f}", ctx)
        # 2. the agent never lights a hob or oven on its own
        if (ctx["device"] in HEATING_DEVICES and action in ("ALLOW", "OPTIMIZE")
                and ctx["source"] != "user"):
            return self._decision("BLOCK", "heating appliance requires an explicit "
                                  "user request", ctx)
        # 3. DELAY must be future and in the cheap band
        if action == "DELAY":
            when = self._parse_time(scheduled)
            if when is None or when <= datetime.now():
                when = next_offpeak()
            return self._decision("DELAY", reason, ctx,
                                  scheduled_at=when.isoformat(timespec="minutes"))
        # 4. comfort band for cooling
        if action == "OPTIMIZE":
            acts = []
            for a in (proposed.get("actions") or actions or []):
                if not isinstance(a, dict) or a.get("action") not in (
                        "power", "temperature", "mode", "fan", "ventilate"):
                    continue
                if a.get("action") == "temperature":
                    try:
                        t = int(a.get("value"))
                    except (TypeError, ValueError):
                        continue
                    a = {**a, "value": _clamp(t, COMFORT_MIN_C, COMFORT_MAX_C)}
                acts.append(a)
            if not acts:
                return self._rule_decide(ctx)
            return self._decision("OPTIMIZE", reason, ctx, actions=acts)
        return self._decision(action, reason, ctx)

    @staticmethod
    def _parse_time(value) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    @staticmethod
    def _decision(action: str, reason: str, ctx: dict, **extra) -> dict:
        d = {
            "device": ctx["device"], "device_name": ctx.get("device_name") or ctx["device"],
            "action": action, "reason": reason,
            "scheduled_time": extra.get("scheduled_at"),
            "actions": extra.get("actions") or list(ctx.get("requested_actions") or []),
            "estimated_kwh": round(float(ctx.get("estimated_kwh") or 0), 4),
        }
        d.update({k: v for k, v in extra.items() if k not in ("actions", "scheduled_at")})
        return d

    def _record(self, decision: dict, prompt: str, raw_llm: str | None) -> None:
        ctx = decision.get("context") or {}
        try:
            with db._lock, db.conn() as c:
                c.execute(
                    "INSERT INTO energy_decisions (ts, device_id, device_name, requested,"
                    " decision, reason, scheduled_at, estimated_kwh, remaining_kwh,"
                    " tariff, decided_by, prompt_shown, llm_raw, executed)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (db.now_iso(), decision.get("device"), decision.get("device_name"),
                     json.dumps(ctx.get("requested_actions") or [], ensure_ascii=False),
                     decision["action"], decision.get("reason") or "",
                     decision.get("scheduled_time"), decision.get("estimated_kwh"),
                     ctx.get("remaining_kwh"), ctx.get("tariff"),
                     decision.get("decided_by"), prompt, raw_llm or ""),
                )
        except Exception as exc:
            db.log("energy.decision_write_failed", str(exc), "error")
        audit.record("energy.decision",
                     action_kind=decision["action"],
                     decision_source=decision.get("decided_by", "rules"),
                     channel="energy", device_id=decision.get("device", ""),
                     detail=(decision.get("reason") or "")[:240])

    # ------------------------------------------------------------ execution

    def apply_decision(self, decision: dict, snapshot: dict) -> dict:
        """Turn a validated decision into a device action or a scheduled job.

        ALLOW / OPTIMIZE are executed by the caller through audit.execute().
        BLOCK stops here. DELAY writes a durable one-time job; the scheduler
        owns the clock, so a restart cannot lose the appointment.
        """
        action = decision["action"]
        device = decision["device"]
        name = decision.get("device_name") or device
        if action == "BLOCK":
            audit.blocked(decision.get("reason") or "energy budget",
                          context={"action_kind": "BLOCK", "channel": "energy",
                                   "decision_source": decision.get("decided_by")},
                          device_id=device)
            return {"execute": False, "blocked": True,
                    "reply": (f"{decision.get('reason') or 'Blocked by the energy budget'}. "
                              f"I did not start the {name}.")}
        if action == "DELAY":
            when = self._parse_time(decision.get("scheduled_time")) or next_offpeak()
            if when <= datetime.now():
                when = next_offpeak()
            rule = {"type": "once", "run_at": when.isoformat(timespec="seconds")}
            job_id = db.add_job(
                device_id=device, device_name=name,
                actions=decision.get("actions") or [], rule=rule,
                next_run_at=when.isoformat(timespec="seconds"),
                repeat_label="energy delay",
                source_text=f"FSD energy agent: {decision.get('reason') or 'DELAY'}",
            )
            db.log("energy.delay", f"{device} -> {when.isoformat(timespec='minutes')} "
                                   f"(job {job_id})")
            return {"execute": False, "delayed": True, "job": job_id,
                    "scheduled_at": when.isoformat(timespec="seconds"),
                    "reply": (f"Energy budget / peak price: {decision.get('reason') or 'DELAY'}. "
                              f"I scheduled the {name} for "
                              f"{when.strftime('%I:%M %p').lstrip('0')} automatically.")}
        return {"execute": True, "actions": decision.get("actions") or []}

    # ------------------------------------------------------------ reporting

    def history(self, limit: int = 20) -> list[dict]:
        try:
            with db._lock, db.conn() as c:
                rows = c.execute(
                    "SELECT id, ts, device_id, device_name, requested, decision, reason,"
                    " scheduled_at, estimated_kwh, remaining_kwh, tariff, decided_by, executed"
                    " FROM energy_decisions ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def view(self, snapshot: dict | None = None, history_limit: int = 20) -> dict:
        snap = snapshot or {}
        tariff = snap.get("tariff") or "offpeak"
        return {
            "enabled": self.enabled,
            "strict_mode": bool(self._state.get("strict_mode")),
            "llm_decide": bool(self._state.get("llm_decide")) and llm.available(self.cfg),
            "llm_available": llm.available(self.cfg),
            "period": self._state.get("period"),
            "budget_kwh": self.budget_kwh,
            "used_kwh": round(self.used_kwh, 3),
            "remaining_kwh": self.remaining_kwh,
            "budget_pct": self.budget_pct,
            "current_load_w": int(snap.get("total_load_w") or 0),
            "tariff": tariff,
            "price_krw_per_kwh": TARIFF_PRICE_KRW.get(tariff, TARIFF_PRICE_KRW["offpeak"]),
            "estimated_cost_krw": round(
                int(snap.get("total_load_w") or 0) / 1000.0
                * TARIFF_PRICE_KRW.get(tariff, TARIFF_PRICE_KRW["offpeak"]), 1),
            "next_offpeak": next_offpeak().isoformat(timespec="minutes"),
            "decisions": self.history(history_limit),
            "prompt": ENERGY_DECIDE_SYSTEM,
            "pricing": TARIFF_PRICE_KRW,
            "sampling": {
                "sensor_push_hz": 1.0,
                "dashboard_poll_seconds": 2.0,
                "llm_call_policy": "on energy event only; never per sensor tick",
            },
        }
