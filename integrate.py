"""Windows shell integration: right-click menu, Send To, Start menu, start with Windows.

Everything is per-user (HKCU / %APPDATA%), so no admin rights are needed.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

APP = "Satsuma"
VERB = "Convert with Satsuma"
NOWIN = 0x08000000 if os.name == "nt" else 0
CTX_KEYS = [rf"Software\Classes\*\shell\{APP}", rf"Software\Classes\Directory\shell\{APP}"]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def assets_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets"


def launcher() -> tuple[str, list[str]]:
    """(executable, leading args) that start Satsuma without a console window."""
    if getattr(sys, "frozen", False):
        return sys.executable, []
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), [str(Path(__file__).resolve().with_name("app.py"))]


def command(*args: str) -> str:
    exe, pre = launcher()
    return subprocess.list2cmdline([exe, *pre, *args])


def _shell_folder(*parts: str) -> Path:
    return Path(os.environ["APPDATA"], "Microsoft", "Windows", *parts)


def _shortcut(path: Path, args: list[str]):
    exe, pre = launcher()
    q = lambda s: str(s).replace("'", "''")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{q(path)}');"
          f"$s.TargetPath='{q(exe)}';$s.Arguments='{q(subprocess.list2cmdline([*pre, *args]))}';"
          f"$s.IconLocation='{q(assets_dir() / 'icon.ico')}';$s.Description='{APP}';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   creationflags=NOWIN, capture_output=True)


def _notify_shell():
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


# ---------------------------------------------------------------- context menu

def context_installed() -> bool:
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, CTX_KEYS[0]))
        return True
    except OSError:
        return False


def install_context():
    import winreg
    for k in CTX_KEYS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, k) as key:
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, VERB)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(assets_dir() / "icon.ico"))
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Player")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, k + r"\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command("--open") + ' "%1"')
    _notify_shell()


def uninstall_context():
    import winreg
    for k in CTX_KEYS:
        for sub in (k + r"\command", k):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
    _notify_shell()


# --------------------------------------------------------------------- startup

def startup_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP)
        return True
    except OSError:
        return False


def set_startup(on: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if on:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, command("--tray"))
        else:
            try:
                winreg.DeleteValue(key, APP)
            except OSError:
                pass


# ----------------------------------------------------------------- all at once

def install():
    install_context()
    _shortcut(_shell_folder("SendTo", f"{APP}.lnk"), ["--open"])
    _shortcut(_shell_folder("Start Menu", "Programs", f"{APP}.lnk"), ["--tray"])
    set_startup(True)


def uninstall():
    uninstall_context()
    set_startup(False)
    for p in (_shell_folder("SendTo", f"{APP}.lnk"), _shell_folder("Start Menu", "Programs", f"{APP}.lnk")):
        try:
            p.unlink()
        except OSError:
            pass
