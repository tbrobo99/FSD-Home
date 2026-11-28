"""Proactive decision engine (Phase 2).

The loop, once a second:

    sensors.tick -> risk.evaluate -> policy.candidates -> DECIDE -> execute / ask

Where the LLM sits in that chain is the whole design. It is not the timer, not
the sensor, and not the safety gate — those are deterministic code, and Phase 1
already argued why a model must never be any of them. The LLM owns exactly the
decisions rules cannot express:

  * which of the candidate actions to take, and which to leave alone
  * in what order, when a mold risk and a thermal risk fire together

Phrasing a Tier-2 question used to be a third job. It is now a template by
default: the model took ~2 s and left out the five-minute deadline every time,
and the template says all four things a senior needs (what is wrong, what will
change, how long they have, what to say). `llm_phrase` turns the model back on
for anyone who wants the richer sentence.

That is a real decision, not polishing: given the same candidates the model may
drop one because the dehumidifier is offline, or abstain entirely because the
trend says the drum is already drying. Its output is validated against a
whitelist before anything moves — it can be wrong about *what* to do, but it
cannot corrupt the house, and a parse failure falls back to the safe default
(Tier 1 executes, Tier 2 is only ever proposed).

Tier-2 execution goes through ApprovalQueue.confirm(), and only the user's
"yes" gets there.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime

from . import approval, audit, budget, db, events, llm, policy, preference, regions, risk, sensors

DECIDE_SYSTEM = """You are the decision layer of a proactive smart-home agent for a
household that includes a senior. Sensors have flagged a risk; rules have
proposed candidate actions. Your job is to choose.

Choose only from the candidates, and list them in the order they should run.
You may:
- take some and leave others (a candidate whose device is offline or already in
  the wanted state should be left alone),
- reorder them when the order matters,
- take none, if the evidence says the situation is resolving on its own.

You cannot execute anything yourself.

Answer with the kind names you chose, in order, as JSON only and nothing else:
{"chosen": ["candidate_kind", "..."]}

Use an empty list, {"chosen": []}, when the right answer is to do nothing."""

# The paragraph that used to sit above ("Tier-1 actions you choose run
# automatically; Tier-2 actions you choose are put to the user for approval and
# never run without a yes") is gone on purpose. It was true and it was the wrong
# thing to tell the model: given a Tier-2 candidate it would leave it off the
# list, run after run, because asking the user reads as a burden. Measured, 8/8
# runs on the oven scenario: with the sentence, the model chose only the range
# hood; without it, both the hood and the dryer delay. A model that will not
# propose the Tier-2 action is a model that never exercises the product's whole
# point. Nothing about safety depended on that sentence — `_intervene` routes
# Tier-2 to ApprovalQueue and only `confirm()` can execute it, whatever the
# model says.

# FR-8: the energy addendum extends DECIDE_SYSTEM instead of replacing it.
# The original role, whitelist rule and JSON contract stay intact — the model
# still chooses only from rule-generated candidates. What changes is the
# information it must weigh and the four verdicts a *start* may receive.
# Kept as a separate string so the base prompt can be shown to a professor
# unchanged, with this block visibly added on top.
ENERGY_DECISION_ADDENDUM = """

ENERGY BUDGET (when the risk kind is "energy"):
The household pre-authorised autonomous energy control by setting a monthly
kWh budget. You still choose only from the listed candidates; you never invent
an action. For each candidate that would START or raise the load of a device:
  - ALLOW  — the estimated kWh fits the remaining budget.
  - BLOCK  — the estimate would exceed the remaining budget, or the device is
             outside autonomous authority (cooktop, oven).
  - DELAY  — the device is not running yet and is movable (washer, dryer,
             dishwasher) and the band is peak. Name the off-peak time.
  - OPTIMIZE — a reversible setpoint change saves energy without stopping a plan.
Never DELAY a device that is already running: a real appliance may not support
pause and resume. Return the same JSON shape, with each chosen entry as an
object: {"kind": "...", "verdict": "ALLOW|BLOCK|DELAY|OPTIMIZE",
"reason": "one short clause", "scheduled_at": "ISO time if DELAY"}.
A BLOCK verdict you return is honoured; it is never overridden into a start."""

QUESTION_SYSTEM = """You are writing one short question for an elderly homeowner.
The house wants to take an action that changes their plans, so it must ask
first. Rules:
- one question, at most two short sentences, no technical terms
- name the appliance and what will change, and the deadline for answering
- offer a one-word reply (yes / no)
- warm and plain, never alarming
Output the question text only."""


def _clean(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    return text


def _join(items: list[str]) -> str:
    """'a', 'a and b', 'a, b and c' — the way a person says a list."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    # A label can contain its own "and" (a past-tense report such as
    # "turned the dryer off, and left it for you to restart"). Joining those
    # with another "and" gives one sentence with three of them, which reads as
    # a single long instruction rather than two.
    if any(" and " in i for i in items):
        return "; ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _plain(r: risk.Risk, snap: dict) -> str:
    """The same finding in words a senior can act on.

    The index score, the extrapolation and the wattage belong in the payload,
    where the audit trail and the specification can see them. They do not
    belong in a sentence read aloud to someone who has never heard of an index
    — "Thermal overload 100/100 in the kitchen (31.5 °C, predicted 31.5 °C in
    30 min)" is a report, not a sentence, and it was the wrong thing to say to
    the person the product exists for.
    """
    if r.kind == "mold":
        hours = float(r.drivers.get("sealed_hours") or 0.0)
        if hours >= 1:
            return (f"The washing machine has been shut for {hours:.0f} hours "
                    f"with wet washing still inside it")
        return "The washing machine is shut with wet washing still inside it"

    if r.kind == "heat_health":
        room = str(r.drivers.get("room") or "room").lower()
        return (f"It feels like {r.current:.0f}\u00b0C in the {room} \u2014 "
                f"hot and humid enough to be hard on the body")

    if r.kind == "energy":
        band = "at the peak rate" if r.drivers.get("tariff") == "peak" else "right now"
        return f"The house is drawing {r.current / 1000:.1f} kW {band}"

    running = [name for name, key in (("the dryer", "dryer"), ("the oven", "oven"))
               if snap["appliances"].get(key, {}).get("power")]
    if running:
        return (f"The kitchen is at {r.current:.0f}\u00b0C and "
                f"{_join(running)} {'are' if len(running) > 1 else 'is'} adding heat")
    return f"The kitchen is getting warm \u2014 it is {r.current:.0f}\u00b0C now"


def _devices(ap) -> str:
    """The appliances an approval is about, named for a person."""
    names = sorted({a.get("device_name") or a.get("device_id") or "an appliance"
                    for a in (ap.actions or [])})
    return _join(names) if names else "an appliance"


