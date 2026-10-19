"""'Copy diagnostic info' / 'Report a problem': everything a bug report needs, copied to the
clipboard for the user to look at and paste. Nothing is sent anywhere automatically."""
from __future__ import annotations

import os
import platform
import sys
import webbrowser
from pathlib import Path

from PySide6.QtGui import QGuiApplication

ISSUE_URL = "https://github.com/kalharakeh/satsuma/issues/new?template=bug_report.md"


def _windows_version() -> str:
    if os.name != "nt":
        return platform.platform()
    v = sys.getwindowsversion()
    name = "Windows 11" if v.build >= 22000 else "Windows 10" if v.major == 10 else f"Windows {v.major}"
    return f"{name} (build {v.build})"


def report(settings, log_path: Path, version: str) -> str:
    import engine
    try:
        import aipack
        ai = "installed" if aipack.installed() else "not installed"
    except Exception as e:
        ai = f"error: {e}"
    try:
        ff = engine.ffmpeg_exe()
        ff = "bundled" if Path(ff).parent == engine._app_dir() / "bin" else ff.replace(str(Path.home()), "~")
    except Exception as e:
        ff = f"missing ({e})"
    data = getattr(settings, "data", {})
    lines = [
        f"Satsuma {version} ({'installed app' if getattr(sys, 'frozen', False) else 'from source'})",
        f"{_windows_version()}, Python {platform.python_version()}, {platform.machine()}",
        f"FFmpeg: {ff} | AI pack: {ai}",
        "Settings: " + ", ".join(f"{k}={'default' if data.get(k) is None else data.get(k)}" for k in
                                 ("shift_drag", "explorer_only", "text_drag", "text_panel", "update_check")),
        f"Watch folders: {len(data.get('watch') or [])} | Presets file: "
        f"{'yes' if (Path(os.environ.get('APPDATA', '')) / 'Satsuma' / 'presets.json').exists() else 'no'}",
        "",
        "Last log lines (check them for anything private before posting):",
    ]
    try:
        tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]
    except OSError:
        tail = ["(no log yet)"]
    home = str(Path.home())
    lines += [l.replace(home, "~") for l in tail]  # don't publish the Windows user name
    return "```\n" + "\n".join(lines) + "\n```"


def copy_report(settings, log_path: Path, version: str) -> str:
    text = report(settings, log_path, version)
    QGuiApplication.clipboard().setText(text)
    return text


def report_problem(settings, log_path: Path, version: str):
    copy_report(settings, log_path, version)
    webbrowser.open(ISSUE_URL)
