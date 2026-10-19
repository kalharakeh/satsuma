"""Send to phone: serve files over the local Wi-Fi and show a QR code to scan.

Nothing goes through the internet. A tiny web server listens on this PC only
while the window is open (and at most 20 minutes); the link carries a random
token, so other people on the network can't guess it.
"""
from __future__ import annotations

import html
import os
import secrets
import socket
import tempfile
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton

from dialogs import _base

LIFETIME_MS = 20 * 60 * 1000


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def qr_pixmap(text: str, box: int = 8) -> QPixmap:
    import qrcode
    q = qrcode.QRCode(border=2, box_size=box, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(text)
    q.make(fit=True)
    im = q.make_image(fill_color="black", back_color="white").get_image().convert("RGB")
    data = im.tobytes("raw", "RGB")
    return QPixmap.fromImage(QImage(data, im.width, im.height, im.width * 3, QImage.Format_RGB888).copy())


class _Events(QObject):
    downloaded = Signal(str)


def make_server(files: list[Path], token: str, events: _Events) -> ThreadingHTTPServer:
    by_name = {f.name: f for f in files}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            parts = urllib.parse.unquote(self.path).strip("/").split("/", 1)
            if not parts or parts[0] != token:
                self.send_error(404)
                return
            if len(parts) == 1 or not parts[1]:
                rows = "".join(
                    f'<a class="f" href="/{token}/{urllib.parse.quote(n)}"><b>{html.escape(n)}</b>'
                    f"<span>{p.stat().st_size / 1048576:.1f} MB</span></a>" for n, p in by_name.items())
                body = f"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Satsuma</title><style>body{{font-family:system-ui;background:#1c1c1e;color:#f2f2f7;margin:0;padding:24px}}
h1{{font-size:20px}}.f{{display:flex;justify-content:space-between;gap:12px;background:#2c2c2e;color:#f2f2f7;
padding:16px;border-radius:12px;margin:10px 0;text-decoration:none;border:1px solid #3a3a3c}}
.f span{{color:#8e8e93;white-space:nowrap}}b{{word-break:break-all}}p{{color:#8e8e93}}</style>
<h1>&#127818; Files from Satsuma</h1>{rows}<p>Tap a file to download it.</p>""".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            f = by_name.get(parts[1])
            if not f or not f.is_file():
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(f.stat().st_size))
            self.send_header("Content-Disposition",
                             f"attachment; filename*=UTF-8''{urllib.parse.quote(f.name)}")
            self.end_headers()
            try:
                with open(f, "rb") as fh:
                    while chunk := fh.read(1 << 16):
                        self.wfile.write(chunk)
                events.downloaded.emit(f.name)
            except (ConnectionError, OSError):
                pass

    return ThreadingHTTPServer(("0.0.0.0", 0), Handler)


class PhoneShare(QDialog):
    def __init__(self, files: list[Path]):
        super().__init__()
        self.tmp = None
        files = self._prepare(files)
        self.token = secrets.token_urlsafe(9)
        self.events = _Events()
        self.server = make_server(files, self.token, self.events)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.server_address[1]
        self.url = f"http://{lan_ip()}:{port}/{self.token}/"
        names = files[0].name if len(files) == 1 else f"{len(files)} files"
        v = _base(self, "Send to phone", f"{names} - scan with your phone's camera")
        qr = QLabel()
        qr.setPixmap(qr_pixmap(self.url))
        qr.setAlignment(Qt.AlignCenter)
        v.addWidget(qr)
        link = QLabel(f"<a style='color:#FF8A1F' href='{self.url}'>{self.url}</a>")
        link.setAlignment(Qt.AlignCenter)
        link.setTextInteractionFlags(Qt.TextBrowserInteraction)
        link.setOpenExternalLinks(True)
        v.addWidget(link)
        self.status = QLabel("Your phone must be on the same Wi-Fi. If Windows asks, allow Satsuma on "
                             "private networks. Sharing stops when you close this window.", objectName="help")
        self.status.setWordWrap(True)
        self.status.setMaximumWidth(380)
        v.addWidget(self.status)
        row = QHBoxLayout()
        copy = QPushButton("Copy link")
        copy.setAutoDefault(False)
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.url))
        done = QPushButton("Stop sharing")
        done.setAutoDefault(False)
        done.setDefault(True)
        done.clicked.connect(self.accept)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(done)
        v.addLayout(row)
        self.count = 0
        self.events.downloaded.connect(self._downloaded, Qt.QueuedConnection)
        QTimer.singleShot(LIFETIME_MS, self.accept)

    def _prepare(self, files: list[Path]) -> list[Path]:
        if len(files) == 1 and files[0].is_file():
            return files
        if all(f.is_file() for f in files) and len(files) <= 20:
            return files
        # folders, or lots of files: zip them so the phone gets one download
        self.tmp = tempfile.TemporaryDirectory(prefix="satsuma-phone-")
        z = Path(self.tmp.name) / f"{files[0].stem if len(files) == 1 else 'Satsuma files'}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                items = [(p, p.relative_to(f.parent)) for p in f.rglob("*") if p.is_file()] if f.is_dir() else [(f, Path(f.name))]
                for p, arc in items:
                    zf.write(p, arc.as_posix())
        return [z]

    def _downloaded(self, name: str):
        self.count += 1
        self.status.setText(f"✓ {name} downloaded ({self.count}x). Close this window when you're done.")

    def done(self, r):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        if self.tmp:
            self.tmp.cleanup()
        super().done(r)


def show_qr(text: str, title: str = "QR code"):
    dlg = QDialog()
    v = _base(dlg, title, (text[:120] + "…") if len(text) > 120 else text)
    if len(text.encode("utf-8")) > 2900:
        v.addWidget(QLabel("Too long for a QR code (max ~2,900 characters).", objectName="help"))
        pix = None
    else:
        pix = qr_pixmap(text)
        lab = QLabel()
        lab.setPixmap(pix)
        lab.setAlignment(Qt.AlignCenter)
        v.addWidget(lab)
    row = QHBoxLayout()
    if pix is not None:
        save = QPushButton("Save PNG…")
        save.setAutoDefault(False)

        def do_save():
            f, _ = QFileDialog.getSaveFileName(dlg, "Save QR code", str(Path.home() / "Desktop" / "qr-code.png"),
                                               "PNG (*.png)")
            if f:
                pix.save(f, "PNG")
        save.clicked.connect(do_save)
        row.addWidget(save)
    row.addStretch(1)
    close = QPushButton("Close")
    close.setAutoDefault(False)
    close.setDefault(True)
    close.clicked.connect(dlg.accept)
    row.addWidget(close)
    v.addLayout(row)
    dlg.exec()