class ProactiveEngine:
    """Watches the simulated house and intervenes under the risk-graded policy."""

    def __init__(self, adapter, cfg: dict) -> None:
        pcfg = cfg.get("proactive", {}) or {}
        self.adapter = adapter
        self.cfg = cfg
        self.tick_seconds = float(pcfg.get("tick_seconds", 1.0))
        self.cooldown_seconds = float(pcfg.get("cooldown_seconds", 180))
        # How long the house stays quiet about a risk the user has already
        # answered — "no", or the fail-safe running out. Without it, always
        # putting the Tier-2 question to the user would mean asking the same
        # question every five minutes for as long as the condition lasts.
        self.quiet_seconds = float(pcfg.get("quiet_seconds", 1800))
        # Preference learning records a user's repeated explicit answers so a
        # future version could ask less often. It is OFF by default and never
        # changes what this engine executes: every Tier-2 answer still goes
        # through confirm()/decline() exactly as FR-4 requires.
        # FR-8: pre-authorisation. Setting a monthly energy budget is the user's
        # authorisation for the house to act inside it. ON by default for the
        # energy scenario only; other risks keep the Tier-2 question. Turning
        # this off restores the old "always ask" behaviour.
        self.preauthorised = bool((cfg.get("energy") or {}).get("preauthorised", True))
        self.learn_enabled = preference.learning_enabled(cfg)
        if self.learn_enabled:
            try:
                preference.init()
            except Exception as exc:
                db.log("preference.init_failed", str(exc), "error")
                self.learn_enabled = False
        self.learn_threshold = int((cfg.get("preference") or {}).get("threshold", 3))
        self.llm_decide = bool(pcfg.get("llm_decide", True)) and llm.available(cfg)
        # Off by default. The template question already names the situation, the
        # change, the deadline and the one-word answer; the model's version was
        # measured at 1.6–3.0 s (median 2.0 s, n=4) and consistently dropped the
        # deadline the prompt asks it to include. Two seconds and a worse
        # sentence is not a trade worth making in the path that a fail-safe
        # clock is running against. Flip this on if a richer question is ever
        # worth the wait.
        self.llm_phrase = bool(pcfg.get("llm_phrase", False)) and llm.available(cfg)

        self.sensors = sensors.SensorSimulator(tick_seconds=self.tick_seconds)
        # Every successful device write -- from any path -- moves the simulated
        # house, so the whole-house load the planner sees is the real one.
        audit.add_listener(self.sensors.note_action)
        # Starts the house did not make -- someone pressed the appliance's own
        # button -- are noticed by comparing power states between ticks.
        self._power_seen: dict[str, bool] = {}
        self._our_writes: dict[str, float] = {}
        audit.add_listener(self._note_write)
        self.approvals = approval.ApprovalQueue(
            ttl_seconds=policy.APPROVAL_TTL_SECONDS)

        # FR-5 cooktop safeguards. The simulator has no stove sensor, so the
        # local calendar and the oven's power state are the only honest
        # "cooking detected" signals. Both limits are monotonic wall-clock
        # seconds, never the number of LLM calls or ticks.
        self.cooktop_require_presence = bool(pcfg.get("cooktop_require_presence", True))
        self.cooktop_no_cook_timeout = float(pcfg.get("cooktop_no_cook_timeout", 300))
        self.cooktop_max_runtime = float(pcfg.get("cooktop_max_runtime", 1800))

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cooldown: dict[str, float] = {}     # risk kind -> monotonic deadline
        self._active: dict[str, dict] = {}        # risk kind -> last intervention
        self._last_risks: list = []               # every evaluated risk, last tick
        self._last_snapshot: dict = {}
        self._logged_risks: set[str] = set()      # de-duplicate risk.triggered rows
        self._cooktop_on_since: float | None = None
        self._cooktop_last_cooking: float | None = None
        self._pending_dryer_reminder = False

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.sensors.start()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="proactive")
        self._thread.start()
        db.log("proactive.start",
               f"engine started (llm_decide={'on' if self.llm_decide else 'off'})")

    def stop(self) -> None:
        self._stop.set()
        self.sensors.stop()

    def _loop(self) -> None:
        while not self._stop.wait(self.tick_seconds):
            try:
                self.tick()
            except Exception as exc:
                # The engine watches the house; it must not be able to break it.
                db.log("proactive.error", f"{type(exc).__name__}: {exc}", "error")

    # ---------------------------------------------------------------- one pass

    def tick(self, now: datetime | None = None) -> dict:
        """One observe-decide-act pass. Returns what happened (for tests/demos)."""
        now = now or datetime.now()
        snap = self.sensors.snapshot()
        self.sensors.record(snap)
        self._last_snapshot = snap
        # FR-8: integrate measured instantaneous draw into the monthly kWh.
        # This is the only writer of used_kwh; a restart resumes from SQLite.
        try:
            budget.accrue(snap.get("total_load_w", 0.0), now=now, cfg=self.cfg)
        except Exception as exc:
            db.log("budget.accrue_failed", str(exc), "error")

        evaluated = risk.evaluate(snap, self.sensors.history, now)
        self._last_risks = evaluated            # live scores for the dashboard
        fired = [r for r in evaluated if r.score >= r.threshold]
        events: list[dict] = []
        fired_kinds = {r.kind for r in fired}
        budget.observe(fired_kinds, (snap.get("occupancy") or {}).get("user_home"))
        try:
            events.extend(self._watch_manual_starts(snap, now, fired_kinds))
        except Exception as exc:
            db.log("planner.manual_failed", f"{type(exc).__name__}: {exc}", "error")
        # `_active` describes what the house is doing about a risk *now*. Once
        # the risk clears it must go, or the status line keeps reporting an
        # overload that ended minutes ago (FR-4 truthful reporting).
        for kind in list(self._active):
            if kind not in fired_kinds:
                del self._active[kind]

        # FR-6: one `risk.triggered` row when a risk crosses its threshold, not
        # one per second for as long as it lasts. The set is cleared when the
        # risk goes away, so a later recurrence is a new event.
        self._logged_risks &= fired_kinds
        for r in fired:
            if r.kind in self._logged_risks:
                continue
            self._logged_risks.add(r.kind)
            try:
                audit.record("risk.triggered", risk_kind=r.kind,
                             risk_score=round(r.score, 1), detail=r.headline)
            except Exception as exc:
                db.log("audit.write_failed", f"risk.triggered: {exc}", "error")

        pending_kinds = {a.risk_kind for a in self.approvals.pending()}
        for r in fired:
            if self._cooling(r.kind):
                continue
            if r.kind in pending_kinds:
                continue  # already waiting on the user for this risk
            events.append(self._intervene(r, snap, now))

        for ap in self.approvals.expire_due(now):
            events.append({"type": "expired", "approval": ap.id})
            self._cooldown[ap.risk_kind] = time.monotonic() + self.quiet_seconds
            db.add_message("system",
                           f"\u23f3 I asked about {_devices(ap)} and did not hear back, "
                           f"so I left it alone. Anything already done stays as it is.",
                           {"approval": ap.to_dict()})

        # FR-5: the safeguards and the follow-up notice also run on ticks where
        # no risk fired. A risk *clearing* is an event, not an absence of one.
        events.extend(self._housekeeping(snap, fired_kinds, now))
        return {"ts": snap["ts"], "risks": [r.kind for r in fired], "events": events}

    # ---------------------------------------------------------------- intervene

    def _intervene(self, r: risk.Risk, snap: dict, now: datetime) -> dict:
        cands = policy.candidates(r, snap)
        # FR-3: an unknown kind is blocked and recorded, never quietly treated
        # as Tier 2 by `tier_of()`'s fail-safe default and then put to the user.
        allowed = [c for c in cands if c.kind in policy.ACTION_TIERS]
        for c in cands:
            if c.kind not in policy.ACTION_TIERS:
                audit.blocked(f"unknown action kind {c.kind}",
                              context={"risk_kind": r.kind,
                                       "risk_score": round(r.score, 1),
                                       "action_kind": c.kind, "tier": "tier2",
                                       "decision_source": "rules"},
                              device_id=c.device_id)
        cands = allowed
        if not cands:
            return {"type": "no_candidates", "risk": r.kind}

        chosen, decided_by = self._decide(r, cands, snap)
        tier1 = [c for c in chosen if policy.tier_of(c.kind) == policy.TIER1]

        # FR-8: the energy scenario runs under pre-authorisation. Setting the
        # monthly budget IS the authorisation, so `pause_washer` (a former
        # Tier-2 candidate) no longer needs a Yes. The budget gate below is the
        # new limit; hard safety (`budget.AUTONOMY_FORBIDDEN`) still applies.
        # Non-energy risks keep the original Tier-2 question — a hot cooktop
        # is not made safe by a kWh budget.
        preauthorised = r.kind == "energy" and self.preauthorised
        energy_tier2 = [c for c in chosen if policy.tier_of(c.kind) == policy.TIER2] \
            if preauthorised else []
        tier2 = [c for c in cands if policy.tier_of(c.kind) == policy.TIER2] \
            if not preauthorised else []

        executed = []
        runnable = tier1 + energy_tier2
        for c in runnable:
            # Algorithm 1 revalidates before *any* adapter call. `candidates()`
            # is state-gated (a hood already on is not proposed again), so a
            # candidate the fresh policy would not raise is a stale action.
            allowed_now, why = self._revalidate_candidate(c, r, snap)
            ctx = self._context(r, c, decided_by)
            if not allowed_now:
                audit.blocked(why, context=ctx, device_id=c.device_id)
                executed.append({"kind": c.kind, "label": c.label, "ok": False,
                                 "error": why, "blocked": True})
                db.log("proactive.tier1_blocked", f"{r.kind}: {c.kind} -> {why}")
                continue

            # The single budget gate, before any adapter call. A rule that
            # refuses what the model chose is recorded as rules_override —
            # this is the third provenance value the professor asked to see.
            verdict = budget.gate(
                self.adapter, c.device_id, c.actions, now=now, cfg=self.cfg,
                channel="engine", judge=decided_by)
            if verdict["decision"] == budget.BLOCK:
                audit.blocked(verdict["reason"],
                              context={**ctx, "decision_source": budget.SOURCE_OVERRIDE},
                              device_id=c.device_id)
                executed.append({"kind": c.kind, "label": c.label, "ok": False,
                                 "error": verdict["reason"], "blocked": True})
                db.log("proactive.budget_blocked",
                       f"{r.kind}: {c.kind} -> {verdict['reason']}")
                continue
            if verdict["decision"] == budget.DELAY:
                self._queue_delay(c, verdict, now, decided_by)
                executed.append({"kind": c.kind, "label": c.label, "ok": True,
                                 "delayed": True, "at": verdict.get("scheduled_at")})
                continue

            res = audit.execute(self.adapter, c.device_id, c.actions, context=ctx)
            ok = bool(res.get("ok"))
            executed.append({"kind": c.kind, "label": c.label, "ok": ok,
                             "error": res.get("error"),
                             "audit_blocked": bool(res.get("audit_blocked"))})
            db.log("proactive.tier1",
                   f"{r.kind}: {c.label} -> {'ok' if ok else res.get('error')}")
            if ok:
                events.emit("action", "Actioned automatically", c.label,
                            risk_kind=r.kind, tier=policy.TIER1)

        # FR-4: one approval, one action, one question, one approval_id. A
        # "yes" to stopping the dryer can never authorise the cooktop.
        approval_ids: list[str] = []
        questions: list[str] = []
        for c in tier2:
            question = self._phrase(r, [c], snap)
            ap = self.approvals.propose(
                risk_kind=r.kind,
                question=question,
                summary=c.label,
                actions=[_candidate_dict(c)],
                risk_score=round(r.score, 1),
                action_kind=c.kind,
                decision_source=decided_by,
                tier=policy.TIER2,
                proposed_state=_device_state(snap, c.device_id),
            )
            approval_ids.append(ap.id)
            questions.append(question)
        if questions:
            # Live notification: the page pushes this to the browser's system
            # tray while it is open. Best-effort; a failure here never blocks
            # the approval card or the safety loop.
            events.emit("approval", "Your home needs an answer",
                        questions[0], risk_kind=r.kind,
                        approval_ids=approval_ids)
            # One announcement in the transcript; the interface renders one
            # card per question. Two text-to-speech prompts back to back would
            # cut each other off, so the spoken aid covers the batch once.
            db.add_message("system", "\n".join(questions), {
                "approvals": [self.approvals.get(i).to_dict() for i in approval_ids],
                "approval": self.approvals.get(approval_ids[0]).to_dict(),
            })

        # FR-4: report only what the adapter confirmed. A failed call is said
        # out loud; a blocked one (state changed) is simply not claimed.
        done = [c.done for c, e in zip(tier1, executed) if e.get("ok")]
        failed = [c for c, e in zip(tier1, executed)
                  if not e.get("ok") and not e.get("blocked")]
        head = _plain(r, snap)
        if failed:
            tail = " ".join(f"I tried to {c.label}, but {c.device_name} did not "
                            f"respond." for c in failed)
            body = (f"\U0001f6e1 {head}. "
                    + (f"So I {_join(done)}. " if done else "") + tail)
        elif done:
            body = f"\U0001f6e1 {head}. So I {_join(done)}."
        elif tier2:
            body = (f"\U0001f6e1 {head}. Changing that moves your plans, "
                    f"so I am asking you first.")
        else:
            body = f"\U0001f6e1 {head}. I checked \u2014 nothing needs doing right now."
        db.add_message("system", body, {
            "risk": r.kind, "score": round(r.score, 1), "drivers": r.drivers,
            # The technical line stays in the payload, not in the sentence: the
            # spec and the audit trail need the number, the user does not.
            "headline": r.headline,
            "decided_by": decided_by, "approvals": approval_ids,
            "approval": approval_ids[0] if approval_ids else None,
            "executed": executed,
        })

        self._cooldown[r.kind] = time.monotonic() + self.cooldown_seconds
        self._active[r.kind] = {"score": r.score, "at": now.isoformat(timespec="seconds"),
                                "executed": [c.kind for c in tier1],
                                "approvals": approval_ids,
                                "approval": approval_ids[0] if approval_ids else None}
        return {"type": "intervened", "risk": r.kind, "decided_by": decided_by,
                "tier1": [c.kind for c in tier1], "tier2": [c.kind for c in tier2],
                "approvals": approval_ids,
                "approval": approval_ids[0] if approval_ids else None}

    # ---------------------------------------------------------------- the LLM's job

    def _decide(self, r: risk.Risk, cands: list, snap: dict):
        """Choose among candidates. Returns (chosen_in_order, decided_by).

        FR-8 provenance: the returned source is one of `budget.SOURCES`. A real
        model reply is `llm`; a parse failure or an unreachable model is
        `rules_fallback`; a rule that refuses what the model picked is
        `rules_override`. The exact prompt, input and output are appended to the
        decision table so "the LLM decided" can be checked, not asserted.
        """
        b = budget.state(cfg=self.cfg)
        energy = r.kind == "energy"
        system = DECIDE_SYSTEM + (ENERGY_DECISION_ADDENDUM if energy else "")
        user = (
            f"Risk: {r.headline}\n"
            f"Drivers: {json.dumps(r.drivers, ensure_ascii=False)}\n"
            f"User home: {snap['occupancy'].get('user_home')}\n\n"
            + (f"Budget: {b['used_kwh']:.3f} of {b['budget_kwh']:.1f} kWh used, "
               f"{b['remaining_kwh']:.3f} kWh left, strict={b['strict_mode']}.\n"
               f"Tariff band: {snap.get('tariff')}; "
               f"instantaneous draw: {snap.get('total_load_w', 0) / 1000:.2f} kW.\n\n"
               if energy else "")
            + "Candidates:\n" + "\n".join(
                f"- kind={c.kind} tier={policy.tier_of(c.kind)} "
                f"device={c.device_name} does: {c.label} (why: {c.reason})"
                for c in cands)
        )
        if not self.llm_decide:
            self._trace_decision(r, cands, budget.SOURCE_RULES, system, user,
                                 "(model disabled)")
            return list(cands), budget.SOURCE_RULES
        # Names, not numbers. Asking for `[1, 3]` instead of the kind names was
        # measured ~0.7 s cheaper and cost the product its whole point: over six
        # runs the model answered "hood only" every time and never proposed the
        # Tier-2 action, where the named schema asked for both. A cheaper wrong
        # decision is not cheaper. max_tokens stays a runaway guard, not a
        # latency lever.
        raw_output = ""
        try:
            msg = llm.chat(self.cfg, [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ], json_mode=True, temperature=0, max_tokens=200 if energy else 80)
            raw_output = msg.get("content") or ""
            raw = json.loads(_clean(raw_output))
        except Exception as exc:
            db.log("proactive.decide_fallback",
                   f"{type(exc).__name__}: {exc}; took the rule default")
            self._trace_decision(r, cands, budget.SOURCE_FALLBACK, system, user,
                                 raw_output or f"{type(exc).__name__}: {exc}")
            return list(cands), budget.SOURCE_FALLBACK

        # A response without the key is a parse we do not trust. A response with
        # the key and an empty list is a decision — the model is saying there is
        # nothing worth doing. Those two must not be confused: the first falls
        # back to the rule default, the second is honoured.
        if not isinstance(raw, dict) or "chosen" not in raw:
            db.log("proactive.decide_fallback",
                   "no 'chosen' key; took the rule default")
            self._trace_decision(r, cands, budget.SOURCE_FALLBACK, system, user,
                                 raw_output or "no 'chosen' key")
            return list(cands), budget.SOURCE_FALLBACK

        kinds, verdicts = [], {}
        for item in raw.get("chosen") or []:
            # Two shapes: a bare kind (legacy) or an energy object with a
            # verdict. Both are accepted; the verdict never widens authority.
            if isinstance(item, dict):
                k = item.get("kind")
                if item.get("verdict"):
                    verdicts[str(k)] = str(item["verdict"]).upper()
            else:
                k = item
            if isinstance(k, str):
                kinds.append(k.strip())

        if not kinds:
            db.log("proactive.decide", f"{r.kind}: abstained (nothing worth doing)")
            self._trace_decision(r, cands, budget.SOURCE_LLM, system, user, raw_output)
            return [], budget.SOURCE_LLM

        by_kind = {c.kind: c for c in cands}
        chosen, seen = [], set()
        for k in kinds:
            if k not in policy.ACTION_TIERS:
                # FR-3: a name the model invented is rejected and recorded.
                audit.blocked("model returned an action outside the whitelist",
                              context={"risk_kind": r.kind,
                                       "risk_score": round(r.score, 1),
                                       "action_kind": k[:64],
                                       "decision_source": budget.SOURCE_LLM,
                                       "channel": "engine"})
                db.log("proactive.blocked",
                       f"{r.kind}: model proposed unknown action {k!r}")
                continue
            if k in by_kind and k not in seen:
                # A BLOCK verdict from the model is never turned into a start.
                if verdicts.get(k) == budget.BLOCK:
                    budget.record_decision(budget.BLOCK, budget.SOURCE_LLM,
                                           by_kind[k].device_id,
                                           "model voted BLOCK", channel="engine",
                                           prompt=system, model_input=user,
                                           model_output=raw_output)
                    continue
                chosen.append(by_kind[k])
                seen.add(k)
        if not chosen and kinds:
            # Every verdict was BLOCK, or nothing matched the table. Abstention
            # is a decision when it came from the model and said BLOCK; it is a
            # fallback only when no candidate was recognised at all.
            matched = any(k in by_kind for k in kinds)
            src = budget.SOURCE_LLM if matched and verdicts else budget.SOURCE_FALLBACK
            db.log("proactive.decide_fallback",
                   f"{r.kind}: no action chosen; source={src}")
            self._trace_decision(r, cands, src, system, user, raw_output)
            return [], src
        db.log("proactive.decide",
               f"{r.kind}: chose {', '.join(c.kind for c in chosen)}")
        self._trace_decision(r, cands, budget.SOURCE_LLM, system, user, raw_output)
        return chosen, budget.SOURCE_LLM

    def _trace_decision(self, r: risk.Risk, cands: list, source: str,
                        system: str, user: str, output: str) -> None:
        """Append the real prompt/input/output for one decision to the ledger."""
        for c in cands[:1]:
            budget.record_decision(
                "DECIDED", source, c.device_id, r.headline,
                channel="engine", prompt=system, model_input=user,
                model_output=output)

    def _phrase(self, r: risk.Risk, tier2: list, snap: dict) -> str:
        """One plain-language question for the Tier-2 request."""
        wants = _join([c.label for c in tier2])
        # The fallback has to be as plain as the model's answer would be: it is
        # shown whenever the model is off, slow, or unreachable, which is
        # exactly when nobody is around to polish it.
        fallback = (f"{_plain(r, snap)}. I would like to {wants}. "
                    f"Shall I? Reply yes or no \u2014 no answer in 5 minutes "
                    f"keeps everything as it is.")
        if not self.llm_phrase:
            return fallback
        try:
            msg = llm.chat(self.cfg, [
                {"role": "system", "content": QUESTION_SYSTEM},
                {"role": "user", "content":
                    f"Situation: {_plain(r, snap)}. Requested change: {wants}. "
                    f"User is {'home' if snap['occupancy'].get('user_home') else 'out'}."},
            ], temperature=0.2, max_tokens=120)
            text = (msg.get("content") or "").strip()
            if text and len(text) < 400:
                return text
        except Exception:
            pass
        return fallback

    # ---------------------------------------------------------------- FR-8 deferral

    def _queue_delay(self, c, verdict: dict, now: datetime, source: str) -> str:
        """Turn a DELAY verdict into a durable job. The scheduler re-checks the
        budget when the job actually runs, so a queued job cannot spend money
        the household used in the meantime."""
        when = verdict.get("scheduled_at") or (
            now.replace(hour=22, minute=0, second=0, microsecond=0).isoformat(timespec="minutes"))
        job_id = db.add_job(
            device_id=c.device_id,
            device_name=c.device_name,
            actions=c.actions,
            rule={"type": "once", "reason": "energy budget DELAY",
                  "decision_source": source},
            next_run_at=when,
            repeat_label="",
            source_text=f"Delayed: {verdict.get('reason', '')}",
        )
        when_short = str(when)[11:16]
        db.add_message(
            "system",
            f"⏳ I did not start {c.device_name} now — {verdict.get('reason', '')}. "
            f"It is scheduled for {when_short}. Your budget has not been spent yet.",
            {"energy_delay": True, "job_id": job_id, "device_id": c.device_id,
             "decision_source": source, "scheduled_at": when},
        )
        events.emit("energy", "Deferred to save energy",
                    f"{c.device_name} → {when_short}",
                    device_id=c.device_id, job_id=job_id)
        db.log("proactive.energy_delay",
               f"{c.kind} -> {when}: {verdict.get('reason', '')}")
        return job_id

    # ---------------------------------------------------------------- user decisions

    def confirm(self, approval_id: str) -> dict:
        """The user said yes. Revalidate against a fresh snapshot, then act.

        FR-4/5/6: one approval authorises exactly one Level-2 action; a stale
        "yes" is refused with a reason; intent reaches the audit log before the
        adapter does. No job is created here — the user restarts the dryer by
        hand, and a later automatic restart would be a decision they never saw.
        """
        ap = self.approvals.confirm(approval_id)
        if not ap:
            # Two ways to get here, and they are not the same thing. Either
            # there is no such approval, or there was one and this answer
            # arrived after its deadline. The second case is an expiry, so it
            # has to end like a sweep expiry: nothing changes, the user is
            # told, and the risk goes quiet instead of being asked again on
            # the next tick.
            late = self.approvals.take_late_expiry(approval_id)
            if late is None:
                return {"ok": False, "error": "no pending approval with that id"}
            self._cooldown[late.risk_kind] = time.monotonic() + self.quiet_seconds
            db.add_message("system",
                           f"\u23f3 That one closed before I heard back, so I changed "
                           f"nothing. {_devices(late)} are as they were.",
                           {"approval": late.to_dict()})
            return {"ok": False, "expired": True, "approval": late.to_dict()}

        device_id = _device_id_of(ap)
        if not ap.audit_ok:
            audit.blocked("confirmation audit row could not be written",
                          context=ap.audit_ctx(), device_id=device_id)
            return {"ok": False, "audit_blocked": True, "approval": ap.to_dict(),
                    "error": "the audit trail could not be written; nothing was changed"}

        snap = self.sensors.snapshot()
        allowed, reason = self._revalidate(ap, snap, datetime.now())
        if not allowed:
            audit.blocked(reason, context=ap.audit_ctx(), device_id=device_id)
            db.log("proactive.revalidation_blocked", f"{ap.id}: {reason}")
            db.add_message("system",
                           f"\u2717 You said yes, but I did not change {_devices(ap)}: "
                           f"{reason}. Everything is as it was.",
                           {"approval": ap.to_dict(), "blocked_reason": reason})
            return {"ok": False, "blocked": True, "reason": reason,
                    "approval": ap.to_dict()}

        item = ap.actions[0]
        res = audit.execute(self.adapter, item["device_id"], item["actions"],
                            context=ap.audit_ctx())
        if not res.get("ok"):
            error = res.get("error") or "the device did not accept the command"
            audit.blocked(error, context=ap.audit_ctx(), device_id=device_id)
            db.add_message("system",
                           f"\u2717 You said yes, but {_devices(ap)} could not be "
                           f"changed: {error}. Nothing else was changed.",
                           {"approval": ap.to_dict(), "executed": [], "error": error})
            return {"ok": False, "error": error,
                    "audit_blocked": bool(res.get("audit_blocked")),
                    "approval": ap.to_dict(), "executed": []}

        kind = item.get("kind") or ap.action_kind
        if self.learn_enabled:
            try:
                preference.record_decision(ap.risk_kind, kind, True,
                                           threshold=self.learn_threshold)
            except Exception as exc:
                db.log("preference.record_failed", str(exc), "error")
        if kind == "preheat_cooktop":
            self._cooktop_on_since = time.monotonic()
            self._cooktop_last_cooking = (time.monotonic()
                                          if self._cooking_detected(snap, datetime.now())
                                          else None)
        elif kind == "stop_dryer":
            # FR-5: remind once the risk clears; never restart the appliance.
            self._pending_dryer_reminder = True

        done = item.get("done") or item.get("label") or item.get("device_id", "")
        executed = [{"device_id": device_id, "ok": True, "error": None}]
        db.add_message("system", f"\u2713 You said yes, so I {done}.",
                       {"approval": ap.to_dict(), "executed": executed})
        return {"ok": True, "approval": ap.to_dict(), "executed": executed}

    def decline(self, approval_id: str) -> dict:
        ap = self.approvals.decline(approval_id)
        if not ap:
            return {"ok": False, "error": "no pending approval with that id"}
        # No is an answer. Asking again in three minutes is not respect, it is
        # a machine that did not listen.
        self._cooldown[ap.risk_kind] = time.monotonic() + self.quiet_seconds
        if self.learn_enabled:
            try:
                kind = ap.action_kind or (ap.actions[0].get("kind") if ap.actions else "")
                preference.record_decision(ap.risk_kind, kind, False,
                                           threshold=self.learn_threshold)
            except Exception as exc:
                db.log("preference.record_failed", str(exc), "error")
        db.add_message("system",
                       f"\u2717 All right \u2014 I left {_devices(ap)} as it was.",
                       {"approval": ap.to_dict()})
        return {"ok": True, "approval": ap.to_dict()}

    # ---------------------------------------------------------------- FR-5 gates

    @staticmethod
    def _context(r: risk.Risk, c, decided_by: str) -> dict:
        """The fields an action's intent and outcome rows carry (FR-6)."""
        return {"risk_kind": r.kind, "risk_score": round(r.score, 1),
                "action_kind": c.kind, "tier": policy.tier_of(c.kind),
                "decision_source": decided_by, "channel": "engine"}

    @staticmethod
    def _revalidate_candidate(c, r: risk.Risk, snap: dict) -> tuple[bool, str]:
        """Algorithm 1's check for an autonomous action, on a fresh snapshot."""
        if c.kind not in {x.kind for x in policy.candidates(r, snap)}:
            return False, "the device state changed before the action ran"
        return True, ""

    def _revalidate(self, ap, snap: dict, now: datetime) -> tuple[bool, str]:
        """FR-5 pre-execution revalidation, immediately before the adapter.

        Everything a stale "yes" could have outlived is checked against a new
        sensor snapshot: the approval, the risk, the action's permission, the
        device state, and the safety interlocks. A refusal is returned with a
        reason the user can read — a silent refusal looks like a broken button.
        """
        # 1. The approval itself.
        if ap.status != "confirmed":
            return False, "that approval is no longer valid"
        try:
            if datetime.fromisoformat(ap.expires_at) <= datetime.now():
                return False, "the five-minute window closed"
        except (ValueError, TypeError):
            return False, "the approval has no readable deadline"

        # 2. The action the user saw, and nothing else.
        item = (ap.actions or [{}])[0]
        kind = item.get("kind") or ap.action_kind
        if kind not in policy.ACTION_TIERS:
            return False, "that action is not on the whitelist"
        if policy.tier_of(kind) != policy.TIER2:
            return False, "that action is not a Level 2 action"

        # 3. The reason for the action still holds, recomputed from the fresh
        #    snapshot. Preheating the cooktop prepares a meal; it does not
        #    relieve the overload, and stopping the dryer alone usually clears
        #    the thermal index. Tying it to the index would cancel every
        #    preheat that follows a dryer stop, so it is checked against the
        #    meal instead.
        active = []
        if kind == "preheat_cooktop":
            if _cooking_starts_in_min(snap, now) is None:
                return False, "there is no cooking on the calendar any more"
        else:
            active = [x for x in risk.evaluate(snap, self.sensors.history, now)
                      if x.kind == ap.risk_kind and x.score >= x.threshold]
            if not active:
                return False, "the risk is no longer active"

        # 4. The device is still in the state the question described.
        state = _device_state(snap, item.get("device_id", ""))
        if ("power" in ap.proposed_state
                and state.get("power") != ap.proposed_state["power"]):
            return False, "the device state changed since you were asked"
        if active and kind not in {c.kind for c in policy.candidates(active[0], snap)}:
            return False, "the action is no longer needed"

        # 5. Safety interlocks. A hob with nobody in the house is exactly the
        #    case the two levels exist to prevent.
        if (kind == "preheat_cooktop" and self.cooktop_require_presence
                and not (snap.get("occupancy") or {}).get("user_home")):
            return False, "nobody is home to watch the cooktop"
        return True, ""

    @staticmethod
    def _cooking_detected(snap: dict, now: datetime) -> bool:
        """The simulator has no stove sensor. Its honest signals are the local
        cooking calendar and the oven's power state — nothing is inferred from
        the model, from a prompt, or from the fact that the cooktop is on."""
        if (snap.get("appliances") or {}).get("oven", {}).get("power"):
            return True
        for entry in snap.get("calendar") or []:
            try:
                start = datetime.fromisoformat(entry["start"])
                end = datetime.fromisoformat(entry.get("end") or entry["start"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= now <= end:
                return True
        return False

    def _cooktop_guard(self, snap: dict, now: datetime) -> list[dict]:
        """Auto-off when cooking never starts, or at the maximum run time.

        This is a safety interlock, not a policy candidate: it can only switch
        a heating appliance *off*, and it is not one of the seven whitelisted
        actions the user is asked about.
        """
        on = bool((snap.get("appliances") or {}).get("cooktop", {}).get("power"))
        if not on:
            self._cooktop_on_since = None
            self._cooktop_last_cooking = None
            return []
        clock = time.monotonic()
        if self._cooktop_on_since is None:
            self._cooktop_on_since = clock
        # A meal that is still coming counts as cooking for the idle timer:
        # the cooktop was preheated *for* it, so the no-cooking countdown
        # starts at the scheduled start, not when the cooktop came on.
        if (self._cooking_detected(snap, now)
                or _cooking_starts_in_min(snap, now) is not None):
            self._cooktop_last_cooking = clock
        since_on = clock - self._cooktop_on_since
        since_cook = clock - (self._cooktop_last_cooking
                              if self._cooktop_last_cooking is not None
                              else self._cooktop_on_since)
        reason = ""
        if since_on >= self.cooktop_max_runtime:
            reason = "it had reached its maximum continuous run time"
        elif since_cook >= self.cooktop_no_cook_timeout:
            reason = "no cooking was detected"
        if not reason:
            return []
        res = audit.execute(self.adapter, "cooktop",
                            [{"action": "power", "value": False}],
                            context={"risk_kind": "thermal",
                                     "action_kind": "cooktop_auto_off",
                                     "tier": "safety", "channel": "safety-interlock",
                                     "decision_source": "rules"})
        self._cooktop_on_since = None
        self._cooktop_last_cooking = None
        db.log("proactive.cooktop_auto_off", f"{reason}: ok={res.get('ok')}")
        db.add_message("system",
                       f"\U0001f6e1 I switched the cooktop off because {reason}. "
                       f"Nothing else was changed.",
                       {"safety_interlock": "cooktop_auto_off",
                        "reason": reason, "ok": bool(res.get("ok"))})
        return [{"type": "cooktop_auto_off", "reason": reason,
                 "ok": bool(res.get("ok"))}]

    def _dryer_followup(self, fired_kinds: set[str], snap: dict) -> list[dict]:
        """FR-5: one notice when the risk clears, so the user can restart by hand."""
        if not self._pending_dryer_reminder:
            return []
        if (snap.get("appliances") or {}).get("dryer", {}).get("power"):
            self._pending_dryer_reminder = False   # already restarted by hand
            return []
        if "thermal" in fired_kinds:
            return []                              # still too hot; wait
        self._pending_dryer_reminder = False
        events.emit("reminder", "Dryer is still off",
                    "The kitchen cooled down. Restart it yourself when ready.")
        db.add_message("system",
                       "\U0001f514 The kitchen has cooled down. The dryer is still off "
                       "\u2014 you can start it yourself when you are ready. "
                       "I have not restarted it for you.",
                       {"notification": "dryer_restart_reminder"})
        try:
            audit.record("notification.dryer_restart", risk_kind="thermal",
                         action_kind="stop_dryer", tier="tier2",
                         device_id="dryer", execution_status="not_applicable",
                         detail="risk cleared; user restarts the dryer by hand")
        except Exception as exc:
            db.log("audit.write_failed", f"notification.dryer_restart: {exc}", "error")
        return [{"type": "dryer_restart_reminder"}]

    def _housekeeping(self, snap: dict, fired_kinds: set[str],
                      now: datetime) -> list[dict]:
        """Safeguards must not depend on the risk loop deciding to intervene."""
        out: list[dict] = []
        for fn in (lambda: self._cooktop_guard(snap, now),
                   lambda: self._dryer_followup(fired_kinds, snap)):
            try:
                out.extend(fn())
            except Exception as exc:
                db.log("proactive.error",
                       f"housekeeping {type(exc).__name__}: {exc}", "error")
        return out

    # ---------------------------------------------------------------- state

    def _cooling(self, kind: str) -> bool:
        until = self._cooldown.get(kind)
        return until is not None and time.monotonic() < until

    def state(self) -> dict:
        try:
            energy = budget.state(cfg=self.cfg)
        except Exception:
            energy = {"available": False}
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "llm_decide": self.llm_decide,
            "preauthorised": self.preauthorised,
            "energy": energy,
            "scenario": self._last_snapshot.get("scenario"),
            "rooms": self._last_snapshot.get("rooms"),
            "appliances": self._last_snapshot.get("appliances"),
            "occupancy": self._last_snapshot.get("occupancy"),
            "active": self._active,
            "pending_approvals": [a.to_dict() for a in self.approvals.pending()],
            # FR-4: quiet periods are visible (risk kind -> seconds left), so
            # the user can see why the house is not asking.
            "quiet": {k: round(until - time.monotonic())
                      for k, until in self._cooldown.items()
                      if until > time.monotonic()},
            # Live index values, including those below the threshold, for the
            # dashboard. A kind with nothing to score is reported as 0.
            "risks": self._risk_view(),
            "heat_load_w": self._last_snapshot.get("heat_load_w", 0),
            "total_load_w": self._last_snapshot.get("total_load_w", 0),
            "tariff": self._last_snapshot.get("tariff", "offpeak"),
            "cooktop_guard": self._cooktop_view(),
        }

    def _risk_view(self) -> dict:
        thresholds = {"mold": risk.MOLD_THRESHOLD, "thermal": risk.THERMAL_THRESHOLD,
                      "heat_health": risk.HEAT_HEALTH_THRESHOLD, "energy": risk.ENERGY_THRESHOLD}
        out = {k: {"kind": k, "score": 0.0, "threshold": t, "current": None,
                   "predicted": None, "drivers": {}, "firing": False}
               for k, t in thresholds.items()}
        for r in self._last_risks:
            out[r.kind] = {"kind": r.kind, "score": round(r.score, 1),
                           "threshold": r.threshold, "metric": r.metric,
                           "current": round(r.current, 1),
                           "predicted": round(r.predicted, 1),
                           "drivers": r.drivers, "headline": r.headline,
                           "firing": r.score >= r.threshold}
        return out

    def _cooktop_view(self) -> dict | None:
        """Seconds until each cooktop interlock fires, while it is armed."""
        if self._cooktop_on_since is None:
            return None
        clock = time.monotonic()
        since_cook = clock - (self._cooktop_last_cooking
                              if self._cooktop_last_cooking is not None
                              else self._cooktop_on_since)
        snap = self._last_snapshot or {}
        return {
            "max_off_in": max(0, round(self.cooktop_max_runtime
                                       - (clock - self._cooktop_on_since))),
            "idle_off_in": max(0, round(self.cooktop_no_cook_timeout - since_cook)),
            "meal_in_min": _cooking_starts_in_min(snap),
            "cooking": self._cooking_detected(snap, datetime.now()) if snap else False,
        }

    def _sync_simulated_devices(self) -> None:
        """Make the simulated device list agree with the scenario just loaded.

        The simulator and the mock adapter keep separate state; without this
        the device list kept the previous run's appliances on or off while the
        risk cards showed the new scenario. Only the mock backend is touched —
        a demo reset must never switch real appliances.
        """
        if getattr(self.adapter, "backend_name", "") != "mock":
            return
        snap = self.sensors.snapshot()
        appliances = snap.get("appliances") or {}
        for dev in self.adapter.list_devices():
            did = dev.get("id", "")
            if did == "washer":
                # Powering the mock washer starts a wash; leave its cycle to
                # the simulator and only stop any drum ventilation.
                acts = [{"action": "ventilate", "value": False}]
            elif did in appliances and "power" in appliances[did]:
                acts = [{"action": "power", "value": bool(appliances[did]["power"])}]
            elif did.startswith("ac_"):
                # Follow the scenario's air conditioners (the peak scenario
                # runs two of them cold), so the device list and the load
                # planner see the same house the simulator draws power for.
                room = {"ac_living": "Living Room", "ac_bedroom": "Bedroom",
                        "ac_kitchen": "Kitchen"}.get(did)
                ac = (snap.get("ac_targets") or {}).get(room) or {}
                acts = [{"action": "power", "value": bool(ac.get("power"))}]
                if ac.get("power") and ac.get("target_temp") is not None:
                    acts.append({"action": "temperature", "value": ac["target_temp"]})
            else:
                continue
            try:
                self.adapter.apply(did, acts)
            except Exception as exc:
                db.log("scenario.sync_failed", f"{did}: {exc}", "error")

    # ---------------------------------------------------------------- by-hand starts

    def _note_write(self, device_id: str, actions: list[dict]) -> None:
        self._our_writes[device_id] = time.monotonic()

    def _watch_manual_starts(self, snap: dict, now: datetime, firing: set[str]) -> list[dict]:
        """Someone switched an appliance on by hand and the house is now over
        the max kW: let the load planner make room, stop it and book it, or
        keep the overshoot as small as it can -- and say what it did."""
        from . import loadplan
        try:
            devices = loadplan.devices_with_state(self.adapter)
        except Exception:
            return []
        started = []
        for d in devices:
            on = loadplan._running(d)
            prev = self._power_seen.get(d["id"])
            self._power_seen[d["id"]] = on
            ours = time.monotonic() - self._our_writes.get(d["id"], -1e9) < 5
            if prev is False and on and not ours:
                started.append(d)
                try:
                    audit.record("device.started_by_hand", device_id=d["id"], channel="by-hand",
                                 decision_source="user", detail=f"{d['name']} switched on at the appliance")
                except Exception:
                    pass
        if not started:
            return []
        cap = budget.max_kw(self.cfg)
        st = budget.state(now, self.cfg)
        load = float(snap.get("total_load_w") or 0.0)
        if not cap or not st.get("strict_mode") or load <= cap * 1000.0:
            return []
        out = []
        region = regions.get(None)
        band = "peak" if budget._in_peak(now, region) else "offpeak"
        user_home = (snap.get("occupancy") or {}).get("user_home")
        for d in started:
            add = loadplan._draw(d)
            plans = loadplan.build_plans(dev=d, add_w=add, load_w=load, cap_kw=cap, devices=devices,
                                         firing=firing, user_home=user_home, band=band, now=now,
                                         region=region, channel="by-hand", started_by_hand=True)
            facts = loadplan.facts_text(dev=d, add_w=add, load_w=load, cap_kw=cap, band=band,
                                        user_home=user_home, firing=firing,
                                        who="someone at the appliance (by hand)")
            plan, source, trace = loadplan.choose(self.cfg, plans, facts, band)
            ctx = {"channel": "planner", "decision_source": source, "tier": "energy"}
            k = plan["kind"]
            if k in (loadplan.MAKE_ROOM, loadplan.OVER):
                loadplan.run_steps(self.adapter, plan["steps"], source=source)
                budget.adjust_load(-sum(s["saves_w"] for s in plan["steps"]))
            elif k in (loadplan.STOP, loadplan.STOP_BOOK):
                audit.execute(self.adapter, d["id"], [{"action": "power", "value": False}],
                              context={**ctx, "action_kind": "stop_by_hand_start"})
                budget.adjust_load(-add)
                if k == loadplan.STOP_BOOK:
                    db.add_job(device_id=d["id"], device_name=d["name"],
                               actions=[{"action": "power", "value": True}],
                               rule={"type": "once", "reason": "energy budget DELAY",
                                     "decision_source": source},
                               next_run_at=plan["book"],
                               source_text="started by hand over the kW limit")
            note = loadplan.describe(plan, cap)
            budget.record_decision({loadplan.MAKE_ROOM: "MAKE_ROOM", loadplan.OVER: "ALLOW_OVER",
                                    loadplan.STOP: "STOP", loadplan.STOP_BOOK: "STOP_BOOK"}.get(k, "ALLOW"),
                                   source, d["id"], f"{d['name']} started by hand: {note}",
                                   channel="by-hand", prompt=trace.get("prompt"),
                                   model_input=trace.get("model_input"),
                                   model_output=trace.get("model_output"), now=now)
            db.add_message("system", f"\u26a1 {d['name']} was switched on by hand. {note}",
                           {"energy_plan": k, "device_id": d["id"], "decision_source": source})
            events.emit("energy", "Over your kW limit", f"{d['name']}: {note}", device_id=d["id"])
            db.log("planner.by_hand", f"{d['id']} -> {k} ({source})")
            out.append({"type": "by_hand", "device": d["id"], "plan": k, "source": source})
        return out

    def reset_scenario(self, name: str) -> None:
        """Demo control: load a scenario and start the engine's memory fresh.

        Open questions from the previous scenario are closed as expired —
        nothing they asked about is executed — and every quiet period,
        reminder and interlock timer is cleared.
        """
        self.approvals.close_all("scenario reset")
        self.sensors.apply_scenario(name)
        self._sync_simulated_devices()
        self._power_seen = {}       # a scenario load is not someone pressing buttons
        self._cooldown.clear()
        self._active.clear()
        self._logged_risks.clear()
        self._last_risks = []
        self._cooktop_on_since = None
        self._cooktop_last_cooking = None
        self._pending_dryer_reminder = False
        try:
            audit.record("scenario.reset", decision_source="user", channel="demo",
                         detail=name)
        except Exception as exc:
            db.log("audit.write_failed", f"scenario.reset: {exc}", "error")
        db.add_message("system", f"Demo: loaded the “{name}” scenario.",
                       {"scenario": name})


def _cooking_starts_in_min(snap: dict, now: datetime | None = None) -> float | None:
    """Minutes until the soonest calendar entry starting within the thermal
    index's lookahead window, or None when no meal is coming."""
    now = now or datetime.now()
    soonest = None
    for entry in snap.get("calendar") or []:
        try:
            start = datetime.fromisoformat(entry["start"])
        except (KeyError, TypeError, ValueError):
            continue
        delta = (start - now).total_seconds() / 60.0
        if 0 <= delta <= risk.CALENDAR_LOOKAHEAD_MIN and (soonest is None or delta < soonest):
            soonest = delta
    return soonest


def _candidate_dict(c) -> dict:
    return {"kind": c.kind, "device_id": c.device_id, "device_name": c.device_name,
            "actions": c.actions, "label": c.label, "done": c.done}


_AC_ROOMS = {"ac_living": "Living Room", "ac_bedroom": "Bedroom",
             "ac_kitchen": "Kitchen"}


def _device_state(snap: dict, device_id: str) -> dict:
    """The device state a question was based on, for later revalidation."""
    if device_id.startswith("ac_"):
        room = _AC_ROOMS.get(device_id)
        ac = (snap.get("ac_targets") or {}).get(room, {})
        return {"power": bool(ac.get("power"))}
    dev = (snap.get("appliances") or {}).get(device_id, {})
    return {"power": bool(dev.get("power"))}


def _device_id_of(ap) -> str:
    return (ap.actions or [{}])[0].get("device_id", "") or ""
