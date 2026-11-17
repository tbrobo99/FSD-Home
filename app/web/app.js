"use strict";

/* FSD-Home front end — v3 "soft modern" (Figma: ★ v3 · Screens).

   Four tabs, each one phone screen: Home (how the house is, in one sentence),
   Devices, Activity (the append-only audit trail, FR-6) and Settings. Talking
   is the button in the middle of the tab bar. A Level 2 question opens a
   bottom sheet by itself: the reason once, then one card per action with its
   own Yes / No (FR-3/FR-4). Silence changes nothing. */

const $ = (sel) => document.querySelector(sel);

const ICON = {
  air_conditioner: "i-ac",
  air_purifier: "i-purifier",
  washer: "i-washer",
  dryer: "i-dryer",
  dish_washer: "i-washer",
  refrigerator: "i-fridge",
  water_heater: "i-fridge",
  dehumidifier: "i-dehumid",
  range_hood: "i-hood",
  oven: "i-oven",
  cooktop: "i-cooktop",
};

const BACKEND_LABEL = { mock: "Simulator", thinq: "LG ThinQ" };
const MODE_LABEL = { cool: "Cool", heat: "Heat", dry: "Dry", fan: "Fan" };
const MODES = ["cool", "heat", "dry", "fan"];
const ROOM_ORDER = ["Living Room", "Kitchen", "Bedroom", "Balcony"];
const ROOM_SHORT = { "Living Room": "Living", Kitchen: "Kitchen", Bedroom: "Bedroom", Balcony: "Balcony" };

let snapshot = null;   // /api/snapshot: devices, jobs, mode
let pro = null;        // /api/proactive: live risks, approvals, sensor state
let auditRows = null;  // /api/audit: the FR-6 trail
let activeRoom = "All";
let actFilter = "all";
let currentTab = "home";
let sheetId = null;
let chipsSig = "";
let askSig = "";            // ids of the pending questions shown in the sheet
let askDismissed = "";      // the user closed the sheet for this set of questions
let askBusy = false;
let spokenAsks = "";

/* ---------- helpers ---------- */

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...(opts || {}),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function findDevice(id) {
  return snapshot ? (snapshot.devices || []).find((d) => d.id === id) : null;
}

