"""Simulated week, before and after: does Strict Mode actually help?

    python -m eval.week            # writes docs/eval/week.md, week.svg, week.json

One household, seven days, one-minute steps, the same appliance draws the
simulator uses (budget.power_w). Every controllable start in the FSD runs goes
through the real `budget.can_start()`, and a booked job is re-checked when it
comes due exactly as `scheduler.py` does it. Nothing here is a second copy of
the rules.

Three runs of the *same* week:

  no_fsd      every appliance starts when the household asks for it
  peak_shift  Strict Mode as on main: chores asked for in the peak band wait
              for off-peak (no kW cap)
  max_kw      Strict Mode plus a 3.0 kW cap: chores that would push the house
              over the cap are booked later and re-checked

What is honest about it, and what is not:

* It is a model household, not metered data. Draws are nameplate figures.
* Cooking, lighting, the fridge and air conditioning are not controllable, so
  they run identically in all three runs; a peak they cause alone is reported
  as such.
* Shifting a load in time does not change kWh or CO2 with a single national
  emission factor. Only the cost (time-of-use price) and the peak kW change.
  The report says so instead of claiming CO2 savings it did not make.
* Prices: KEPCO residential time-of-use (Jeju) as in regions.py, peak 16-22 h.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import budget, db, regions  # noqa: E402

REGION = "KR"
MAX_KW = 3.0
START = datetime(2026, 11, 23, 0, 0)          # a Monday
DAYS = 7
STEP = 1                                       # minutes
SEED = 7
OUT = ROOT / "docs" / "eval"

DEVICES = [
    {"id": "washer", "name": "Washer", "type": "washer", "online": True},
    {"id": "dryer", "name": "Dryer", "type": "dryer", "online": True},
    {"id": "dish_washer", "name": "Dishwasher", "type": "dish_washer", "online": True},
]
CHORE_MIN = {"washer": 60, "dryer": 60, "dish_washer": 60}
ON = [{"action": "power", "value": True}]


class SimAdapter:
    """Enough of the adapter interface for budget.can_start()."""

    backend_name = "sim"

    def __init__(self):
        self.running: dict[str, datetime] = {}

    def get_device(self, device_id):
        d = next((x for x in DEVICES if x["id"] == device_id), None)
        if d is None:
            return None
        return {**d, "state": {"power": device_id in self.running}}


# ------------------------------------------------------------------ household

@dataclass
class Day:
    oven: tuple[int, int] | None            # minute-of-day start, duration
    cooktop: tuple[int, int]
    ac: list[tuple[int, int]]
    washer_ask: int | None
    dish_ask: int


def household(rng: random.Random) -> list[Day]:
    """Seven days of requests, plus a quiet eighth day so that a chore booked
    late on Sunday can still finish inside the measured window."""
    days = []
    for d in range(DAYS):
        weekend = d >= 5
        j = lambda m: m + rng.choice((-20, -10, 0, 10, 20))  # noqa: E731
        days.append(Day(
            oven=(j(18 * 60 + 30), 30) if d in (0, 2, 4, 5) else None,
            cooktop=(j(12 * 60), 25) if weekend else (j(19 * 60), 20),
            ac=[(18 * 60, 300)] + ([(13 * 60, 240)] if weekend else []),
            washer_ask=j(18 * 60 + 40) if d != 3 else None,
            dish_ask=j(20 * 60 + 10),
        ))
    days.append(Day(oven=None, cooktop=(0, 0), ac=[], washer_ask=None, dish_ask=-1))
    return days


def base_load_w(day: Day, minute: int) -> float:
    """Loads nobody shifts: fridge, standby, lights, cooking, cooling."""
    w = 150.0 + 120.0                                   # fridge average + standby
    if 18 * 60 <= minute < 23 * 60:
        w += 200.0                                      # lights and TV
    for start, dur in ([day.oven] if day.oven else []):
        if start <= minute < start + dur:
            w += budget.power_w({"type": "oven"}, ON)
    s, dur = day.cooktop
    if s <= minute < s + dur:
        w += budget.power_w({"type": "cooktop"}, ON)
    for s, dur in day.ac:
        if s <= minute < s + dur:
            w += budget.power_w({"type": "air_conditioner"}, ON)
    return w


# ------------------------------------------------------------------ one run

@dataclass
class Run:
    name: str
    load_kw: list[float] = field(default_factory=list)
    starts: list[dict] = field(default_factory=list)
    gave_up: int = 0
    base_kw: list[float] = field(default_factory=list)


def simulate(mode: str, days: list[Day]) -> Run:
    run = Run(mode)
    adapter = SimAdapter()
    budget.set_budget(100000, strict_mode=(mode != "no_fsd"), now=START)
    budget.set_max_kw(MAX_KW if mode == "max_kw" else None, now=START)

    # (due time, device id, asked at, cap retries)
    queue: list[tuple[datetime, str, datetime, int]] = []

    def chores_w(now):
        w = 0.0
        for dev_id, began in list(adapter.running.items()):
            if now >= began + timedelta(minutes=CHORE_MIN[dev_id]):
                del adapter.running[dev_id]
                if dev_id == "washer":
                    queue.append((now, "dryer", now, 0))   # dryer asked when wash ends
            else:
                w += budget.power_w({"type": dev_id}, ON)
        return w

    def start(dev_id, now, asked):
        adapter.running[dev_id] = now
        run.starts.append({"device": dev_id, "asked": asked.isoformat(timespec="minutes"),
                           "started": now.isoformat(timespec="minutes"),
                           "wait_min": int((now - asked).total_seconds() // 60)})

    for d, day in enumerate(days):
        for minute in range(0, 24 * 60, STEP):
            now = START + timedelta(days=d, minutes=minute)
            if day.washer_ask == minute:
                queue.append((now, "washer", now, -1))
            if day.dish_ask == minute:
                queue.append((now, "dish_washer", now, -1))

            base = base_load_w(day, minute)
            load = base + chores_w(now)
            due = sorted([q for q in queue if q[0] <= now], key=lambda q: q[0])
            for item in due:
                queue.remove(item)
                _, dev_id, asked, tries = item
                if mode == "no_fsd":
                    start(dev_id, now, asked)
                    load += budget.power_w({"type": dev_id}, ON)
                    continue
                v = budget.can_start(adapter, dev_id, ON, now=now, region=REGION,
                                     load_w=load, channel="scheduler")
                booked = tries >= 0                      # came from a booking
                if v["decision"] == budget.ALLOW or (
                        booked and v["decision"] == budget.DELAY
                        and v.get("limit") != "max_kw"):
                    # scheduler.py honours a peak-band booking at its time
                    start(dev_id, now, asked)
                    load += budget.power_w({"type": dev_id}, ON)
                elif v["decision"] == budget.DELAY:
                    nxt = 0 if tries < 0 else tries + (1 if v.get("limit") == "max_kw" else 0)
                    if nxt > budget.CAP_MAX_RETRIES:
                        run.gave_up += 1
                        continue
                    when = datetime.fromisoformat(v["scheduled_at"])
                    queue.append((when, dev_id, asked, nxt))
                else:
                    run.gave_up += 1
            run.load_kw.append(round(load / 1000.0, 3))
            run.base_kw.append(round(base / 1000.0, 3))
    return run


# ------------------------------------------------------------------ metrics

def metrics(run: Run) -> dict:
    R = regions.get(REGION)
    h = STEP / 60.0
    kwh = peak_kwh = cost = 0.0
    for i, kw in enumerate(run.load_kw):
        t = START + timedelta(minutes=i * STEP)
        e = kw * h
        kwh += e
        in_peak = budget._in_peak(t, R)
        peak_kwh += e if in_peak else 0.0
        cost += e * (R["peak"] if in_peak else R["offpeak"])
    over = sum(1 for kw in run.load_kw if kw > MAX_KW) * STEP
    over_base = sum(1 for kw in run.base_kw if kw > MAX_KW) * STEP
    waits = [s["wait_min"] for s in run.starts]
    return {
        "peak_kw": max(run.load_kw),
        "minutes_over_cap": over,
        "minutes_over_cap_unmovable": over_base,
        "kwh": round(kwh, 1),
        "peak_band_kwh": round(peak_kwh, 1),
        "cost_krw": round(cost),
        "co2_kg": round(kwh * R["co2_kg_per_kwh"], 1),
        "chores_done": len(run.starts),
        "chores_not_done": run.gave_up,
        "avg_wait_min": round(sum(waits) / len(waits)) if waits else 0,
        "max_wait_min": max(waits) if waits else 0,
    }


# ------------------------------------------------------------------ chart

COLORS = {"no_fsd": "#2a78d6", "peak_shift": "#eb6834", "max_kw": "#1baf7a"}
TITLES = {"no_fsd": "No FSD", "peak_shift": "Strict Mode, peak shift only",
          "max_kw": f"Strict Mode + {MAX_KW:g} kW cap"}


def svg(runs: list[Run], day: int) -> str:
    W, PH, L, R_, T = 980, 150, 56, 20, 90
    gap = 34
    H = T + len(runs) * (PH + gap) + 20
    ymax = 6.0
    x = lambda m: L + (W - L - R_) * m / (24 * 60)        # noqa: E731
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
           f'width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{L}" y="26" font-size="17" font-weight="700" fill="#191f28">'
           f'Whole-house load on {(START + timedelta(days=day)):%a %d %b} (simulated), kW</text>',
           f'<text x="{L}" y="46" font-size="12.5" fill="#4e5968">Shaded: KEPCO time-of-use '
           f'peak band 16–22 h. Dashed: {MAX_KW:g} kW cap. Same household, same requests in all three.</text>']
    for k, run in enumerate(runs):
        top = T + k * (PH + gap)
        y = lambda kw: top + PH - PH * min(kw, ymax) / ymax  # noqa: E731
        out.append(f'<rect x="{x(16*60):.1f}" y="{top}" width="{x(22*60)-x(16*60):.1f}" '
                   f'height="{PH}" fill="#f2f4f6"/>')
        for kw in (0, 2, 4, 6):
            out.append(f'<line x1="{L}" x2="{W-R_}" y1="{y(kw):.1f}" y2="{y(kw):.1f}" '
                       f'stroke="#e5e8eb" stroke-width="1"/>')
            out.append(f'<text x="{L-8}" y="{y(kw)+4:.1f}" font-size="11" fill="#6b7684" '
                       f'text-anchor="end">{kw}</text>')
        out.append(f'<line x1="{L}" x2="{W-R_}" y1="{y(MAX_KW):.1f}" y2="{y(MAX_KW):.1f}" '
                   f'stroke="#191f28" stroke-width="1.2" stroke-dasharray="5 4"/>')
        series = run.load_kw[day * 24 * 60 // STEP:(day + 1) * 24 * 60 // STEP]
        pts = []
        for i, kw in enumerate(series):
            m = i * STEP
            if pts:
                pts.append(f"{x(m):.1f},{float(pts[-1].split(',')[1]):.1f}")
            pts.append(f"{x(m):.1f},{y(kw):.1f}")
        area = f"{x(0):.1f},{y(0):.1f} " + " ".join(pts) + f" {x(24*60):.1f},{y(0):.1f}"
        c = COLORS[run.name]
        out.append(f'<polygon points="{area}" fill="{c}" fill-opacity="0.14"/>')
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{c}" '
                   f'stroke-width="2" stroke-linejoin="round"/>')
        pk = max(series)
        out.append(f'<text x="{L}" y="{top-9}" font-size="14" font-weight="700" fill="#191f28">'
                   f'<tspan fill="{c}">■</tspan> {TITLES[run.name]}'
                   f'<tspan font-weight="400" fill="#4e5968">  ·  peak this day {pk:.1f} kW</tspan></text>')
        # Name the spike nobody can move, and the rebound the cap removes.
        spike = max(range(len(series)), key=lambda i: series[i])
        if run.name != "no_fsd":
            out.append(f'<text x="{x(spike*STEP)+8:.1f}" y="{y(series[spike])+12:.1f}" font-size="11" '
                       f'fill="#4e5968">cooking + AC: not movable</text>')
        if run.name == "peak_shift":
            late = [i for i in range(22 * 60 // STEP, len(series)) if series[i] > MAX_KW]
            if late:
                out.append(f'<text x="{x(late[0]*STEP)-6:.1f}" y="{y(series[late[0]])-6:.1f}" '
                           f'font-size="11" fill="#4e5968" text-anchor="end">'
                           f'every chore restarts at 22:00 → new peak</text>')
        if run.name == "max_kw":
            out.append(f'<text x="{W-R_-4}" y="{y(MAX_KW)-6:.1f}" font-size="11" '
                       f'fill="#4e5968" text-anchor="end">chores staggered under the cap</text>')
        if k == len(runs) - 1:
            for hh in range(0, 25, 3):
                out.append(f'<text x="{x(hh*60):.1f}" y="{top+PH+16}" font-size="11" '
                           f'fill="#6b7684" text-anchor="middle">{hh:02d}:00</text>')
    out.append("</svg>")
    return "\n".join(out)


# ------------------------------------------------------------------ report

def report(res: dict) -> str:
    a, b, c = res["no_fsd"], res["peak_shift"], res["max_kw"]

    def pct(new, old):
        return f"{(new - old) / old * 100:+.0f}%" if old else "—"

    rows = [
        ("Highest load (kW)", "peak_kw", "{:.1f}"),
        (f"Minutes over {MAX_KW:g} kW", "minutes_over_cap", "{:,}"),
        ("… of which cooking / cooling alone", "minutes_over_cap_unmovable", "{:,}"),
        ("Energy used (kWh)", "kwh", "{:.1f}"),
        ("kWh in the 16–22 h peak band", "peak_band_kwh", "{:.1f}"),
        ("Cost, time-of-use (₩)", "cost_krw", "{:,}"),
        ("CO₂ (kg)", "co2_kg", "{:.1f}"),
        ("Chores done / not done", None, None),
        ("Average / longest wait (min)", None, None),
    ]
    lines = ["| | No FSD | Peak shift only | Peak shift + "
             f"{MAX_KW:g} kW cap | Cap vs no FSD |",
             "|---|---:|---:|---:|---:|"]
    for label, key, fmt in rows:
        if key:
            lines.append(f"| {label} | {fmt.format(a[key])} | {fmt.format(b[key])} | "
                         f"{fmt.format(c[key])} | {pct(c[key], a[key])} |")
        elif label.startswith("Chores"):
            lines.append(f"| {label} | {a['chores_done']} / {a['chores_not_done']} | "
                         f"{b['chores_done']} / {b['chores_not_done']} | "
                         f"{c['chores_done']} / {c['chores_not_done']} | |")
        else:
            lines.append(f"| {label} | {a['avg_wait_min']} / {a['max_wait_min']} | "
                         f"{b['avg_wait_min']} / {b['max_wait_min']} | "
                         f"{c['avg_wait_min']} / {c['max_wait_min']} | |")
    return "\n".join(lines)


def main() -> dict:
    tmp = tempfile.TemporaryDirectory()
    db.DATA_DIR = Path(tmp.name)
    db.DB_PATH = db.DATA_DIR / "eval.db"
    db.init()

    days = household(random.Random(SEED))
    runs = [simulate(m, days) for m in ("no_fsd", "peak_shift", "max_kw")]
    res = {r.name: metrics(r) for r in runs}
    # The day whose no-FSD peak is highest is the one worth drawing.
    # The day where the cap changes the most is the one worth drawing.
    per_day = [sum(1 for kw in runs[1].load_kw[d * 1440 // STEP:(d + 1) * 1440 // STEP] if kw > MAX_KW)
               - sum(1 for kw in runs[2].load_kw[d * 1440 // STEP:(d + 1) * 1440 // STEP] if kw > MAX_KW)
               for d in range(DAYS)]
    show = per_day.index(max(per_day))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "week.json").write_text(json.dumps(
        {"assumptions": {"region": REGION, "max_kw": MAX_KW, "start": START.isoformat(),
                         "days": DAYS, "step_min": STEP, "seed": SEED},
         "results": res,
         "starts": {r.name: r.starts for r in runs}}, indent=2), encoding="utf-8")
    (OUT / "week.svg").write_text(svg(runs, show), encoding="utf-8")
    (OUT / "week.md").write_text(
        "# Simulated week: before and after Strict Mode\n\n"
        "Generated by `python -m eval.week`. One model household, seven days, "
        "one-minute steps (plus a quiet eighth day so late bookings can finish). Every FSD start goes through the real `budget.can_start()`. "
        "Cooking, lights, the fridge and cooling run the same in all three columns.\n\n"
        + report(res) + "\n\n![load curves](week.svg)\n\n"
        "Shifting a load in time does not change kWh or CO₂ with one national grid "
        "factor, so those rows stay equal; the saving is in peak kW and time-of-use cost. "
        "Prices: KEPCO residential time-of-use (Jeju), peak 16–22 h.\n",
        encoding="utf-8")
    tmp.cleanup()
    return res


if __name__ == "__main__":
    out = main()
    print(json.dumps(out, indent=2))
