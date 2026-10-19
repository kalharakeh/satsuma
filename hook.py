"""Global Shift-drag detection.

One low-level mouse hook (pynput) serves two features:

* Files: left button down in Explorer / the desktop, cursor moved a few pixels,
  Shift held -> `started` fires and the app shows its drop panel at the cursor.
  `released` fires when the button comes back up. The press itself is never
  touched, so Explorer's own drag-and-drop carries the files to the panel.
* Text: Shift + press anywhere else is handled by textdrag.TextDragFilter, which
  may hold the press back (see textdrag.py).
"""
from __future__ import annotations

import ctypes
import os
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
NO_TEXT_DRAG = FILE_MANAGERS | {
    "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",  # taskbar
    "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow",           # Start, flyouts
    "TaskManagerWindow", "ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS",  # terminals select differently
}

if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                             ctypes.POINTER(wintypes.DWORD)]


def key_down(vk: int) -> bool:
    return IS_WIN and bool(user32.GetAsyncKeyState(vk) & 0x8000)


def shift_down() -> bool:
    return key_down(0x10)


def window_at(x: int | None = None, y: int | None = None) -> tuple[str, int]:
    """(class name of the top-level window at a point - default: the cursor - and its process id)."""
    pt = wintypes.POINT()
    if x is None:
        user32.GetCursorPos(ctypes.byref(pt))
    else:
        pt.x, pt.y = x, y
    hwnd = user32.WindowFromPoint(pt)
    root = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(root, buf, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    return buf.value, pid.value


def window_under_cursor() -> tuple[str, int]:
    return window_at()


def _elevated(process_handle) -> bool | None:
    tok = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process_handle, 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
        return None
    try:
        val, size = wintypes.DWORD(), wintypes.DWORD()
        if not advapi32.GetTokenInformation(tok, 20, ctypes.byref(val), 4, ctypes.byref(size)):  # TokenElevation
            return None
        return bool(val.value)
    finally:
        kernel32.CloseHandle(tok)


_ME_ELEVATED: bool | None = None
_elev_cache: dict[int, bool] = {}


def can_inject_into(pid: int) -> bool:
    """False for admin windows when we aren't admin - Windows would drop our replayed clicks."""
    global _ME_ELEVATED
    if _ME_ELEVATED is None:
        _ME_ELEVATED = bool(_elevated(kernel32.GetCurrentProcess()))
    if _ME_ELEVATED:
        return True
    if pid in _elev_cache:
        return not _elev_cache[pid]
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        elevated = True  # can't even look: assume protected, don't intercept
    else:
        try:
            e = _elevated(h)
            elevated = True if e is None else e
        finally:
            kernel32.CloseHandle(h)
    if len(_elev_cache) > 256:
        _elev_cache.clear()
    _elev_cache[pid] = elevated
    return not elevated


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
        self.text = None  # textdrag.TextDragFilter, created in start()

    def start(self) -> str | None:
        """Start the hook. Returns an error message, or None when running."""
        if not IS_WIN:
            return "Shift-drag needs Windows."
        try:
            from pynput import mouse
        except Exception as e:
            return f"pynput isn't available ({e})."
        from textdrag import TextDragFilter
        self.text = TextDragFilter(self._text_eligible)
        self._button = mouse.Button.left
        self._listener = mouse.Listener(on_move=self._on_move, on_click=self._on_click,
                                        win32_event_filter=self._filter)
        self._listener.daemon = True
        self._listener.start()
        return None

    def stop(self):
        if self._listener:
            self._listener.stop()

    # Runs inside the hook for every mouse event: must be quick and must never raise
    # (except pynput's suppress exception, which is how an event gets swallowed).
    def _filter(self, msg, data):
        if data.flags & 0x3:  # injected (by us or another tool): leave it alone
            return False
        verdict = "pass"
        try:
            if self.text is not None:
                verdict = self.text.handle(msg, data.pt.x, data.pt.y, shift_down())
        except Exception:
            verdict = "pass"
        if verdict == "suppress":
            self._listener.suppress_event()  # raises; the event goes nowhere
        return True

    def _text_eligible(self, x: int, y: int) -> bool:
        if not self.settings.get("text_drag") or key_down(0x11) or key_down(0x12):  # Ctrl / Alt held
            return False
        cls, pid = window_at(x, y)
        if pid == os.getpid() or cls in NO_TEXT_DRAG:
            return False
        return can_inject_into(pid)

    # Callbacks run on pynput's thread: keep them tiny, never raise.
    def _on_click(self, x, y, button, pressed, injected=False):
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

    def _on_move(self, x, y, injected=False):
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