function fmtWhen(iso) {
  if (!iso) return "—";
  const when = new Date(iso);
  if (isNaN(when)) return iso;
  const now = new Date();
  const time = when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (when.toDateString() === now.toDateString()) return `today ${time}`;
  const tomorrow = new Date(now);
  tomorrow.setDate(now.getDate() + 1);
  if (when.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`;
  return `${when.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function cap(text) {
  const s = String(text == null ? "" : text);
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function repeatShort(label) {
  const s = String(label || "").trim();
  if (!/every/i.test(s)) return "once";
  return s.replace(/\s+at\s+\d{1,2}:\d{2}\s*$/, "").trim();
}

function icon(id, cls = "ic") {
  return `<svg class="${cls}" aria-hidden="true"><use href="#${id}"/></svg>`;
}

function clock(sec) {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function hhmm(ts) {
  return String(ts || "").slice(11, 16);
}

/* Homie, the house mascot, drawn at status-icon size. Same shapes as the
   Figma "Homie (mascot)" component; one face per mood. */
const MASCOT = {
  calm: { body: "#7ccba8", face: '<circle cx="38" cy="56" r="4.2" fill="#16161c"/><circle cx="58" cy="56" r="4.2" fill="#16161c"/><path d="M41 65 Q48 72 55 65" stroke="#16161c" stroke-width="3.2" stroke-linecap="round" fill="none"/>' },
  busy: { body: "#efa27f", face: '<path d="M33 58 q5 -6 10 0 M53 58 q5 -6 10 0" stroke="#16161c" stroke-width="3.2" stroke-linecap="round" fill="none"/><path d="M42 66 Q48 71 54 66" stroke="#16161c" stroke-width="3.2" stroke-linecap="round" fill="none"/>' },
  ask: { body: "#e9c35b", face: '<circle cx="38" cy="56" r="4.2" fill="#16161c"/><circle cx="58" cy="56" r="4.2" fill="#16161c"/><circle cx="48" cy="68" r="3.6" stroke="#16161c" stroke-width="3" fill="none"/>' },
  watch: { body: "#ea94ad", face: '<circle cx="38" cy="56" r="5.6" fill="#fff" stroke="#16161c" stroke-width="2.4"/><circle cx="58" cy="56" r="5.6" fill="#fff" stroke="#16161c" stroke-width="2.4"/><circle cx="40.5" cy="56" r="2.6" fill="#16161c"/><circle cx="60.5" cy="56" r="2.6" fill="#16161c"/><path d="M43 68 h10" stroke="#16161c" stroke-width="3.2" stroke-linecap="round"/>' },
};

function mascot(mood) {
  const m = MASCOT[mood] || MASCOT.calm;
  return `<svg viewBox="0 0 96 96" aria-hidden="true">
    <use href="#m-house" fill="${m.body}" stroke="#16161c" stroke-width="3.4" stroke-linejoin="round"/>
    ${m.face}</svg>`;
}

/* ---------- words for actions and risks ---------- */

const ACTION_TEXT = {
  ventilate_washer: "Air the washer drum",
  dehumidifier_on: "Dehumidifier on",
  purifier_on: "Air purifier on",
  ventilate_kitchen: "Range hood on",
  precool_kitchen: "Pre-cool the kitchen",
  stop_dryer: "Stop the dryer",
  preheat_cooktop: "Preheat the cooktop",
  cooktop_auto_off: "Cooktop safety shut-off",
};

const ACTION_DONE = {
  ventilate_washer: "Washer drum airing",
  dehumidifier_on: "Dehumidifier on",
  purifier_on: "Air purifier on",
  ventilate_kitchen: "Range hood on",
  precool_kitchen: "Kitchen pre-cooling to 24°",
  stop_dryer: "Dryer stopped — restart it whenever you like",
  preheat_cooktop: "Cooktop preheating for dinner",
  cooktop_auto_off: "Cooktop switched off for safety",
};

/* The Level 2 questions, short. The engine's full sentence stays the
   fallback for any action this table does not know. */
const QUESTION = {
  stop_dryer: { q: "Stop the dryer now?", c: "You restart it yourself later.", icon: "i-dryer", tone: "" },
  preheat_cooktop: { q: "Preheat the cooktop?", c: "Turns itself off if no cooking starts.", icon: "i-cooktop", tone: "pink" },
};

const RISK = {
  mold: { title: "Washer", icon: "i-washer" },
  thermal: { title: "Kitchen", icon: "i-flame" },
};

/* ---------- voice ---------- */


/* The browser listens (Web Speech API), the text box stays permanent: a
   misheard word is fixed by typing, never by repeating yourself at a machine.
   English first because the course, the demo and the examiner are English;
   Korean is one tap away because the household is not. */
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const VOICE_LANGS = ["en-US", "ko-KR"];
const VOICE_PAUSE_MS = 2000;   // a pause this long means "I have finished"
const VOICE_TAIL_QUIET_MS = 900;   // a "final" this long after any audio is a guess
const VOICE_MAX_RESTARTS = 8;
let voiceLang = VOICE_LANGS[0];
let recognition = null;
let listening = false;
let ttsOn = false;
let voiceTimer = null;         // the pause that ends the turn
let voiceRestarts = 0;
let voiceBase = "";            // words finalised before the current session
let voiceSaid = "";            // finalised words so far in this turn
let voiceWarned = false;       // the "I heard nothing" hint, once per turn
let voiceDone = false;         // this turn has already been handed to send()
let voiceFinals = 0;           // finalised phrases the engine has handed back
let voiceInterim = "";         // the last thing the engine was still guessing at
let voiceLastHeard = 0;        // when any text last arrived from the engine

function speak(text, force) {
  if (!(ttsOn || force) || !("speechSynthesis" in window)) return;
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (!clean) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(clean);
  utter.lang = voiceLang;
  utter.rate = 0.95;  // the audience is not in a hurry
  window.speechSynthesis.speak(utter);
}

function setListening(on) {
  listening = on;
  for (const id of ["#mic", "#talkFab"]) {
    const btn = $(id);
    if (!btn) continue;
    btn.classList.toggle("listening", on);
    btn.setAttribute("aria-pressed", String(on));
  }
  const label = $("#micLabel");
  if (label) label.textContent = on ? "Listening — pause when you're done" : "Tap to talk";
  const input = $("#input");
  if (input) input.placeholder = on ? "Listening…" : "Or type here…";
}

/* The engine hands back each phrase without the space that belonged between
   them: "turn on the living" and "room AC" are two phrases, and gluing them
   with + makes one word out of two. */
function glue(a, b) {
  if (!a) return b || "";
  if (!b) return a;
  if (/[\s-]$/.test(a) || /^[\s,.;:!?]/.test(b)) return a + b;
  return `${a} ${b}`;
}

/* End the turn: take whatever is in the box and send it. Guarded by voiceDone so
   the three ways a turn can end — the pause timer, a second tap, the engine
   giving up — cannot send the same sentence twice. */
function voiceFinish() {
  if (voiceDone) return;
  voiceDone = true;
  clearTimeout(voiceTimer);
  voiceTimer = null;
  const text = ($("#input").value || voiceSaid).replace(/\s+/g, " ").trim();
  setListening(false);
  try { if (recognition) recognition.stop(); } catch (_) { /* already stopped */ }
  if (text) send(text);
}

/* The pause, not the engine, decides when the sentence is over. Every result
   pushes it back, so a breath in the middle of a sentence costs nothing. */
function voiceArmPause() {
  clearTimeout(voiceTimer);
  voiceTimer = setTimeout(voiceFinish, VOICE_PAUSE_MS);
}

function startVoice() {
  if (!SpeechRecognition) {
    toast("Voice needs Chrome or Safari — please type.");
    return;
  }
  if (listening) { voiceFinish(); return; }   // a second tap means "send now"
  if (!recognition) {
    recognition = new SpeechRecognition();
    recognition.interimResults = true;
    // continuous, because the default ends the session at the first short
    // pause: "turn on the living room AC … and set it to 24" arrived as two
    // half sentences, and the first half was already on its way to the model
    // before the second half was spoken.
    recognition.continuous = true;
    recognition.onresult = (event) => {
      // Recompute from index 0 rather than from resultIndex: with continuous
      // on, the results list is the whole turn, and rebuilding it is idempotent
      // where appending to it is not.
      const now = Date.now();
      let finalText = "";
      let interim = "";
      let finals = 0;
      for (let i = 0; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finals++;
          finalText = glue(finalText, result[0].transcript);
        } else if (String(result[0].transcript || "").trim()) {
          interim = glue(interim, result[0].transcript);
        }
      }
      if (interim) voiceInterim = interim;

      if (finals > voiceFinals) {
        // Speech is announced before it is confirmed: while a person is
        // talking, the engine keeps handing back interim text, and the final
        // phrase lands as the confirmation of something it was already
        // guessing at. A phrase that arrives with nothing interim behind it,
        // more than a second after the last audio, was not spoken — the engine
        // filled a silence. "turn on living room" came back as "turn on living
        // room they say": the tail was invented, and because every result
        // pushed the pause timer back, a slow speaker's turn never ended on
        // their own schedule.
        const invented = voiceSaid && !voiceInterim &&
                         now - voiceLastHeard > VOICE_TAIL_QUIET_MS;
        if (invented) return;        // drop it; the pause timer keeps running
        voiceFinals = finals;
        voiceInterim = "";
      } else if (!interim) {
        return;                      // nothing new: do not extend the turn
      }

      voiceLastHeard = now;
      voiceSaid = glue(voiceBase, finalText).trim();
      // Live in the box: the user sees what was heard before it is sent, which
      // is the difference between trusting a microphone and guessing at one.
      $("#input").value = glue(voiceSaid, interim).trim();
      voiceArmPause();
    };
    recognition.onerror = (event) => {
      if (voiceDone) return;
      if (event.error === "not-allowed") {
        toast("Microphone permission denied — please type.");
        voiceDone = true;      // stop onend from restarting a denied mic
      } else if (event.error === "no-speech") {
        // Silence, not a pause: nothing at all was captured this turn. On a
        // laptop this is almost always the wrong input device — Windows keeps
        // the headset-jack microphone selected, which is a microphone that
        // exists and hears nothing unless something is plugged into it. Saying
        // so is the difference between "the app is broken" and "pick the other
        // microphone".
        if (!voiceWarned && !voiceSaid && !$("#input").value.trim()) {
          voiceWarned = true;
          toast("I heard nothing — check which microphone Windows is using.");
        }
        return;                // onend decides; nothing was said yet
      } else if (event.error !== "aborted") {
        toast("Voice stopped — please type.");
      }
    };
    recognition.onend = () => {
      if (voiceDone) { setListening(false); return; }
      // Safari ends the session at every pause even with continuous = true.
      // Start it again and keep what has been heard, so the pause timer — not
      // the engine — is what ends the turn. Delayed slightly to avoid spinning.
      if (listening && voiceRestarts < VOICE_MAX_RESTARTS) {
        voiceRestarts++;
        setTimeout(() => {
          if (voiceDone || !listening) return;
          // A new session starts with an empty results list, so bank what the
          // last one heard before asking for more.
          voiceBase = voiceSaid;
          voiceFinals = 0;
          voiceInterim = "";
          try { recognition.start(); } catch (_) { voiceFinish(); }
        }, 250);
        return;
      }
      voiceFinish();
    };
  }
  recognition.lang = voiceLang;
  voiceBase = "";
  voiceSaid = "";
  voiceWarned = false;
  voiceDone = false;
  voiceRestarts = 0;
  voiceFinals = 0;
  voiceInterim = "";
  voiceLastHeard = Date.now();
  clearTimeout(voiceTimer);
  $("#input").value = "";
  try {
    recognition.start();
    setListening(true);
  } catch (err) {
    setListening(false);  // already running; the tap is a stop
  }
}

function renderLangButton() {
  document.querySelectorAll("#lang [data-lang]").forEach((b) => {
    const on = b.dataset.lang === voiceLang;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  });
}

/* ---------- home: hero ---------- */

function riskFacts(r) {
  const d = (r && r.drivers) || {};
  if (!r) return [];
  if (r.kind === "mold") {
    const out = [];
    if (d.drum_humidity != null) out.push(["sky", `Drum ${Math.round(d.drum_humidity)}% humid`]);
    if (d.sealed_hours) out.push(["butter", `Shut ${d.sealed_hours < 1 ? Math.round(d.sealed_hours * 60) + " min" : d.sealed_hours.toFixed(1) + " h"}`]);
    return out;
  }
  const out = [];
  if (d.kitchen_temp != null) out.push(["peach", `${Math.round(d.kitchen_temp)}°C`]);
  if (d.heat_load_w) out.push(["sky", `${(d.heat_load_w / 1000).toFixed(1)} kW running`]);
  if (d.cooking_scheduled_in_min != null) out.push(["butter", `Dinner in ${d.cooking_scheduled_in_min} min`]);
  return out;
}

function heroState() {
  const pending = (pro && pro.pending_approvals) || [];
  const risks = (pro && pro.risks) || {};
  if (pending.length) {
    const r = risks[pending[0].risk_kind];
    const facts = riskFacts(r).map((f) => f[1]).slice(0, 2).join(" · ");
    return { mood: "ask",
             title: pending.length === 1 ? "Can I ask you something?" : `Can I ask you ${pending.length} things?`,
             sub: facts || "It only takes a tap.", cta: true };
  }
  const g = pro && pro.cooktop_guard;
  if (g) {
    let sub;
    if (g.cooking) sub = "Cooking in progress.";
    else if (g.meal_in_min != null) sub = `Turns off by itself if no cooking starts. Dinner in ${Math.round(g.meal_in_min)} min.`;
    else sub = `Turns off in ${Math.ceil(g.idle_off_in / 60)} min if no cooking starts.`;
    return { mood: "watch", title: "Watching the cooktop", sub };
  }
  if (risks.mold && risks.mold.firing) {
    return { mood: "busy", title: "Airing the washer for you", sub: "Drum vent, dehumidifier and purifier are on." };
  }
  if (risks.thermal && risks.thermal.firing) {
    return { mood: "busy", title: "Cooling the kitchen down", sub: "Range hood and kitchen AC are on." };
  }
  return { mood: "calm", title: "All cozy at home", sub: "Nothing needs you right now." };
}

function renderHero() {
  const s = heroState();
  const box = $("#hero");
  box.dataset.mood = s.mood;
  box.innerHTML = `<div class="status-ic">${mascot(s.mood)}</div>
    <div class="hero-text"><strong>${esc(s.title)}</strong><span>${esc(s.sub)}</span></div>
    ${s.cta && $("#askSheet").hidden ? `<button type="button" class="hero-cta" data-open-ask>Answer</button>` : ""}`;
}

/* ---------- home: risk rings (FR-2) ---------- */

const RING_C = 2 * Math.PI * 38;

function riskDetail(r) {
  const d = (r && r.drivers) || {};
  if (r && r.kind === "mold") {
    if (d.drum_humidity == null) return "Washer is open or empty";
    return `${Math.round(d.drum_humidity)}% humid · ${d.sealed_hours >= 1 ? "shut " + d.sealed_hours.toFixed(0) + " h" : "sealed"}`;
  }
  if (!r || d.kitchen_temp == null) return "Nothing hot";
  return `${d.kitchen_temp.toFixed(1)}°C · ${(d.heat_load_w / 1000 || 0).toFixed(1)} kW`;
}

function renderRisks() {
  const risks = (pro && pro.risks) || {};
  const quiet = (pro && pro.quiet) || {};
  const asking = new Set(((pro && pro.pending_approvals) || []).map((a) => a.risk_kind));
  $("#risks").innerHTML = ["mold", "thermal"].map((kind) => {
    const r = risks[kind] || { kind, score: 0, threshold: 50, drivers: {} };
    const score = Math.round(r.score || 0);
    const level = r.firing ? "high" : score >= r.threshold * 0.6 ? "watch" : "low";
    const tag = { high: ["pink", "On it"], watch: ["butter", "Watching"], low: ["mint", "All good"] }[level];
    const q = quiet[kind] > 60 && !asking.has(kind) && r.firing
      ? `<p class="quiet">Quiet for ${Math.ceil(quiet[kind] / 60)} min</p>` : "";
    return `<article class="tile" data-kind="${kind}" data-level="${level}">
      <div class="tile-top">${icon(RISK[kind].icon)}<span class="tag tag-${tag[0]}">${tag[1]}</span></div>
      <div class="ring" role="meter" aria-label="${RISK[kind].title} risk" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${score}">
        <svg viewBox="0 0 84 84"><circle class="track" cx="42" cy="42" r="38"/>
          <circle class="prog" cx="42" cy="42" r="38" stroke-dasharray="${RING_C.toFixed(1)}"
            stroke-dashoffset="${(RING_C * (1 - Math.min(100, Math.max(3, score)) / 100)).toFixed(1)}"/></svg>
        <b>${score}</b>
      </div>
      <h3>${RISK[kind].title}</h3>
      <p>${esc(riskDetail(r))}</p>
      ${q}
    </article>`;
  }).join("");
}

/* ---------- home: heat load ---------- */

const FULL_KITCHEN_W = 2500;   // risk.py: 2500 W counts as a full kitchen

function renderHeat() {
  const w = (pro && pro.heat_load_w) || 0;
  const pct = Math.min(100, Math.round((w / FULL_KITCHEN_W) * 100));
  const level = w >= FULL_KITCHEN_W ? "high" : w >= FULL_KITCHEN_W * 0.6 ? "watch" : "low";
  const app = (pro && pro.appliances) || {};
  const on = [["oven", "oven"], ["cooktop", "cooktop"], ["dryer", "dryer"], ["dish_washer", "dishwasher"]]
    .filter(([k]) => app[k] && app[k].power).map(([, n]) => n);
  const box = $("#heat");
  box.dataset.level = level;
  box.innerHTML = `<div class="heat-head">${icon("i-flame")}<span>Heat load</span><strong>${(w / 1000).toFixed(1)} kW</strong></div>
    <div class="bar" role="meter" aria-label="Heat-producing load" aria-valuemin="0" aria-valuemax="${FULL_KITCHEN_W}" aria-valuenow="${w}"><span style="width:${pct}%"></span></div>
    ${on.length ? `<small>Running: ${esc(on.join(", "))}</small>` : ""}`;
}

/* ---------- home: quick chips ---------- */

const SCENES = [
  { key: "off", icon: "i-power", label: "All off",
    say: () => "turn off everything in the living room" },
  { key: "cool", icon: "i-temp", label: "Cool 24°",
    say: () => "turn on the living room AC and set it to 24 degrees" },
  { key: "fresh", icon: "i-wind", label: "Fresh air",
    say: () => "it's stuffy in the living room" },
];

function renderQuick() {
  $("#quick").innerHTML = SCENES.map((s) =>
    `<button type="button" data-scene="${s.key}">${icon(s.icon)}<span>${esc(s.label)}</span></button>`).join("");
}

/* ---------- home: "just now" ---------- */

function renderRecent() {
  const rows = (auditRows || []).filter((r) => {
    if (r.event_type === "notification.dryer_restart") return true;
    return r.event_type === "action.executed" && ACTION_DONE[r.action_kind];
  });
  const fresh = rows.filter((r) => Date.now() - new Date(r.ts).getTime() < 15 * 60 * 1000).slice(0, 2);
  const box = $("#recent");
  if (!fresh.length || (pro && (pro.pending_approvals || []).length)) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<p>Just now</p><ul>${fresh.map((r) => `<li>${icon("i-check")}<span>${esc(
    r.event_type === "notification.dryer_restart" ? "Kitchen cooled down — you can restart the dryer"
      : ACTION_DONE[r.action_kind])}</span></li>`).join("")}</ul>`;
}

/* ---------- the ask sheet (FR-3 / FR-4) ---------- */

function askLeft(ap) {
  return (new Date(ap.expires_at).getTime() - Date.now()) / 1000;
}

function renderAsk() {
  const list = (pro && pro.pending_approvals) || [];
  const sheet = $("#askSheet");
  const sig = list.map((a) => a.id).join("|");
  if (!list.length) {
    if (!sheet.hidden) closeSheets();
    askSig = ""; askDismissed = "";
    return;
  }
  if (sig !== askSig) {
    askSig = sig;
    const r = ((pro && pro.risks) || {})[list[0].risk_kind];
    const title = list[0].risk_kind === "mold" ? "The washer is getting damp" : "The kitchen is getting hot";
    $("#askBody").innerHTML = `
      <div class="why">
        <div class="status-ic">${mascot("ask")}</div>
        <div><h2 id="askTitle">${esc(title)}</h2>
          <div class="facts">${riskFacts(r).map(([t, l]) => `<span class="tag tag-${t}">${esc(l)}</span>`).join("")}</div></div>
      </div>
      <div class="count"><span>${list.length === 1 ? "1 quick question" : `${list.length} quick questions`}</span><span id="askClock"></span></div>
      ${list.map((ap) => {
        const k = ap.action_kind || (ap.actions && ap.actions[0] && ap.actions[0].kind);
        const q = QUESTION[k] || { q: ap.question || ap.summary, c: "", icon: "i-sparkle", tone: "" };
        return `<div class="card q" role="group" aria-label="${esc(q.q)}" data-ask="${esc(ap.id)}">
          <div class="q-top"><div class="q-ic ${q.tone}">${icon(q.icon)}</div>
            <div><strong>${esc(q.q)}</strong>${q.c ? `<span>${esc(q.c)}</span>` : ""}
              <span class="q-own" data-clock="${esc(ap.id)}"></span></div></div>
          <div class="answers">
            <button type="button" class="answer yes" data-answer="yes" data-id="${esc(ap.id)}">${icon("i-check")}Yes<small>네</small></button>
            <button type="button" class="answer no" data-answer="no" data-id="${esc(ap.id)}">${icon("i-x")}No<small>아니요</small></button>
          </div></div>`;
      }).join("")}
      <p class="rule">No answer? Nothing changes.</p>`;
    if (askDismissed !== sig) openSheet("askSheet");
    // One spoken prompt for the whole set, so two questions never cut each
    // other off. Read aloud regardless of the read-aloud setting.
    if (spokenAsks !== sig) {
      spokenAsks = sig;
      const qs = list.map((ap) => (QUESTION[ap.action_kind] || {}).q || ap.question).join(" ");
      speak(`${title}. ${list.length === 1 ? "One question." : list.length + " questions."} ${qs}`, true);
    }
  }
  const lefts = list.map(askLeft);
  const min = Math.min(...lefts);
  const spread = Math.max(...lefts) - min > 5;
  const c = $("#askClock");
  if (c) c.textContent = min > 0 ? `${clock(min)} left` : "Time is up";
  for (const ap of list) {
    const el = document.querySelector(`[data-clock="${ap.id}"]`);
    if (el) el.textContent = spread ? `${clock(askLeft(ap))} left` : "";
  }
}

async function answer(yes, id) {
  if (askBusy || !id) return;
  askBusy = true;
  document.querySelectorAll(`[data-id="${CSS.escape(id)}"]`).forEach((b) => { b.disabled = true; });
  try {
    const res = await api(`/api/approvals/${encodeURIComponent(id)}/${yes ? "confirm" : "decline"}`,
      { method: "POST", body: JSON.stringify({ channel: "ui" }) });
    // `expired` and `blocked` are answers too, and the toast must say what the
    // devices actually did (FR-4), not "Done".
    if (res && res.expired) toast("Too late — nothing was changed.");
    else if (res && res.ok === false) toast(cap(res.reason || res.error || "That question had already closed."));
    else toast(yes ? "Done." : "Okay — nothing changed.");
    await refresh();
  } catch (err) {
    toast(`Could not reach the house: ${err.message}`);
  } finally {
    askBusy = false;
  }
}

/* ---------- devices ---------- */

function devState(d) {
  const st = d.state || {};
  const on = !!st.power;
  switch (d.type) {
    case "air_conditioner":
      return on ? [MODE_LABEL[st.mode] || "On", st.target_temp != null ? `${st.target_temp}°` : ""].filter(Boolean).join(" ") : "Off";
    case "air_purifier": return on ? `PM2.5 ${st.pm25 == null ? "—" : st.pm25}` : "Off";
    case "washer": return on ? `Washing · ${st.remaining_min == null ? "?" : st.remaining_min} min` : (st.state === "done" ? "Cycle finished" : "Standby");
    case "refrigerator": return `${st.fridge_temp == null ? "—" : st.fridge_temp}°C · ${st.duty_cycle == null ? "—" : Math.round(st.duty_cycle * 100) + "%"}`;
    case "range_hood": return on ? `Fan ${st.fan || "auto"}` : "Off";
    case "oven": return on ? `Heating · ${st.target_temp == null ? "—" : st.target_temp + "°C"}` : "Off";
    default: return on ? "On" : "Off";
  }
}

function shortName(d) {
  const room = d.room || "";
  const n = String(d.name || "");
  return activeRoom !== "All" && n.startsWith(room + " ") ? n.slice(room.length + 1) : n;
}

function renderDevices() {
  const all = (snapshot && snapshot.devices) || [];
  const rooms = ROOM_ORDER.filter((r) => all.some((d) => d.room === r));
  const onCount = all.filter((d) => (d.state || {}).power).length;
  $("#devCount").textContent = `${all.length} devices · ${onCount} on`;
  $("#roomFilter").innerHTML = ["All", ...rooms].map((r) =>
    `<button type="button" role="tab" aria-selected="${r === activeRoom}" class="${r === activeRoom ? "on" : ""}" data-room="${esc(r)}">${esc(r === "All" ? "All" : ROOM_SHORT[r] || r)}</button>`).join("");
  const list = activeRoom === "All" ? all : all.filter((d) => d.room === activeRoom);
  const sensor = ((pro && pro.rooms) || {})[activeRoom];
  const roomOn = list.filter((d) => (d.state || {}).power).length;
  const stats = activeRoom === "All"
    ? [[`${onCount}/${all.length}`, "Devices on"], [`${((pro && pro.heat_load_w) || 0) / 1000 >= 0 ? (((pro && pro.heat_load_w) || 0) / 1000).toFixed(1) : "—"} kW`, "Heat load"], [String(rooms.length), "Rooms"]]
    : [[sensor ? `${sensor.temp.toFixed(1)}°` : "—", "Temperature"], [sensor ? `${Math.round(sensor.humidity)}%` : "—", "Humidity"], [`${roomOn}/${list.length}`, "On"]];
  $("#roomStats").innerHTML = stats.map(([v, l]) => `<div><strong>${esc(v)}</strong><span>${esc(l)}</span></div>`).join("");
  $("#devices").innerHTML = list.length ? list.map((d) => {
    const on = !!(d.state || {}).power;
    return `<article class="dev${on ? " on" : ""}${d.online === false ? " offline" : ""}">
      <button type="button" class="dev-open" data-open="${esc(d.id)}" aria-label="${esc(d.name)} details">
        <span class="dev-ic">${icon(ICON[d.type] || "i-devices")}</span>
        <span class="dev-name">${esc(shortName(d))}</span>
        <span class="dev-state">${esc(devState(d))}</span>
      </button>
      <button type="button" class="switch" role="switch" aria-checked="${on}" aria-label="${esc(d.name)}"
              data-power="${esc(d.id)}" data-on="${on ? 1 : 0}"></button>
    </article>`;
  }).join("") : `<p class="empty">Nothing in this room.</p>`;
}

/* ---------- activity: the audit trail in words (FR-6) ---------- */

function auditItem(r) {
  const act = ACTION_TEXT[r.action_kind] || r.action_kind || "";
  const req = r.approval_id ? `request ${String(r.approval_id).slice(0, 6)}` : "";
  switch (r.event_type) {
    case "risk.triggered":
      return { tone: "warn", icon: "i-flame",
               title: r.risk_kind === "mold" ? "Washer getting damp" : "Kitchen getting hot",
               detail: `Score ${Math.round(r.risk_score || 0)} · acts at 50` };
    case "approval.proposed": return { tone: "ask", icon: "i-sparkle", title: `Asked you: ${act}`, detail: req };
    case "approval.confirmed": return { tone: "ok", icon: "i-check", title: `You said yes: ${act}`, detail: req };
    case "approval.declined": return { tone: "plain", icon: "i-x", title: `You said no: ${act}`, detail: req };
    case "approval.expired": return { tone: "plain", icon: "i-clock", title: `No answer: ${act}`, detail: "Nothing changed" };
    case "action.executed": {
      const who = r.approval_id ? "You said yes" : r.channel === "safety-interlock" ? "Safety rule"
        : r.channel === "engine" ? "Did it on its own" : "From the app";
      return { tone: "ok", icon: "i-check", title: ACTION_DONE[r.action_kind] || `Done: ${act || r.device_id || ""}`, detail: [who, req].filter(Boolean).join(" · ") };
    }
    case "action.failed": return { tone: "bad", icon: "i-x", title: `${act || r.device_id || "Device"} didn't respond`, detail: r.detail || "" };
    case "action.blocked": return { tone: "plain", icon: "i-x", title: `Didn't go ahead: ${act || r.device_id || ""}`, detail: cap(r.detail || "") };
    case "notification.dryer_restart": return { tone: "plain", icon: "i-check", title: "Kitchen cooled down", detail: "You can restart the dryer" };
    case "scenario.reset": return { tone: "plain", icon: "i-sparkle", title: "Demo scenario loaded", detail: r.detail || "" };
    default: return { tone: "plain", icon: "i-clock", title: r.event_type, detail: r.detail || "" };
  }
}

function renderActivity() {
  document.querySelectorAll("#actFilter [data-act]").forEach((b) => b.classList.toggle("on", b.dataset.act === actFilter));
  const rows = (auditRows || []).filter((r) => r.event_type !== "action.intent").filter((r) =>
    actFilter === "all" ? true
      : actFilter === "asked" ? r.event_type.startsWith("approval.")
      : r.event_type === "action.executed");
  $("#auditLog").innerHTML = rows.length ? rows.slice(0, 30).map((r) => {
    const it = auditItem(r);
    return `<li data-tone="${it.tone}"><span class="t-ic">${icon(it.icon)}</span>
      <div class="t-text"><strong>${esc(it.title)}</strong>${it.detail ? `<span>${esc(it.detail)}</span>` : ""}<code>${esc(r.event_type)}</code></div>
      <span class="t-time">${esc(hhmm(r.ts))}</span></li>`;
  }).join("") : `<li class="empty">Nothing recorded yet.</li>`;
}

/* ---------- settings ---------- */

function renderJobs() {
  const jobs = (snapshot && snapshot.jobs) || [];
  $("#jobs").innerHTML = jobs.length ? jobs.map((j) => `
    <div class="li">${icon("i-clock")}
      <div class="li-text"><strong>${esc(cap(j.summary || j.device_name || "Scheduled task"))}</strong>
        <span>${esc(repeatShort(j.repeat_label) || "once")} · ${j.enabled ? "next " + esc(fmtWhen(j.next_run_at)) : "disabled"}</span></div>
      <button type="button" class="del" data-del="${esc(j.id)}" aria-label="Delete task">×</button>
    </div>`).join("")
    : `<div class="li">${icon("i-clock")}<div class="li-text"><strong>Nothing scheduled</strong><span>Ask the house to set something</span></div></div>`;
}

function renderMode() {
  const mode = (snapshot && snapshot.mode) || "cascade";
  document.querySelectorAll("[data-agent-mode]").forEach((b) => {
    const on = b.dataset.agentMode === mode;
    b.classList.toggle("on", on);
    b.setAttribute("aria-pressed", String(on));
  });
}

/* ---------- chat ---------- */

function liveBuild() {
  return (snapshot && snapshot.build) || "";
}

/* ---------- chat scrolling ----------
   The transcript is its own scroll container now (see .chat in style.css).
   Nothing here touches the document: on a phone the browser scrolls the
   nearest scrollable ancestor to reveal a focused field, and the field lives
   in the talk bar, so scrolling the page was what made the transcript jump. */
function chatBox() { return $("#chat"); }

function nearBottom(el, slack = 60) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= slack;
}

function scrollChatToEnd(force = false) {
  const el = chatBox();
  if (!el) return;
  // Never yank someone who scrolled up to re-read a message.
  if (!force && !nearBottom(el)) return;
  el.scrollTop = el.scrollHeight;
}

function addMsg(role, text, message, opts = {}) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  el.textContent = text;
  const tag = tagFor(message);
  if (tag) {
    const stamp = document.createElement("span");
    stamp.className = "stamp";
    stamp.textContent = tag;
    el.appendChild(stamp);
  }
  const trace = (message && message.payload && message.payload.trace) || [];
  if (trace.length) {
    const det = document.createElement("details");
    det.className = "trace";
    const sum = document.createElement("summary");
    const calls = trace.filter((s) => s.tool !== "offer").length;
    sum.textContent = (message.payload && message.payload.adopted)
      ? `took the rules' reading · ${calls} ${calls === 1 ? "step" : "steps"}`
      : `declined the reading · ${calls} ${calls === 1 ? "step" : "steps"}`;
    det.appendChild(sum);
    const ol = document.createElement("ol");
    trace.forEach((s) => {
      const li = document.createElement("li");
      li.textContent = s.result || s.tool;
      if (s.tool === "offer") li.className = "offer";
      ol.appendChild(li);
    });
    det.appendChild(ol);
    el.appendChild(det);
  }
  chatBox().appendChild(el);
  scrollChatToEnd(opts.force);

  // Read replies aloud when asked to — and always read a Tier-2 question,
  // because that is the one message that must not be missed. A transcript
  // rebuild is not a new message and must stay silent.
  const approval = message && message.payload && message.payload.approval;
  if (!opts.silent && (ttsOn || approval)) speak(text);
  return el;
}

function tagFor(msg) {
  if (!msg) return null;
  const rows = [];
  const when = msg.ts || msg.created_at;
  if (when) {
    const t = new Date(when);
    rows.push(isNaN(t) ? String(when).slice(11, 16) : t.toTimeString().slice(0, 5));
  }
  // A reply written by a build that no longer runs is a historical answer;
  // marking it is the difference between reading a transcript and trusting it.
  // Only a message that carries a build can be called stale: an optimistic user
  // bubble has no payload, and stamping it "older build" a second after it was
  // typed is a false alarm — the one thing a diagnostic must never do.
  const b = (msg.payload || {}).build;
  if (b && liveBuild() && b !== liveBuild()) rows.push(`old build ${b}`);
  return rows.length ? rows.join(" · ") : null;
}

function renderChips() {
  const devices = (snapshot && snapshot.devices) || [];
  const primary = devices.find((d) => d.type === "air_conditioner") || devices[0];
  if (!primary) return;
  const name = primary.name;
  const room = primary.room || "";
  const on = !!(primary.state || {}).power;

  const chips = [
    [`Turn ${on ? "off" : "on"} the ${name}`, on ? "Turn off" : "Turn on"],
    [`Set the ${name} to 24 degrees`, "24°C"],
    ["It's stuffy in here", "It's stuffy"],
    ["What scheduled tasks do I have?", "Schedules"],
    ["거실 에어컨 꺼줘", "한국어 예시"],
  ];
  if (room) chips.push([`Turn off everything in the ${room}`, "Whole room"]);

  const sig = chips.map((c) => c[0]).join("|");
  if (sig === chipsSig) return;
  chipsSig = sig;
  $("#chips").innerHTML = chips
    .map(([say, label]) => `<button data-say="${esc(say)}">${esc(label)}</button>`)
    .join("");
}

async function send(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return;
  await openTalk(false);
  addMsg("user", trimmed, { ts: new Date().toISOString() }, { force: true });
  $("#input").value = "";
  // The agent tier takes 4–8 s on a real model. Silence for that long reads as
  // a dead app, so say something immediately and replace it when the answer
  // lands. Built by hand rather than through addMsg: a placeholder should not
  // be read aloud.
  const pending = document.createElement("div");
  pending.className = "msg assistant pending";
  pending.textContent = "Working on it…";
  chatBox().appendChild(pending);
  scrollChatToEnd(true);
  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ text: trimmed }),
    });
    pending.remove();
    addMsg("assistant", data.reply || "…", { ts: new Date().toISOString(), payload: data }, { force: true });
    showReply(data.reply || "");
    await refresh();
    return data.reply || "";
  } catch (err) {
    pending.remove();
    addMsg("system", `Request failed: ${err.message}`);
    return "";
  }
}

