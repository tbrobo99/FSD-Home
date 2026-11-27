"""HTTP server (Python standard library, no dependencies)."""
from __future__ import annotations

import json
import mimetypes
import queue
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import audit, auth, budget, db, events

WEB_DIR = Path(__file__).resolve().parent / "web"

# mimetypes on Windows does not know .webmanifest, and a manifest served as
# application/octet-stream is silently ignored -- the app then just never
# becomes installable. Spell the important ones out.
EXTRA_TYPES = {
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml",
    ".js": "application/javascript",
    ".png": "image/png",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "LGHomeAgent/1.1"
    agent_ref = None  # injected by serve()

    # ------------------------------------------------ helpers

    def log_message(self, fmt, *args):
        pass  # keep the console clean

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, data, code: int = 200) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    # ------------------------------------------------ auth gate

    def _cookie(self, name: str) -> str:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def _is_public(self, path: str) -> bool:
        # Everything needed to render the login page stays public; all data
        # and control routes are gated. Local runs (no DEMO_PASSWORD) never
        # reach here as gated, because auth.enabled() is False.
        return path in ("/login", "/login.html", "/api/login", "/api/auth")

    def _authorized(self, path: str) -> bool:
        if not auth.enabled() or self._is_public(path):
            return True
        return auth.verify_token(self._cookie(auth.COOKIE))

    def _secure_cookie(self) -> bool:
        # Behind Caddy the browser is on HTTPS; trust its forwarded header so
        # the cookie gets the Secure flag, but stay HTTP-friendly locally.
        fwd = (self.headers.get("X-Forwarded-Proto") or "").lower()
        return "https" in fwd or getattr(self.connection, "cipher", None) is not None

    def _deny(self) -> None:
        self._json({"error": "login required"}, 401)

    def _sse(self) -> None:
        """Server-sent events: push live notifications to an open page.

        Kept deliberately simple and bounded: a 60 s stream, a heartbeat every
        15 s so proxies do not drop it, and a queue per subscriber. The client
        reconnects automatically, so expiry is a feature, not a leak.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = events.subscribe()
        deadline = time.monotonic() + 60
        next_ping = time.monotonic() + 15
        try:
            while time.monotonic() < deadline:
                try:
                    ev = q.get(timeout=0.5)
                    self.wfile.write(events.sse(ev).encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    if time.monotonic() >= next_ping:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        next_ping = time.monotonic() + 15
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            events.unsubscribe(q)

    def _static(self, rel: str) -> None:
        rel = rel.lstrip("/") or "index.html"
        if not re.fullmatch(r"[A-Za-z0-9_\-./]+", rel) or ".." in rel:
            self._send(403, b"forbidden", "text/plain")
            return
        path = WEB_DIR / rel
        if not path.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = (EXTRA_TYPES.get(path.suffix.lower())
                 or mimetypes.guess_type(str(path))[0]
                 or "application/octet-stream")
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # ------------------------------------------------ routes

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path
        if path == "/api/auth":
            return self._json({"enabled": auth.enabled(),
                               "authenticated": not auth.enabled() or
                               auth.verify_token(self._cookie(auth.COOKIE))})
        if path in ("/login", "/login.html"):
            return self._static("login.html")
        if not self._authorized(path):
            return self._deny()
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path in ("/energy", "/energy.html"):
            # The dashboard is the fourth tab of the same app, not a second
            # page. Old links still work: they land on that tab directly.
            return self._redirect("/?ui=v6#energy")
        if path == "/api/events":
            return self._sse()
        if path in ("/hub", "/hub/", "/hub/index.html"):
            # v4: device-card home, rooms, energy and menu tabs (Figma page
            # "v4 · ThinQ-style Screens"). Starts go through the command path.
            return self._static("hub/index.html")
        if path in ("/soft", "/soft/", "/soft/index.html"):
            # The v3 "soft modern" interface from the Figma design (savings
            # card, region and language pickers). "/" keeps the v1-v6 switcher.
            return self._static("soft/index.html")
        if path == "/api/snapshot":
            return self._json(self.agent_ref.snapshot())
        if path == "/api/mode":
            return self._json({"mode": self.agent_ref.snapshot()["mode"]})
        if path == "/api/messages":
            return self._json({"messages": db.recent_messages(60)})
        if path == "/api/logs":
            return self._json({"logs": db.recent_logs(60)})
        if path == "/api/audit":
            # FR-6: the append-only trail is readable but never writable from
            # the API. There is deliberately no POST/DELETE route for it.
            try:
                limit = max(1, min(200, int((parse_qs(url.query).get("limit") or ["40"])[0])))
            except ValueError:
                limit = 40
            return self._json({"audit": db.recent_audit(limit)})
        if path == "/api/savings":
            # FR-7: estimated energy / cost / CO2 savings from executed actions.
            from . import savings
            q = parse_qs(url.query)
            period = (q.get("period") or ["week"])[0]
            region = (q.get("region") or [None])[0]
            return self._json(savings.report("today" if period == "today" else "week", region=region))
        if path == "/api/budget":
            # FR-8: instantaneous kW, cumulative kWh and money are separate
            # fields. `decisions` carries the provenance of recent verdicts.
            q = parse_qs(url.query)
            region = (q.get("region") or [None])[0]
            engine = getattr(self.agent_ref, "proactive", None)
            cfg = self.agent_ref.cfg
            snap = (getattr(engine, "_last_snapshot", None) or {}) if engine else {}
            return self._json({
                **budget.state(cfg=cfg, region=region),
                # kW here is instantaneous and separate from cumulative kWh.
                "total_load_w": snap.get("total_load_w"),
                "tariff_band": snap.get("tariff"),
                "decisions": budget.decisions(30),
                "scope": "simulator",
                "preauthorised": bool(engine.preauthorised) if engine else True,
            })
        if path == "/api/regions":
            # Country presets: language, currency, prices, emission factor.
            from . import regions
            return self._json({"default": regions.DEFAULT, "regions": list(regions.REGIONS.values())})
        if path == "/api/approvals":
            # Phase 2: Tier-2 requests waiting on the user. Absent engine ->
            # empty list, never a 500.
            engine = getattr(self.agent_ref, "proactive", None)
            if engine is None:
                return self._json({"approvals": []})
            return self._json({"approvals": [a.to_dict() for a in engine.approvals.pending()]})
        if path == "/api/proactive":
            engine = getattr(self.agent_ref, "proactive", None)
            return self._json(engine.state() if engine else {"running": False})
        if path == "/api/preferences":
            engine = getattr(self.agent_ref, "proactive", None)
            if not engine or not getattr(engine, "learn_enabled", False):
                return self._json({"enabled": False, "preferences": []})
            from . import preference
            try:
                return self._json({"enabled": True,
                                   "preferences": preference.all_preferences()})
            except Exception:
                return self._json({"enabled": True, "preferences": []})
        if path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return self._static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = self._body()

        if path == "/api/login":
            if not auth.enabled():
                return self._json({"ok": True, "enabled": False})
            if not auth.check(str(body.get("password") or "")):
                return self._json({"error": "wrong password"}, 401)
            token = auth.issue_token()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", auth.cookie_header(token, self._secure_cookie()))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"enabled":true}')
            return
        if path == "/api/logout":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Set-Cookie", auth.clear_cookie_header())
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        if not self._authorized(path):
            return self._deny()

        if path == "/api/chat":
            text = (body.get("text") or "").strip()
            if not text:
                return self._json({"error": "text must not be empty"}, 400)
            result = self.agent_ref.handle(text)
            return self._json({**result, "snapshot": self.agent_ref.snapshot()})

        if path == "/api/budget":
            # Setting the budget is the pre-authorisation act. Record it in
            # the audit trail as a deliberate policy change.
            if "max_kw" in body:
                # The instantaneous cap (kW) is separate from the monthly
                # budget (kWh). null or 0 turns it off.
                raw = body.get("max_kw")
                try:
                    cap = budget.set_max_kw(float(raw) if raw not in (None, "") else None)
                except (TypeError, ValueError) as exc:
                    return self._json({"error": f"max_kw: {exc}"}, 400)
                db.audit_write("energy.max_kw_set", execution_status="configured",
                               detail=f"max kW cap {cap if cap else 'off'}")
                db.log("budget.max_kw", f"{cap}")
                if "budget_kwh" not in body:
                    return self._json({"ok": True, **budget.state(cfg=self.agent_ref.cfg)})
            try:
                kwh = float(body.get("budget_kwh"))
            except (TypeError, ValueError):
                return self._json({"error": "budget_kwh must be a number"}, 400)
            if not 0 < kwh <= 100000:
                return self._json({"error": "budget_kwh out of range"}, 400)
            strict = bool(body.get("strict_mode", True))
            result = budget.set_budget(kwh, strict)
            db.audit_write("energy.budget_set", execution_status="configured",
                           detail=f"monthly budget {kwh} kWh, strict={strict}")
            db.log("budget.set", f"{kwh} kWh strict={strict}")
            return self._json({"ok": True, **result})

        if path == "/api/mode":
            # Switched live, not on restart: the point of the two modes is that
            # the same sentence can be asked of both, one after the other.
            mode = str(body.get("mode") or "").lower()
            if mode not in ("cascade", "agent"):
                return self._json({"error": "mode must be cascade or agent"}, 400)
            self.agent_ref.cfg.setdefault("agent", {})["mode"] = mode
            return self._json({"ok": True, "mode": mode,
                               "snapshot": self.agent_ref.snapshot()})

        if path == "/api/control":
            device_id = body.get("device_id")
            actions = body.get("actions") or []
            if not device_id or not actions:
                return self._json({"error": "device_id and actions are required"}, 400)
            # FR-8: the raw API is a fourth execution path and must obey the
            # same budget as the engine. The budget is a hard limit by default;
            # `override` is the one explicit, audited exception — an emergency
            # manual action that is allowed to spend past the budget, with the
            # bypass and its reason written to the append-only audit log.
            override = bool(body.get("override"))
            if override:
                budget.record_decision(budget.ALLOW, budget.SOURCE_RULES,
                                       device_id, "emergency manual override",
                                       channel="manual-override")
                verdict = None
            else:
                try:
                    verdict = budget.gate(
                        self.agent_ref.adapter, device_id, actions,
                        cfg=self.agent_ref.cfg, channel="manual")
                except Exception as exc:
                    # Same rule as the chat path: an unreadable budget is not
                    # an empty one. Refuse the write and say why; switching a
                    # device off stays available because it lowers the load.
                    detail = f"{type(exc).__name__}: {exc}"[:300]
                    try:
                        audit.record("budget.gate_failed", device_id=device_id,
                                     channel="manual",
                                     execution_status="blocked",
                                     decision_source=budget.SOURCE_RULES,
                                     action_kind="energy_gate", detail=detail)
                    except Exception:
                        pass
                    if self.agent_ref._adds_load(actions):
                        return self._json({
                            "ok": False, "blocked": True,
                            "error": "the energy budget could not be checked; "
                                     "nothing was sent to the device",
                            "snapshot": self.agent_ref.snapshot(),
                        }, 503)
                    verdict = None
                if verdict is not None and verdict["decision"] == budget.BLOCK:
                    db.log("control.budget_blocked",
                           f"{device_id}: {verdict['reason']}")
                    return self._json({
                        "ok": False, "blocked": True,
                        "error": verdict["reason"], "budget": verdict,
                        "snapshot": self.agent_ref.snapshot(),
                    }, 403)
            res = audit.execute(self.agent_ref.adapter, device_id, actions)
            db.log("control.manual",
                   f"{device_id} {actions} -> {res.get('ok')}"
                   + (" [OVERRIDE]" if override else ""))
            return self._json({**res, "budget": verdict,
                               "snapshot": self.agent_ref.snapshot()})

        if path == "/api/jobs/clear":
            n = db.clear_jobs()
            return self._json({"ok": True, "deleted": n,
                               "snapshot": self.agent_ref.snapshot()})

        if path == "/api/messages/clear":
            db.clear_messages()
            return self._json({"ok": True})

        if path == "/api/scenario":
            # Demo control: load a deterministic starting state without a
            # restart. Only the scenarios run.py also accepts.
            engine = getattr(self.agent_ref, "proactive", None)
            name = str(body.get("name") or "")
            if engine is None:
                return self._json({"error": "proactive engine not running"}, 400)
            if name not in ("idle", "mold", "oven", "oven_running", "heatwave", "peak"):
                return self._json({"error": "unknown scenario"}, 400)
            engine.reset_scenario(name)
            return self._json({"ok": True, "scenario": name})

        m = re.fullmatch(r"/api/approvals/([A-Za-z0-9_]+)/(confirm|decline)", path)
        if m:
            # The only door into Tier-2 execution. No approval id, no action —
            # the queue itself refuses anything not pending.
            engine = getattr(self.agent_ref, "proactive", None)
            if engine is None:
                return self._json({"error": "proactive engine not running"}, 400)
            fn = engine.confirm if m.group(2) == "confirm" else engine.decline
            return self._json(fn(m.group(1)))

        return self._json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if not self._authorized(path):
            return self._deny()
        m = re.fullmatch(r"/api/jobs/([A-Za-z0-9_]+)", path)
        if m:
            ok = db.delete_job(m.group(1))
            return self._json({"ok": ok, "snapshot": self.agent_ref.snapshot()})
        return self._json({"error": "not found"}, 404)


def serve(agent, host: str = "127.0.0.1", port: int = 8848) -> ThreadingHTTPServer:
    Handler.agent_ref = agent
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
