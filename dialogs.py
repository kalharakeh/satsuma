"""Small dark dialogs: options for an action (trim times, password, rename pattern) and watch folders."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout)

import engine

DIALOG_QSS = """
QDialog { background: #1C1C1E; }
QLabel { color: #F2F2F7; font-size: 12px; }
QLabel#help, QLabel#preview { color: #8E8E93; font-size: 11px; }
QLabel#title { font-size: 15px; font-weight: 600; }
QLineEdit, QComboBox { background: #2C2C2E; color: #F2F2F7; border: 1px solid #3A3A3C; border-radius: 6px;
                       padding: 6px 8px; font-size: 13px; selection-background-color: #FF8A1F; }
QLineEdit:focus, QComboBox:focus { border-color: #FF8A1F; }
QCheckBox { color: #F2F2F7; font-size: 12px; }
QListWidget { background: #2C2C2E; color: #F2F2F7; border: 1px solid #3A3A3C; border-radius: 6px; font-size: 12px; }
QListWidget::item { padding: 6px; }
QListWidget::item:selected { background: #3A3A3C; }
QPushButton { background: #2C2C2E; color: #F2F2F7; border: 1px solid #3A3A3C; border-radius: 6px;
              padding: 6px 14px; font-size: 12px; }
QPushButton:hover { border-color: #FF8A1F; }
QPushButton:default { background: #FF8A1F; color: #1A1A1A; border: none; font-weight: 600; }
"""


def _base(dlg: QDialog, title: str, subtitle: str = "") -> QVBoxLayout:
    dlg.setWindowTitle(f"Satsuma - {title}")
    dlg.setStyleSheet(DIALOG_QSS)
    dlg.setWindowFlag(Qt.WindowStaysOnTopHint)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(18, 16, 18, 14)
    v.setSpacing(10)
    t = QLabel(title, objectName="title")
    v.addWidget(t)
    if subtitle:
        s = QLabel(subtitle, objectName="help")
        s.setWordWrap(True)
        v.addWidget(s)
    return v


class ParamDialog(QDialog):
    """Asks for an action's parameters. Remembers the last values (except passwords)."""

    def __init__(self, action, files: list[Path], settings):
        super().__init__()
        self.action, self.files, self.settings = action, files, settings
        names = files[0].name if len(files) == 1 else f"{len(files)} files"
        v = _base(self, action.label.rstrip("…."), names)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(8)
        saved = (settings.get("params") or {}).get(action.id, {})
        self.fields: dict[str, object] = {}
        for p in action.params:
            if p.type == "bool":
                w = QCheckBox(p.label)
                w.setChecked(bool(saved.get(p.key, p.default)))
                form.addRow("", w)
            elif p.type == "choice":
                w = QComboBox()
                w.setEditable(True)
                w.addItems([str(c) for c in p.choices])
                w.setCurrentText(str(saved.get(p.key, p.default) or ""))
                if p.help:
                    w.setToolTip(p.help)
                    w.lineEdit().setPlaceholderText(p.help)
                w.setMinimumWidth(260)
                form.addRow(p.label, w)
            else:
                w = QLineEdit()
                if p.type == "password":
                    w.setEchoMode(QLineEdit.Password)
                else:
                    w.setText(str(saved.get(p.key, p.default) or ""))
                if p.help:
                    w.setPlaceholderText(p.help)
                w.setMinimumWidth(260)
                form.addRow(p.label, w)
            self.fields[p.key] = w
        v.addLayout(form)
        self.preview = QLabel("", objectName="preview")
        self.preview.setWordWrap(True)
        v.addWidget(self.preview)
        if action.id in ("vid-trim", "aud-trim", "vid-gifclip"):
            self._show_length()
        if action.id == "rename":
            for w in self.fields.values():
                if isinstance(w, QLineEdit):
                    w.textChanged.connect(self._rename_preview)
            self._rename_preview()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Run")
        bb.button(QDialogButtonBox.Ok).setDefault(True)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        first = next((w for w in self.fields.values() if isinstance(w, (QLineEdit, QComboBox))), None)
        if first:
            first.setFocus()
            (first.lineEdit() if isinstance(first, QComboBox) else first).selectAll()

    def _show_length(self):
        try:
            secs = engine.media_duration(engine.ffmpeg_exe(), self.files[0])
        except Exception:
            secs = 0
        if secs:
            from features import fmt_time
            self.preview.setText(f"Length: {fmt_time(secs)}. The fast cut snaps to the nearest keyframe, "
                                 "so it can start a second or two early.")

    def _rename_preview(self):
        from features import rename_plan
        vals = self.values()
        try:
            plan = rename_plan(self.files, vals.get("pattern") or "{name}", int(vals.get("start") or 1))
            lines = [f"{a.name}  →  {b.name}" for a, b in plan[:4]]
            if len(plan) > 4:
                lines.append(f"… and {len(plan) - 4} more")
            self.preview.setText("\n".join(lines))
        except Exception as e:
            self.preview.setText(str(e))

    def values(self) -> dict:
        out = {}
        for k, w in self.fields.items():
            out[k] = w.isChecked() if isinstance(w, QCheckBox) else w.currentText() if isinstance(w, QComboBox) else w.text()
        return out

    def _accept(self):
        vals = self.values()
        keep = {p.key: vals[p.key] for p in self.action.params if p.type != "password"}
        allp = dict(self.settings.get("params") or {})
        allp[self.action.id] = keep
        self.settings.set("params", allp)
        self.accept()


def watchable_actions() -> list:
    return [a for a in engine.ACTIONS if a.each and not a.params]


class WatchDialog(QDialog):
    """Folders where every new file is converted automatically."""

    def __init__(self, ctrl):
        super().__init__()
        self.settings = ctrl.settings
        v = _base(self, "Watch folders",
                  "New files dropped into these folders are converted automatically. "
                  "The result is saved next to the file, as usual.")
        self.list = QListWidget()
        self.list.setMinimumSize(460, 160)
        v.addWidget(self.list)
        row = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Folder")
        browse = QPushButton("Browse…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        v.addLayout(row)
        row2 = QHBoxLayout()
        self.action = QComboBox()
        kind_names = {"image": "Images", "video": "Videos", "audio": "Audio", "pdf": "PDFs",
                      "word": "Documents", "slides": "Documents", "sheet": "Documents",
                      "csv": "CSV", "json": "JSON", "archive": "Archives"}
        for a in watchable_actions():
            k = sorted(a.kinds)[0] if len(a.kinds) < 4 else ""
            prefix = kind_names.get(k, "Any file")
            self.action.addItem(f"{prefix}: {a.label}", a.id)
        add = QPushButton("Add")
        add.setAutoDefault(False)
        add.clicked.connect(self._add)
        remove = QPushButton("Remove selected")
        remove.setAutoDefault(False)
        remove.clicked.connect(self._remove)
        row2.addWidget(self.action, 1)
        row2.addWidget(add)
        v.addLayout(row2)
        bb = QHBoxLayout()
        bb.addWidget(remove)
        bb.addStretch(1)
        done = QPushButton("Done")
        done.setAutoDefault(False)
        done.setDefault(True)
        done.clicked.connect(self.accept)
        bb.addWidget(done)
        v.addLayout(bb)
        self._refresh()

    def _rules(self) -> list[dict]:
        return list(self.settings.get("watch") or [])

    def _refresh(self):
        self.list.clear()
        for r in self._rules():
            a = engine.BY_ID.get(r["action"])
            it = QListWidgetItem(f"{r['folder']}   →   {a.label if a else r['action']}")
            self.list.addItem(it)
        if not self._rules():
            it = QListWidgetItem("No folders yet - pick one below.")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Folder to watch")
        if d:
            self.folder.setText(str(Path(d)))

    def _add(self):
        folder = self.folder.text().strip()
        if not folder or not Path(folder).is_dir():
            self.folder.setFocus()
            self.folder.setPlaceholderText("Pick an existing folder first")
            return
        rules = self._rules()
        rules.append({"folder": str(Path(folder)), "action": self.action.currentData()})
        self.settings.set("watch", rules)
        self.folder.clear()
        self._refresh()

    def _remove(self):
        rows = sorted({i.row() for i in self.list.selectedIndexes()}, reverse=True)
        rules = self._rules()
        for r in rows:
            if r < len(rules):
                rules.pop(r)
        self.settings.set("watch", rules)
        self._refresh()
