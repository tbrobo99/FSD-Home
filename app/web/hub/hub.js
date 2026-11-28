/* FSD-Home "hub" interface (v4) — device-card home, rooms, energy, menu.
   Figma: page "★ v4 · Hub Screens (English)". No build step, no framework.
   Every device start goes through the agent's command path, so the energy
   gate (budget, max kW, peak band) answers exactly as it does in chat. */
"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ico = (id, cls = "ic") => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;

const lang = "en";   // the hub interface is English only
let tab = (location.hash || "#home").slice(1);
let room = "all";
let snap = null, pro = null, budget = null, week = null;
let today = null, auditRows = null;
let savePeriod = "week", actFilter = "all";
let region = "KR";
try { region = localStorage.getItem("hub.region") || "KR"; } catch (_) {}
let lastReply = "";
let serverSkewMs = 0;   // server-local clock minus phone clock (deadlines are server-local)

const T = {
  en: {
    "tab.home": "Home", "tab.devices": "Devices", "tab.energy": "Energy", "tab.activity": "Activity", "tab.menu": "Menu",
    home: "My Home", devices: "Devices", energy: "Energy", activity: "Activity", menu: "Menu",
    justNow: "Just now", quiet: (m) => `Quiet ${m} min`, region: "Country / region",
    savedTitle: "What the house saved", today: "Today", thisWeek: "This week",
    kwhSaved: "kWh saved", offBill: "off the bill", co2Avoided: "kg CO₂ avoided",
    shifted: (k) => `${k} kWh moved off the peak`, moldN: (n) => `Mold prevented ${n}×`,
    noneSaved: "Nothing saved yet in this period.",
    noTou: "No common time-of-use rate here, so moving a load saves no money.",
    saveNote: (p, src, f, csrc) => `Estimate from executed actions · ${p}/kWh (${src}) · ${f} kg CO₂/kWh (${csrc})`,
    timeline: "What the house did", locked: "Can't be edited", fAll: "All", fAsked: "Asked you", fDone: "Done",
    empty: "Nothing recorded yet.", earlier: "Earlier design (/soft/)", console: "Console (other screen versions)",
    calm: "All cozy at home", calmSub: "FSD checks the house every second",
    watch: "Keeping an eye on it", act: "The house is handling it",
    mold: "Mold", thermal: "Kitchen", heat: "Heat", power: "Power",
    map: "Home map", fav: "Favorites", all: "All",
    month: "Electricity this month", now: "Now", limit: "limit", peak: "peak rate", offpeak: "off-peak",
    on: "On", off: "Off", offline: "Offline", scheduled: "scheduled",
    usage: "Used", cost: "Estimated cost", left: "Budget left", maxkw: "Max power limit",
    maxkwNote: "A washer, dryer or dishwasher that would go over the limit is booked for later instead of started. Cooling, the fridge and air cleaning are never limited.",
    setLimit: "Set limit", limitOff: "No limit", jobs: "Booked", noJobs: "Nothing booked",
    savings: "Saved this week", estimate: "estimate", money: "Money", kwh: "Energy",
    decisions: "Recent decisions", noDecisions: "No decisions yet",
    talk: "Talk to your home", talkPh: "e.g. turn on the dryer", send: "Send",
    language: "Language", demo: "Demo situations", activity: "Activity", classic: "Other screens",
    askNo: "No", askYes: "Yes", askNote: (s) => `No answer in ${s}? Nothing changes.`,
    askTitle: "Your home needs an answer", spare: (k) => `${k} kW spare`, over: "over the limit",
    sc: { idle: "Normal", mold: "Wet washer", oven: "Dryer + dinner soon", heatwave: "Hot night", peak: "Peak rate" },
    byHand: "Started by hand (demo)", press: { dryer: "Someone starts the dryer", styler: "Someone starts the Styler", dehumidifier: "Someone starts the dehumidifier" },
    rooms: { "Living Room": "Living room", Kitchen: "Kitchen", Bedroom: "Bedroom", Balcony: "Balcony" },
    cool: "cool", sent: "Sent", failed: "Could not reach the house",
    q: { stop_dryer: "Stop the dryer now?", preheat_cooktop: "Preheat the cooktop?", pause_washer: "Pause the wash until the peak ends?" },
    rk: { thermal: "The kitchen is getting too hot", mold: "The washer is trapping moisture", heat_health: "A room is too hot", energy: "The house is drawing a lot of power" },
    tg: { kitchen: (v) => `Kitchen ${v}°`, dryer: "Dryer running", cook: (m) => `Cooking in ${m} min`, load: (v) => `${v} kW now` },
  },
};
const t = (k) => (T.en[k] !== undefined ? T.en[k] : k);