let chatSig = "";

async function loadMessages(force = false) {
  const data = await api("/api/messages");
  const list = data.messages || [];
  // Rebuilding the transcript for an unchanged list is not free: it resets the
  // scroll position, and with read-aloud on it re-reads every message. Compare
  // a signature first and leave the DOM alone when nothing has arrived.
  const sig = list
    .map((m, i) => `${i}:${m.role}:${(m.content || "").length}:${(m.content || "").slice(0, 24)}`)
    .join("|");
  if (!force && sig === chatSig && chatBox().children.length) return;
  chatSig = sig;
  const box = chatBox();
  box.innerHTML = "";
  for (const m of list) addMsg(m.role, m.content, m, { silent: true });
  if (!list.length) {
    addMsg("system", "Say something, or tap a suggestion below.", null, { silent: true });
  }
  requestAnimationFrame(() => scrollChatToEnd(true));
}

/* ---------- decision mode ---------- */

async function setMode(mode) {
  try {
    const data = await api("/api/mode", { method: "POST", body: JSON.stringify({ mode }) });
    if (data.snapshot) {
      snapshot = data.snapshot;
      renderAll();
    }
  } catch (err) {
    toast(`Failed: ${err.message}`);
  }
}

/* ---------- toast and replies ---------- */

let toastTimer = null;

