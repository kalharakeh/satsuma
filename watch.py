"""Watch folders: convert every new file that lands in a folder.

Polls every 2 seconds (cheap for normal folders, works on network drives too).
A file is picked up once its size has stayed the same for two checks, so
half-copied downloads are left alone. Files that were already there when
watching started, and files Satsuma itself produced, are never touched.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer

import engine


class WatchManager(QObject):
    def __init__(self, ctrl):
        super().__init__(ctrl)
        self.ctrl = ctrl
        self.timer = QTimer(self, interval=2000, timeout=self.scan)
        self.rules: list[dict] = []
        self.known: dict[str, set[str]] = {}          # folder -> names already seen/handled
        self.pending: dict[str, tuple[int, int]] = {}  # path -> (size, stable checks)
        self.produced: set[str] = set()

    def start(self):
        self.reload()
        self.timer.start()

    def reload(self):
        self.rules = [r for r in (self.ctrl.settings.get("watch") or [])
                      if Path(r.get("folder", "")).is_dir() and r.get("action") in engine.BY_ID]
        for r in self.rules:
            if r["folder"] not in self.known:  # ignore what's already there
                self.known[r["folder"]] = {p.name for p in self._files(r["folder"])}

    def note_outputs(self, row):
        for o in row.outputs:
            self.produced.add(str(Path(o)).lower())

    @staticmethod
    def _files(folder: str):
        try:
            return [p for p in Path(folder).iterdir()
                    if p.is_file() and not p.name.startswith(("~", ".")) and not p.name.endswith((".part", ".crdownload", ".tmp"))]
        except OSError:
            return []

    def scan(self):
        for r in self.rules:
            act = engine.BY_ID.get(r["action"])
            known = self.known.setdefault(r["folder"], set())
            files = self._files(r["folder"])
            known &= {p.name for p in files}  # forget files that left, so a re-added one counts as new
            for p in files:
                key = str(p).lower()
                if p.name in known or key in self.produced:
                    known.add(p.name)
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                last, stable = self.pending.get(key, (-1, 0))
                stable = stable + 1 if size == last and size > 0 else 0
                self.pending[key] = (size, stable)
                if stable < 2:
                    continue
                known.add(p.name)
                self.pending.pop(key, None)
                if act and act.applies([p]):
                    self.ctrl.jobs.add(act, [p])