/* What each whitelisted action is called, and how it reads once done. */
const ACTION = {
  ventilate_washer: ["Air the washer drum", "Washer drum airing"], dehumidifier_on: ["Dehumidifier on", "Dehumidifier on"],
  purifier_on: ["Air purifier on", "Air purifier on"], ventilate_kitchen: ["Range hood on", "Range hood on"],
  precool_kitchen: ["Pre-cool the kitchen", "Kitchen pre-cooling to 24°"], stop_dryer: ["Stop the dryer", "Dryer stopped — restart it whenever you like"],
  preheat_cooktop: ["Preheat the cooktop", "Cooktop preheating for dinner"], cooktop_auto_off: ["Cooktop safety shut-off", "Cooktop switched off for safety"],
  cool_room: ["Cool the room gently", "Room cooling gently to 26°"], eco_ac: ["Raise the AC 2°", "AC raised 2° to save power"],
  pause_washer: ["Pause the wash", "Wash paused — restart it after the peak"],
};
const SAVE_KIND = { eco_ac: "AC raised at peak", cooktop_auto_off: "Cooktop auto-off", ventilate_washer: "Washer aired (no tub clean)",
  pause_washer: "Wash moved off peak", stop_dryer: "Dryer paused while cooking" };
const REGIONS = [["KR", "Korea"], ["US", "United States (average)"], ["US-AZ", "Arizona"], ["US-CA", "California"],
  ["US-FL", "Florida"], ["US-NY", "New York"], ["US-TX", "Texas"], ["US-WA", "Washington"], ["GB", "United Kingdom"],
  ["DE", "Germany"], ["FR", "France"], ["ES", "Spain"], ["IT", "Italy"]];
const actName = (k) => (ACTION[k] || [k])[0];
const actDone = (k) => (ACTION[k] || [])[1];
const hhmm = (ts) => String(ts || "").slice(11, 16);
const money = (v, s, digits) => {
  const d = digits != null ? digits : ((s && s.decimals) || 0);
  try { return Number(v || 0).toLocaleString(s && s.locale || "en", { style: "currency", currency: s.currency, currencyDisplay: "narrowSymbol", minimumFractionDigits: d, maximumFractionDigits: d }); }
  catch (_) { return `${(s && s.symbol) || ""}${Number(v || 0).toFixed(d)}`; }
};
const num = (v, d = 1) => Number(v || 0).toLocaleString("en", { maximumFractionDigits: d });
const ICON = { air_conditioner: "i-ac", air_purifier: "i-purifier", range_hood: "i-hood", oven: "i-oven",
  cooktop: "i-cooktop", refrigerator: "i-fridge", washer: "i-washer", dryer: "i-dryer", dehumidifier: "i-dehumid",
  styler: "i-sparkle" };
const ROOMS = ["Living Room", "Kitchen", "Bedroom", "Balcony"];
const FAV = ["ac_living", "washer", "dryer", "purifier"];
const nameOf = (d) => d.name;
const roomOf = (r) => t("rooms")[r] || r;

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...opts });
  if (res.status === 401) { location.href = "/login"; throw new Error("auth"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok && !data.error) data.error = `HTTP ${res.status}`;
  return data;
}

