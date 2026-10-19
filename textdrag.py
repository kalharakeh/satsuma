"""Shift-drag selected text: drop a copy of it wherever you release the mouse.

How it works (Windows):
  1. Shift + left-press outside Explorer: the press is held back for a moment,
     so the app doesn't turn it into "extend selection".
  2. No movement -> the click is replayed untouched (Shift-click still works).
     Movement      -> Satsuma copies the selection (Ctrl+C) and a "Copy here"
     chip follows the cursor. Nothing selected -> the original press is
     replayed, so an ordinary Shift-drag still happens.
  3. Release -> click at that spot and paste (Ctrl+V). The clipboard you had
     before is put back afterwards. Right-click while dragging cancels.

Split in two: `TextDragFilter` runs inside the low-level mouse hook and only
decides (fast, no I/O); `TextDrag` runs on the Qt thread and does the work.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QMimeData, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFontMetrics, QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

import inject

WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_RBUTTONDOWN, WM_RBUTTONUP = 0x200, 0x201, 0x202, 0x204, 0x205
THRESHOLD_SQ = 10 * 10
PASS, SUPPRESS = "pass", "suppress"


class TextDragFilter(QObject):
    """State machine fed from the mouse hook thread. Emits signals to the Qt thread."""

    started = Signal(int, int)            # origin x, y
    dropped = Signal(int, int)            # release x, y
    clicked = Signal(int, int, int, int)  # origin, release: replay a plain Shift-click
    cancelled = Signal()

    IDLE, PENDING, DRAGGING, PASSTHRU, CANCELLED = range(5)

    def __init__(self, eligible):
        """eligible(x, y) -> bool: may we intercept a Shift-press here?"""
        super().__init__()
        self.eligible = eligible
        self.state = self.IDLE
        self.origin = (0, 0)

    def handle(self, msg: int, x: int, y: int, shift: bool) -> str:
        s = self.state
        if msg == WM_LBUTTONDOWN:
            if s == self.IDLE and shift and self.eligible(x, y):
                self.state, self.origin = self.PENDING, (x, y)
                return SUPPRESS
            return PASS
        if msg == WM_MOUSEMOVE:
            if s == self.PENDING:
                dx, dy = x - self.origin[0], y - self.origin[1]
                if dx * dx + dy * dy > THRESHOLD_SQ:
                    self.state = self.DRAGGING
                    self.started.emit(*self.origin)
            return PASS
        if msg == WM_LBUTTONUP:
            self.state = self.IDLE
            if s == self.PENDING:
                self.clicked.emit(*self.origin, x, y)
                return SUPPRESS
            if s == self.DRAGGING:
                self.dropped.emit(x, y)
                return SUPPRESS
            if s == self.CANCELLED:
                return SUPPRESS
            return PASS
        if msg in (WM_RBUTTONDOWN, WM_RBUTTONUP) and s in (self.DRAGGING, self.CANCELLED):
            if msg == WM_RBUTTONDOWN and s == self.DRAGGING:
                self.state = self.CANCELLED
                self.cancelled.emit()
            return SUPPRESS
        return PASS

    def passthrough(self):
        """Called from the Qt thread: stop intercepting; the user's own release goes to the app."""
        if self.state == self.DRAGGING:
            self.state = self.PASSTHRU


