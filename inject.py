"""Synthetic keyboard/mouse input via SendInput (Windows only)."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

IS_WIN = os.name == "nt"

VK_SHIFT, VK_CONTROL, VK_LSHIFT, VK_RSHIFT = 0x10, 0x11, 0xA0, 0xA1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


if IS_WIN:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD


def _send(*inputs: INPUT):
    if IS_WIN and inputs:
        arr = (INPUT * len(inputs))(*inputs)
        user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _key(vk: int, up: bool = False) -> INPUT:
    return INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)))


def _mouse(flags: int) -> INPUT:
    return INPUT(INPUT_MOUSE, _U(mi=MOUSEINPUT(0, 0, 0, flags, 0, 0)))


def release_shift():
    """Make Shift look released, even while the physical key is still held."""
    _send(_key(VK_LSHIFT, True), _key(VK_RSHIFT, True), _key(VK_SHIFT, True))


def press_shift():
    _send(_key(VK_SHIFT))


def chord(vk: int, *, ctrl: bool = True):
    seq = ([_key(VK_CONTROL)] if ctrl else []) + [_key(vk), _key(vk, True)] + ([_key(VK_CONTROL, True)] if ctrl else [])
    _send(*seq)


def copy():
    chord(ord("C"))


def paste():
    chord(ord("V"))


def move(x: int, y: int):
    if IS_WIN:
        user32.SetCursorPos(int(x), int(y))


def left_down(x: int | None = None, y: int | None = None):
    if x is not None:
        move(x, y)
    _send(_mouse(MOUSEEVENTF_LEFTDOWN))


def left_up(x: int | None = None, y: int | None = None):
    if x is not None:
        move(x, y)
    _send(_mouse(MOUSEEVENTF_LEFTUP))


def clipboard_seq() -> int:
    return int(user32.GetClipboardSequenceNumber()) if IS_WIN else 0
