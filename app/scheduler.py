"""Scheduled task runner.

Why the LLM is not the timer: a model holds no state. Restart the process and
the pending "turn the AC off at 23:00" is gone, with no way to notice. Here a
dedicated thread scans SQLite once a second for due jobs, runs them, and writes
the next run time back — so a crash or power loss resumes cleanly.
"""
from __future__ import annotations

import threading
import traceback
from datetime import datetime

from . import audit, budget, db, nlu
from .devices.base import describe_action


class Scheduler:
    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")

    def start(self) -> None:
        self._catch_up()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------ restart recovery

    def _catch_up(self) -> None:
        """Deal with jobs that came due while the process was not running.

        Deliberately does NOT fire them. Running a command hours late is worse
        than not running it — turning the AC on because it was supposed to turn
        on three days ago is a bug, not a feature. One-time jobs are closed out
        as missed; recurring jobs skip the lost occurrence and reschedule.
        """
        now = datetime.now()
        missed = 0

        for job in db.list_jobs(only_enabled=True):
            raw = job.get("next_run_at")
            if not raw:
                continue
            try:
                due = datetime.fromisoformat(raw)
            except ValueError:
                continue
            if due >= now:
                continue

            missed += 1
            when = due.strftime("%b %d, %H:%M")
            if job["rule"].get("type") == "once":
                db.update_job_after_run(
                    job["id"], None, f"Missed: agent was offline at {when}", disable=True)
            else:
                nxt = nlu.compute_next(job["rule"], now)
                db.update_job_after_run(
                    job["id"],
                    nxt.isoformat(timespec="seconds") if nxt else None,
                    f"Missed the {when} run; rescheduled")

        if missed:
            db.log("scheduler.catchup", f"{missed} overdue job(s) handled")
            db.add_message(
                "system",
                f"{missed} scheduled task(s) came due while the agent was "
                f"offline. Nothing was fired late \u2014 one-time tasks were closed "
                f"out as missed and recurring ones moved to their next slot.",
                {"catchup": missed},
            )

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self._tick()
            except Exception:
                db.log("scheduler.error", traceback.format_exc(), "error")

    def _tick(self) -> None:
        now = datetime.now()
        for job in db.due_jobs(now.isoformat(timespec="seconds")):
            self._run_job(job, now)

    def _run_job(self, job: dict, now: datetime) -> None:
        device = self.adapter.get_device(job["device_id"])
        name = job.get("device_name") or job["device_id"]

        if not device:
            result = "Failed: device not found"
        elif not device.get("online", True):
            result = "Failed: device offline"
        else:
            # FR-8: re-check at execution time, not at scheduling time. A job
            # queued while budget remained can come due after the household
            # spent it; an hour-old verdict is not authorisation to spend now.
            verdict = budget.gate(
                self.adapter, job["device_id"], job["actions"], now=now,
                channel="scheduler", judge=budget.SOURCE_RULES)
            if verdict["decision"] == budget.BLOCK:
                result = f"Blocked by budget: {verdict['reason']}"
                db.log("scheduler.budget_blocked", f"{job['id']} {result}")
            elif (verdict["decision"] == budget.DELAY
                  and verdict.get("limit") == "max_kw"):
                # The max kW cap is about the load *now*, so an old booking is
                # no permission to exceed it. Try again later, but not forever.
                rule = dict(job.get("rule") or {})
                tries = int(rule.get("cap_retries", 0)) + 1
                if tries <= budget.CAP_MAX_RETRIES:
                    rule["cap_retries"] = tries
                    when = verdict.get("scheduled_at")
                    result = (f"Waiting: {verdict['reason']}; "
                              f"next try {str(when)[11:16]}")
                    db.defer_job(job["id"], when, result, rule)
                    db.log("scheduler.max_kw_deferred", f"{job['id']} {result}")
                    db.add_message(
                        "system",
                        f"\u23f3 {now.strftime('%H:%M')} {name} still does not fit "
                        f"under {verdict.get('max_kw'):g} kW (house at "
                        f"{verdict.get('load_kw'):.1f} kW). Trying again at "
                        f"{str(when)[11:16]}.",
                        {"job_id": job["id"], "energy_delay": True,
                         "scheduled_at": when},
                    )
                    return
                result = (f"Not started: {name} did not fit under "
                          f"{verdict.get('max_kw'):g} kW in "
                          f"{budget.CAP_MAX_RETRIES * budget.CAP_RETRY_MINUTES // 60} hours")
                db.log("scheduler.max_kw_gave_up", f"{job['id']} {result}")
            elif verdict["decision"] == budget.DELAY:
                # The job IS the deferral. Delaying again at execution time
                # would push a task back forever. Honour the appointment, but
                # record that the band was still peak when it ran.
                budget.record_decision(budget.ALLOW, budget.SOURCE_OVERRIDE,
                                       job["device_id"],
                                       "due job: deferral already applied when queued",
                                       channel="scheduler")
                res = audit.execute(self.adapter, job["device_id"], job["actions"])
                done = ", ".join(describe_action(a) for a in job["actions"])
                result = f"Success: {done}" if res.get("ok") else f"Failed: {res.get('error')}"
            else:
                res = audit.execute(self.adapter, job["device_id"], job["actions"])
                done = ", ".join(describe_action(a) for a in job["actions"])
                result = f"Success: {done}" if res.get("ok") else f"Failed: {res.get('error')}"

        rule = job["rule"]
        is_once = rule.get("type") == "once"
        next_at = None
        if not is_once:
            nxt = nlu.compute_next(rule, now)
            next_at = nxt.isoformat(timespec="seconds") if nxt else None

        db.update_job_after_run(job["id"], next_at, result, disable=is_once)
        db.log("schedule.run", f"{job['id']} {name} {result}")

        # Push into the conversation stream so the user can see whether the
        # 23:00 run actually happened.
        db.add_message(
            "system",
            f"\u23f1 {now.strftime('%H:%M')} Scheduled task ran \u00b7 {name} \u00b7 {result}",
            {"job_id": job["id"], "result": result},
        )
