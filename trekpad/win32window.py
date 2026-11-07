"""Move, resize, pin, minimize and close the app window with plain Win32 calls.

Why not pywebview's window.resize()/on_top/move()? Those marshal onto the UI thread while
still holding Python's GIL; resizing makes the UI thread call back into Python (window
events), which needs the GIL -> deadlock. That froze the app when entering/leaving the desk
strip. These calls go through ctypes (which releases the GIL) and are asynchronous
(SWP_ASYNCWINDOWPOS / ShowWindowAsync / PostMessage), so they never wait on the UI thread.
All sizes and positions here are physical pixels.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

user32 = ctypes.windll.user32
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
user32.SetWindowPos.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.IsWindowVisible.argtypes = [wt.HWND]
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]

HWND_TOPMOST, HWND_NOTOPMOST = wt.HWND(-1), wt.HWND(-2)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_ASYNCWINDOWPOS = 0x1, 0x2, 0x4, 0x10, 0x4000
SW_MINIMIZE = 6
WM_CLOSE = 0x0010


class Win32Window:
    def __init__(self, title: str):
        self.title = title
        self._hwnd = None

    @property
    def hwnd(self):
        if self._hwnd is None:
            pid, found = os.getpid(), []

            def cb(h, _l):
                p = wt.DWORD()
                user32.GetWindowThreadProcessId(h, ctypes.byref(p))
                if p.value == pid and user32.IsWindowVisible(h):
                    buf = ctypes.create_unicode_buffer(256)
                    user32.GetWindowTextW(h, buf, 256)
                    if buf.value == self.title:
                        found.append(h)
                        return False
                return True

            user32.EnumWindows(WNDENUMPROC(cb), 0)
            self._hwnd = found[0] if found else None
        return self._hwnd

    def rect(self):
        r = wt.RECT()
        if not self.hwnd or not user32.GetWindowRect(self.hwnd, ctypes.byref(r)):
            return None
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def set_size(self, w: int, h: int, topmost: bool | None = None):
        if not self.hwnd:
            return
        flags = SWP_NOMOVE | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS
        if topmost is None:
            flags |= SWP_NOZORDER
            after = None
        else:
            after = HWND_TOPMOST if topmost else HWND_NOTOPMOST
        user32.SetWindowPos(self.hwnd, after, 0, 0, max(1, int(w)), max(1, int(h)), flags)

    def move_to(self, x: int, y: int):
        if self.hwnd:
            user32.SetWindowPos(self.hwnd, None, int(x), int(y), 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_ASYNCWINDOWPOS)

    def minimize(self):
        if self.hwnd:
            user32.ShowWindowAsync(self.hwnd, SW_MINIMIZE)

    def close(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