class Chip(QWidget):
    """Small label that follows the cursor while dragging text."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setStyleSheet("background: rgba(28,28,30,240); color: #F2F2F7; border: 1px solid #FF8A1F;"
                                 "border-radius: 8px; padding: 5px 9px; font-size: 12px;")
        lay.addWidget(self.label)
        self.timer = QTimer(self, interval=16, timeout=self._follow)

    def start(self, text: str):
        one = " ".join(text.split())
        fm = QFontMetrics(self.label.font())
        preview = fm.elidedText(one, Qt.ElideRight, 260)
        n = len(text)
        self.label.setText(f"<span style='color:#FF8A1F;font-weight:600'>Copy here</span> &nbsp;"
                           f"“{preview.replace('<', '&lt;')}”"
                           + (f" <span style='color:#8E8E93'>({n:,} chars)</span>" if n > 60 else ""))
        self.adjustSize()
        self._follow()
        self.show()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.hide()

    def _follow(self):
        p = QCursor.pos()
        self.move(p + QPoint(16, 20))


class TextDrag(QObject):
    """Qt-thread side: copy, show the chip, paste at the drop point, restore the clipboard."""

    COPY_WAIT_MS = 160

    def __init__(self, flt: TextDragFilter, log=lambda m: None, panel_factory=None, on_action=None):
        super().__init__()
        self.flt, self.log = flt, log
        self.panel_factory, self.on_action = panel_factory, on_action
        self.panel = None
        self.chip = Chip()
        self.saved = None
        self.text: str | None = None
        self.deciding = False
        self.aborted = False
        self.pending_drop: tuple[int, int] | None = None
        self.origin = (0, 0)
        flt.started.connect(self._started, Qt.QueuedConnection)
        flt.dropped.connect(self._dropped, Qt.QueuedConnection)
        flt.clicked.connect(self._replay_click, Qt.QueuedConnection)
        flt.cancelled.connect(self._cancel, Qt.QueuedConnection)

    # -- clipboard snapshot (text, html, image, files) so we can put it back
    def _snapshot(self):
        md = QGuiApplication.clipboard().mimeData()
        if md is None:
            return None
        snap = {}
        if md.hasText():
            snap["text"] = md.text()
        if md.hasHtml():
            snap["html"] = md.html()
        if md.hasUrls():
            snap["urls"] = list(md.urls())
        if md.hasImage():
            snap["image"] = QGuiApplication.clipboard().image()
        return snap

    def _restore(self):
        snap, self.saved = self.saved, None
        cb = QGuiApplication.clipboard()
        if not snap:
            cb.clear()
            return
        md = QMimeData()
        if "text" in snap:
            md.setText(snap["text"])
        if "html" in snap:
            md.setHtml(snap["html"])
        if "urls" in snap:
            md.setUrls(snap["urls"])
        if "image" in snap:
            md.setImageData(snap["image"])
        cb.setMimeData(md)

    # -- flow
    def _started(self, x: int, y: int):
        self.origin = (x, y)
        self.text, self.pending_drop, self.deciding, self.aborted = None, None, True, False
        self.saved = self._snapshot()
        seq = inject.clipboard_seq()
        inject.release_shift()  # so the app sees Ctrl+C, not Ctrl+Shift+C
        inject.copy()
        QTimer.singleShot(self.COPY_WAIT_MS, lambda: self._decide(seq))

    def _decide(self, seq: int):
        self.deciding = False
        if self.aborted:  # right-click cancelled while we were copying
            return
        text = QGuiApplication.clipboard().text() if inject.clipboard_seq() != seq else ""
        if text.strip():
            self.text = text
            if self.pending_drop:
                self._paste_at(*self.pending_drop)
            else:
                self.chip.start(text)
                if self.panel_factory:
                    try:
                        self.panel = self.panel_factory()
                    except Exception as e:
                        self.log(f"text panel: {e}")
            return
        # nothing selected: behave like an ordinary Shift-drag from the original point
        if inject.clipboard_seq() != seq:
            self._restore()
        else:
            self.saved = None
        inject.press_shift()
        if self.pending_drop:
            inject.left_down(*self.origin)
            inject.left_up(*self.pending_drop)
        else:
            here = QCursor.pos()
            self.flt.passthrough()
            inject.left_down(*self.origin)
            inject.move(here.x(), here.y())

    def _dropped(self, x: int, y: int):
        if self.deciding:
            self.pending_drop = (x, y)
        elif self.text is not None:
            key = self.panel.hit(QCursor.pos()) if self.panel is not None else None
            if key:
                self._tool(key)
            else:
                self._paste_at(x, y)

    def _close_panel(self):
        if self.panel is not None:
            try:
                self.panel.close()
            except RuntimeError:
                pass
            self.panel = None

    def _tool(self, key: str):
        text, self.text = self.text, None
        self.chip.stop()
        self._close_panel()
        if key in TRANSFORMS:
            # the selection is still there in the source app (we never clicked): paste over it
            new = TRANSFORMS[key](text)
            QGuiApplication.clipboard().setText(new)
            inject.release_shift()
            QTimer.singleShot(60, inject.paste)
            QTimer.singleShot(700, self._restore)
            self.log(f"text drag: {key} on {len(text)} chars")
            return
        self._restore()
        if self.on_action:
            QTimer.singleShot(0, lambda: self.on_action(key, text))

    def _paste_at(self, x: int, y: int):
        self.chip.stop()
        self._close_panel()
        inject.release_shift()
        inject.left_down(x, y)  # put the caret at the drop point
        inject.left_up(x, y)
        QTimer.singleShot(90, self._do_paste)

    def _do_paste(self):
        if self.text is not None and QGuiApplication.clipboard().text() != self.text:
            QGuiApplication.clipboard().setText(self.text)
        inject.paste()
        self.log(f"text drag: pasted {len(self.text or '')} chars")
        self.text = None
        QTimer.singleShot(600, self._restore)  # give the target time to read the clipboard

    def _replay_click(self, ox: int, oy: int, x: int, y: int):
        inject.left_down(ox, oy)
        inject.left_up(x, y)

    def _cancel(self):
        self.aborted = True
        self.chip.stop()
        self._close_panel()
        self.text = None
        self.pending_drop = None
        if self.saved is not None:
            self._restore()


# ------------------------------------------------------------------ transforms

def _title(t: str) -> str:
    return re.sub(r"(?<![\w'\u2019])(\w)([\w'\u2019]*)", lambda m: m.group(1).upper() + m.group(2).lower(), t)


def _clean(t: str) -> str:
    """Join lines broken mid-sentence (PDF copies), squeeze spaces, keep paragraphs."""
    t = t.replace("\r\n", "\n").replace("\u00a0", " ").replace("\u00ad", "")
    paras = re.split(r"\n\s*\n", t.strip())
    out = []
    for p in paras:
        p = re.sub(r"(\w)-\n(\w)", r"\1\2", p)        # re-join hyphenated words
        p = re.sub(r"\s*\n\s*", " ", p)
        p = re.sub(r"[ \t]{2,}", " ", p)
        p = re.sub(r" ([,.;:!?])", r"\1", p)
        out.append(p.strip())
    return "\n\n".join(out)


TRANSFORMS = {"upper": str.upper, "lower": str.lower, "title": _title, "clean": _clean}
