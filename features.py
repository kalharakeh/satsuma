"""More actions: trim, PDF passwords, OCR, background removal, subtitles, batch rename.

The AI features (background removal, subtitles) download their model once, on first
use, into %LOCALAPPDATA%\\Satsuma\\models. After that they run fully offline.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from engine import (ANY, BY_ID, Action, ConversionError, Param, Progress, _fitz, _open_pdf,
                    _save_pdf, _stem, beside, ffmpeg_exe, flatten, kind_of, media_duration,
                    natural_key, open_image, register, run_ffmpeg, staged, unique)

IS_WIN = os.name == "nt"


def local_dir(*parts: str) -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    p = base.joinpath("Satsuma", *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def download(url: str, dest: Path, progress: Progress, span=(0.0, 0.5)):
    """Fetch a model file once. Progress is mapped into `span` of the job's bar."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    progress(span[0] + (span[1] - span[0]) * done / total)
        if total and done != total:
            raise OSError(f"connection dropped after {done // 1048576} MB")
        os.replace(tmp, dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ConversionError(f"First use needs a one-time model download, and it failed ({e}). "
                              "Check your internet connection and try again.")
    return dest


# ------------------------------------------------------------------- trim

def parse_time(text: str) -> float:
    """'90', '1:30', '00:01:30.5' -> seconds."""
    text = str(text).strip().replace(",", ".")
    if not text:
        raise ValueError
    secs = 0.0
    for part in text.split(":"):
        secs = secs * 60 + float(part)
    return secs


def fmt_time(s: float) -> str:
    h, rem = divmod(max(s, 0), 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h):02}:{int(m):02}:{sec:06.3f}".rstrip("0").rstrip(".")


