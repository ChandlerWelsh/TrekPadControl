"""Local HTTP server: serves the app's interface and the control API.

The interface files (/, /style.css, /app.js) contain no secrets and are public on the
local network. Every /api call needs the token (header `X-Token: <token>` or
`?token=<token>`). The window and the phone pass it in the URL fragment (#token=…),
which browsers never send to the server.
"""
from __future__ import annotations

import concurrent.futures
import hmac
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, config
from .ftms import PAD_MAX_MPH, PAD_MIN_MPH
from .pad import PadError

log = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent / "ui"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"),
          "/app.js": ("app.js", "application/javascript; charset=utf-8")}
THEMES = ("system", "light", "dark")


class ApiServer:
    def __init__(self, pad, store, recorder, cfg: dict, info: dict | None = None, host: str = "0.0.0.0"):
        self.pad, self.store, self.recorder, self.cfg = pad, store, recorder, cfg
        self.info = info or {}
        self.httpd = ThreadingHTTPServer((host, int(cfg["api_port"])), self._handler_class())
        self.httpd.daemon_threads = True

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, name="api", daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def settings(self) -> dict:
        return {"theme": self.cfg.get("theme", "system"), "max_speed_mph": self.cfg["max_speed_mph"],
                "speed_step_mph": self.cfg["speed_step_mph"], "incline_step_pct": self.cfg["incline_step_pct"],
                "version": __version__, **self.info}

    def _handler_class(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "TrekPadControl"

            def log_message(self, fmt, *args):  # default writes to stderr (None under pythonw)
                log.debug("%s %s", self.address_string(), fmt % args)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def _send(self, code: int, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, obj):
                self._send(code, json.dumps(obj).encode(), "application/json")

            def _handle(self, method: str):
                url = urlparse(self.path)
                path = url.path
                if method == "GET" and path in STATIC:
                    name, ctype = STATIC[path]
                    return self._send(200, (UI_DIR / name).read_bytes(), ctype)
                path = path.rstrip("/")
                q = {k: v[-1] for k, v in parse_qs(url.query).items()}
                token = self.headers.get("X-Token") or q.pop("token", "")
                if not path.startswith("/api"):
                    return self._json(404, {"error": "not found"})
                if not hmac.compare_digest(token.encode(), api.cfg["api_token"].encode()):
                    return self._json(401, {"error": "Missing or wrong token"})
                params = {}
                if method == "POST":
                    n = int(self.headers.get("Content-Length") or 0)
                    if 0 < n < 65536:
                        try:
                            body = json.loads(self.rfile.read(n) or b"{}")
                            if isinstance(body, dict):
                                params.update(body)
                        except ValueError:
                            pass
                params.update(q)
                try:
                    return self._route(method, path, params)
                except (KeyError, ValueError, TypeError) as e:
                    return self._json(400, {"error": f"Bad or missing parameter: {e}"})
                except PadError as e:
                    return self._json(409, {"error": str(e)})
                except concurrent.futures.TimeoutError:
                    return self._json(504, {"error": "The pad didn't respond in time."})
                except Exception as e:
                    log.exception("api error")
                    return self._json(500, {"error": str(e)})

            def _route(self, method, path, p):
                pad, cfg = api.pad, api.cfg
                if method == "GET" and path == "/api/status":
                    s = pad.snapshot()
                    s.update(recording_workout=api.recorder.active,
                             workouts_waiting_for_health=api.store.count_unsynced())
                    return self._json(200, s)
                if path == "/api/settings":
                    if method == "POST":
                        if "theme" in p:
                            if p["theme"] not in THEMES:
                                raise ValueError("theme")
                            cfg["theme"] = p["theme"]
                        if "max_speed_mph" in p:
                            v = round(min(max(float(p["max_speed_mph"]), PAD_MIN_MPH), PAD_MAX_MPH), 1)
                            cfg["max_speed_mph"] = v
                            pad.max_mph = v
                        config.save(cfg)
                    return self._json(200, api.settings())
                if method == "GET" and path == "/api/workouts":
                    unsynced = str(p.get("unsynced", "")).lower() in ("1", "true", "yes")
                    items = api.store.list(unsynced_only=unsynced)
                    return self._json(200, {"count": len(items), "workouts": items})
                m = re.fullmatch(r"/api/workouts/(\d+)/synced", path)
                if method == "POST" and m:
                    ok = api.store.mark_synced(int(m[1]))
                    return self._json(200 if ok else 404, {"ok": ok})
                if method != "POST":
                    return self._json(404, {"error": "Not found (control commands use POST)"})
                sstep = float(p.get("step", cfg["speed_step_mph"]))
                istep = float(p.get("step", cfg["incline_step_pct"]))
                commands = {
                    "/api/start": lambda: pad.call("start"),
                    "/api/stop": lambda: pad.call("stop"),
                    "/api/speed": lambda: pad.call("set_speed", float(p["mph"])),
                    "/api/speed/up": lambda: pad.call("change_speed", sstep),
                    "/api/speed/down": lambda: pad.call("change_speed", -sstep),
                    "/api/incline": lambda: pad.call("set_incline", float(p["pct"])),
                    "/api/incline/up": lambda: pad.call("change_incline", istep),
                    "/api/incline/down": lambda: pad.call("change_incline", -istep),
                }
                if path not in commands:
                    return self._json(404, {"error": "Not found"})
                commands[path]()
                return self._json(200, {"ok": True, "status": pad.snapshot()})

        return Handler
