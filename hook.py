"""Global Shift-drag detection.

A low-level mouse hook (pynput) watches for: left button down, cursor moved
more than a few pixels, Shift held. When that happens during a drag that
started in Explorer or on the desktop, `started` fires and the app shows its
drop panel at the cursor. `released` fires when the button comes back up.
Shift can be pressed before or during the drag.
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

IS_WIN = os.name == "nt"
THRESHOLD_SQ = 14 * 14
FILE_MANAGERS = {
    "CabinetWClass", "ExploreWClass",   # Explorer windows
    "Progman", "WorkerW",               # desktop
    "#32770",                           # Open / Save dialogs
    "dopus.lister", "TTOTAL_CMD",       # Directory Opus, Total Commander
}

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]


def shift_down() -> bool:
    return IS_WIN and bool(user32.GetAsyncKeyState(0x10) & 0x8000)


def window_under_cursor() -> tuple[str, int]:
    """(class name of the top-level window under the cursor, its process id)."""
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    hwnd = user32.WindowFromPoint(pt)
    root = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(root, buf, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    return buf.value, pid.value


class DragWatcher(QObject):
    started = Signal()
    released = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self._down = None
        self._eligible = False
        self._fired = False
        self._listener = None

    def start(self) -> str | None:
        """Start the hook. Returns an error message, or None when running."""
        if not IS_WIN:
            return "Shift-drag needs Windows."
        try:
            from pynput import mouse
        except Exception as e:
            return f"pynput isn't available ({e})."
        self._button = mouse.Button.left
        self._listener = mouse.Listener(on_move=self._on_move, on_click=self._on_click)
        self._listener.daemon = True
        self._listener.start()
        return None

    def stop(self):
        if self._listener:
            self._listener.stop()

    # Hook callbacks run on pynput's thread: keep them tiny, never raise.
    def _on_click(self, x, y, button, pressed):
        try:
            if button != self._button:
                return
            if pressed:
                self._down = (x, y)
                self._fired = False
                self._eligible = self._is_eligible()
            else:
                if self._fired:
                    self.released.emit()
                self._down = None
                self._fired = False
        except Exception:
            pass

    def _on_move(self, x, y):
        try:
            if self._down is None or self._fired or not self._eligible:
                return
            dx, dy = x - self._down[0], y - self._down[1]
            if dx * dx + dy * dy > THRESHOLD_SQ and shift_down():
                self._fired = True
                self.started.emit()
        except Exception:
            pass

    def _is_eligible(self) -> bool:
        if not self.settings.get("shift_drag"):
            return False
        cls, pid = window_under_cursor()
        if pid == os.getpid():
            return False
        return cls in FILE_MANAGERS or not self.settings.get("explorer_only")
