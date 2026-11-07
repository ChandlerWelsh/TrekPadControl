"""Entry point: wires the pad, workout recorder, local server, hotkeys and window together.

The interface is HTML/CSS (trekpad/ui) served by the local server and shown in a
frameless native window (pywebview, Edge WebView2). The same page works in a phone
browser on the same Wi-Fi.
"""
from __future__ import annotations

import logging
import queue
import socket
import sys
import threading
import winreg
from logging.handlers import RotatingFileHandler

import webview

from . import config
from .api import ApiServer
from .hotkeys import Hotkeys, pretty
from .pad import PadController
from .workouts import WorkoutRecorder, WorkoutStore

log = logging.getLogger("trekpad")

FULL_SIZE = (400, 676)
MINI_SIZE = (560, 52)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = RotatingFileHandler(config.APP_DIR / "trekpad.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if sys.stderr is not None:  # None under pythonw
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("bleak").setLevel(logging.WARNING)


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # UDP connect sends nothing; just picks the LAN interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def windows_prefers_dark() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


class DesktopBridge:
    """Functions the page can call as window.pywebview.api.* (window controls only).
    Attributes starting with _ are not exposed to the page.

    Window operations run one at a time on a worker thread and the page's call returns at
    once: changing on_top and then resizing from inside a page call deadlocks the window
    (found when leaving the desk strip froze the app)."""

    def __init__(self, pad):
        self._pad = pad
        self._window = None
        self._mini = False
        # Sizes in resize() units, which differ slightly from creation units at high DPI;
        # the page measures itself afterwards and nudges them with grow().
        self._sizes = {False: FULL_SIZE, True: MINI_SIZE}
        self._confirmed = False
        self._stop_on_exit = False
        self._ops: queue.Queue = queue.Queue()
        threading.Thread(target=self._worker, name="window-ops", daemon=True).start()

    def _worker(self):
        while True:
            op = self._ops.get()
            try:
                op()
            except Exception:
                log.exception("window operation failed")

    def minimize(self):
        self._ops.put(self._window.minimize)

    def set_mini(self, on):
        on = bool(on)
        self._mini = on

        def op():
            self._window.resize(*self._sizes[on])  # resize first, then on_top (the other order hangs)
            self._window.on_top = on
        self._ops.put(op)

    def grow(self, dw, dh):
        """The page measured itself and needs the current window this much bigger."""
        dw, dh = int(dw), int(dh)
        if not (dw or dh) or abs(dw) > 200 or abs(dh) > 200:
            return
        mode = self._mini
        w, h = self._sizes[mode]
        self._sizes[mode] = (max(200, w + dw), max(30, h + dh))
        self._ops.put(lambda: self._window.resize(*self._sizes[mode]))

    def close_app(self, stop_belt=False):
        self._stop_on_exit = bool(stop_belt)
        self._confirmed = True
        self._ops.put(self._window.destroy)

    def _on_closing(self):
        """Alt+F4 / taskbar close: ask first if the belt is moving."""
        if self._confirmed:
            return True
        s = self._pad.snapshot()
        if s["connected"] and s["running"]:
            self._window.evaluate_js("window.tp && window.tp.askClose()")
            return False
        return True


def main():
    cfg = config.load()
    setup_logging()
    log.info("TrekPad Control starting")

    store = WorkoutStore(config.APP_DIR / "workouts.db")
    recorder = WorkoutRecorder(store, cfg["min_workout_seconds"], cfg["workout_end_idle_seconds"])

    def remember_address(addr):
        cfg["address"] = addr
        config.save(cfg)

    pad = PadController(cfg.get("address") or None, cfg["max_speed_mph"], on_address_found=remember_address)
    pad.sample_listeners.append(recorder.feed)

    hk = cfg["hotkeys"]
    actions = {
        "speed_up": ("Speed +", lambda: pad.submit("change_speed", cfg["speed_step_mph"])),
        "speed_down": ("Speed −", lambda: pad.submit("change_speed", -cfg["speed_step_mph"])),
        "incline_up": ("Incline +", lambda: pad.submit("change_incline", cfg["incline_step_pct"])),
        "incline_down": ("Incline −", lambda: pad.submit("change_incline", -cfg["incline_step_pct"])),
        "start": ("Start", lambda: pad.submit("start")),
        "stop": ("Stop", lambda: pad.submit("stop")),
    }
    port = int(cfg["api_port"])
    info = {"lan_url": f"http://{lan_ip()}:{port}",
            "hotkeys": [[pretty(hk[n]), label] for n, (label, _) in actions.items() if hk.get(n)]}
    try:
        api = ApiServer(pad, store, recorder, cfg, info)
    except OSError:
        log.error("port %s busy", port)
        webview.create_window("TrekPad", html=f"<body style='font:14px Segoe UI;padding:20px'>"
                              f"Port {port} is busy. TrekPad Control is probably already running.</body>",
                              width=380, height=140)
        webview.start()
        return

    pad.start_background()
    api.start()

    hotkeys = Hotkeys([(hk[name], fn) for name, (_, fn) in actions.items() if hk.get(name)])
    hotkeys.start()
    if hotkeys.failed:
        pad._msg("Some shortcuts are taken by another app: " + ", ".join(pretty(c) for c in hotkeys.failed))

    theme = cfg.get("theme", "system")
    dark = theme == "dark" or (theme == "system" and windows_prefers_dark())
    bridge = DesktopBridge(pad)
    window = webview.create_window(
        "TrekPad", url=f"http://127.0.0.1:{port}/#token={cfg['api_token']}",
        width=FULL_SIZE[0], height=FULL_SIZE[1], min_size=(200, 30),  # default min height (100) is taller than the strip
        resizable=False, frameless=True, easy_drag=False,
        background_color="#121417" if dark else "#F3F4F6", js_api=bridge)
    bridge._window = window
    window.events.closing += bridge._on_closing
    webview.start(private_mode=False, storage_path=str(config.APP_DIR / "webview"))

    log.info("closing")
    recorder.finish()
    pad.shutdown(stop_belt=bridge._stop_on_exit)
    api.stop()


if __name__ == "__main__":
    main()
