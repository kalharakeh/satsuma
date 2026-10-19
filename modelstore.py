"""Downloadable AI models: fetched once, checked against a known SHA-256, then used offline.

Each file is taken from Satsuma's own GitHub release "models-v1" first (a mirror made by
.github/workflows/models.yml), and from the original project only if that fails. Either way
the hash must match, or the file is thrown away.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from engine import ConversionError, Progress

MIRROR = "https://github.com/kalharakeh/satsuma/releases/download/models-v1/"
HF = "https://huggingface.co/Systran/faster-whisper-small/resolve/536b0662742c02347bc0e980a01041f333bce120/"

# name -> list of (file name, size, sha256, mirror asset name, upstream URL)
MODELS = {
    "u2net": [
        ("u2net.onnx", 175997641, "8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491",
         "u2net.onnx", "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"),
    ],
    "whisper-small": [
        ("config.json", 2370, "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
         "whisper-small-config.json", HF + "config.json"),
        ("tokenizer.json", 2203239, "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab",
         "whisper-small-tokenizer.json", HF + "tokenizer.json"),
        ("vocabulary.txt", 459861, "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913",
         "whisper-small-vocabulary.txt", HF + "vocabulary.txt"),
        ("model.bin", 483546902, "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
         "whisper-small-model.bin", HF + "model.bin"),
    ],
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def models_root() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    p = base / "Satsuma" / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def fetch(name: str, progress: Progress, span=(0.0, 1.0)) -> Path:
    """Make sure every file of a model is present and verified. Returns the model's folder."""
    from features import download
    files = MODELS[name]
    folder = models_root() / name
    folder.mkdir(parents=True, exist_ok=True)
    total = sum(f[1] for f in files) or 1
    done = 0
    for fname, size, digest, asset, upstream in files:
        dest = folder / fname
        marker = folder / (fname + ".ok")
        if dest.exists() and marker.exists() and dest.stat().st_size == size:
            done += size
            continue
        a = span[0] + (span[1] - span[0]) * done / total
        b = span[0] + (span[1] - span[0]) * (done + size) / total
        last_error = None
        for url in (MIRROR + asset, upstream, upstream):  # the 2nd upstream try covers a dropped connection
            dest.unlink(missing_ok=True)
            try:
                download(url, dest, progress, (a, b))
            except ConversionError as e:
                last_error = e
                continue
            if sha256(dest) == digest:
                marker.write_text(digest)
                break
            dest.unlink(missing_ok=True)
            last_error = ConversionError(f"The downloaded {fname} didn't match its checksum, so it was deleted.")
        else:
            raise last_error or ConversionError(f"Couldn't download {fname}.")
        done += size
    return folder


def model_file(name: str, progress: Progress, span=(0.0, 1.0)) -> Path:
    return fetch(name, progress, span) / MODELS[name][0][0]