function toast(text, ms = 2600) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, ms);
}

/* The house's last answer, kept in the talk sheet until the next one. */
function showReply(text) {
  const clean = String(text || "").trim();
  if (!clean) return;
  $("#replyText").textContent = clean;
  $("#reply").hidden = false;
  $("#reply").classList.remove("pending");
  speak(clean);
}

function showPending(label) {
  $("#replyText").textContent = label || "Working on it…";
  $("#reply").hidden = false;
  $("#reply").classList.add("pending");
}

/* Quick chips run without leaving Home: the answer comes back as a toast long
   enough to read, and is spoken when read-aloud is on. */
async function runScene(scene) {
  toast("Working on it…", 8000);
  try {
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ text: scene.say() }) });
    if (data.snapshot) { snapshot = data.snapshot; renderAll(); }
    toast(data.reply || "Done.", 5000);
    speak(data.reply || "");
  } catch (err) {
    toast(`Could not reach the house: ${err.message}`);
  }
}

/* ---------- sheets (ask, talk, device) ---------- */

const SHEETS = ["askSheet", "talkSheet", "sheet"];

function openSheet(id) {
  for (const s of SHEETS) $(`#${s}`).hidden = s !== id;
  $("#scrim").hidden = false;
  renderHero();
}

function closeSheets() {
  if (!$("#askSheet").hidden && askSig) askDismissed = askSig;   // reopen from the hero
  for (const s of SHEETS) $(`#${s}`).hidden = true;
  $("#scrim").hidden = true;
  sheetId = null;
  if (listening) voiceFinish();
  renderHero();
}

