"""Checks GitHub Releases for a newer Satsuma and installs it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

REPO = "kalharakeh/satsuma"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES = f"https://github.com/{REPO}/releases/latest"
FROZEN = getattr(sys, "frozen", False)
DAY_MS = 24 * 3600 * 1000


def parse_version(v: str) -> tuple:
    v = v.strip().lstrip("vV").split("-")[0].split("+")[0]
    out = []
    for part in v.split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out + [0] * (3 - len(out)))


def is_newer(latest: str, current: str) -> bool:
    if not current or not current[0].isdigit():
        return False  # running from a dev checkout
    return parse_version(latest) > parse_version(current)


class Updater(QObject):
    available = Signal(str, str)   # version, notes
    progress = Signal(int)         # percent while downloading
    failed = Signal(str)

    def __init__(self, settings, current: str, log=lambda m: None):
        super().__init__()
        self.settings, self.current, self.log = settings, current, log
        self.latest: dict | None = None
        self.timer = QTimer(self, interval=DAY_MS, timeout=self.check)

    def start(self):
        if self.settings.get("update_check") is False:
            return
        QTimer.singleShot(15_000, self.check)  # don't slow down start-up
        self.timer.start()

    def check(self, manual: bool = False):
        def work():
            try:
                req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                                           "User-Agent": f"Satsuma/{self.current}"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = json.load(r)
                tag = data.get("tag_name", "")
                self.settings.set("update_last_check", int(time.time()))
                # a release is announced only once its installer has been built and attached
                ready = any(a.get("name") == "Satsuma-Setup.exe" and a.get("state", "uploaded") == "uploaded"
                            for a in data.get("assets", []))
                if is_newer(tag, self.current) and ready and (manual or tag != self.settings.get("update_skipped")):
                    self.latest = data
                    self.available.emit(tag.lstrip("v"), (data.get("body") or "")[:600])
                elif manual:
                    self.failed.emit("You have the latest version.")
            except Exception as e:
                self.log(f"update check failed: {e}")
                if manual:
                    self.failed.emit(f"Couldn't check for updates ({e.__class__.__name__}).")
        threading.Thread(target=work, daemon=True).start()

    def run_installer(self, setup: Path, version: str):
        """Start the installer on its own, so it survives Satsuma quitting (and being force-closed)."""
        setup_log = Path(tempfile.gettempdir()) / "Satsuma-update.log"
        self.settings.set("updating_to", version)
        self.log(f"update: starting installer {setup} (its log: {setup_log})")
        args = [str(setup), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={setup_log}"]
        if os.name == "nt":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, and out of any job object Satsuma runs in
            flags = 0x00000008 | 0x00000200
            try:
                subprocess.Popen(args, creationflags=flags | 0x01000000, close_fds=True)  # CREATE_BREAKAWAY_FROM_JOB
            except OSError:
                subprocess.Popen(args, creationflags=flags, close_fds=True)
        else:
            subprocess.Popen(args)
        self.progress.emit(100)
        self.stage.emit("installing")

    stage = Signal(str)  # "installing" once the installer has been started

    def install(self):
        """Download the new installer and run it. Satsuma quits; the installer restarts it."""
        if not FROZEN or not self.latest:
            self.log("update: not an installed copy - opening the releases page")
            webbrowser.open(RELEASES)
            return
        asset = next((a for a in self.latest.get("assets", []) if a.get("name") == "Satsuma-Setup.exe"), None)
        if not asset:
            self.log("update: release has no installer yet - opening the releases page")
            webbrowser.open(RELEASES)
            return
        version = self.latest["tag_name"].lstrip("v")

        def work():
            try:
                dest = Path(tempfile.gettempdir()) / f"Satsuma-Setup-{self.latest['tag_name']}.exe"
                self.log(f"update: downloading {version} ({asset.get('size', 0) // 1048576} MB) to {dest}")
                req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "Satsuma"})
                with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
                    total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
                    done = 0
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            self.progress.emit(int(done * 100 / total))
                if asset.get("size") and dest.stat().st_size != asset["size"]:
                    raise OSError("download incomplete")
                digest = (asset.get("digest") or "").removeprefix("sha256:")
                if digest:
                    import hashlib
                    h = hashlib.sha256()
                    with open(dest, "rb") as f:
                        while chunk := f.read(1 << 20):
                            h.update(chunk)
                    if h.hexdigest() != digest:
                        dest.unlink(missing_ok=True)
                        raise OSError("checksum mismatch")
                self.run_installer(dest, version)
            except Exception as e:
                self.log(f"update: failed - {e}")
                self.failed.emit(f"Update download failed ({e}). You can get it from GitHub instead.")
        threading.Thread(target=work, daemon=True).start()
