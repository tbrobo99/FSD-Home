"use strict";

/* Local UI version switcher + a read-only "house at a glance" rail.
 *
 * This file is deliberately additive. It never touches app.js, never binds a
 * control that can act on a device, and only ever reads /api/snapshot,
 * /api/proactive and /api/audit. The three stylesheets are overlays on the
 * shared style.css, so every existing id and class keeps its meaning.
 *
 * Pick a version with ?ui=v1 / v2 / v3 / v0 (v0 = original). The choice is
 * remembered in localStorage until changed again.
 */

(function () {
  const VERSIONS = {
    v0: null,                       // the original style.css, no overlay
    v1: "/themes/v1-calm.css",
    v2: "/themes/v2-night.css",
    v3: "/themes/v3-console.css",
  };
  // The wide console is the default: on a laptop or a projector it uses the
  // whole screen and keeps the approval cards and the safety log side by side.
  // ?ui=v0 / v1 / v2 / v3 (or the switcher) overrides it for this browser.
  const DEFAULT = "v3";
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
    }
    .glance { display: none; }
    @media (max-width: 699px) {
      .ui-switch { top: 6px; right: 6px; transform: scale(.9); transform-origin: top right; }
    }
  `;
  const link = $("#uiTheme");
  if (link) {
    link.parentNode.insertBefore(base, link);   // overlay CSS must win
    const href = VERSIONS[version];
    if (href) link.href = href;
    else link.removeAttribute("href");
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

    rail.innerHTML = `
      <div class="glance-inner">
        <p class="glance-eyebrow">House at a glance</p>
        <div class="glance-hero">
          <span class="glance-score">${score == null ? "clear" : score}</span>
          <span class="glance-score-label">${score == null ? "no active risk" : "risk score / 100"}</span>
        </div>
        <div class="glance-metrics">
          <div><b>${number(kitchen.temp, "°")}</b><span>Kitchen</span></div>
          <div><b>${number(kitchen.humidity, "%")}</b><span>Humidity</span></div>
          <div><b>${on}/${devices.length}</b><span>Devices on</span></div>
        </div>
        <div class="glance-meta">
          <span>${pending.length ? `${pending.length} waiting for an answer` : "Nothing needs an answer"}</span>
          <span>${escapeHtml((snap && snap.backend) || "simulator")}</span>
        </div>
        <div class="glance-block">
          <p class="glance-caption">Rooms</p>
          <div class="glance-rooms">${roomRows}</div>
        </div>
        <div class="glance-block">
          <p class="glance-caption">Safety log <span>append-only</span></p>
          <div class="glance-trail">${trailRows}</div>
        </div>
        <p class="glance-foot">Read-only view · no controls here</p>
      </div>`;
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
    const box = $("#uiSwitch");
    if (box) {
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
