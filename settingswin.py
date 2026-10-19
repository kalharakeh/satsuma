"""One window for everything: general switches, presets, watch folders, AI pack and updates."""
from __future__ import annotations

import json
import shlex

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

import engine
import integrate
import presets
from dialogs import DIALOG_QSS, WatchDialog, _base

IS_WIN = __import__("os").name == "nt"
LANGS = [("en", "English"), ("sv", "Swedish"), ("ar", "Arabic"), ("de", "German"), ("fr", "French"),
         ("es", "Spanish"), ("it", "Italian"), ("nl", "Dutch"), ("no", "Norwegian"), ("da", "Danish"),
         ("fi", "Finnish"), ("pl", "Polish"), ("tr", "Turkish"), ("pt", "Portuguese"), ("uk", "Ukrainian"),
         ("ru", "Russian"), ("fa", "Persian"), ("hi", "Hindi"), ("zh-CN", "Chinese"), ("ja", "Japanese")]

TAB_QSS = DIALOG_QSS + """
QTabWidget::pane { border: 1px solid #3A3A3C; border-radius: 8px; top: -1px; }
QTabBar::tab { background: transparent; color: #8E8E93; padding: 7px 14px; font-size: 12px; }
QTabBar::tab:selected { color: #F2F2F7; border-bottom: 2px solid #FF8A1F; }
QSpinBox { background: #2C2C2E; color: #F2F2F7; border: 1px solid #3A3A3C; border-radius: 6px; padding: 5px; }
"""


def _check(text: str, value: bool, fn, tip: str = "") -> QCheckBox:
    c = QCheckBox(text)
    c.setChecked(bool(value))
    if tip:
        c.setToolTip(tip)
    c.toggled.connect(fn)
    return c


def _help(text: str) -> QLabel:
    lab = QLabel(text, objectName="help")
    lab.setWordWrap(True)
    return lab


class PresetForm(QDialog):
    def __init__(self, cfg: dict | None = None):
        super().__init__()
        cfg = dict(cfg or {"for": "image", "format": "webp", "quality": 80, "max_px": 1600, "strip": True})
        v = _base(self, "Preset", "A one-click tile in the panel")
        f = QFormLayout()
        self.name = QLineEdit(cfg.get("name", ""))
        self.tile = QLineEdit(cfg.get("tile", ""))
        self.tile.setMaxLength(9)
        self.hint = QLineEdit(cfg.get("hint", ""))
        self.hint.setMaxLength(14)
        self.kind = QComboBox()
        for k in ("image", "video", "audio"):
            self.kind.addItem(k.capitalize(), k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(cfg.get("for", "image"))))
        self.fmt = QComboBox()
        self.fmt.addItems(["jpg", "png", "webp", "avif", "heic"])
        self.fmt.setCurrentText(str(cfg.get("format", "jpg")))
        self.quality = QSpinBox()
        self.quality.setRange(1, 100)
        self.quality.setValue(int(cfg.get("quality") or 85))
        self.maxpx = QSpinBox()
        self.maxpx.setRange(0, 20000)
        self.maxpx.setSpecialValueText("no limit")
        self.maxpx.setValue(int(cfg.get("max_px") or 0))
        self.strip = QCheckBox("Remove EXIF / GPS")
        self.strip.setChecked(bool(cfg.get("strip")))
        self.ext = QLineEdit(cfg.get("ext", ".mp4"))
        self.args = QLineEdit(" ".join(shlex.quote(a) for a in cfg.get("ffmpeg", [])))
        self.args.setPlaceholderText("-c:v libx264 -crf 28 -vf scale=-2:720 -c:a aac")
        self.tag = QLineEdit(cfg.get("tag", ""))
        self.tag.setPlaceholderText("added to the file name, e.g. web")
        for label, w in (("Name", self.name), ("Tile text", self.tile), ("Small text", self.hint),
                         ("For", self.kind), ("Format", self.fmt), ("Quality", self.quality),
                         ("Longest side (px)", self.maxpx), ("", self.strip), ("Output extension", self.ext),
                         ("FFmpeg arguments", self.args), ("Name tag", self.tag)):
            f.addRow(label, w)
        self.form = f
        v.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.kind.currentIndexChanged.connect(self._toggle)
        self._toggle()
        self.setStyleSheet(TAB_QSS)

    def _toggle(self):
        img = self.kind.currentData() == "image"
        for w in (self.fmt, self.quality, self.maxpx, self.strip):
            self.form.setRowVisible(w, img)
        for w in (self.ext, self.args):
            self.form.setRowVisible(w, not img)

    def value(self) -> dict:
        k = self.kind.currentData()
        cfg = {"name": self.name.text().strip() or "My preset", "tile": self.tile.text().strip() or "★",
               "hint": self.hint.text().strip(), "for": k, "tag": self.tag.text().strip() or "preset"}
        if k == "image":
            cfg.update(format=self.fmt.currentText(), quality=self.quality.value(), strip=self.strip.isChecked())
            if self.maxpx.value():
                cfg["max_px"] = self.maxpx.value()
        else:
            ext = self.ext.text().strip() or ".mp4"
            cfg.update(ext=ext if ext.startswith(".") else "." + ext, ffmpeg=shlex.split(self.args.text()))
        return cfg


