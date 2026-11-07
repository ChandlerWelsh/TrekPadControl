"""System-wide keyboard shortcuts via the Win32 RegisterHotKey API (no extra packages)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading

log = logging.getLogger(__name__)

MODS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
KEYS = {
    "page_up": 0x21, "pgup": 0x21, "page_down": 0x22, "pgdn": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "insert": 0x2D, "delete": 0x2E,
    "space": 0x20, "plus": 0xBB, "minus": 0xBD,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)},
    **{str(d): 0x30 + d for d in range(10)},
}


def parse(combo: str) -> tuple[int, int]:
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    mods, key = 0, None
    for p in parts:
        if p in MODS:
            mods |= MODS[p]
        elif p in KEYS:
            key = KEYS[p]
        else:
            raise ValueError(f"unknown key '{p}' in '{combo}'")
    if key is None:
        raise ValueError(f"no key in '{combo}'")
    return mods, key


def pretty(combo: str) -> str:
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win",
             "page_up": "PgUp", "page_down": "PgDn", "home": "Home", "end": "End"}
    return "+".join(names.get(p, p.title()) for p in combo.lower().split("+"))


class Hotkeys:
    """Registers hotkeys on a background thread. `bindings` = [(combo, callback)]."""

    def __init__(self, bindings):
        self.bindings = bindings
        self.failed: list[str] = []
        self._ready = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="hotkeys", daemon=True).start()
        self._ready.wait(3)

    def _run(self):
        user32 = ctypes.windll.user32
        callbacks = {}
        for i, (combo, cb) in enumerate(self.bindings, start=1):
            try:
                mods, vk = parse(combo)
            except ValueError as e:
                log.warning("hotkey: %s", e)
                self.failed.append(combo)
                continue
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                callbacks[i] = cb
            else:
                log.warning("hotkey %s is already used by another app", combo)
                self.failed.append(combo)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in callbacks:
                try:
                    callbacks[msg.wParam]()
                except Exception:
                    log.exception("hotkey callback failed")
