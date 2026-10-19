"""The optional AI pack: background removal, upscaling, speech-to-text and fallback OCR.

The installer leaves these libraries out (they'd roughly double its size). The
first time an AI tool runs, the matching Satsuma-AI-pack.zip is downloaded from
the GitHub release into %LOCALAPPDATA%\\Satsuma\\aipack-<version> and added to
the import path. Running from source, `pip install -r requirements-ai.txt`
does the same job and nothing is downloaded.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from engine import ConversionError, Progress

REPO = "kalharakeh/satsuma"
NEEDED = ("onnxruntime", "faster_whisper", "rapidocr_onnxruntime")
FROZEN = getattr(sys, "frozen", False)

try:
    from version import __version__
except ImportError:
    __version__ = "dev"
try:  # written by the release build: a fingerprint of the pack's contents, and the zip's checksum
    from version import AIPACK_ID, AIPACK_SHA256
except ImportError:
    AIPACK_ID, AIPACK_SHA256 = "", ""


def pack_dir() -> Path:
    override = os.environ.get("SATSUMA_AIPACK_DIR")
    if override:
        return Path(override)
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    # named by content, not app version: an update that doesn't change the AI libraries reuses the pack
    return base / "Satsuma" / f"aipack-{AIPACK_ID or __version__}"


_active = False


def activate() -> bool:
    """Put an installed pack on the import path. True if the AI libraries can be imported."""
    global _active
    d = pack_dir()
    if not _active and (d / "READY").exists():
        sys.path.insert(0, str(d))
        if os.name == "nt":
            try:
                os.add_dll_directory(str(d))  # bundled VC++ runtime DLLs
            except (OSError, AttributeError):
                pass
        _active = True
    return all(importlib.util.find_spec(m) is not None for m in NEEDED)


def installed() -> bool:
    try:
        return activate()
    except Exception:
        return False


def _urls() -> list[tuple[str, str]]:
    """(url, expected sha256 or '') - only this release's own pack has a known checksum."""
    urls = []
    if __version__ and __version__[0].isdigit() and "dev" not in __version__:
        urls.append((f"https://github.com/{REPO}/releases/download/v{__version__}/Satsuma-AI-pack.zip", AIPACK_SHA256))
    if not AIPACK_SHA256:  # dev builds only; a release never falls back to an unverified pack
        urls.append((f"https://github.com/{REPO}/releases/latest/download/Satsuma-AI-pack.zip", ""))
    return urls


def ensure(progress: Progress, span=(0.0, 0.5)):
    """Make sure the AI libraries are importable, downloading the pack if needed."""
    if installed():
        return
    if not FROZEN:
        raise ConversionError("This tool needs the AI libraries: pip install -r requirements-ai.txt")
    d = pack_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    lock = d.parent / (d.name + ".lock")
    # another job may already be downloading it: wait for that one
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        if time.time() - lock.stat().st_mtime > 1800:  # stale lock from a crashed download
            lock.unlink(missing_ok=True)
            return ensure(progress, span)
        for _ in range(1200):
            time.sleep(1)
            if (d / "READY").exists():
                if installed():
                    return
                break
        raise ConversionError("The AI pack download didn't finish. Try again.")
    try:
        from features import download
        with tempfile.TemporaryDirectory(prefix="satsuma-aipack-", dir=d.parent) as td:
            z = Path(td) / "pack.zip"
            last = None
            for url, digest in _urls() * 2:  # twice: a dropped connection gets one retry
                try:
                    download(url, z, progress, (span[0], span[0] + (span[1] - span[0]) * 0.85))
                except ConversionError as e:
                    last = e
                    z.unlink(missing_ok=True)
                    continue
                if digest:
                    from modelstore import sha256
                    if sha256(z) != digest:
                        z.unlink(missing_ok=True)
                        last = ConversionError("The AI pack download didn't match its checksum, so it was deleted.")
                        continue
                break
            else:
                raise last or ConversionError("Couldn't download the AI pack.")
            out = Path(td) / "x"
            with zipfile.ZipFile(z) as zf:
                zf.extractall(out)
            progress(span[1])
            (out / "READY").write_text(__version__)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            os.replace(out, d)
        for old in d.parent.glob("aipack-*"):  # packs from older versions
            if old != d and old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
    finally:
        lock.unlink(missing_ok=True)
    if not installed():
        raise ConversionError("The AI pack was installed but can't be loaded. Please report this on GitHub.")