function toast(msg, ms = 4200) {
  const el = $("#toast"); el.textContent = msg; el.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

/* ---------------- derived state */
const jobFor = (id) => ((snap && snap.jobs) || []).find((j) => j.enabled && j.device_id === id && j.next_run_at);
function devStatus(d) {
  const s = d.state || {};
  if (d.online === false) return [t("offline"), ""];
  const job = jobFor(d.id);
  if (!s.power && job) return [`${String(job.next_run_at).slice(11, 16)} ${t("scheduled")}`, "sched"];
  if (!s.power) {
    if (d.type === "washer" && s.state && s.state !== "standby") return [s.state, ""];
    return [t("off"), ""];
  }
  if (d.type === "air_conditioner") return [`${t("on")} · ${t("cool")} ${s.target_temp}°`, ""];
  if (d.type === "refrigerator") return [`${s.fridge_temp}°C`, ""];
  if (d.type === "washer" && s.state) return [`${t("on")} · ${s.state}`, ""];
  return [t("on"), ""];
}
function riskLevel(score) { return score >= 50 ? "danger" : score >= 30 ? "warn" : ""; }

/* ---------------- render pieces */
function devCard(d) {
  const on = !!(d.state && d.state.power);
  const [st, cls] = devStatus(d);
  return `<div class="dev ${on ? "on" : ""}">
    <div class="dev-top"><span class="dev-ic">${ico(ICON[d.type] || "i-devices")}</span>
      <button class="pwr" type="button" data-power="${esc(d.id)}" aria-pressed="${on}" aria-label="${esc(nameOf(d))} ${on ? t("off") : t("on")}" ${d.online === false ? "disabled" : ""}>${ico("i-power")}</button></div>
    <b>${esc(nameOf(d))}</b><span class="st ${cls}">${esc(st)}</span></div>`;
}

function energyMini() {
  const e = (pro && pro.energy) || (budget || {});
  const pct = Math.min(100, e.used_pct || 0);
  const load = ((snap && snap.total_load_w) || 0) / 1000;
  const band = (snap && snap.tariff) === "peak" ? t("peak") : t("offpeak");
  const cap = e.max_kw ? ` · ${t("limit")} ${e.max_kw} kW` : "";
  return `<button class="card" type="button" data-go="energy" style="text-align:left">
    <div class="sec"><h2>${t("month")}</h2><span class="small muted"><b>${e.used_kwh ?? "–"}</b> / ${e.budget_kwh ?? "–"} kWh</span></div>
    <div class="bar ${(e.used_pct || 0) > 100 ? "over" : pct > 80 ? "warn" : ""}"><i style="width:${pct}%"></i></div>
    <span class="small muted">${t("now")} ${load.toFixed(1)} kW${cap} · ${band}</span></button>`;
}

function viewHome() {
  const r = (pro && pro.risks) || {};
  const sc = (k) => Math.round((r[k] && r[k].score) || 0);
  const scores = [["mold", sc("mold")], ["thermal", sc("thermal")], ["heat", sc("heat_health")], ["power", sc("energy")]];
  const worst = Math.max(...scores.map((s) => s[1]));
  const head = worst >= 50 ? [t("act"), "danger"] : worst >= 30 ? [t("watch"), "warn"] : [t("calm"), ""];
  const firing = Object.values(r).filter((x) => x && x.firing).sort((a, b) => b.score - a.score)[0];
  const headline = firing ? (t("rk")[firing.kind] || firing.headline) : "";
  const devs = (snap && snap.devices) || [];
  const byRoom = (rm) => devs.filter((d) => d.room === rm);
  const box = { "Living Room": [2, 3, 42, 94], Kitchen: [46, 3, 52, 46], Bedroom: [46, 52, 24, 45], Balcony: [72, 52, 26, 45] };
  const plan = ROOMS.map((rm) => { const [x, y, w, h] = box[rm];
    return `<div class="room" style="left:${x}%;top:${y}%;width:${w}%;height:${h}%"><span>${esc(roomOf(rm))}</span>
      <div class="pins">${byRoom(rm).map((d) => `<i class="pin ${d.state && d.state.power ? "on" : ""}" title="${esc(nameOf(d))}">${ico(ICON[d.type] || "i-devices")}</i>`).join("")}</div></div>`; }).join("");
  const favs = FAV.map((id) => devs.find((d) => d.id === id)).filter(Boolean);
  return `
    <section class="card">
      <div class="status-top"><i class="dot ${head[1]}"></i><div><b>${esc(head[0])}</b><span class="small muted">${esc(headline || t("calmSub"))}</span></div></div>
      <div class="risks">${scores.map(([k, v]) => `<div class="risk ${riskLevel(v)}"><b>${v}</b><span>${t(k)}</span>${quietFor(k) ? `<em class="quiet">${t("quiet")(quietFor(k))}</em>` : ""}</div>`).join("")}</div>
    </section>
    ${justNow()}
    <section class="card"><button class="sec" type="button" data-go="devices"><h2>${t("map")}</h2><span class="faint">${ico("i-right", "ic sm")}</span></button>
      <div class="plan">${plan}</div></section>
    <div class="sec"><h2>${t("fav")}</h2><button type="button" class="muted small" data-go="devices">${t("all")}</button></div>
    <div class="grid">${favs.map(devCard).join("")}</div>
    ${energyMini()}`;
}

/* A risk that asked and got "no" (or no answer) stays quiet for 30 minutes. */
function quietFor(label) {
  const kind = { thermal: "thermal", power: "energy" }[label];
  if (!kind || !pro) return 0;
  const left = (pro.quiet || {})[kind] || 0;
  const asking = (pro.pending_approvals || []).some((a) => a.risk_kind === kind);
  const firing = ((pro.risks || {})[kind] || {}).firing;
  return left > 60 && firing && !asking ? Math.ceil(left / 60) : 0;
}

/* Executed actions of the last 15 minutes, in plain words. */
function justNow() {
  if (pro && (pro.pending_approvals || []).length) return "";
  const now = Date.now() + serverSkewMs;
  const rows = (auditRows || []).filter((r) => r.event_type === "action.executed" && actDone(r.action_kind)
    && now - Date.parse(r.ts) < 15 * 60 * 1000).slice(0, 3);
  if (!rows.length) return "";
  return `<section class="card"><div class="sec"><h2>${t("justNow")}</h2></div>
    <div class="list">${rows.map((r) => `<div class="item"><span class="item-ic ok">${ico("i-check")}</span>
      <div><b>${esc(actDone(r.action_kind))}</b><span class="small muted">${esc(r.approval_id ? "You said yes" : r.channel === "safety-interlock" ? "Safety rule" : "Did it on its own")} · ${esc(hhmm(r.ts))}</span></div></div>`).join("")}</div></section>`;
}

function viewDevices() {
  const devs = (snap && snap.devices) || [];
  const rooms = room === "all" ? ROOMS : [room];
  return rooms.map((rm) => { const list = devs.filter((d) => d.room === rm); if (!list.length) return "";
    return `<p class="group-title">${esc(roomOf(rm))} · ${list.length}</p><div class="grid">${list.map(devCard).join("")}</div>`; }).join("");
}

function jobWhy(j) {
  if (j.last_result && j.last_result.startsWith("Waiting")) return "Still over the limit, moved again";
  if (/deferral|Delayed/.test(j.source_text || "")) return "Moved by the peak rate or the kW limit";
  return j.summary || j.source_text || "";
}

function viewEnergy() {
  const e = (budget || (pro && pro.energy) || {});
  const pct = Math.min(100, e.used_pct || 0);
  const load = ((snap && snap.total_load_w) || 0) / 1000;
  const cap = e.max_kw;
  const capPct = cap ? Math.min(100, (load / cap) * 100) : 0;
  const sym = e.symbol || "₩";
  const jobs = ((snap && snap.jobs) || []).filter((j) => j.enabled && j.next_run_at);
  const decs = (e.decisions || []).slice(0, 6);
  const w = week || {};
  const fmtMoney = (v) => (v == null ? "–" : `${w.symbol || sym}${Number(v).toLocaleString(undefined, { maximumFractionDigits: w.decimals ?? 0 })}`);
  return `
    <section class="card">
      <span class="small muted">${t("month")}</span>
      <div class="big"><b>${e.used_kwh != null ? Number(e.used_kwh).toFixed(1) : "–"}</b><span class="muted">/ ${e.budget_kwh ?? "–"} kWh</span></div>
      <div class="bar ${(e.used_pct || 0) > 100 ? "over" : pct > 80 ? "warn" : ""}"><i style="width:${pct}%"></i></div>
      <div class="row"><span class="muted">${t("cost")}</span><b>${sym}${Number(e.estimated_cost || 0).toLocaleString()}</b></div>
      <div class="row"><span class="muted">${t("left")}</span><b>${e.remaining_kwh ?? "–"} kWh</b></div>
    </section>
    <section class="card">
      <div class="sec"><h2>${t("maxkw")}</h2><b style="color:var(--accent)">${cap ? cap + " kW" : t("limitOff")}</b></div>
      ${cap ? `<div class="bar ${load > cap ? "over" : capPct > 80 ? "warn" : "ok"}"><i style="width:${capPct}%"></i></div>
      <span class="small muted">${t("now")} ${load.toFixed(1)} kW · ${load > cap ? t("over") : t("spare")((cap - load).toFixed(1))}</span>` : ""}
      <span class="tiny faint">${t("maxkwNote")}</span>
      <div class="limit"><input id="maxkw" type="number" inputmode="decimal" min="0.5" max="50" step="0.1" placeholder="kW" value="${cap || ""}" aria-label="${t("maxkw")}">
        <button class="btn" type="button" id="saveCap">${t("setLimit")}</button></div>
    </section>
    <section class="card"><div class="sec"><h2>${t("jobs")}</h2></div>
      <div class="list">${jobs.length ? jobs.map((j) => `<div class="item"><span class="item-ic">${ico("i-clock")}</span>
        <div><b>${esc(j.device_name)} · ${esc(String(j.next_run_at).slice(11, 16))}</b>
        <span class="small muted">${esc(jobWhy(j))}</span></div></div>`).join("") : `<span class="empty">${t("noJobs")}</span>`}</div></section>
    <section class="card"><div class="sec"><h2>${t("savings")}</h2><span class="tiny faint">${t("estimate")}</span></div>
      <div class="pills"><div class="pill"><b>${fmtMoney(w.money_saved)}</b><span>${t("money")}</span></div>
        <div class="pill"><b>${w.kwh_saved != null ? (Number(w.kwh_saved) + Number(w.kwh_shifted || 0)).toFixed(1) : "–"} kWh</b><span>${t("kwh")}</span></div>
        <div class="pill"><b>${w.co2_kg != null ? Number(w.co2_kg).toFixed(1) : "–"} kg</b><span>CO₂</span></div></div></section>
    <section class="card"><div class="sec"><h2>${t("decisions")}</h2></div>
      <div class="list">${decs.length ? decs.map((d) => `<div class="small"><span class="tag ${String(d.decision).toLowerCase()}">${esc(d.decision)}</span><b>${esc(d.device_id)}</b> <span class="faint">${esc(String(d.ts || "").slice(11, 16))} · ${esc(d.source)}</span><div class="muted">${esc(d.reason)}</div></div>`).join("") : `<span class="empty">${t("noDecisions")}</span>`}</div></section>`;
}

function auditItem(r) {
  const act = r.action_kind ? actName(r.action_kind) : (r.device_id || "");
  const req = r.approval_id ? `request ${String(r.approval_id).slice(0, 6)}` : "";
  switch (r.event_type) {
    case "risk.triggered":
      return ["warn", "i-flame", { mold: "Washer getting damp", heat_health: "Room getting hot", energy: "Power use high" }[r.risk_kind] || "Kitchen getting hot",
        `Score ${Math.round(r.risk_score || 0)} · acts at 50`];
    case "approval.proposed": return ["ask", "i-sparkle", `Asked you: ${act}`, req];
    case "approval.confirmed": return ["ok", "i-check", `You said yes: ${act}`, req];
    case "approval.declined": return ["plain", "i-x", `You said no: ${act}`, req];
    case "approval.expired": return ["plain", "i-clock", `No answer: ${act}`, "Nothing changed"];
    case "action.executed": {
      const who = r.approval_id ? "You said yes" : r.channel === "safety-interlock" ? "Safety rule"
        : r.channel === "engine" ? "Did it on its own" : "From the app";
      return ["ok", "i-check", actDone(r.action_kind) || `Done: ${act}`, [who, req].filter(Boolean).join(" · ")];
    }
    case "action.failed": return ["bad", "i-x", `${act || "—"} didn't respond`, r.detail || ""];
    case "action.blocked": return ["plain", "i-x", `Didn't go ahead: ${act}`, r.detail || ""];
    case "energy.decision": return [/^(BLOCK|STOP)/.test(r.detail || "") ? "bad" : /^(DELAY|ALLOW_OVER)/.test(r.detail || "") ? "warn" : "ok", "i-bolt",
      { MAKE_ROOM: "Made room under the kW limit", ALLOW_OVER: "Kept the overshoot small", STOP: "Switched off: over the kW limit",
        STOP_BOOK: "Moved to later: over the kW limit", BLOCK: "Did not start", DELAY: "Booked for later" }[(r.detail || "").split(" ")[0]] || "Energy check",
      (r.detail || "").replace(/^\S+ \S+?: /, "")];
    case "device.started_by_hand": return ["plain", "i-power", "Switched on by hand", r.detail || ""];
    case "notification.dryer_restart": return ["plain", "i-check", "Kitchen cooled down", "You can restart the dryer"];
    case "scenario.reset": return ["plain", "i-sparkle", "Demo situation loaded", r.detail || ""];
    default: return ["plain", "i-clock", r.event_type, r.detail || ""];
  }
}

function viewActivity() {
  const s = savePeriod === "today" ? today : week;
  let save = "";
  if (s) {
    const max = Math.max(0.001, ...s.actions.map((a) => a.kwh_saved + a.kwh_shifted));
    const extra = [s.kwh_shifted ? t("shifted")(num(s.kwh_shifted)) : "", s.mold_prevented ? t("moldN")(s.mold_prevented) : ""].filter(Boolean);
    const a = s.assumptions || {};
    save = `<section class="card">
      <div class="sec"><h2>${t("savedTitle")}</h2><span class="seg">${[["today", t("today")], ["week", t("thisWeek")]].map(([k, v]) =>
        `<button type="button" data-save="${k}" class="${k === savePeriod ? "on" : ""}" aria-pressed="${k === savePeriod}">${v}</button>`).join("")}</span></div>
      <div class="pills"><div class="pill"><b>${esc(num(s.kwh_saved))}</b><span>${t("kwhSaved")}</span></div>
        <div class="pill"><b>${esc(money(s.money_saved, s))}</b><span>${t("offBill")}</span></div>
        <div class="pill"><b>${esc(num(s.co2_kg, 2))}</b><span>${t("co2Avoided")}</span></div></div>
      ${s.actions.length ? `<div class="bars">${s.actions.map((x) => `<div class="bar-row">
        <span>${esc(SAVE_KIND[x.kind] || x.kind)}${x.count > 1 ? ` ×${x.count}` : ""}</span>
        <div class="bar ${x.kwh_saved ? "ok" : ""}"><i style="width:${Math.round(100 * (x.kwh_saved + x.kwh_shifted) / max)}%"></i></div>
        <b>${esc(num(x.kwh_saved + x.kwh_shifted, 2))} kWh</b></div>`).join("")}</div>` : `<span class="empty">${t("noneSaved")}</span>`}
      ${extra.length ? `<span class="small muted">${esc(extra.join(" · "))}</span>` : ""}
      ${!s.tou && s.kwh_shifted ? `<span class="tiny faint">${t("noTou")}</span>` : ""}
      <span class="tiny faint">${esc(t("saveNote")(money(a.price, s, s.decimals ? 3 : 1), a.tariff_source, num(a.co2_kg_per_kwh, 4), a.co2_source))}</span>
    </section>`;
  }
  // Routine ALLOW checks are in the Energy tab's ledger; the timeline keeps
  // the energy verdicts that changed something (BLOCK, DELAY).
  const rows = (auditRows || []).filter((r) => r.event_type !== "action.intent")
    .filter((r) => r.event_type !== "energy.decision" || /^(BLOCK|DELAY|MAKE_ROOM|ALLOW_OVER|STOP)/.test(r.detail || "")).filter((r) =>
    actFilter === "all" ? true : actFilter === "asked" ? r.event_type.startsWith("approval.") : r.event_type === "action.executed");
  return `${save}
    <section class="card">
      <div class="sec"><h2>${t("timeline")}</h2><span class="lock">${ico("i-lock", "ic xs")}${t("locked")}</span></div>
      <span class="seg">${[["all", t("fAll")], ["asked", t("fAsked")], ["done", t("fDone")]].map(([k, v]) =>
        `<button type="button" data-act="${k}" class="${k === actFilter ? "on" : ""}" aria-pressed="${k === actFilter}">${v}</button>`).join("")}</span>
      <div class="list">${rows.length ? rows.slice(0, 30).map((r) => { const [tone, icn, title, detail] = auditItem(r);
        return `<div class="tl" data-tone="${tone}"><span class="item-ic">${ico(icn)}</span>
          <div><b>${esc(title)}</b>${detail ? `<span class="small muted">${esc(detail)}</span>` : ""}<code>${esc(r.event_type)}</code></div>
          <span class="tiny faint">${esc(hhmm(r.ts))}</span></div>`; }).join("") : `<span class="empty">${t("empty")}</span>`}</div>
    </section>`;
}

function viewMenu() {
  return `
    <section class="card"><div class="sec"><h2>${t("talk")}</h2></div>
      <form class="talk" id="talkForm"><input id="talkInput" autocomplete="off" placeholder="${esc(t("talkPh"))}" aria-label="${esc(t("talk"))}">
        <button class="btn" type="submit">${t("send")}</button></form>
      ${lastReply ? `<div class="reply">${esc(lastReply)}</div>` : ""}</section>
    <section class="card">
      <label class="menu-row" for="regionPick"><span>${t("region")}</span>
        <select id="regionPick" aria-label="${t("region")}">${REGIONS.map(([c, n]) => `<option value="${c}" ${c === region ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
      <a class="menu-row" href="/soft/" style="color:inherit;text-decoration:none"><span>${t("earlier")}</span>${ico("i-right", "ic sm")}</a>
      <a class="menu-row" href="/?ui=v6" style="color:inherit;text-decoration:none"><span>${t("console")}</span>${ico("i-right", "ic sm")}</a>
    </section>
    <section class="card"><div class="sec"><h2>${t("demo")}</h2></div>
      ${Object.entries(t("sc")).map(([k, v]) => `<button class="menu-row" type="button" data-scenario="${k}"><span>${esc(v)}</span>${ico("i-right", "ic sm")}</button>`).join("")}</section>
    <section class="card"><div class="sec"><h2>${t("byHand")}</h2></div>
      <span class="tiny faint">As if someone pressed the appliance's own button. With a max kW limit set, the house reacts if this takes it over.</span>
      ${Object.entries(t("press")).map(([k, v]) => `<button class="menu-row" type="button" data-press="${k}"><span>${esc(v)}</span>${ico("i-right", "ic sm")}</button>`).join("")}</section>`;
}

/* ---------------- ask sheet (Level 2 approvals) */
function renderSheet() {
  const list = (pro && pro.pending_approvals) || [];
  const sheet = $("#sheet"), scrim = $("#scrim");
  if (!list.length) { sheet.hidden = scrim.hidden = true; sheet.innerHTML = ""; return; }
  const left = Math.max(0, Math.min(...list.map((a) => (Date.parse(a.expires_at) - (Date.now() + serverSkewMs)) / 1000)));
  const mmss = `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}`;
  const sig = list.map((a) => a.id).join(",");
  if (sheet.dataset.sig !== sig) {
    sheet.dataset.sig = sig;
    const rk = list[0].risk_kind, dr = ((pro.risks || {})[rk] || {}).drivers || {}, tg = t("tg");
    const tags = [];
    if (dr.kitchen_temp != null) tags.push(tg.kitchen(Math.round(dr.kitchen_temp * 10) / 10));
    if (((pro.appliances || {}).dryer || {}).power) tags.push(tg.dryer);
    if (dr.cooking_scheduled_in_min != null) tags.push(tg.cook(dr.cooking_scheduled_in_min));
    if (dr.total_load_w != null) tags.push(tg.load((dr.total_load_w / 1000).toFixed(1)));
    sheet.innerHTML = `<i class="grab"></i><h3>${esc(t("rk")[rk] || t("askTitle"))}</h3>
      <div class="tags">${tags.map((x) => `<span>${esc(x)}</span>`).join("")}</div>
      ${list.map((a) => `<div class="ask"><b>${esc(t("q")[a.action_kind] || a.question)}</b><div class="ask-btns">
        <button type="button" data-answer="no" data-id="${esc(a.id)}">${t("askNo")}</button>
        <button type="button" class="yes" data-answer="yes" data-id="${esc(a.id)}">${t("askYes")}</button></div></div>`).join("")}
      <span class="tiny faint" id="askClock"></span>`;
  }
  const clock = $("#askClock"); if (clock) clock.textContent = t("askNote")(mmss);
  sheet.hidden = scrim.hidden = false;
}

/* ---------------- main render */
function render() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-t]").forEach((el) => { el.textContent = t(el.dataset.t); });
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  $("#titleText").textContent = t(tab);
  const chips = $("#roomChips");
  chips.hidden = tab !== "devices";
  if (tab === "devices") chips.innerHTML = [["all", t("all")], ...ROOMS.map((r) => [r, roomOf(r)])]
    .map(([k, v]) => `<button class="chip ${room === k ? "on" : ""}" type="button" data-room="${esc(k)}">${esc(v)}</button>`).join("");
  const keep = document.activeElement && document.activeElement.id;
  const val = keep && document.activeElement.value;
  if (!(keep === "maxkw" || keep === "talkInput" || keep === "regionPick")) {
    $("#view").innerHTML = tab === "devices" ? viewDevices() : tab === "energy" ? viewEnergy()
      : tab === "activity" ? viewActivity() : tab === "menu" ? viewMenu() : viewHome();
  }
  const pend = ((pro && pro.pending_approvals) || []).length;
  $("#bellBadge").hidden = !pend && !((snap && snap.jobs) || []).some((j) => j.enabled && j.next_run_at);
  renderSheet();
  if (keep && val != null && $("#" + keep)) $("#" + keep).value = val;
}

async function refresh() {
  try {
    const rg = encodeURIComponent(region);
    const [s, p, b, w, d, a] = await Promise.all([
      api("/api/snapshot"), api("/api/proactive").catch(() => null),
      tab === "energy" ? api(`/api/budget?region=${rg}`).catch(() => null) : Promise.resolve(budget),
      tab === "energy" || tab === "activity" ? api(`/api/savings?period=week&region=${rg}`).catch(() => null) : Promise.resolve(week),
      tab === "activity" ? api(`/api/savings?period=today&region=${rg}`).catch(() => null) : Promise.resolve(today),
      tab === "home" || tab === "activity" ? api("/api/audit?limit=60").then((x) => x.audit).catch(() => null) : Promise.resolve(auditRows),
    ]);
    snap = s; pro = p; budget = b; week = w; today = d; auditRows = a;
    if (s && s.now) { const n = Date.parse(s.now); if (!isNaN(n)) serverSkewMs = n - Date.now(); }
    render();
  } catch (e) { if (e.message !== "auth") toast(t("failed")); }
}

/* ---------------- events */
function go(name) { tab = ["home", "devices", "energy", "activity", "menu"].includes(name) ? name : "home";
  history.replaceState(null, "", "#" + tab); window.scrollTo(0, 0); render(); refresh(); }

document.addEventListener("click", async (ev) => {
  const el = ev.target.closest("button, a"); if (!el) return;
  if (el.dataset.tab) return go(el.dataset.tab);
  if (el.dataset.go) return go(el.dataset.go);
  if (el.dataset.room) { room = el.dataset.room; return render(); }
  if (el.dataset.save) { savePeriod = el.dataset.save; return render(); }
  if (el.dataset.act) { actFilter = el.dataset.act; return render(); }
  if (el.dataset.power) {
    // Through the command path, not the raw control API: a start meets the
    // same energy gate as a spoken command and can be booked for later.
    const d = snap.devices.find((x) => x.id === el.dataset.power); if (!d) return;
    const on = !!(d.state && d.state.power);
    el.disabled = true;
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ text: `turn ${on ? "off" : "on"} the ${d.name}` }) }).catch(() => ({}));
    if (r.reply) toast(r.reply, 6500);
    if (r.snapshot) snap = r.snapshot;
    return refresh();
  }
  if (el.dataset.answer) {
    el.disabled = true;
    const r = await api(`/api/approvals/${encodeURIComponent(el.dataset.id)}/${el.dataset.answer === "yes" ? "confirm" : "decline"}`, { method: "POST", body: "{}" }).catch(() => ({}));
    if (r.message || r.reply || r.error) toast(r.message || r.reply || r.error);
    $("#sheet").dataset.sig = "";
    return refresh();
  }
  if (el.dataset.press) {
    const r = await api("/api/demo/press", { method: "POST", body: JSON.stringify({ device_id: el.dataset.press, on: true }) }).catch(() => ({}));
    toast(r.error || `${r.device} switched on by hand`); return go("home");
  }
  if (el.dataset.scenario) {
    await api("/api/scenario", { method: "POST", body: JSON.stringify({ name: el.dataset.scenario }) }).catch(() => {});
    toast(t("sc")[el.dataset.scenario]); return go("home");
  }
  if (el.id === "saveCap") {
    const v = $("#maxkw").value.trim();
    const r = await api("/api/budget", { method: "POST", body: JSON.stringify({ max_kw: v === "" ? null : Number(v) }) });
    toast(r.error || (r.max_kw ? `${t("maxkw")}: ${r.max_kw} kW` : t("limitOff")));
    $("#maxkw").blur(); return refresh();
  }
});

window.addEventListener("hashchange", () => {
  const h = (location.hash || "#home").slice(1);
  if (h !== tab) go(h);
});

document.addEventListener("change", (ev) => {
  if (ev.target.id !== "regionPick") return;
  region = ev.target.value;
  try { localStorage.setItem("hub.region", region); } catch (_) {}
  week = today = budget = null;
  ev.target.blur(); refresh();
});

document.addEventListener("submit", async (ev) => {
  if (ev.target.id !== "talkForm") return;
  ev.preventDefault();
  const input = $("#talkInput"); const text = input.value.trim(); if (!text) return;
  input.value = ""; input.blur();
  const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ text }) }).catch(() => ({}));
  lastReply = r.reply || r.error || t("failed"); render(); refresh();
});

try {
  const es = new EventSource("/api/events");
  es.onmessage = (e) => { try { const ev = JSON.parse(e.data); if (ev.title) toast(`${ev.title}${ev.body ? " · " + ev.body : ""}`); } catch (_) {} refresh(); };
} catch (_) {}

render();
refresh();
setInterval(refresh, 3000);
setInterval(renderSheet, 1000);