async function openTalk(listen) {
  if ($("#talkSheet").hidden) {
    openSheet("talkSheet");
    await loadMessages().catch(() => {});
  }
  if (listen && !listening) startVoice();
}

function renderSheet() {
  const device = findDevice(sheetId);
  if (!device) return closeSheets();
  const st = device.state || {};
  const on = !!st.power;
  const caps = device.capabilities || [];
  const sub = [device.room || device.type_label, devState(device)].filter(Boolean).join(" · ");
  let body = `<div><h2>${esc(device.name)}</h2><p class="sub">${esc(sub)}</p></div>`;
  if (caps.includes("temperature")) {
    const target = st.target_temp == null ? 26 : st.target_temp;
    body += `<div class="stepper">
      <button type="button" data-step="-1" ${on ? "" : "disabled"} aria-label="Cooler">−</button>
      <span class="target">${target}°C</span>
      <button type="button" data-step="1" ${on ? "" : "disabled"} aria-label="Warmer">+</button></div>`;
  }
  if (caps.includes("mode")) {
    body += `<div class="pills">${MODES.map((mode) =>
      `<button type="button" data-mode="${mode}" class="${st.mode === mode ? "active" : ""}">${MODE_LABEL[mode]}</button>`).join("")}</div>`;
  }
  body += `<button type="button" class="sheet-power${on ? " on" : ""}" data-sheet-power="${on ? 0 : 1}">${on ? "Turn off" : "Turn on"}</button>`;
  $("#sheetBody").innerHTML = body;
}

