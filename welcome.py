"""First-run welcome tour: how Satsuma works, a place to try it, and the main switches."""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

import integrate
from dialogs import DIALOG_QSS

IS_WIN = os.name == "nt"
TOUR_VERSION = 2  # 2 = shown once to 1.0/1.1 users too; bump to show the tour again after big changes

QSS = DIALOG_QSS + """
QLabel#h1 { font-size: 22px; font-weight: 700; }
QLabel#lead { color: #C7C7CC; font-size: 13px; }
QLabel#step { color: #8E8E93; font-size: 11px; font-weight: 600; }
QLabel#dot { background: #3A3A3C; border-radius: 4px; }
QLabel#dot[on="true"] { background: #FF8A1F; }
QLabel#kbd { background: #2C2C2E; border: 1px solid #48484A; border-radius: 6px; padding: 3px 8px; font-weight: 600; }
"""


def _asset(name: str) -> str:
    return str(integrate.assets_dir() / name)


def practice_folder() -> Path:
    """A folder with a picture and a text file to try Shift-drag on."""
    d = Path(tempfile.gettempdir()) / "Satsuma - try me"
    d.mkdir(exist_ok=True)
    pic = d / "Try me - Shift-drag this picture.png"
    if not pic.exists():
        from PIL import Image, ImageDraw
        im = Image.new("RGB", (1200, 800), (255, 138, 31))
        dr = ImageDraw.Draw(im)
        for i in range(0, 1200, 60):
            dr.ellipse([i, 300 + (i % 240) // 2, i + 200, 500 + (i % 240) // 2], fill=(255, 190 - i % 80, 60))
        im.save(pic)
    txt = d / "Try me - select some text in here.txt"
    if not txt.exists():
        txt.write_text("Select this sentence, hold Shift and drag it into another window.\n\n"
                       "Or drop it on UPPER, QR or Translate in the little panel that appears.\n", encoding="utf-8")
    return d


def _page(step: str, title: str, lead: str, image: str | None = None, extra: QWidget | None = None) -> QWidget:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(4, 0, 4, 0)
    v.setSpacing(10)
    v.addWidget(QLabel(step, objectName="step"))
    v.addWidget(QLabel(title, objectName="h1"))
    lab = QLabel(lead, objectName="lead")
    lab.setWordWrap(True)
    lab.setTextFormat(Qt.RichText)
    v.addWidget(lab)
    if image:
        pic = QLabel()
        pm = QPixmap(_asset(image))
        if not pm.isNull():
            pic.setPixmap(pm.scaled(560, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        pic.setAlignment(Qt.AlignCenter)
        v.addWidget(pic, 1)
    if extra:
        v.addWidget(extra)
    v.addStretch(1)
    return w


def _button(text: str, fn) -> QWidget:
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    b = QPushButton(text)
    b.setAutoDefault(False)
    b.clicked.connect(fn)
    h.addWidget(b)
    h.addStretch(1)
    return row


class Welcome(QDialog):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl, self.settings = ctrl, ctrl.settings
        self.setWindowTitle("Welcome to Satsuma")
        self.setStyleSheet(QSS)
        self.setFixedSize(640, 560)
        v = QVBoxLayout(self)
        v.setContentsMargins(26, 22, 26, 18)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)

        def open_folder():
            d = practice_folder()
            if IS_WIN:
                os.startfile(d)
            else:
                subprocess.Popen(["xdg-open", str(d)])

        def open_text():
            f = practice_folder() / "Try me - select some text in here.txt"
            if IS_WIN:
                subprocess.Popen(["notepad.exe", str(f)])
            else:
                subprocess.Popen(["xdg-open", str(f)])

        self.stack.addWidget(_page(
            "1 of 4", "Shift-drag a file",
            "Hold <b>Shift</b> while you drag any file in Explorer or on the desktop. A panel of formats "
            "pops up under the cursor; drop the file on one. The converted copy is saved <b>next to the "
            "original</b>, which is never changed.",
            "welcome-files.png", _button("Try it - open a practice folder", open_folder)))
        self.stack.addWidget(_page(
            "2 of 4", "Shift-drag text, too",
            "Select text in any app, hold <b>Shift</b> and drag it. Let go anywhere to paste a copy, or "
            "drop it on a tool: UPPER, Clean, Translate, QR code and more. Your clipboard is put back afterwards.",
            "welcome-text.png", _button("Try it - open a sample text", open_text)))
        ways = QLabel(
            "<p><b>Right-click a file &rarr; Convert with Satsuma</b> (on Windows 11: <i>Show more options</i>)</p>"
            "<p><b>Send To &rarr; Satsuma</b> for many files at once</p>"
            "<p><b>The orange tray icon</b> next to the clock: convert files, undo, settings. If you don't see "
            "it, click <span style='font-weight:700'>^</span> and drag the orange icon onto the taskbar so it "
            "stays visible.</p>"
            "<p>Results appear in the bottom-right corner; click one to open its folder. "
            "<b>Undo</b> sends a result to the Recycle Bin.</p>", objectName="lead")
        ways.setWordWrap(True)
        self.stack.addWidget(_page("3 of 4", "Other ways in", "", None, ways))
        self.stack.addWidget(self._setup_page())

        bottom = QHBoxLayout()
        self.dots = []
        for _ in range(self.stack.count()):
            d = QLabel(objectName="dot")
            d.setFixedSize(8, 8)
            self.dots.append(d)
            bottom.addWidget(d)
        bottom.addStretch(1)
        self.back = QPushButton("Back")
        self.back.setAutoDefault(False)
        self.back.clicked.connect(lambda: self._go(-1))
        self.next = QPushButton("Next")
        self.next.setDefault(True)
        self.next.clicked.connect(lambda: self._go(1))
        skip = QPushButton("Skip")
        skip.setAutoDefault(False)
        skip.clicked.connect(self.accept)
        bottom.addWidget(skip)
        bottom.addWidget(self.back)
        bottom.addWidget(self.next)
        v.addLayout(bottom)
        self._go(0)

    def _setup_page(self) -> QWidget:
        s = self.settings
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        checks = []

        def add(text, on, fn):
            c = QCheckBox(text)
            c.setChecked(bool(on))
            c.toggled.connect(lambda val: _safe(fn, val))
            v.addWidget(c)
            checks.append(c)
        if IS_WIN:
            add("Start Satsuma with Windows", integrate.startup_enabled(), integrate.set_startup)
            add("Add “Convert with Satsuma” to the right-click menu", integrate.context_installed(),
                lambda on: integrate.install_context() if on else integrate.uninstall_context())
        add("Shift-drag selected text", s.get("text_drag"), lambda on: s.set("text_drag", on))
        add("Check for updates once a day", s.get("update_check") is not False, lambda on: s.set("update_check", on))
        self.ai_check = QCheckBox("Download the AI tools now (about 90 MB): background removal, upscaling, "
                                  "subtitles. Otherwise they download the first time you use one.")
        try:
            import aipack
            installed = aipack.installed()
        except Exception:
            installed = False
        self.ai_check.setVisible(not installed)
        v.addWidget(self.ai_check)
        hint = QLabel("You can change all of this later: right-click the tray icon → <b>Settings</b>.",
                      objectName="lead")
        hint.setTextFormat(Qt.RichText)
        v.addSpacing(8)
        v.addWidget(hint)
        return _page("4 of 4", "A few choices", "These are the defaults. Change anything you like.", None, box)

    def _go(self, delta: int):
        i = self.stack.currentIndex() + delta
        if i >= self.stack.count():
            self.accept()
            return
        i = max(0, i)
        self.stack.setCurrentIndex(i)
        last = i == self.stack.count() - 1
        self.next.setText("Start using Satsuma" if last else "Next")
        self.back.setEnabled(i > 0)
        for n, d in enumerate(self.dots):
            d.setProperty("on", n == i)
            d.style().unpolish(d)
            d.style().polish(d)

    def done(self, r):
        self.settings.set("welcomed", TOUR_VERSION)
        if self.ai_check.isVisible() and self.ai_check.isChecked():
            import engine
            act = engine.BY_ID.get("ai-install")
            if act:
                self.ctrl.jobs.add(act, [])
        super().done(r)


def _safe(fn, val):
    try:
        fn(val)
    except Exception:
        pass
