"use strict";

/* Local UI version switcher + a read-only "house at a glance" rail.
 *
 * This file is deliberately additive. It never touches app.js, never binds a
 * control that can act on a device, and only ever reads /api/snapshot,
 * /api/proactive and /api/audit. The three stylesheets are overlays on the
 * shared style.css, so every existing id and class keeps its meaning.
 *
 * Pick a version with ?ui=v0 / v1 / v2 / v3 / v4 (v0 = original). The choice
 * is remembered in localStorage until changed again.
 */

(function () {
  // A version is a list of overlays, applied in order on top of style.css.
  // v4 is DUAL-END: one stylesheet gives a 390px phone layout and a
  // full-width three-column workbench on a laptop / projector. Both ends are
  // real interfaces -- the demo runs on a laptop, Level-2 approvals are
  // answered on a phone. It builds on the original style.css, not on v3.
  const VERSIONS = {
    v0: [],                                     // the original style.css
    v1: ["/themes/v1-calm.css"],
    v2: ["/themes/v2-night.css"],
    v3: ["/themes/v3-console.css"],
    v4: ["/themes/v4-dual.css"],
    // V5 keeps V4's responsive phone / laptop structure, then replaces its
    // visual language with the calmer senior-care presentation.
    v5: ["/themes/v4-dual.css", "/themes/v5-care.css"],
    // V6 is the mission console shown in the reference presentation: a dark
    // signal-coloured field, an isometric home view and a read-only telemetry
    // rail. It reuses V4's dual-end grid and replaces the visual language.
    v6: ["/themes/v4-dual.css", "/themes/v6-mission.css"],
  };
  // Default is v6: both ends come from the same overlay, so nothing has to be
  // switched when moving from a phone to a laptop / projector. v0..v5 stay
  // available via ?ui= for comparison.
  const DEFAULT = "v6";
  const KEY = "lg-ui-version";
  const $ = (s) => document.querySelector(s);

  function chosen() {
    const fromUrl = new URLSearchParams(location.search).get("ui");
    if (fromUrl && Object.prototype.hasOwnProperty.call(VERSIONS, fromUrl)) return fromUrl;
    let saved = null;
    try { saved = localStorage.getItem(KEY); } catch (_) { /* private mode */ }
    return saved && Object.prototype.hasOwnProperty.call(VERSIONS, saved) ? saved : DEFAULT;
  }

  const version = chosen();
  const root = document.documentElement;
  root.dataset.ui = version;

  /* The base rules for the switcher live *before* the overlay link in the
     cascade, so a version can restyle them. Keeping them here means the
     original style.css stays byte-for-byte unchanged. */
  const base = document.createElement("style");
  base.textContent = `
    .ui-switch {
      position: fixed; top: 10px; right: 10px; z-index: 90;
      display: flex; align-items: center; gap: 2px;
      padding: 3px; border-radius: 999px;
      background: rgba(25, 31, 40, .78);
      border: 1px solid rgba(255, 255, 255, .22);
      box-shadow: 0 2px 10px rgba(16, 24, 40, .18);
      font: 600 11px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .ui-switch button {
      width: 26px; height: 24px; border: 0; border-radius: 999px;
      background: transparent; color: rgba(255, 255, 255, .72);
      cursor: pointer; font: inherit; letter-spacing: 0;
    }
    .ui-switch button:hover { background: rgba(255, 255, 255, .16); color: #fff; }
    .ui-switch button.is-on { background: #fff; color: #14202f; }
    .ui-switch-label {
      padding: 0 8px 0 6px; color: rgba(255, 255, 255, .62);
      letter-spacing: .08em; font-size: 10px;
      cursor: pointer; user-select: none;
    }
    /* Collapsed by default: a floating button row would sit on top of the
       content, and a mis-tap would swap the whole interface on someone who
       did not ask for it -- bad for senior users. Only the tiny "UI" handle
       stays visible; tapping it expands the five version buttons. */
    .ui-switch { transition: opacity .2s; }
    .ui-switch.collapsed { opacity: .5; }
    .ui-switch.collapsed:hover, .ui-switch.collapsed:focus-within { opacity: 1; }
    .ui-switch.collapsed button { display: none; }
    .ui-switch.collapsed .ui-switch-label { padding: 0 10px; }
    .glance { display: none; }
    @media (max-width: 699px) {
      .ui-switch { top: 6px; right: 6px; transform: scale(.78); transform-origin: top right; }
    }
  `;
  const link = $("#uiTheme");
  if (link) {
    link.parentNode.insertBefore(base, link);   // every overlay must win
    const sheets = VERSIONS[version] || [];
    if (!sheets.length) {
      link.removeAttribute("href");
    } else {
      link.href = sheets[0];
      // Extra overlays are appended after the first link, so the cascade is
      // style.css -> overlay 1 -> overlay 2 ..., in this exact order.
      for (const href of sheets.slice(1)) {
        const extra = document.createElement("link");
        extra.rel = "stylesheet";
        extra.href = href;
        link.parentNode.appendChild(extra);
      }
    }
  } else {
    document.head.appendChild(base);
  }

  /* ---------------- read-only data for the decorative rail ---------------- */

  const ROOMS = ["Living Room", "Kitchen", "Bedroom", "Balcony"];

  function number(v, suffix, fallback = "—") {
    return v == null || Number.isNaN(Number(v)) ? fallback
      : `${Math.round(Number(v) * 10) / 10}${suffix || ""}`;
  }

  async function get(path) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" } });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  /* ---------------- v6 mission console: isometric home view ----------------
   * Pure SVG, generated here so index.html and app.js stay untouched. Every
   * light is driven by a device's real `power` state and the risk tone comes
   * from /api/proactive. It is decorative and read-only: no click handlers.
   */

  const DEV_POINT = {
    ac_living: [414, 176], purifier: [456, 199], ac_bedroom: [252, 112],
    range_hood: [232, 138], oven: [272, 156], ac_kitchen: [204, 150],
    cooktop: [257, 172], fridge: [150, 122], washer: [497, 250],
    dryer: [532, 214], dehumidifier: [470, 282],
  };

  function isoBox(cx, cy, w, d, h, cls) {
    const A = [cx, cy], B = [cx + w, cy + w * .5];
    const C = [cx + w - d, cy + w * .5 + d * .5], D = [cx - d, cy + d * .5];
    const pts = (a, b, c, e) => [a, b, c, e].map((p) => p.join(",")).join(" ");
    const down = (p) => [p[0], p[1] + h];
    return `<g class="${cls}">
      <polygon class="block" points="${pts(A, B, C, D)}"/>
      <polygon class="wall-shade" points="${pts(D, C, down(C), down(D))}"/>
      <polygon class="block-top" points="${pts(B, C, down(C), down(B))}"/>
    </g>`;
  }

  function missionScene() {
    let grid = "";
    for (let i = 1; i < 8; i++) {
      const t = i / 8;
      grid += `<line class="grid-line" x1="${40 + 280 * t}" y1="${190 - 130 * t}"
        x2="${320 + 280 * t}" y2="${320 - 130 * t}"/>`;
      grid += `<line class="grid-line" x1="${40 + 280 * t}" y1="${190 + 130 * t}"
        x2="${320 + 280 * t}" y2="${60 + 130 * t}"/>`;
    }
    // Each node carries a ping ring. CSS only animates the ring for nodes the
    // API reports as on (or as a risk); nothing is invented here.
    const dots = Object.entries(DEV_POINT).map(([id, p]) =>
      `<g class="dev-dot" data-dev="${id}" transform="translate(${p[0]},${p[1]})"
          role="button" tabindex="0" aria-label="device ${id}, show live reading">
         <circle class="ping" r="4.2"/><circle class="halo" r="12"/><circle class="core" r="4.2"/>
       </g>`).join("");
    const lamps = [232, 266, 300].map((x) => `
      <line class="lamp" x1="${x}" y1="24" x2="${x}" y2="92"/>
      <polygon class="lamp-shade" points="${x - 9},92 ${x + 9},92 ${x + 5},106 ${x - 5},106"/>
      <polygon class="lamp-cone" points="${x - 5},106 ${x + 5},106 ${x + 20},164 ${x - 20},164"/>
      <circle class="lamp-glow" cx="${x}" cy="164" r="7"/>`).join("");

    const sec = document.createElement("section");
    sec.className = "mission-stage";
    sec.id = "missionStage";
    sec.setAttribute("aria-label", "Live home view, read only");
    sec.innerHTML = `
      <div class="stage-top">
        <span class="stage-id">LIVE HOME · SIMULATOR</span>
        <span class="stage-state" id="stageState" data-tone="clear">SCANNING…</span>
      </div>
      <svg class="iso" viewBox="0 0 640 360" role="img" aria-label="Isometric view of the home">
        <defs>
          <linearGradient id="sweepGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="#3ad4f2" stop-opacity="0"/>
            <stop offset=".5" stop-color="#7ce7ff" stop-opacity=".34"/>
            <stop offset="1" stop-color="#3ad4f2" stop-opacity="0"/>
          </linearGradient>
          <clipPath id="floorClip">
            <polygon points="320,60 600,190 320,320 40,190"/>
          </clipPath>
        </defs>
        <polygon class="wall" points="40,190 320,60 320,10 40,140"/>
        <polygon class="wall" points="320,60 600,190 600,140 320,10"/>
        <polygon class="floor" points="320,60 600,190 320,320 40,190"/>
        <polygon class="zone-fill kitchen" points="40,190 320,60 320,190 180,255"/>
        <polygon class="zone-fill living" points="180,255 320,190 600,190 320,320"/>
        ${grid}
        <polygon class="window" points="72,151 168,104 168,72 72,119"/>
        <polygon class="window" points="472,104 568,151 568,119 472,72"/>
        ${lamps}
        ${isoBox(250, 176, 84, 38, 26, "island")}
        ${isoBox(112, 118, 30, 28, 64, "fridge")}
        ${isoBox(398, 218, 92, 40, 24, "sofa")}
        ${isoBox(414, 262, 42, 24, 12, "table")}
        ${isoBox(452, 126, 56, 34, 22, "dining")}
        <ellipse class="hub-ring" cx="320" cy="268" rx="15" ry="7"/>
        <circle class="hub-core" cx="320" cy="252" r="7"/>
        <line class="hub-stem" x1="320" y1="252" x2="320" y2="268"/>
        <text class="zone" x="96" y="222">KITCHEN</text>
        <text class="zone" x="476" y="243">LIVING</text>
        <text class="zone" x="264" y="52">BEDROOM</text>
        <text class="zone" x="500" y="300">BALCONY</text>
        ${dots}
        <g clip-path="url(#floorClip)">
          <rect class="sweep" x="-160" y="40" width="150" height="300" fill="url(#sweepGrad)"/>
        </g>
        <path class="scan" d="M40,190 L320,60 L600,190 L320,320 Z"/>
      </svg>
      <div class="stage-foot">
        <span>DEVICE NODES · <b class="pulse-dot"></b> LIVE</span>
        <span id="stageNodes">0 / 0 ON</span>
      </div>`;
    return sec;
  }

  let stageSnap = null;   // last good snapshot, for the read-only node popup

  function paintStage(snap, pro) {
    const stage = $("#missionStage");
    if (!stage) return;
    stageSnap = snap;
    const devices = (snap && snap.devices) || [];
    const active = (pro && pro.active) || {};
    const risks = Object.keys(active);
    const riskKind = risks[0] || "";
    const state = $("#stageState");
    if (state) {
      state.textContent = riskKind ? `RISK ACTIVE · ${riskKind.toUpperCase()}` : "ALL CLEAR · NO ACTIVE RISK";
      state.dataset.tone = riskKind ? "risk" : "clear";
    }
    let onCount = 0;
    stage.querySelectorAll("[data-dev]").forEach((node) => {
      const dev = devices.find((d) => d.id === node.dataset.dev);
      const st = (dev && dev.state) || {};
      const power = !!st.power;
      if (power) onCount++;
      node.classList.toggle("on", power);
      node.classList.toggle("risk", !power && (riskKind === "thermal" || riskKind === "mold")
        && ["cooktop", "oven", "dryer"].includes(node.dataset.dev));
      node.classList.toggle("heat", power && st.current_temp != null && st.current_temp >= 30);
    });
    const nodes = $("#stageNodes");
    if (nodes) nodes.textContent = `${onCount} / ${devices.length} ON`;
  }

  /* Clicking a node opens a small READ-ONLY card with that device's live
   * reading. It has no controls and writes nothing; it simply makes the
   * picture explorable during a demo without pretending to control anything. */
  function showNodeCard(id) {
    let card = $("#nodeCard");
    if (!card) {
      card = document.createElement("div");
      card.id = "nodeCard";
      card.className = "node-card";
      card.setAttribute("role", "dialog");
      card.setAttribute("aria-label", "Device live reading, read only");
      $("#missionStage").appendChild(card);
    }
    const dev = ((stageSnap && stageSnap.devices) || []).find((d) => d.id === id);
    if (!dev) return;
    const st = dev.state || {};
    const rows = [
      ["POWER", st.power ? "ON" : "OFF"],
      ["TEMP", st.current_temp != null ? `${st.current_temp}°C` : "—"],
      ["MODE", st.mode || "—"],
      ["ID", dev.id],
    ].map(([k, v]) => `<div><span>${k}</span><b>${escapeHtml(String(v))}</b></div>`).join("");
    card.innerHTML = `
      <div class="node-card-top"><span>${escapeHtml(dev.name || dev.id)}</span>
        <button class="node-card-x" aria-label="Close">×</button></div>
      <div class="node-card-rows">${rows}</div>
      <p class="node-card-foot">LIVE READING · READ ONLY</p>`;
    card.hidden = false;
    card.querySelector(".node-card-x").addEventListener("click", () => { card.hidden = true; });
  }

  function paint(snap, pro, trail) {
    const rail = $("#glance");
    if (!rail) return;
    const devices = (snap && snap.devices) || [];
    const rooms = (pro && pro.rooms) || {};
    const active = (pro && pro.active) || {};
    const pending = (pro && pro.pending_approvals) || [];
    const kitchen = rooms.Kitchen || {};
    const on = devices.filter((d) => (d.state || {}).power).length;
    const risk = active.thermal || active.mold;
    const score = risk ? Math.round(risk.score) : null;
    const events = (trail && trail.audit) || [];

    const roomRows = ROOMS.map((name) => {
      const r = rooms[name] || {};
      return `<div class="glance-room">
        <span>${escapeHtml(name)}</span>
        <b>${number(r.temp, "°")}</b>
      </div>`;
    }).join("");

    const trailRows = events.slice(0, 5).map((a) => `
      <div class="glance-event">
        <span class="glance-dot" data-kind="${escapeHtml(String(a.event_type || "").split(".")[0])}"></span>
        <span class="glance-event-name">${escapeHtml(a.event_type)}</span>
        <time>${escapeHtml(String(a.ts || "").slice(11, 19))}</time>
      </div>`).join("") || `<p class="glance-none">No recorded events yet.</p>`;

    // v6 turns the rail into a signal console. The bars are real readings:
    // kitchen temperature, kitchen humidity and how many devices are on.
    // Nothing here is a radio measurement the program cannot actually make.
    const mission = version === "v6";
    const pct = (v, lo, hi) => Math.max(4, Math.min(100,
      Math.round(((Number(v) - lo) / (hi - lo)) * 100)));
    const channel = (label, value, fill, tone) => `
      <div><b>${value}</b><span>${label}</span>
        <span class="chan-bar ${tone}"><i style="width:${fill}%"></i></span>
      </div>`;
    const metrics = mission ? (
      channel("THERMAL · °C", number(kitchen.temp, ""), pct(kitchen.temp, 16, 34), "amber")
      + channel("HUMIDITY · %", number(kitchen.humidity, ""), pct(kitchen.humidity, 0, 100), "")
      + channel("DEVICE LOAD", `${on}/${devices.length}`, devices.length
        ? Math.round((on / devices.length) * 100) : 0, "green")
    ) : `
      <div><b>${number(kitchen.temp, "°")}</b><span>Kitchen</span></div>
      <div><b>${number(kitchen.humidity, "%")}</b><span>Humidity</span></div>
      <div><b>${on}/${devices.length}</b><span>Devices on</span></div>`;

    rail.innerHTML = `
      <div class="glance-inner">
        <p class="glance-eyebrow">${mission ? "CHANNEL SCORES · LIVE" : "House at a glance"}</p>
        <div class="glance-hero"${risk ? ' data-tone="risk"' : ""}>
          <span class="glance-score">${score == null ? "CLEAR" : score}</span>
          <span class="glance-score-label">${score == null ? "no active risk" : "risk score / 100"}</span>
        </div>
        <div class="glance-metrics">${metrics}</div>
        <div class="glance-meta">
          <span>${pending.length ? `${pending.length} waiting for an answer` : "Nothing needs an answer"}</span>
          <span>${escapeHtml((snap && snap.backend) || "simulator")}</span>
        </div>
        <div class="glance-block">
          <p class="glance-caption">${mission ? "CONTEXT REGISTRY" : "Rooms"} <span>${mission ? "temperature" : ""}</span></p>
          <div class="glance-rooms">${roomRows}</div>
        </div>
        <div class="glance-block">
          <p class="glance-caption">${mission ? "PROCESS LOG" : "Safety log"} <span>append-only</span></p>
          <div class="glance-trail">${trailRows}</div>
        </div>
        <p class="glance-foot">${mission ? "READ-ONLY TELEMETRY · NO DEVICE CONTROL" : "Read-only view · no controls here"}</p>
      </div>`;
    if (mission) paintStage(snap, pro);
  }

  async function refreshGlance() {
    try {
      const results = await Promise.all([
        get("/api/snapshot"),
        get("/api/proactive").catch(() => null),
        get("/api/audit?limit=5").catch(() => null),
      ]);
      paint(results[0], results[1], results[2]);
    } catch (_) { /* keep the last good reading */ }
  }

  /* ---------------- switcher ---------------- */

  document.addEventListener("DOMContentLoaded", () => {
    if (version === "v6") {
      const home = $("#view-home");
      const first = home && home.querySelector("#askQueue");
      if (home && first && !$("#missionStage")) {
        home.insertBefore(missionScene(), first);
        const stage = $("#missionStage");
        stage.querySelectorAll("[data-dev]").forEach((node) => {
          const open = () => showNodeCard(node.dataset.dev);
          node.addEventListener("click", open);
          node.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
          });
        });
      }
    }
    const box = $("#uiSwitch");
    if (box) {
      // Start collapsed. The label is the handle: click / Enter / Space
      // expands the version row, clicking it again collapses it.
      box.classList.add("collapsed");
      const label = box.querySelector(".ui-switch-label");
      if (label) {
        label.setAttribute("role", "button");
        label.setAttribute("tabindex", "0");
        label.setAttribute("aria-expanded", "false");
        const toggle = () => {
          const collapsed = box.classList.toggle("collapsed");
          label.setAttribute("aria-expanded", String(!collapsed));
        };
        label.addEventListener("click", toggle);
        label.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
        });
      }
      box.querySelectorAll("[data-ui]").forEach((btn) => {
        const on = btn.dataset.ui === version;
        btn.classList.toggle("is-on", on);
        btn.setAttribute("aria-pressed", String(on));
        btn.addEventListener("click", () => {
          const next = btn.dataset.ui;
          if (next === version) return;
          try { localStorage.setItem(KEY, next); } catch (_) { /* ignore */ }
          const url = new URL(location.href);
          url.searchParams.set("ui", next);
          location.href = url.toString();
        });
      });
    }
    // The rail is hidden by v0 and by the base stylesheet, but it is cheap
    // enough to keep live: switching versions only needs a style change.
    refreshGlance();
    setInterval(refreshGlance, 4000);
  });
})();