function openDevice(id) {
  if (!findDevice(id)) return;
  sheetId = id;
  renderSheet();
  openSheet("sheet");
}

async function control(deviceId, actions) {
  try {
    const data = await api("/api/control", { method: "POST", body: JSON.stringify({ device_id: deviceId, actions }) });
    if (data.snapshot) { snapshot = data.snapshot; renderAll(); }
    if (data.ok === false) toast(data.error || "The device did not respond.");
  } catch (err) {
    toast(`Control failed: ${err.message}`);
  }
}

async function loadScenario(name) {
  try {
    await api("/api/scenario", { method: "POST", body: JSON.stringify({ name }) });
    toast("Scenario loaded.");
    askSig = ""; askDismissed = ""; spokenAsks = "";
    await refresh();
    switchTab("home");
  } catch (err) {
    toast(`Could not load the scenario: ${err.message}`);
  }
}

/* ---------- render ---------- */

function renderAll() {
  if (!snapshot) return;
  $("#stamp").textContent = `${BACKEND_LABEL[snapshot.backend] || snapshot.backend || ""} · ${snapshot.build || ""}`;
  $("#today").textContent = new Date().toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  renderAsk();
  renderHero();
  renderRisks();
  renderHeat();
  if (!$("#quick").children.length) renderQuick();
  renderRecent();
  renderDevices();
  renderActivity();
  renderJobs();
  renderChips();
  renderMode();
  if (sheetId) renderSheet();
}

