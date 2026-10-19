"""Editors that collect parameters visually before a job runs:
blur (faces + boxes), PDF pages (reorder/rotate/delete), and sign (place a signature)."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QColor, QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
                           QShortcut, QTransform)
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QListView, QListWidget, QListWidgetItem, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from dialogs import DIALOG_QSS, _base

ACCENT = QColor("#FF8A1F")


def _pil_to_qimage(im) -> QImage:
    im = im.convert("RGBA")
    data = im.tobytes("raw", "RGBA")
    return QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy()


def _buttons(dlg: QDialog, ok_text: str) -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText(ok_text)
    bb.button(QDialogButtonBox.Ok).setDefault(True)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


# ================================================================ blur

class BoxCanvas(QWidget):
    """Shows a picture scaled to fit; drag to add boxes, right-click a box to remove it."""
    changed = Signal()

    def __init__(self, pix: QPixmap, boxes: list[list[int]], max_size=QSize(960, 620)):
        super().__init__()
        self.pix = pix
        s = min(1.0, max_size.width() / pix.width(), max_size.height() / pix.height())
        self.scale = s
        self.setFixedSize(int(pix.width() * s), int(pix.height() * s))
        self.boxes = [QRectF(*b) for b in boxes]  # in image pixels
        self.drag_from: QPointF | None = None
        self.drag_now: QPointF | None = None
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)

    def to_img(self, p) -> QPointF:
        return QPointF(p.x() / self.scale, p.y() / self.scale)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self.pix)
        pen = QPen(ACCENT, 2)
        p.setPen(pen)
        fill = QColor(ACCENT)
        fill.setAlpha(70)
        for b in self.boxes:
            r = QRectF(b.x() * self.scale, b.y() * self.scale, b.width() * self.scale, b.height() * self.scale)
            p.fillRect(r, fill)
            p.drawRoundedRect(r, 6, 6)
        if self.drag_from and self.drag_now:
            p.setPen(QPen(Qt.white, 1, Qt.DashLine))
            p.drawRect(QRectF(self.drag_from * self.scale, self.drag_now * self.scale).normalized())

    def mousePressEvent(self, e):
        pt = self.to_img(e.position())
        if e.button() == Qt.RightButton:
            for b in reversed(self.boxes):
                if b.contains(pt):
                    self.boxes.remove(b)
                    self.changed.emit()
                    self.update()
                    return
        elif e.button() == Qt.LeftButton:
            self.drag_from = self.drag_now = pt

    def mouseMoveEvent(self, e):
        if self.drag_from is not None:
            self.drag_now = self.to_img(e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if self.drag_from is not None and e.button() == Qt.LeftButton:
            r = QRectF(self.drag_from, self.to_img(e.position())).normalized()
            if r.width() > 4 and r.height() > 4:
                self.boxes.append(r)
                self.changed.emit()
            self.drag_from = self.drag_now = None
            self.update()

    def result(self) -> list[list[int]]:
        return [[int(b.x()), int(b.y()), int(b.width()), int(b.height())] for b in self.boxes]


def find_faces(src: Path) -> list[list[int]]:
    """Face boxes, detected in a helper process (OpenCV must not load into the Qt process)."""
    import json
    import subprocess
    import integrate
    from engine import NOWIN
    exe, pre = integrate.launcher()
    if not getattr(__import__("sys"), "frozen", False):
        exe = __import__("sys").executable  # python.exe, so stdout works
    try:
        r = subprocess.run([exe, *pre, "--faces", str(src)], capture_output=True, text=True, timeout=60,
                           creationflags=NOWIN)
        return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 and r.stdout.strip() else []
    except (subprocess.TimeoutExpired, ValueError, OSError, IndexError):
        return []


def blur_editor(action, files, settings) -> dict | None:
    from engine import open_image
    src = files[0]
    im = open_image(src)
    faces = find_faces(src)
    dlg = QDialog()
    v = _base(dlg, "Blur faces & areas", src.name)
    canvas = BoxCanvas(QPixmap.fromImage(_pil_to_qimage(im)), faces)
    v.addWidget(canvas, alignment=Qt.AlignCenter)
    info = QLabel(objectName="help")

    def refresh():
        n = len(canvas.boxes)
        found = f"Found {len(faces)} face{'s' if len(faces) != 1 else ''}. " if faces is not None else ""
        info.setText(f"{found}Drag to cover more (number plates, names, screens). Right-click a box to remove it. "
                     f"{n} area{'s' if n != 1 else ''} will be hidden.")
    canvas.changed.connect(refresh)
    refresh()
    v.addWidget(info)
    row = QHBoxLayout()
    style = QComboBox()
    style.addItem("Blur", "blur")
    style.addItem("Pixelate", "pixelate")
    style.addItem("Black box", "black")
    i = style.findData(settings.get("blur_style") or "blur")
    style.setCurrentIndex(max(i, 0))
    row.addWidget(QLabel("Style"))
    row.addWidget(style)
    row.addStretch(1)
    row.addWidget(_buttons(dlg, "Save copy"))
    v.addLayout(row)
    if not dlg.exec() or not canvas.boxes:
        return None
    settings.set("blur_style", style.currentData())
    return {"rects": canvas.result(), "style": style.currentData()}


# ============================================================ PDF pages

def pdf_pages_editor(action, files, settings) -> dict | None:
    from engine import _open_pdf
    src = files[0]
    doc = _open_pdf(src)
    if doc.page_count > 400:
        from engine import ConversionError
        raise ConversionError("Over 400 pages - too many to edit here.")
    dlg = QDialog()
    v = _base(dlg, "Edit pages", f"{src.name} - drag to reorder, select and press Delete to remove")
    lst = QListWidget()
    lst.setViewMode(QListView.IconMode)
    lst.setIconSize(QSize(120, 160))
    lst.setGridSize(QSize(140, 196))
    lst.setResizeMode(QListView.Adjust)
    lst.setMovement(QListView.Snap)
    lst.setDragDropMode(QListWidget.InternalMove)
    lst.setSelectionMode(QListWidget.ExtendedSelection)
    lst.setMinimumSize(760, 480)
    thumbs: dict[int, QPixmap] = {}
    for i, page in enumerate(doc):
        z = 160 / max(page.rect.width, page.rect.height)
        pm = page.get_pixmap(matrix=__import__("pymupdf").Matrix(z * 1.5, z * 1.5), alpha=False)
        img = QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format_RGB888).copy()
        thumbs[i] = QPixmap.fromImage(img)
        it = QListWidgetItem(QIcon(thumbs[i]), f"{i + 1}")
        it.setData(Qt.UserRole, i)
        it.setData(Qt.UserRole + 1, 0)
        it.setTextAlignment(Qt.AlignHCenter)
        lst.addItem(it)
    v.addWidget(lst)

    def rotate(deg):
        for it in lst.selectedItems():
            r = (it.data(Qt.UserRole + 1) + deg) % 360
            it.setData(Qt.UserRole + 1, r)
            it.setIcon(QIcon(thumbs[it.data(Qt.UserRole)].transformed(QTransform().rotate(r))))

    def delete():
        for it in lst.selectedItems():
            lst.takeItem(lst.row(it))
        count.setText(f"{lst.count()} of {doc.page_count} pages kept")

    def reset():
        lst.clear()
        for i in range(doc.page_count):
            it = QListWidgetItem(QIcon(thumbs[i]), f"{i + 1}")
            it.setData(Qt.UserRole, i)
            it.setData(Qt.UserRole + 1, 0)
            lst.addItem(it)
        count.setText(f"{doc.page_count} pages")

    row = QHBoxLayout()
    for text, fn in (("↺ Rotate left", lambda: rotate(-90)), ("↻ Rotate right", lambda: rotate(90)),
                     ("Delete", delete), ("Reset", reset)):
        b = QPushButton(text)
        b.setAutoDefault(False)
        b.clicked.connect(fn)
        row.addWidget(b)
    QShortcut(QKeySequence.Delete, lst, activated=delete)
    count = QLabel(f"{doc.page_count} pages", objectName="help")
    row.addWidget(count)
    row.addStretch(1)
    row.addWidget(_buttons(dlg, "Save copy"))
    v.addLayout(row)
    if not dlg.exec():
        return None
    order = [lst.item(r).data(Qt.UserRole) for r in range(lst.count())]
    rot = {str(lst.item(r).data(Qt.UserRole)): lst.item(r).data(Qt.UserRole + 1)
           for r in range(lst.count()) if lst.item(r).data(Qt.UserRole + 1)}
    if order == list(range(doc.page_count)) and not rot:
        return None
    return {"order": order, "rotate": rot}


# ================================================================== sign

def _signature_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Satsuma"
    base.mkdir(parents=True, exist_ok=True)
    return base / "signature.png"


class SignaturePad(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(560, 200)
        self.paths: list[QPainterPath] = []
        self.setCursor(Qt.CrossCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.white)
        p.setPen(QPen(QColor("#ccc"), 1, Qt.DashLine))
        p.drawLine(30, 150, self.width() - 30, 150)
        p.setPen(QPen(QColor("#0b2a6b"), 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)

    def mousePressEvent(self, e):
        path = QPainterPath(e.position())
        self.paths.append(path)

    def mouseMoveEvent(self, e):
        if self.paths and e.buttons() & Qt.LeftButton:
            self.paths[-1].lineTo(e.position())
            self.update()

    def image(self) -> QImage | None:
        if not self.paths:
            return None
        bounds = QRectF()
        for path in self.paths:
            bounds = bounds.united(path.boundingRect())
        bounds = bounds.adjusted(-8, -8, 8, 8).intersected(QRectF(self.rect()))
        img = QImage(int(bounds.width() * 3), int(bounds.height() * 3), QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(3, 3)
        p.translate(-bounds.topLeft())
        p.setPen(QPen(QColor("#0b2a6b"), 3.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for path in self.paths:
            p.drawPath(path)
        p.end()
        return img


def draw_signature(parent=None) -> bool:
    dlg = QDialog(parent)
    v = _base(dlg, "Draw your signature", "Sign with the mouse or a pen. It's saved for next time.")
    pad = SignaturePad()
    v.addWidget(pad)
    row = QHBoxLayout()
    clear = QPushButton("Clear")
    clear.setAutoDefault(False)
    clear.clicked.connect(lambda: (pad.paths.clear(), pad.update()))
    row.addWidget(clear)
    row.addStretch(1)
    row.addWidget(_buttons(dlg, "Use this"))
    v.addLayout(row)
    if dlg.exec() and (img := pad.image()) is not None:
        img.save(str(_signature_path()))
        return True
    return False


class PageCanvas(QWidget):
    placed = Signal()

    def __init__(self):
        super().__init__()
        self.page_pix: QPixmap | None = None
        self.sig: QPixmap | None = None
        self.center: QPointF | None = None
        self.width_frac = 0.28
        self.setCursor(Qt.PointingHandCursor)

    def set_page(self, pix: QPixmap):
        self.page_pix = pix
        self.setFixedSize(pix.size())
        self.update()

    def sig_rect(self) -> QRectF | None:
        if not (self.center and self.sig and self.page_pix):
            return None
        w = self.page_pix.width() * self.width_frac
        h = w * self.sig.height() / max(1, self.sig.width())
        return QRectF(self.center.x() - w / 2, self.center.y() - h / 2, w, h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.page_pix:
            p.drawPixmap(0, 0, self.page_pix)
        r = self.sig_rect()
        if r:
            p.drawPixmap(r.toRect(), self.sig)
            p.setPen(QPen(ACCENT, 1, Qt.DashLine))
            p.drawRect(r)

    def mousePressEvent(self, e):
        self.center = e.position()
        self.update()
        self.placed.emit()

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.center = e.position()
            self.update()


def sign_editor(action, files, settings) -> dict | None:
    import pymupdf
    from engine import _open_pdf
    src = files[0]
    doc = _open_pdf(src)
    dlg = QDialog()
    v = _base(dlg, "Sign", f"{src.name} - click where the signature should go, drag to adjust")
    top = QHBoxLayout()
    page_box = QSpinBox()
    page_box.setRange(1, doc.page_count)
    page_box.setValue(doc.page_count)  # signatures usually go on the last page
    page_box.setPrefix("Page ")
    page_box.setSuffix(f" of {doc.page_count}")
    size = QSlider(Qt.Horizontal)
    size.setRange(10, 60)
    size.setValue(28)
    size.setFixedWidth(160)
    draw = QPushButton("Draw signature…")
    draw.setAutoDefault(False)
    load = QPushButton("Use a picture…")
    load.setAutoDefault(False)
    top.addWidget(page_box)
    top.addWidget(QLabel("Size"))
    top.addWidget(size)
    top.addStretch(1)
    top.addWidget(draw)
    top.addWidget(load)
    v.addLayout(top)
    canvas = PageCanvas()
    v.addWidget(canvas, alignment=Qt.AlignCenter)
    hint = QLabel("", objectName="help")
    v.addWidget(hint)
    bb = _buttons(dlg, "Save signed copy")
    v.addWidget(bb)
    state = {"scale": 1.0}

    def load_sig():
        path = Path(settings.get("signature_image") or _signature_path())
        if path.is_file():
            canvas.sig = QPixmap(str(path))
            settings.set("signature_image", str(path))
            hint.setText("Click on the page to place the signature.")
        else:
            canvas.sig = None
            hint.setText("Draw a signature or pick a picture of it first.")
        bb.button(QDialogButtonBox.Ok).setEnabled(canvas.sig is not None and canvas.center is not None)
        canvas.update()

    def show_page():
        page = doc[page_box.value() - 1]
        z = min(760 / page.rect.width, 560 / page.rect.height)
        state["scale"] = z
        pm = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        canvas.set_page(QPixmap.fromImage(QImage(pm.samples, pm.width, pm.height, pm.stride,
                                                 QImage.Format_RGB888).copy()))
        if canvas.center is None:
            canvas.center = QPointF(pm.width * 0.7, pm.height * 0.86)

    def on_draw():
        if draw_signature(dlg):
            settings.set("signature_image", str(_signature_path()))
            load_sig()

    def on_load():
        f, _ = QFileDialog.getOpenFileName(dlg, "Signature picture", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            settings.set("signature_image", f)
            load_sig()

    def on_size(val):
        canvas.width_frac = val / 100
        canvas.update()

    draw.clicked.connect(on_draw)
    load.clicked.connect(on_load)
    page_box.valueChanged.connect(lambda _: show_page())
    size.valueChanged.connect(on_size)
    canvas.placed.connect(load_sig)
    show_page()
    load_sig()
    if canvas.sig is None:
        on_draw()
    if not dlg.exec():
        return None
    r = canvas.sig_rect()
    if r is None:
        return None
    page = doc[page_box.value() - 1]
    z = state["scale"]
    rect = pymupdf.Rect(r.x() / z, r.y() / z, r.right() / z, r.bottom() / z) * page.derotation_matrix
    return {"image": settings.get("signature_image"), "page": page_box.value() - 1,
            "rect": [rect.x0, rect.y0, rect.x1, rect.y1]}


EDITORS = {"blur": blur_editor, "pdfpages": pdf_pages_editor, "sign": sign_editor}