class SettingsWindow(QDialog):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl, self.settings = ctrl, ctrl.settings
        self.setWindowTitle("Satsuma - Settings")
        self.setStyleSheet(TAB_QSS)
        self.setMinimumWidth(560)
        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        tabs = QTabWidget()
        tabs.addTab(self._general(), "General")
        tabs.addTab(self._presets(), "Presets")
        tabs.addTab(self._watch(), "Watch folders")
        tabs.addTab(self._ai(), "AI tools")
        tabs.addTab(self._about(), "About")
        v.addWidget(tabs)
        close = QPushButton("Close")
        close.setAutoDefault(False)
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)

    # -------------------------------------------------------------- General
    def _general(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setSpacing(8)
        s = self.settings
        v.addWidget(_check("Shift-drag files to convert them", s.get("shift_drag"), lambda on: s.set("shift_drag", on)))
        v.addWidget(_check("Only react to drags from Explorer, the desktop and Open/Save dialogs",
                           s.get("explorer_only"), lambda on: s.set("explorer_only", on)))
        v.addWidget(_check("Shift-drag selected text to paste a copy where you let go", s.get("text_drag"),
                           lambda on: s.set("text_drag", on)))
        v.addWidget(_check("Show the text tools panel while dragging text", s.get("text_panel") is not False,
                           lambda on: s.set("text_panel", on)))
        if IS_WIN:
            v.addWidget(_check("“Convert with Satsuma” in the right-click menu", integrate.context_installed(),
                               lambda on: integrate.install_context() if on else integrate.uninstall_context()))
            v.addWidget(_check("Start with Windows", integrate.startup_enabled(), integrate.set_startup))
        v.addWidget(_check("Check for updates once a day", s.get("update_check") is not False,
                           lambda on: s.set("update_check", on)))
        row = QHBoxLayout()
        row.addWidget(QLabel("Translate text to"))
        lang = QComboBox()
        for code, name in LANGS:
            lang.addItem(name, code)
        from PySide6.QtCore import QLocale
        current = s.get("translate_to") or QLocale.system().name().split("_")[0]
        lang.setCurrentIndex(max(0, lang.findData(current)))
        lang.currentIndexChanged.connect(lambda _: s.set("translate_to", lang.currentData()))
        row.addWidget(lang)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    # -------------------------------------------------------------- Presets
    def _presets(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(_help("Presets are your own tiles, shown first in the panel. "
                          "For example “WEBP, max 1600 px, 80%” or “720p for WhatsApp”."))
        self.preset_list = QListWidget()
        self.preset_list.itemDoubleClicked.connect(lambda _: self._edit_preset())
        v.addWidget(self.preset_list)
        row = QHBoxLayout()
        for text, fn in (("Add…", self._add_preset), ("Edit…", self._edit_preset),
                         ("Delete", self._delete_preset), ("Open file", self.ctrl.edit_presets)):
            b = QPushButton(text)
            b.setAutoDefault(False)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        self._load_presets()
        return w

    def _read(self) -> list[dict]:
        p = presets.ensure_presets_file()
        try:
            return json.loads(p.read_text(encoding="utf-8-sig")).get("presets", [])
        except (OSError, ValueError):
            return []

    def _write(self, items: list[dict]):
        p = presets.ensure_presets_file()
        p.write_text(json.dumps({"presets": items}, indent=2, ensure_ascii=False), encoding="utf-8")
        presets.load_presets(force=True)
        self._load_presets()

    def _load_presets(self):
        self.preset_list.clear()
        for cfg in self._read():
            it = QListWidgetItem(f"{cfg.get('tile', '★')}   —   {cfg.get('name', '')}   "
                                 f"({cfg.get('for', 'image')})")
            self.preset_list.addItem(it)

    def _add_preset(self):
        f = PresetForm()
        if f.exec():
            self._write(self._read() + [f.value()])

    def _edit_preset(self):
        row = self.preset_list.currentRow()
        items = self._read()
        if 0 <= row < len(items):
            f = PresetForm(items[row])
            if f.exec():
                items[row] = f.value()
                self._write(items)

    def _delete_preset(self):
        row = self.preset_list.currentRow()
        items = self._read()
        if 0 <= row < len(items):
            items.pop(row)
            self._write(items)

    # -------------------------------------------------------------- Watch
    def _watch(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(_help("Every new file in a watched folder is converted automatically."))
        self.watch_summary = QLabel()
        self.watch_summary.setWordWrap(True)
        v.addWidget(self.watch_summary)
        b = QPushButton("Edit watch folders…")
        b.setAutoDefault(False)
        b.clicked.connect(self._edit_watch)
        row = QHBoxLayout()
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        self._watch_text()
        return w

    def _watch_text(self):
        rules = self.settings.get("watch") or []
        if not rules:
            self.watch_summary.setText("No folders are being watched.")
            return
        lines = []
        for r in rules:
            a = engine.BY_ID.get(r["action"])
            lines.append(f"• {r['folder']}  →  {a.label if a else r['action']}")
        self.watch_summary.setText("\n".join(lines))

    def _edit_watch(self):
        self.ctrl.edit_watch()
        self._watch_text()

    # -------------------------------------------------------------- AI
    def _ai(self) -> QWidget:
        import aipack
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(_help("Background removal, AI upscaling and speech-to-text use local AI models, so your files "
                          "never leave this PC. The libraries for them (the “AI pack”, about 80 MB) "
                          "download the first time you use one of these tools. Models download once too: "
                          "cut-out ~170 MB, speech ~460 MB."))
        self.ai_status = QLabel()
        v.addWidget(self.ai_status)
        row = QHBoxLayout()
        self.ai_btn = QPushButton("Download AI pack now")
        self.ai_btn.clicked.connect(self._get_ai)
        row.addWidget(self.ai_btn)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        ok = aipack.installed()
        self.ai_status.setText("✓ AI pack installed." if ok else "AI pack not installed yet.")
        self.ai_btn.setVisible(not ok)
        return w

    def _get_ai(self):
        act = engine.BY_ID.get("ai-install")
        if act:
            self.ctrl.jobs.add(act, [])
            self.ai_status.setText("Downloading - see the progress card in the corner.")
            self.ai_btn.setEnabled(False)

    # -------------------------------------------------------------- About
    def _about(self) -> QWidget:
        from app import __version__
        w = QWidget()
        v = QVBoxLayout(w)
        title = QLabel(f"<b style='font-size:15px'>Satsuma {__version__}</b>")
        v.addWidget(title)
        v.addWidget(_help("Offline file converter for Windows. Open source (AGPL-3.0)."))
        links = QLabel("<a style='color:#FF8A1F' href='https://github.com/kalharakeh/satsuma'>GitHub</a> &nbsp;·&nbsp; "
                       "<a style='color:#FF8A1F' href='https://github.com/kalharakeh/satsuma/issues'>Report a problem</a> &nbsp;·&nbsp; "
                       "<a style='color:#FF8A1F' href='https://github.com/kalharakeh/satsuma/releases'>Releases</a>")
        links.setOpenExternalLinks(True)
        v.addWidget(links)
        self.update_label = QLabel("", objectName="help")
        v.addWidget(self.update_label)
        row = QHBoxLayout()
        b = QPushButton("Check for updates")
        b.setAutoDefault(False)
        b.clicked.connect(self._check)
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _check(self):
        self.update_label.setText("Checking…")
        up = self.ctrl.updater
        up.failed.connect(self.update_label.setText)
        up.available.connect(lambda ver, _n: self.update_label.setText(
            f"Version {ver} is available - use the tray menu or the notification to install it."))
        up.check(manual=True)