async function refresh() {
  try {
    const [snap, state, trail] = await Promise.all([
      api("/api/snapshot"),
      api("/api/proactive").catch(() => null),
      api("/api/audit?limit=60").catch(() => null),
    ]);
    snapshot = snap;
    pro = state;
    auditRows = trail && trail.audit;
    renderAll();
  } catch (err) {
    /* keep the last good snapshot rather than blanking the screen */
  }
}

/* ---------- navigation ---------- */

const TABS = ["home", "devices", "activity", "settings"];

function switchTab(name) {
  if (!TABS.includes(name)) name = "home";
  currentTab = name;
  document.querySelectorAll(".tab").forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle("active", on);
    if (on) btn.setAttribute("aria-current", "page"); else btn.removeAttribute("aria-current");
  });
  for (const key of TABS) $(`#view-${key}`).hidden = key !== name;
  if (history.replaceState && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
}

/* ---------- events ---------- */

document.addEventListener("click", (event) => {
  const t = event.target;
  const hit = (sel) => t.closest(sel);
  let el;

  if ((el = hit("[data-scenario]"))) return loadScenario(el.dataset.scenario);
  if ((el = hit("[data-answer]"))) return answer(el.dataset.answer === "yes", el.dataset.id);
  if (hit("[data-open-ask]")) { askDismissed = ""; return openSheet("askSheet"); }
  if ((el = hit("[data-room]"))) { activeRoom = el.dataset.room; return renderDevices(); }
  if ((el = hit("[data-act]"))) { actFilter = el.dataset.act; return renderActivity(); }
  if ((el = hit("[data-power]"))) return control(el.dataset.power, [{ action: "power", value: el.dataset.on !== "1" }]);
  if ((el = hit("[data-step]")) && sheetId) {
    const current = (findDevice(sheetId).state || {}).target_temp;
    const next = (current == null ? 26 : current) + Number(el.dataset.step);
    return control(sheetId, [{ action: "temperature", value: Math.max(16, Math.min(30, next)) }]);
  }
  if ((el = hit("[data-mode]")) && sheetId) return control(sheetId, [{ action: "mode", value: el.dataset.mode }]);
  if ((el = hit("[data-sheet-power]")) && sheetId) return control(sheetId, [{ action: "power", value: el.dataset.sheetPower === "1" }]);
  if ((el = hit("[data-agent-mode]"))) return setMode(el.dataset.agentMode);
  if ((el = hit("[data-scene]"))) { const s = SCENES.find((x) => x.key === el.dataset.scene); return s && runScene(s); }
  if ((el = hit("[data-open]"))) return openDevice(el.dataset.open);
  if ((el = hit("[data-tab]"))) return switchTab(el.dataset.tab);
  if ((el = hit("[data-lang]"))) {
    voiceLang = el.dataset.lang;
    renderLangButton();
    return toast(voiceLang.startsWith("ko") ? "한국어 음성 인식" : "English voice input");
  }
  if ((el = hit("[data-del]"))) {
    return api(`/api/jobs/${el.dataset.del}`, { method: "DELETE" })
      .then((data) => { if (data.snapshot) { snapshot = data.snapshot; renderAll(); } })
      .catch(() => {});
  }
  if ((el = hit("[data-say]"))) return send(el.dataset.say);
  if (hit("[data-close]") || t.id === "scrim") return closeSheets();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#scrim").hidden) closeSheets();
});

$("#composer").addEventListener("submit", (event) => {
  event.preventDefault();
  send($("#input").value);
});

$("#mic").addEventListener("click", startVoice);
$("#talkFab").addEventListener("click", () => openTalk(true));

$("#tts").addEventListener("click", () => {
  ttsOn = !ttsOn;
  $("#tts").setAttribute("aria-checked", String(ttsOn));
  if (!ttsOn && "speechSynthesis" in window) window.speechSynthesis.cancel();
  toast(ttsOn ? "Reading replies aloud" : "Reading aloud off");
});

/* ---------- boot ---------- */

refresh();
renderLangButton();
setInterval(refresh, 4000);
setInterval(renderAsk, 1000);   // the clock is the point: it has to move

const startTab = (location.hash || "").replace("#", "");
switchTab(TABS.includes(startTab) ? startTab : "home");

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
