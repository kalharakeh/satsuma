"""Satsuma - offline file converter for Windows.

Hold Shift while dragging any file from Explorer or the desktop: a panel of
formats appears at the cursor. Drop on a tile and the converted copy is saved
next to the original. Also available from the right-click menu and Send To.

    app.py               start in the tray
    app.py --open FILES  show the panel for these files (right-click / Send To)
    app.py --install     register right-click menu, Send To, Start menu, autostart
    app.py --uninstall   remove all of that
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Conversion workers re-launch this script with --run; keep that path free of Qt.
if __name__ == "__main__" and sys.argv[1:2] == ["--run"]:
    import engine
    sys.exit(engine.run_cli(sys.argv[2], sys.argv[3:]))
if __name__ == "__main__" and sys.argv[1:2] == ["--faces"]:  # OpenCV stays out of the UI process
    import json as _json
    import engine  # noqa: F401
    import tools2
    out = sys.stdout if sys.stdout is not None else engine._std_handle_writer()
    out.write(_json.dumps(tools2.detect_faces(engine.Path(sys.argv[2]))) + "\n")
    out.flush()
    sys.exit(0)

import getpass
import weakref
import json
import subprocess
import time
from datetime import datetime

from PySide6.QtCore import (QCoreApplication, QObject, QPoint, QPointF, QProcess, QRect, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QCursor, QFontMetrics, QGuiApplication, QIcon, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGraphicsDropShadowEffect,
                               QGridLayout, QHBoxLayout, QLabel, QMenu, QProgressBar,
                               QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)

try:
    from shiboken6 import isValid
except ImportError:  # pragma: no cover
    def isValid(obj):
        return obj is not None

import engine
import hook
import integrate
import presets
from dialogs import ParamDialog, WatchDialog
from watch import WatchManager

IS_WIN = os.name == "nt"
APP_NAME = "Satsuma"
try:
    from version import __version__
except ImportError:
    __version__ = "dev"
SERVER = f"satsuma-{getpass.getuser()}"
DATA = Path(os.environ.get("APPDATA") or Path.home() / ".config") / APP_NAME
LOG = DATA / "satsuma.log"
MAX_PARALLEL = 2
COLS = 4
TILE_W, TILE_H, GAP = 76, 56, 6
ACCENT = "#FF8A1F"


# --------------------------------------------------------------------- basics

def log(msg: str):
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 1_000_000:
            LOG.replace(LOG.with_suffix(".old.log"))
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg.rstrip()}\n")
    except OSError:
        pass


class Settings:
    DEFAULTS = {"shift_drag": True, "explorer_only": True, "text_drag": True, "welcomed": False}

    def __init__(self):
        self.path = DATA / "settings.json"
        self.data = dict(self.DEFAULTS)
        try:
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def get(self, key):
        return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError as e:
            log(f"settings not saved: {e}")


def asset(name: str) -> str:
    return str(integrate.assets_dir() / name)


def reveal(path: str | Path):
    p = Path(path)
    if IS_WIN:
        subprocess.Popen(f'explorer /select,"{p}"')
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])


def summary(files: list[Path]) -> str:
    if not files:
        return "AI pack"
    if len(files) == 1:
        return files[0].name
    kinds = {engine.kind_of(f) for f in files}
    noun = {"image": "images", "video": "videos", "audio": "audio files", "pdf": "PDFs"}.get(
        next(iter(kinds)) if len(kinds) == 1 else "", "items")
    return f"{len(files)} {noun}"


QSS = f"""
#card {{ background: rgba(28,28,30,246); border: 1px solid rgba(255,255,255,26); border-radius: 16px; }}
QLabel {{ color: #F2F2F7; font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 12px; }}
#brand {{ font-size: 13px; font-weight: 600; }}
#sub, #status, #section, #tileHint, #empty {{ color: #8E8E93; }}
#section {{ font-size: 10px; font-weight: 700; }}
#dot {{ background: {ACCENT}; border-radius: 5px; }}
#tile {{ background: rgba(255,255,255,18); border-radius: 10px; }}
#tile[hot="true"] {{ background: {ACCENT}; }}
#tile[hot="true"] QLabel {{ color: #1A1A1A; }}
#tileTitle {{ font-size: 14px; font-weight: 600; }}
#tileHint {{ font-size: 10px; }}
#empty {{ padding: 14px 2px; }}
#job {{ background: rgba(255,255,255,12); border-radius: 10px; }}
#jobAction {{ color: {ACCENT}; font-weight: 600; }}
#status[state="error"] {{ color: #FF6B60; }}
#status a {{ color: {ACCENT}; }}
QProgressBar {{ background: rgba(255,255,255,26); border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
QToolButton {{ color: #8E8E93; border: none; font-size: 14px; padding: 0 4px; }}
QToolButton:hover {{ color: #F2F2F7; }}
QMenu {{ background: #1C1C1E; border: 1px solid #3A3A3C; border-radius: 8px; padding: 5px; }}
QMenu::item {{ color: #F2F2F7; padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #1A1A1A; }}
QMenu::item:disabled {{ color: #8E8E93; }}
QMenu::separator {{ height: 1px; background: #3A3A3C; margin: 4px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 4px; }}
QToolTip {{ background: #2C2C2E; color: #F2F2F7; border: 1px solid #3A3A3C; padding: 4px; }}
"""


def card(parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
    """The rounded dark card with a soft shadow that both windows are made of."""
    outer = QVBoxLayout(parent)
    outer.setContentsMargins(18, 18, 18, 18)
    c = QFrame(objectName="card")
    shadow = QGraphicsDropShadowEffect(c)
    shadow.setBlurRadius(30)
    shadow.setOffset(QPointF(0, 6))
    shadow.setColor(QColor(0, 0, 0, 150))
    c.setGraphicsEffect(shadow)
    outer.addWidget(c)
    v = QVBoxLayout(c)
    v.setContentsMargins(12, 10, 12, 12)
    v.setSpacing(8)
    return c, v


def header(text: str) -> tuple[QHBoxLayout, QLabel]:
    h = QHBoxLayout()
    h.setSpacing(7)
    dot = QLabel(objectName="dot")
    dot.setFixedSize(10, 10)
    brand = QLabel(text, objectName="brand")
    sub = QLabel("", objectName="sub")
    h.addWidget(dot)
    h.addWidget(brand)
    h.addWidget(sub, 1)
    return h, sub


def set_elided(label: QLabel, text: str, width: int):
    label.setText(QFontMetrics(label.font()).elidedText(text, Qt.ElideMiddle, width))
    label.setToolTip(text)


# ---------------------------------------------------------------------- panel

class Tile(QFrame):
    def __init__(self, panel: "Panel", action, title: str, hint: str):
        super().__init__(objectName="tile")
        self._panel = weakref.ref(panel)  # no reference cycle: Qt and Python must not both free the panel
        self.action = action
        self.setFixedSize(TILE_W, TILE_H)
        self.setProperty("hot", False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 6, 3, 6)
        lay.setSpacing(0)
        for text, name in ((title, "tileTitle"), (hint, "tileHint")):
            if text:
                lab = QLabel(text, objectName=name, alignment=Qt.AlignCenter)
                lab.setAttribute(Qt.WA_TransparentForMouseEvents)
                lay.addWidget(lab)
        self.setToolTip(action.label if action else "Every option for these files")
        self.setCursor(Qt.PointingHandCursor)

    @property
    def panel(self) -> "Panel":
        return self._panel()

    def set_hot(self, on: bool):
        if self.property("hot") == on:
            return
        self.setProperty("hot", on)
        for w in (self, *self.findChildren(QLabel)):
            w.style().unpolish(w)
            w.style().polish(w)

    def enterEvent(self, e):
        p = self.panel
        if p is not None and not p.drag_mode:
            p.set_hot(self)

    def leaveEvent(self, e):
        p = self.panel
        if p is not None and not p.drag_mode:
            p.set_hot(None)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.panel is not None:
            self.panel.activate(self)


class Panel(QWidget):
    """Format picker. Drag mode: appears under a Shift-drag and is a drop target.
    Click mode: a popup for files that came from the right-click menu."""

    WIDTH = COLS * TILE_W + (COLS - 1) * GAP + 24

    def __init__(self, ctrl: "Controller", drag_mode: bool, files: list[Path] | None = None):
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        flags |= (Qt.Tool | Qt.WindowDoesNotAcceptFocus) if drag_mode else Qt.Popup
        super().__init__(None, flags)
        self.ctrl, self.drag_mode = ctrl, drag_mode
        self.files: list[Path] = []
        self._hot: Tile | None = None
        self._dropped = False
        self.body: QWidget | None = None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, drag_mode)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAcceptDrops(drag_mode)  # must be set before the native window exists
        self.card, self.v = card(self)
        self.card.setFixedWidth(self.WIDTH)
        h, self.sub = header(APP_NAME)
        self.v.addLayout(h)
        if files:
            self.set_files(files)
        else:
            self._set_body(self._empty("Drop on a format"))
            self.sub.setText("")

    # -- content
    def _empty(self, text: str) -> QWidget:
        return QLabel(text, objectName="empty", alignment=Qt.AlignCenter)

    def _set_body(self, w: QWidget):
        if self.body is not None:
            self.v.removeWidget(self.body)
            self.body.deleteLater()
        self.body = w
        self.v.addWidget(w)
        self.adjustSize()
        self._fit_screen()

    def set_files(self, files: list[Path]):
        self.files = files
        self._hot = None
        set_elided(self.sub, summary(files), self.WIDTH - 110)
        acts = engine.applicable(files)
        if not acts:
            self._set_body(self._empty("Nothing to convert this to"))
            return
        groups = [("PRESETS", [a for a in acts if a.group == "Presets"], COLS),
                  ("CONVERT TO", [a for a in acts if a.group == "Convert"], 2 * COLS),
                  ("TOOLS", [a for a in acts if a.group == "Tools"], 2 * COLS)]
        groups = [g for g in groups if g[1]]
        overflow = any(len(items) > cap for _, items, cap in groups)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        for i, (name, items, cap) in enumerate(groups):
            last = i == len(groups) - 1
            shown = items[: cap - 1] if (overflow and last and len(items) >= cap) else items[:cap]
            bl.addWidget(QLabel(name, objectName="section"))
            grid = QGridLayout()
            grid.setSpacing(GAP)
            for c in range(COLS):  # keep 4 columns even when a row has fewer tiles
                grid.setColumnMinimumWidth(c, TILE_W)
            tiles = [Tile(self, a, a.tile, a.hint) for a in shown]
            if overflow and last:
                tiles.append(Tile(self, None, "\u2022\u2022\u2022", "all options"))
            for n, t in enumerate(tiles):
                grid.addWidget(t, n // COLS, n % COLS)
            bl.addLayout(grid)
        self._set_body(body)

    # -- placement
    def show_at(self, pos: QPoint):
        self.adjustSize()
        # cursor lands on the header strip, never on a tile
        self.move(pos.x() - 18 - 12 - 46, pos.y() - 18 - 10 - 9)
        self._fit_screen(pos)

    def _fit_screen(self, pos: QPoint | None = None):
        scr = QGuiApplication.screenAt(pos or self.geometry().center()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        r = QRect(self.pos(), self.sizeHint().expandedTo(self.size()))
        x = min(max(r.x(), g.left() - 18), g.right() - r.width() + 18)
        y = min(max(r.y(), g.top() - 18), g.bottom() - r.height() + 18)
        self.move(x, y)

    # -- interaction
    def set_hot(self, tile: Tile | None):
        if tile is self._hot:
            return
        if self._hot is not None and isValid(self._hot):
            self._hot.set_hot(False)
        self._hot = tile
        if tile is not None:
            tile.set_hot(True)

    def _tile_at(self, pos: QPoint) -> Tile | None:
        w = self.childAt(pos)
        while w is not None and not isinstance(w, Tile):
            w = w.parentWidget()
        return w

    def activate(self, tile: Tile | None):
        files, ctrl = list(self.files), self.ctrl
        self.close()
        if not files:
            return
        if tile is None or tile.action is None:
            ctrl.show_menu(files)
        else:
            ctrl.run(tile.action, files)

    def maybe_close(self):
        if not self._dropped:
            self.close()

    # Explorer offers "move" during a Shift-drag. We always answer "copy",
    # so the source file is never moved or deleted.
    def dragEnterEvent(self, e):
        md = e.mimeData()
        files = [Path(u.toLocalFile()) for u in md.urls() if u.isLocalFile()] if md.hasUrls() else []
        if not files:
            e.ignore()
            return
        if files != self.files:
            self.set_files(files)
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dragMoveEvent(self, e):
        self.set_hot(self._tile_at(e.position().toPoint()))
        e.setDropAction(Qt.CopyAction)
        e.accept()

    def dragLeaveEvent(self, e):
        self.set_hot(None)

    def dropEvent(self, e):
        tile = self._tile_at(e.position().toPoint())
        e.setDropAction(Qt.CopyAction)
        e.accept()
        self._dropped = True
        # return to Explorer first; it is blocked until the drop handler finishes
        QTimer.singleShot(0, lambda: self.activate(tile))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


# ----------------------------------------------------------------------- jobs

def undo_record(rec: dict | None) -> bool:
    """Recycle-bin the outputs of a job, or put renamed files back. True on success."""
    if not rec:
        return False
    try:
        if "rename" in rec:
            for new, old in reversed(rec["rename"]):
                if Path(new).exists() and not Path(old).exists():
                    os.replace(new, old)
        else:
            from send2trash import send2trash
            for out in rec.get("trash", []):
                if Path(out).exists():
                    send2trash(str(out))
        return True
    except Exception as e:
        log(f"undo failed: {e}")
        return False


class JobRow(QFrame):
    finished = Signal(object)

    def __init__(self, action, files: list[Path], params: dict | None = None):
        super().__init__(objectName="job")
        self.action, self.files, self.params = action, files, params or {}
        self.undo: dict | None = None
        self.undone = False
        self.outputs: list[str] = []
        self.error = ""
        self.ok = False
        self._buf = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(5)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel()
        set_elided(name, summary(files), 190)
        act = QLabel(f"→ {action.tile}", objectName="jobAction")
        act.setToolTip(action.label)
        top.addWidget(name, 1)
        top.addWidget(act)
        v.addLayout(top)
        self.bar = QProgressBar(textVisible=False)
        self.bar.setFixedHeight(4)
        self.bar.setRange(0, 0)
        v.addWidget(self.bar)
        self.status = QLabel("Waiting…", objectName="status", wordWrap=True)
        self.status.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.status.linkActivated.connect(self._link)
        v.addWidget(self.status)
        self.proc: QProcess | None = None

    def start(self):
        self.status.setText("Working…")
        exe, pre = integrate.launcher()
        self.proc = QProcess(self)
        self.proc.setProgram(exe)
        self.proc.setArguments([*pre, "--run", self.action.id, json.dumps(self.params), *map(str, self.files)])
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        self.proc.errorOccurred.connect(self._crashed)
        self.proc.start()

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            self._line(line.strip())

    def _line(self, line: str):
        tag, _, rest = line.partition(" ")
        if tag == "P":
            try:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(float(rest) * 1000))
            except ValueError:
                pass
        elif tag == "O":
            self.outputs.append(rest)
        elif tag == "E":
            self.error = rest
        elif tag == "U":
            try:
                self.undo = json.loads(rest)
            except ValueError:
                pass

    def _crashed(self, err):
        if err == QProcess.FailedToStart:
            self._finish(False, "Couldn't start the converter.")

    def _done(self, code, status):
        self._buf += "\n"
        self._read()
        err = bytes(self.proc.readAllStandardError()).decode("utf-8", "replace").strip()
        if err:
            log(f"{self.action.id} {self.files}: {err}")
        ok = code == 0 and bool(self.outputs) and not self.error
        self._finish(ok, self.error or (f"Stopped unexpectedly (code {code})." if not ok else ""))

    def _finish(self, ok: bool, error: str):
        if self.ok or self.status.property("state"):
            return
        self.ok = ok
        self.bar.hide()
        if ok:
            what = Path(self.outputs[0]).name if len(self.outputs) == 1 else f"{len(self.outputs)} files"
            verb = "Renamed" if self.undo and "rename" in self.undo else "Saved"
            link = f"style='color:{ACCENT};text-decoration:none'"
            self.status.setText(f"{verb} {what} · <a href='show' {link}>Show</a>"
                                f" · <a href='undo' {link}>Undo</a>")
            self.status.setProperty("state", "done")
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.status.setText(error)
            self.status.setProperty("state", "error")
            log(f"FAILED {self.action.id} {self.files}: {error}")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.finished.emit(self)

    def undo_record(self) -> dict | None:
        if not self.ok or self.undone:
            return None
        return self.undo or {"trash": self.outputs}

    def _link(self, href: str):
        if href == "undo":
            if undo_record(self.undo_record()):
                self.mark_undone()
        elif self.outputs:
            reveal(self.outputs[0])

    def mark_undone(self):
        self.undone = True
        self.status.setText("Undone")
        self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self.ok and self.outputs and not self.undone:
            pos = self.status.mapFrom(self, e.position().toPoint())
            if not self.status.rect().contains(pos):
                reveal(self.outputs[0])


class JobsWindow(QWidget):
    """Small stack of progress cards in the bottom-right corner."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.card, self.v = card(self)
        self.card.setFixedWidth(320)
        h, self.sub = header(APP_NAME)
        close = QToolButton(text="✕")
        close.clicked.connect(self._dismiss)
        h.addWidget(close)
        self.v.addLayout(h)
        self.rows: list[JobRow] = []
        self.queue: list[JobRow] = []
        self.running = 0
        self.hide_timer = QTimer(self, singleShot=True, timeout=self._auto_hide)

    finished = Signal(object)

    def add(self, action, files: list[Path], params: dict | None = None):
        row = JobRow(action, files, params)
        row.finished.connect(self._row_done)
        self.rows.append(row)
        self.queue.append(row)
        self.v.addWidget(row)
        done = [r for r in self.rows if r.status.property("state")]
        while len(self.rows) > 5 and done:
            self._remove(done.pop(0))
        self.hide_timer.stop()
        self._pump()
        self._update_sub()
        self.show()
        self._place()

    def _remove(self, row: JobRow):
        self.rows.remove(row)
        self.v.removeWidget(row)
        row.deleteLater()

    def _pump(self):
        while self.running < MAX_PARALLEL and self.queue:
            self.running += 1
            self.queue.pop(0).start()

    def last_undoable(self) -> "JobRow | None":
        for r in reversed(self.rows):
            if r.undo_record():
                return r
        return None

    def _row_done(self, row: JobRow):
        self.finished.emit(row)
        self.running -= 1
        self._pump()
        self._update_sub()
        self._place()
        if not self.running and not self.queue:
            failed = any(r.status.property("state") == "error" for r in self.rows)
            self.hide_timer.start(10000 if failed else 5000)

    def _update_sub(self):
        busy = self.running + len(self.queue)
        self.sub.setText(f"{busy} working" if busy else "All done")

    def _auto_hide(self):
        if self.underMouse():
            self.hide_timer.start(2000)
            return
        self._dismiss()

    def _dismiss(self):
        for r in [r for r in self.rows if r.status.property("state")]:
            self._remove(r)
        if not self.rows:
            self.hide()
        else:
            self._place()

    def _place(self):
        QTimer.singleShot(0, self._do_place)

    def _do_place(self):
        self.adjustSize()
        scr = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(g.right() - self.width() + 8, g.bottom() - self.height() + 8)


# ----------------------------------------------------------------- text panel

TEXT_TOOLS = [("upper", "UPPER", "in place"), ("lower", "lower", "in place"), ("title", "Title", "Case Words"),
              ("clean", "Clean", "fix spacing"), ("translate", "Translate", "in browser"), ("qr", "QR", "code"),
              ("save", "Save", ".txt file"), ("count", "Count", "words")]


class TextTile(QFrame):
    def __init__(self, key: str, title: str, hint: str):
        super().__init__(objectName="tile")
        self.key = key
        self.setFixedSize(TILE_W, TILE_H)
        self.setProperty("hot", False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 6, 3, 6)
        lay.setSpacing(0)
        for text, name in ((title, "tileTitle"), (hint, "tileHint")):
            lab = QLabel(text, objectName=name, alignment=Qt.AlignCenter)
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(lab)

    def set_hot(self, on: bool):
        if self.property("hot") != on:
            self.setProperty("hot", on)
            for w in (self, *self.findChildren(QLabel)):
                w.style().unpolish(w)
                w.style().polish(w)


class TextPanel(QWidget):
    """Shown while Shift-dragging text. Drop on a tile to transform the text; drop anywhere else to paste it."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.card, v = card(self)
        self.card.setFixedWidth(COLS * TILE_W + (COLS - 1) * GAP + 24)
        h, sub = header(APP_NAME)
        sub.setText("drop on a tool, or anywhere to paste")
        v.addLayout(h)
        grid = QGridLayout()
        grid.setSpacing(GAP)
        self.tiles = [TextTile(*t) for t in TEXT_TOOLS]
        for n, t in enumerate(self.tiles):
            grid.addWidget(t, n // COLS, n % COLS)
        v.addLayout(grid)
        self.timer = QTimer(self, interval=30, timeout=self._hover)
        self.hot = None

    def show_near(self, pos: QPoint):
        self.adjustSize()
        scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        x = pos.x() + 30
        y = pos.y() - self.height() - 10  # above the text, so it doesn't cover where you're heading
        if y < g.top():
            y = pos.y() + 30
        x = min(max(x, g.left()), g.right() - self.width())
        self.move(x, y)
        self.show()
        self.timer.start()

    def tile_at(self, gpos: QPoint) -> "TextTile | None":
        for t in self.tiles:
            if t.rect().contains(t.mapFromGlobal(gpos)):
                return t
        return None

    def _hover(self):
        t = self.tile_at(QCursor.pos())
        if t is not self.hot:
            if self.hot:
                self.hot.set_hot(False)
            self.hot = t
            if t:
                t.set_hot(True)

    def hit(self, gpos: QPoint) -> str | None:
        t = self.tile_at(gpos)
        return t.key if t else None

    def closeEvent(self, e):
        self.timer.stop()
        super().closeEvent(e)


# ----------------------------------------------------------------- controller

class Controller(QObject):
    def __init__(self, app: QApplication, first_run: bool):
        super().__init__()
        self.app = app
        self.settings = Settings()
        self.icon = QIcon(asset("icon.png"))
        app.setWindowIcon(self.icon)
        self.panel: Panel | None = None
        for problem in presets.load_presets(force=True):
            log(problem)
        self.jobs = JobsWindow()
        self.watch = WatchManager(self)
        self.jobs.finished.connect(self.watch.note_outputs)
        self._pending: list[str] = []
        self._pending_timer = QTimer(self, singleShot=True, interval=350, timeout=self._flush_open)

        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)
        QLocalServer.removeServer(SERVER)
        if not self.server.listen(SERVER):
            log(f"IPC listen failed: {self.server.errorString()}")

        self._build_tray()
        self.watcher = hook.DragWatcher(self.settings)
        self.watcher.started.connect(self.on_drag_start)
        self.watcher.released.connect(self.on_drag_end)
        err = self.watcher.start()
        if err:
            log(f"Shift-drag disabled: {err}")
        elif self.watcher.text is not None:
            from textdrag import TextDrag
            self.textdrag = TextDrag(self.watcher.text, log, panel_factory=self._text_panel,
                                     on_action=self.text_action)
        self.watch.start()
        import updater
        self.updater = updater.Updater(self.settings, __version__, log)
        self.updater.available.connect(self._update_available)
        self.updater.failed.connect(lambda msg: self.tray.showMessage("Satsuma", msg, self.icon, 5000))
        self.updater.progress.connect(self._update_progress)
        self.updater.start()
        self.tray.messageClicked.connect(self._message_clicked)
        self._last_message = None
        if first_run or not self.settings.get("welcomed"):
            self.settings.set("welcomed", True)
            QTimer.singleShot(800, lambda: self.tray.showMessage(
                "Satsuma is ready", "Hold Shift while dragging a file to convert it. "
                "Or right-click a file → Convert with Satsuma.", self.icon, 8000))

    # -- tray
    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("Satsuma — Shift-drag a file to convert it")
        m = QMenu()
        head = m.addAction(f"{APP_NAME} {__version__}")
        head.setEnabled(False)
        m.addSeparator()
        m.addAction("Convert files…").triggered.connect(self.pick_files)
        self.undo_action = m.addAction("Undo last conversion")
        self.undo_action.triggered.connect(self.undo_last)
        m.aboutToShow.connect(lambda: self.undo_action.setEnabled(self.jobs.last_undoable() is not None))
        m.addSeparator()
        m.addAction("Settings…").triggered.connect(self.open_settings)
        self.update_action = m.addAction("Update available")
        self.update_action.setVisible(False)
        self.update_action.triggered.connect(self.install_update)
        m.addSeparator()
        m.addAction("Open log").triggered.connect(self._open_log)
        m.addAction("Quit").triggered.connect(self.quit)
        self._menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(
            lambda r: self.pick_files() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    @staticmethod
    def _toggle(menu: QMenu, text: str, checked: bool, fn):
        a = menu.addAction(text)
        a.setCheckable(True)
        a.setChecked(bool(checked))

        def changed(on):
            try:
                fn(on)
            except Exception as e:
                log(f"{text}: {e}")
        a.toggled.connect(changed)
        return a

    # -- settings, updates
    def open_settings(self):
        from settingswin import SettingsWindow
        if getattr(self, "_settings_win", None) and isValid(self._settings_win):
            self._settings_win.raise_()
            self._settings_win.activateWindow()
            return
        self._settings_win = SettingsWindow(self)
        self._settings_win.setAttribute(Qt.WA_DeleteOnClose)
        self._settings_win.show()

    def _update_available(self, version: str, notes: str):
        self.update_action.setText(f"Update to {version}")
        self.update_action.setVisible(True)
        self._last_message = "update"
        self.tray.showMessage(f"Satsuma {version} is available", "Click to update - it takes a minute.",
                              self.icon, 15000)

    def _message_clicked(self):
        if self._last_message == "update":
            self.install_update()

    def install_update(self):
        self._last_message = None
        self.tray.showMessage("Updating Satsuma", "Downloading the new version…", self.icon, 4000)
        self.updater.install()

    def _update_progress(self, pct: int):
        self.update_action.setText(f"Downloading update… {pct}%")
        if pct >= 100:
            QTimer.singleShot(1500, self.quit)  # the installer takes over and restarts Satsuma

    # -- text drag panel
    def _text_panel(self):
        if self.settings.get("text_panel") is False:
            return None
        p = TextPanel()
        p.show_near(QCursor.pos())
        return p

    def text_action(self, action: str, text: str):
        import webbrowser
        from urllib.parse import quote
        if action == "translate":
            from PySide6.QtCore import QLocale
            tl = self.settings.get("translate_to") or QLocale.system().name().split("_")[0]
            webbrowser.open(f"https://translate.google.com/?sl=auto&tl={tl}&text={quote(text[:4800])}&op=translate")
        elif action == "qr":
            from phone import show_qr
            show_qr(text, "QR code")
        elif action == "save":
            from datetime import datetime
            default = Path.home() / "Desktop" / f"Text {datetime.now():%Y-%m-%d %H%M}.txt"
            f, _ = QFileDialog.getSaveFileName(None, "Save text", str(default), "Text (*.txt);;Markdown (*.md)")
            if f:
                Path(f).write_text(text, encoding="utf-8")
                self.tray.showMessage("Saved", Path(f).name, self.icon, 3000)
        elif action == "count":
            words = len(text.split())
            lines = text.count("\n") + 1
            self.tray.showMessage("Text count", f"{words:,} words · {len(text):,} characters · "
                                  f"{lines:,} lines", self.icon, 6000)
        elif action == "search":
            webbrowser.open(f"https://www.google.com/search?q={quote(text[:500])}")

    def undo_last(self):
        row = self.jobs.last_undoable()
        if row and undo_record(row.undo_record()):
            row.mark_undone()
            self.tray.showMessage("Undone", f"{row.action.label}: {summary(row.files)}", self.icon, 3000)

    def edit_presets(self):
        p = presets.ensure_presets_file()
        if not hasattr(self, "_preset_watch"):
            from PySide6.QtCore import QFileSystemWatcher
            self._preset_watch = QFileSystemWatcher([str(p)], self)
            self._preset_watch.fileChanged.connect(self._reload_presets)
        presets.load_presets(force=True)
        if IS_WIN:
            subprocess.Popen(["notepad.exe", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])

    def _reload_presets(self, path: str):
        if path not in self._preset_watch.files() and Path(path).exists():
            self._preset_watch.addPath(path)  # editors replace the file on save
        problems = presets.load_presets(force=True)
        if problems:
            self.tray.showMessage("Preset problem", problems[0], self.icon, 6000)
            for pr in problems:
                log(pr)

    def edit_watch(self):
        WatchDialog(self).exec()
        self.watch.reload()

    def _open_log(self):
        log("log opened")
        if IS_WIN:
            os.startfile(LOG)
        else:
            subprocess.Popen(["xdg-open", str(LOG)])

    def quit(self):
        self.watcher.stop()
        self.tray.hide()
        self.app.quit()

    # -- IPC from right-click / Send To launches
    def _on_connection(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))
                while b"\n" in buf:
                    line, _, rest = bytes(buf).partition(b"\n")
                    buf[:] = rest
                    try:
                        self._message(json.loads(line))
                    except ValueError:
                        pass
            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)

    def _message(self, msg: dict):
        if msg.get("open"):
            self._pending += msg["open"]
            self._pending_timer.start()
        elif msg.get("quit"):
            self.quit()
        elif msg.get("show"):
            self.tray.showMessage("Satsuma is running", "Hold Shift while dragging a file to convert it.",
                                  self.icon, 4000)

    def queue_open(self, files: list[str]):
        self._message({"open": files})

    def _flush_open(self):
        files = list(dict.fromkeys(self._pending))
        self._pending.clear()
        self.open_panel([Path(f) for f in files if Path(f).exists()])

    # -- panel
    def _close_panel(self):
        if self.panel is not None and isValid(self.panel):
            self.panel.close()
        self.panel = None

    def on_drag_start(self):
        self._close_panel()
        self.panel = Panel(self, drag_mode=True)
        self.panel.show_at(QCursor.pos())
        self.panel.show()

    def on_drag_end(self):
        p = self.panel
        if p is not None and isValid(p) and p.drag_mode:
            QTimer.singleShot(300, lambda: isValid(p) and p.maybe_close())

    def open_panel(self, files: list[Path], pos: QPoint | None = None):
        if not files:
            return
        self._close_panel()
        self.panel = Panel(self, drag_mode=False, files=files)
        self.panel.show_at(pos or QCursor.pos())
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def pick_files(self):
        names, _ = QFileDialog.getOpenFileNames(None, "Choose files to convert")
        if names:
            g = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()).availableGeometry()
            self.open_panel([Path(n) for n in names], QPoint(g.center().x(), g.center().y() - 150))

    def show_menu(self, files: list[Path]):
        m = QMenu()
        acts = engine.applicable(files)
        if not acts:
            m.addAction("Nothing to convert this to").setEnabled(False)
        for group, title in (("Presets", "Presets"), ("Convert", "Convert to"), ("Tools", "Tools")):
            items = [a for a in acts if a.group == group]
            if not items:
                continue
            if not m.isEmpty():
                m.addSeparator()
            m.addAction(title).setEnabled(False)
            for a in items:
                m.addAction(a.label).triggered.connect(lambda _=False, a=a: self.run(a, files))
        m.exec(QCursor.pos())

    def run(self, action, files: list[Path], params: dict | None = None):
        if action.ui == "phone":
            from phone import PhoneShare
            try:
                self._phone = PhoneShare(files)
                self._phone.setAttribute(Qt.WA_DeleteOnClose)
                self._phone.show()
                self._phone.raise_()
                self._phone.activateWindow()
            except Exception as e:
                log(f"send to phone: {e}")
                self.tray.showMessage("Send to phone", f"Couldn't start sharing ({e}).", self.icon, 5000)
            return
        if action.editor and params is None:
            from editors import EDITORS
            for f in (files if action.each else [files]):
                group = [f] if action.each else f
                try:
                    p = EDITORS[action.editor](action, group, self.settings)
                except Exception as e:
                    log(f"editor {action.editor}: {e}")
                    self.tray.showMessage("Satsuma", str(e), self.icon, 5000)
                    continue
                if p is not None:
                    self.jobs.add(action, group, p)
            return
        if action.params and params is None:
            dlg = ParamDialog(action, files, self.settings)
            if not dlg.exec():
                return
            params = dlg.values()
        if action.each:
            todo = [[f] for f in files if not (action.skip_ext and f.suffix.lower() in action.skip_ext)]
        else:
            todo = [files]
        for group in todo:
            self.jobs.add(action, group, params)


# ------------------------------------------------------------ single instance

_mutex = None


def acquire_primary() -> bool:
    global _mutex
    if IS_WIN:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        _mutex = k32.CreateMutexW(None, False, f"Local\\{SERVER}")
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    probe = QLocalSocket()
    probe.connectToServer(SERVER)
    alive = probe.waitForConnected(200)
    probe.abort()
    return not alive


def send_to_running(payload: dict) -> bool:
    s = QLocalSocket()
    s.connectToServer(SERVER)
    if not s.waitForConnected(500):
        return False
    s.write(json.dumps(payload).encode() + b"\n")
    s.waitForBytesWritten(1000)
    s.disconnectFromServer()
    if s.state() != QLocalSocket.UnconnectedState:
        s.waitForDisconnected(500)
    return True


def dark_palette(app: QApplication):
    app.setStyle("Fusion")
    p = QPalette()
    for role, color in ((QPalette.Window, "#1C1C1E"), (QPalette.WindowText, "#F2F2F7"),
                        (QPalette.Base, "#2C2C2E"), (QPalette.AlternateBase, "#242426"),
                        (QPalette.Text, "#F2F2F7"), (QPalette.Button, "#2C2C2E"),
                        (QPalette.ButtonText, "#F2F2F7"), (QPalette.Highlight, ACCENT),
                        (QPalette.HighlightedText, "#1A1A1A"), (QPalette.ToolTipBase, "#2C2C2E"),
                        (QPalette.ToolTipText, "#F2F2F7"), (QPalette.PlaceholderText, "#8E8E93")):
        p.setColor(role, QColor(color))
    app.setPalette(p)
    app.setStyleSheet(QSS)


def main() -> int:
    args = sys.argv[1:]
    mode = args[0] if args else "--tray"
    files = [os.path.abspath(a) for a in args[1:]] if mode == "--open" else []

    if mode in ("--uninstall", "--quit"):
        QCoreApplication(sys.argv[:1])
        if IS_WIN and mode == "--uninstall":
            integrate.uninstall()
        send_to_running({"quit": True})
        return 0

    if not acquire_primary():  # already running: hand the files over and leave
        QCoreApplication(sys.argv[:1])
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # let it put the panel in front
        payload = {"open": files} if files else {"show": True}
        for _ in range(40):
            if send_to_running(payload):
                return 0
            time.sleep(0.15)
        return 1

    if mode == "--install" and IS_WIN:
        try:
            integrate.install()
        except Exception as e:
            log(f"install failed: {e}")

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    dark_palette(app)
    ctrl = Controller(app, first_run=mode == "--install")
    if files:
        ctrl.queue_open(files)
    log(f"started ({mode})")
    code = app.exec()
    ctrl.watcher.stop()
    sys.stdout and sys.stdout.flush()
    os._exit(code)  # skip interpreter teardown: Qt objects destroyed in the wrong order can crash on exit


if __name__ == "__main__":
    sys.exit(main())