def trim(src: Path, progress: Progress, params: dict):
    exe = ffmpeg_exe()
    total = media_duration(exe, src)
    try:
        start = parse_time(params.get("start") or "0")
        end = parse_time(params["end"]) if str(params.get("end") or "").strip() else total
    except ValueError:
        raise ConversionError("Times must look like 1:30 or 00:01:30.")
    if total and end > total:
        end = total
    if end <= start:
        raise ConversionError("The end time must be after the start time.")
    video = kind_of(src) == "video"
    exact = bool(params.get("exact"))
    ext = src.suffix.lower()
    if video and exact:
        ext = ".mp4"
        codec = ["-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    elif video:
        codec = ["-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        codec = ["-map", "0:a", "-c", "copy"]
    dest = beside(src, ext, "trimmed")
    return run_ffmpeg(src, dest, ["-t", f"{end - start:.3f}", *codec], progress,
                      pre=["-ss", f"{start:.3f}"], duration=end - start)


TRIM_PARAMS = (
    Param("start", "Start", "time", "0:00", "e.g. 0:15 or 00:01:30"),
    Param("end", "End", "time", "", "empty = until the end"),
    Param("exact", "Frame-exact cut (re-encodes, slower)", "bool", False),
)
register(Action("vid-trim", "Trim…", "Trim", "Tools", frozenset({"video"}), trim, hint="cut a clip",
                params=TRIM_PARAMS, prio=12))
register(Action("aud-trim", "Trim…", "Trim", "Tools", frozenset({"audio"}), trim, hint="cut a clip",
                params=TRIM_PARAMS[:2], prio=10))


# ------------------------------------------------------------ PDF password

def pdf_lock(src: Path, progress: Progress, params: dict):
    pw = params.get("password") or ""
    if len(pw) < 4:
        raise ConversionError("Use a password of at least 4 characters.")
    if pw != params.get("confirm"):
        raise ConversionError("The two passwords don't match.")
    fz = _fitz()
    doc = _open_pdf(src)
    dest = beside(src, ".pdf", "locked")
    with staged(dest) as tmp:
        doc.save(str(tmp), garbage=3, deflate=True, encryption=fz.PDF_ENCRYPT_AES_256,
                 user_pw=pw, owner_pw=pw,
                 permissions=int(fz.PDF_PERM_PRINT | fz.PDF_PERM_COPY | fz.PDF_PERM_ACCESSIBILITY))
    return dest


def pdf_unlock(src: Path, progress: Progress, params: dict):
    fz = _fitz()
    try:
        doc = fz.open(src)
    except Exception as e:
        raise ConversionError(f"Can't open this PDF ({e}).")
    if not doc.is_encrypted and not doc.needs_pass:
        raise ConversionError("This PDF isn't password-protected.")
    if doc.needs_pass and not doc.authenticate(params.get("password") or ""):
        raise ConversionError("Wrong password.")
    if src.stem.endswith("-locked"):  # report-locked.pdf -> report-unlocked.pdf, not report-locked-unlocked
        dest = unique(src.with_name(src.stem[:-7] + "-unlocked.pdf"))
    else:
        dest = beside(src, ".pdf", "unlocked")
    with staged(dest) as tmp:
        doc.save(str(tmp), garbage=3, deflate=True, encryption=fz.PDF_ENCRYPT_NONE)
    return dest


register(Action("pdf-lock", "Add password…", "Lock", "Tools", frozenset({"pdf"}), pdf_lock,
                hint="password", prio=30,
                params=(Param("password", "Password", "password"), Param("confirm", "Repeat password", "password"))))
register(Action("pdf-unlock", "Remove password…", "Unlock", "Tools", frozenset({"pdf"}), pdf_unlock,
                hint="password", prio=31, params=(Param("password", "Current password", "password"),)))


# ------------------------------------------------------------------- OCR

def _ocr_windows(png: Path) -> str | None:
    """Windows' built-in OCR (uses the languages installed in Windows). None if unavailable."""
    if not IS_WIN:
        return None
    try:
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile
    except Exception:
        return None

    async def run():
        f = await StorageFile.get_file_from_path_async(str(png))
        stream = await f.open_async(FileAccessMode.READ)
        bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            return None
        res = await eng.recognize_async(bmp)
        return "\n".join(line.text for line in res.lines)

    try:
        return asyncio.run(run())
    except Exception:
        return None


_rapid = None


def _ocr_rapid(png: Path) -> str:
    global _rapid
    import aipack
    aipack.ensure(lambda f: None)
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        raise ConversionError("OCR isn't available: install rapidocr_onnxruntime.")
    if _rapid is None:
        _rapid = RapidOCR()
    result, _ = _rapid(str(png))
    if not result:
        return ""
    # result rows: [box, text, score]; group into lines by vertical position
    rows = sorted(result, key=lambda r: (min(p[1] for p in r[0]), min(p[0] for p in r[0])))
    lines, current, last_y, height = [], [], None, 0
    for box, text, _score in rows:
        y = sum(p[1] for p in box) / 4
        h = max(p[1] for p in box) - min(p[1] for p in box)
        if last_y is not None and abs(y - last_y) > max(height, h) * 0.6:
            lines.append(" ".join(t for _, t in sorted(current)))
            current = []
        current.append((min(p[0] for p in box), text))
        last_y, height = y, h
    if current:
        lines.append(" ".join(t for _, t in sorted(current)))
    return "\n".join(lines)


def ocr_image_file(png: Path) -> str:
    text = _ocr_windows(png)
    return text if text and text.strip() else _ocr_rapid(png)


def _limit(im, side=4000):
    from PIL import Image
    if max(im.size) > side:
        s = side / max(im.size)
        im = im.resize((round(im.width * s), round(im.height * s)), Image.Resampling.LANCZOS)
    return im


def ocr(src: Path, progress: Progress):
    with tempfile.TemporaryDirectory(prefix="satsuma-ocr-") as td:
        pages: list[Path] = []
        if kind_of(src) == "pdf":
            doc = _open_pdf(src)
            for i, page in enumerate(doc):
                p = Path(td) / f"p{i}.png"
                page.get_pixmap(dpi=200).save(str(p))
                pages.append(p)
        else:
            p = Path(td) / "img.png"
            _limit(flatten(open_image(src))).save(p)
            pages.append(p)
        parts = []
        for i, p in enumerate(pages):
            parts.append(ocr_image_file(p))
            progress((i + 1) / len(pages))
    text = "\n\n".join(t.strip() for t in parts if t)
    if not text.strip():
        raise ConversionError("No text found in this file.")
    dest = beside(src, ".txt", "text")
    with staged(dest) as tmp:
        tmp.write_text(text + "\n", encoding="utf-8")
    return dest


register(Action("img-ocr", "Extract text (OCR)", "Text", "Tools", frozenset({"image"}), ocr, hint="OCR", prio=12))
register(Action("pdf-ocr", "Extract text from scans (OCR)", "OCR", "Tools", frozenset({"pdf"}), ocr, hint="scans", prio=25))


# ---------------------------------------------------------- background removal

U2NET_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx"
_session = None


def _u2net(progress: Progress):
    global _session
    if _session is None:
        import aipack
        aipack.ensure(progress, (0.0, 0.3))
        try:
            import onnxruntime as ort
        except ImportError:
            raise ConversionError("Background removal needs onnxruntime: pip install onnxruntime")
        import modelstore
        legacy = local_dir("models") / "u2net.onnx"  # 1.1.0 kept it here
        if legacy.exists():
            (modelstore.models_root() / "u2net").mkdir(exist_ok=True)
            os.replace(legacy, modelstore.models_root() / "u2net" / "u2net.onnx")
        model = modelstore.model_file("u2net", progress, (0.3, 0.6))
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        _session = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
    return _session


def cutout_mask(im, progress: Progress):
    """Alpha mask (PIL 'L', same size as im) for the main subject, via U²-Net."""
    import numpy as np
    from PIL import Image
    sess = _u2net(progress)
    progress(0.65)
    rgb = im.convert("RGB").resize((320, 320), Image.Resampling.LANCZOS)
    x = np.asarray(rgb, dtype=np.float32)
    x = x / max(float(x.max()), 1e-6)
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    x = x.transpose(2, 0, 1)[None].astype(np.float32)
    pred = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    pred = (pred - pred.min()) / max(float(pred.max() - pred.min()), 1e-6)
    progress(0.9)
    mask = Image.fromarray((pred * 255).astype("uint8"), "L")
    return mask.resize(im.size, Image.Resampling.LANCZOS)


def remove_bg(src: Path, progress: Progress):
    im = open_image(src)
    if getattr(im, "is_animated", False):
        raise ConversionError("Animated images aren't supported.")
    mask = cutout_mask(im, progress)
    out = im.convert("RGBA")
    if "A" in im.getbands():
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, im.getchannel("A"))
    out.putalpha(mask)
    bbox = mask.point(lambda v: 255 if v > 8 else 0).getbbox()
    if not bbox:
        raise ConversionError("Couldn't find a subject in this picture.")
    dest = beside(src, ".png", "cutout")
    with staged(dest) as tmp:
        out.save(tmp, "PNG", optimize=True)
    return dest


register(Action("img-cutout", "Remove background", "Cut out", "Tools", frozenset({"image"}), remove_bg,
                hint="no background", prio=11))


# ------------------------------------------------------------------ subtitles

WHISPER_MODEL = "small"  # good multilingual accuracy on a normal CPU; ~460 MB once


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def transcribe(src: Path, progress: Progress, params: dict | None = None):
    import aipack
    aipack.ensure(progress, (0.0, 0.03))
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ConversionError("Subtitles need faster-whisper: pip install faster-whisper")
    exe = ffmpeg_exe()
    total = media_duration(exe, src) or 1.0
    import modelstore
    progress(0.01)
    first_time = not (modelstore.models_root() / "whisper-small" / "model.bin.ok").exists()
    base = 0.45 if first_time else 0.05  # the first run spends most of its time downloading the model
    folder = modelstore.fetch("whisper-small", progress, (0.03, base))
    try:
        model = WhisperModel(str(folder), device="cpu", compute_type="int8")
    except Exception as e:
        raise ConversionError(f"Couldn't load the speech model - first use needs a one-time download ({e}).")
    with tempfile.TemporaryDirectory(prefix="satsuma-stt-") as td:
        wav = Path(td) / "a.wav"
        import subprocess
        from engine import NOWIN
        r = subprocess.run([exe, "-hide_banner", "-nostdin", "-y", "-i", str(src), "-vn", "-ac", "1",
                            "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], capture_output=True,
                           creationflags=NOWIN)
        if r.returncode != 0 or not wav.exists():
            raise ConversionError("This file has no audio track.")
        progress(base)
        import wave
        import numpy as np
        with wave.open(str(wav), "rb") as w:  # feed samples directly: no PyAV needed
            audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
        segments, info = model.transcribe(audio, vad_filter=True, beam_size=5)
        srt, plain = [], []
        for n, seg in enumerate(segments, 1):
            text = seg.text.strip()
            srt.append(f"{n}\n{_srt_time(seg.start)} --> {_srt_time(seg.end)}\n{text}\n")
            plain.append(text)
            progress(base + (0.99 - base) * min(seg.end / total, 1.0))
    if not srt:
        raise ConversionError("No speech found.")
    dest = beside(src, ".srt")
    with staged(dest) as tmp:
        tmp.write_text("\n".join(srt), encoding="utf-8")
    txt = beside(src, ".txt", "transcript")
    with staged(txt) as tmp:
        tmp.write_text(" ".join(plain) + "\n", encoding="utf-8")
    return [dest, txt]


register(Action("vid-srt", "Subtitles (SRT) from speech", "Subtitles", "Tools", frozenset({"video"}),
                transcribe, hint="speech to text", prio=13))
register(Action("aud-srt", "Transcribe speech", "Transcribe", "Tools", frozenset({"audio"}),
                transcribe, hint="speech to text", prio=11))


# --------------------------------------------------------------- batch rename

def _taken(p: Path) -> datetime:
    """EXIF capture date for photos, else last-modified time."""
    if kind_of(p) == "image":
        try:
            from PIL import Image
            with Image.open(p) as im:
                exif = im.getexif()
                raw = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
            if raw:
                return datetime.strptime(str(raw).strip()[:19], "%Y:%m:%d %H:%M:%S")
        except Exception:
            pass
    return datetime.fromtimestamp(p.stat().st_mtime)


_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TOKEN = re.compile(r"\{(name|n|date|time|taken|ext|folder)(?::(\d+))?\}")


def rename_plan(files: list[Path], pattern: str, start: int = 1) -> list[tuple[Path, Path]]:
    files = sorted(files, key=natural_key)
    width = max(2, len(str(start + len(files) - 1)))
    plan, taken_names = [], set()
    own = {str(f).lower() for f in files}
    for i, f in enumerate(files):
        ext = "" if f.is_dir() else f.suffix
        stamp = None

        def sub(m, f=f, i=i, ext=ext):
            nonlocal stamp
            tok, pad = m.group(1), m.group(2)
            if tok == "n":
                return str(start + i).zfill(int(pad) if pad else width)
            if tok == "name":
                return _stem(f)
            if tok == "ext":
                return ext.lstrip(".")
            if tok == "folder":
                return f.parent.name
            stamp = stamp or _taken(f)
            return stamp.strftime({"date": "%Y-%m-%d", "time": "%H%M%S", "taken": "%Y-%m-%d %H%M%S"}[tok])

        base = _BAD.sub("_", TOKEN.sub(sub, pattern)).strip().rstrip(".")
        if not base:
            raise ConversionError("The pattern gives an empty name.")
        new = f.with_name(base if "{ext}" in pattern else base + ext)
        cand, k = new, 1
        # the files being renamed free up their own names; anything else on disk is taken
        while str(cand).lower() in taken_names or (cand.exists() and str(cand).lower() not in own):
            stem, suf = (new.name, "") if f.is_dir() else (new.stem, new.suffix)
            cand = new.with_name(f"{stem} ({k}){suf}")
            k += 1
        taken_names.add(str(cand).lower())
        plan.append((f, cand))
    return plan


def batch_rename(files: list[Path], progress: Progress, params: dict):
    pattern = (params.get("pattern") or "").strip()
    if not pattern:
        raise ConversionError("Enter a name pattern.")
    try:
        start = int(params.get("start") or 1)
    except ValueError:
        raise ConversionError("'Start at' must be a number.")
    plan = [(a, b) for a, b in rename_plan(files, pattern, start) if a != b]
    if not plan:
        raise ConversionError("Nothing to rename - the names already match.")
    # two passes through temporary names, so swaps like a<->b work
    temps = []
    for a, _ in plan:
        t = a.with_name(f"~satsuma-{os.getpid()}-{len(temps)}{a.suffix}")
        os.replace(a, t)
        temps.append(t)
    done = []
    try:
        for (a, b), t in zip(plan, temps):
            os.replace(t, b)
            done.append((a, b))
            progress(len(done) / len(plan))
    except OSError as e:
        for (a, b) in done:
            os.replace(b, a)
        for (a, _), t in zip(plan[len(done):], temps[len(done):]):
            if t.exists():
                os.replace(t, a)
        raise ConversionError(f"Rename failed, nothing was changed ({e}).")
    return [b for _, b in done], {"rename": [[str(b), str(a)] for a, b in done]}


RENAME_PARAMS = (
    Param("pattern", "New name", "text", "{name} {n}",
          "{name} {n} {date} {time} {taken} {folder} - extension is kept"),
    Param("start", "Start at", "text", "1"),
)
register(Action("rename", "Batch rename…", "Rename", "Tools", ANY, batch_rename, each=False,
                hint="pattern", params=RENAME_PARAMS, prio=80))


# ------------------------------------------------------------- panel order

for _id, _prio in {
    "img-compress": 10, "img-1920": 20, "img-half": 21, "img-1080": 22, "img-strip": 30,
    "img-rotate": 31, "img-gray": 32, "img-combine": 40,
    "vid-compress": 10, "vid-small": 11, "vid-1080": 20, "vid-720": 21, "vid-mute": 22,
    "vid-rotate": 30, "vid-thumb": 31, "vid-strip": 32,
    "aud-normalize": 20, "aud-compress": 21, "aud-strip": 30,
    "pdf-compress": 10, "pdf-merge": 11, "pdf-split": 12, "pdf-rotate": 40, "pdf-strip": 41,
    "arc-extract": 5, "arc-zip": 90, "arc-7z": 91,
}.items():
    if _id in BY_ID:
        BY_ID[_id].prio = _prio
