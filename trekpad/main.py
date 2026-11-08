"""Entry point: wires the pad, workout recorder, local server, hotkeys and window together.

The interface is HTML/CSS (trekpad/ui) served by the local server and shown in a
frameless native window (pywebview, Edge WebView2). The same page works in a phone
browser on the same Wi-Fi.
"""
from __future__ import annotations

import logging
import socket
import sys
import threading
import time
import winreg
from logging.handlers import RotatingFileHandler

import webview

from . import config
from .api import ApiServer
from .deadlock_log import DeadlockLogWatcher
from .dota_gsi import DotaGSI, install_config as install_dota_config
from .gamemode import GameMode, WindowsSignals, windows_beep
from .hotkeys import Hotkeys, pretty
from .pad import PadController
from .win32window import Win32Window
from .workouts import WorkoutRecorder, WorkoutStore, weight_kg_from_config

log = logging.getLogger("trekpad")
WINDOW_TITLE = "TrekPad"

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

    Every window operation goes through Win32Window (asynchronous Win32 calls), never
    through pywebview's window methods, which can deadlock (see win32window.py).
    The page passes CSS pixels plus its devicePixelRatio; sizes here are physical pixels."""

    def __init__(self, pad):
        self._pad = pad
        self._win = Win32Window(WINDOW_TITLE)
        self._mini = False
        self._full_size = None   # physical size of the full window, remembered while in the strip
        self._mini_size = None
        self._drag_origin = None
        self._confirmed = False
        self._stop_on_exit = False

    def minimize(self):
        self._win.minimize()

    def set_mini(self, on, dpr=1.0):
        on, dpr = bool(on), float(dpr) or 1.0
        rect = self._win.rect()
        if rect is None or on == self._mini:
            return
        if on:
            self._full_size = rect[2:]
            w, h = self._mini_size or (round(MINI_SIZE[0] * dpr), round(MINI_SIZE[1] * dpr))
        else:
            self._mini_size = rect[2:]
            w, h = self._full_size or (round(FULL_SIZE[0] * dpr), round(FULL_SIZE[1] * dpr))
        self._mini = on
        self._win.set_size(w, h, topmost=on)

    def grow(self, dw, dh, dpr=1.0):
        """The page measured itself and needs the window this many CSS px bigger."""
        dpr = float(dpr) or 1.0
        dw, dh = round(float(dw) * dpr), round(float(dh) * dpr)
        rect = self._win.rect()
        if rect is None or not (dw or dh) or abs(dw) > 400 or abs(dh) > 400:
            return
        self._win.set_size(max(200, rect[2] + dw), max(30, rect[3] + dh))

    def drag_start(self):
        rect = self._win.rect()
        self._drag_origin = rect[:2] if rect else None

    def drag_to(self, dx, dy, dpr=1.0):
        """Move the window by (dx, dy) CSS px from where the drag started."""
        if self._drag_origin is None:
            return
        dpr = float(dpr) or 1.0
        self._win.move_to(self._drag_origin[0] + round(float(dx) * dpr),
                          self._drag_origin[1] + round(float(dy) * dpr))

    def close_app(self, stop_belt=False):
        self._stop_on_exit = bool(stop_belt)
        self._confirmed = True
        # Close a moment later so this call's reply reaches the page before the WebView goes away.
        threading.Timer(0.2, self._win.close).start()

    def _on_closing(self):
        """Alt+F4 / taskbar close: ask first if the belt is moving. The page picks the
        request up from /api/status (no calls into the window from here)."""
        if self._confirmed:
            return True
        s = self._pad.snapshot()
        if s["connected"] and s["running"]:
            self._pad.ui_ask_close_at = time.time()
            return False
        return True


def main():
    cfg = config.load()
    setup_logging()
    log.info("TrekPad Control starting")

    store = WorkoutStore(config.APP_DIR / "workouts.db")
    recorder = WorkoutRecorder(store, cfg["min_workout_seconds"], cfg["workout_end_idle_seconds"],
                               weight_kg=weight_kg_from_config(cfg))

    def remember_address(addr):
        cfg["address"] = addr
        config.save(cfg)

    pad = PadController(cfg.get("address") or None, cfg["max_speed_mph"], on_address_found=remember_address)
    pad.sample_listeners.append(recorder.feed)
    game = GameMode(pad, cfg["game_mode"], WindowsSignals(), beep=windows_beep, save=lambda: config.save(cfg))
    # Live game sources, one per game (each returns None while that game isn't sending):
    #   Deadlock: its console log (needs the -condebug launch option)
    #   Dota 2: Game State Integration posted to /api/gsi/dota2 (needs -gamestateintegration)
    deadlock_log = DeadlockLogWatcher(game.event)
    dota = DotaGSI(game.event)
    game.sources = {
        "deadlock": lambda: deadlock_log.parser.info() if deadlock_log.available else None,
        "dota2": dota.info,
    }
    dota_cfg = install_dota_config(int(cfg["api_port"]), cfg["dota_gsi_token"])
    log.info("Dota 2 GSI config: %s", dota_cfg or "Dota 2 not found")

    hk = cfg["hotkeys"]
    actions = {
        "speed_up": ("Speed +", lambda: pad.submit("change_speed", cfg["speed_step_mph"])),
        "speed_down": ("Speed −", lambda: pad.submit("change_speed", -cfg["speed_step_mph"])),
        "incline_up": ("Incline +", lambda: pad.submit("change_incline", cfg["incline_step_pct"])),
        "incline_down": ("Incline −", lambda: pad.submit("change_incline", -cfg["incline_step_pct"])),
        "start": ("Start", lambda: pad.submit("start")),
        "stop": ("Stop", lambda: pad.submit("stop")),
        "game_mode": ("Game mode on/off", game.toggle),
    }
    port = int(cfg["api_port"])
    info = {"lan_url": f"http://{lan_ip()}:{port}",
            "hotkeys": [[pretty(hk[n]), label] for n, (label, _) in actions.items() if hk.get(n)]}
    try:
        api = ApiServer(pad, store, recorder, cfg, info, game=game, dota=dota)
    except OSError:
        log.error("port %s busy", port)
        webview.create_window("TrekPad", html=f"<body style='font:14px Segoe UI;padding:20px'>"
                              f"Port {port} is busy. TrekPad Control is probably already running.</body>",
                              width=380, height=140)
        webview.start()
        return

    pad.start_background()
    api.start()
    game.start()
    deadlock_log.start()

    hotkeys = Hotkeys([(hk[name], fn) for name, (_, fn) in actions.items() if hk.get(name)])
    hotkeys.start()
    if hotkeys.failed:
        pad._msg("Some shortcuts are taken by another app: " + ", ".join(pretty(c) for c in hotkeys.failed))

    theme = cfg.get("theme", "system")
    dark = theme == "dark" or (theme == "system" and windows_prefers_dark())
    bridge = DesktopBridge(pad)
    window = webview.create_window(
        WINDOW_TITLE, url=f"http://127.0.0.1:{port}/#token={cfg['api_token']}",
        width=FULL_SIZE[0], height=FULL_SIZE[1], min_size=(200, 30),  # default min height (100) is taller than the strip
        resizable=False, frameless=True, easy_drag=False,
        background_color="#121417" if dark else "#F3F4F6", js_api=bridge)
    window.events.closing += bridge._on_closing
    webview.start(private_mode=False, storage_path=str(config.APP_DIR / "webview"))

    log.info("closing")
    game.stop()
    deadlock_log.stop()
    recorder.finish()
    pad.shutdown(stop_belt=bridge._stop_on_exit)
    api.stop()


if __name__ == "__main__":
    main()
