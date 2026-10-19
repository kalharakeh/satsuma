"""Satsuma conversion engine.

Every conversion runs locally on this PC. The source file is never modified:
results are written to a temporary name next to it and renamed into place
only when they are complete.

Run as a child process by the app:  app.py --run <action_id> <file> [<file> ...]
It reports on stdout, one line each:  P <0..1>   O <output path>   E <error>
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import traceback
import zipfile
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flash

EXT = {
    "image": {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif", ".tif", ".tiff",
              ".ico", ".heic", ".heif", ".avif", ".tga"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".mpg",
              ".mpeg", ".3gp", ".ts", ".mts", ".m2ts"},
    "audio": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma", ".aiff",
              ".aif", ".amr"},
    "pdf": {".pdf"},
    "word": {".docx", ".doc", ".odt", ".rtf"},
    "slides": {".pptx", ".ppt", ".odp"},
    "sheet": {".xlsx", ".xls", ".ods"},
    "csv": {".csv"},
    "json": {".json"},
    "archive": {".zip", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".rar", ".tbz2", ".txz"},
}
ANY = frozenset(set(EXT) | {"file", "folder"})
TAR_DOUBLE = (".tar.gz", ".tar.bz2", ".tar.xz")


class ConversionError(Exception):
    """A failure with a message that is safe to show the user as-is."""


Progress = Callable[[float], None]


def kind_of(p: Path) -> str:
    if p.is_dir():
        return "folder"
    if p.name.lower().endswith(TAR_DOUBLE):
        return "archive"
    ext = p.suffix.lower()
    for k, exts in EXT.items():
        if ext in exts:
            return k
    return "file"


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


# ----------------------------------------------------------------- output helpers

def _stem(src: Path) -> str:
    if src.is_dir():
        return src.name
    low = src.name.lower()
    for d in TAR_DOUBLE:
        if low.endswith(d):
            return src.name[: -len(d)]
    return src.stem


def unique(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suf = (p.name, "") if p.is_dir() or not p.suffix else (p.stem, p.suffix)
    i = 1
    while True:
        q = p.with_name(f"{stem} ({i}){suf}")
        if not q.exists():
            return q
        i += 1


def beside(src: Path, ext: str, tag: str = "") -> Path:
    """Output path next to the source: photo.heic -> photo.jpg, or photo-compressed.jpg."""
    stem = _stem(src) + (f"-{tag}" if tag else "")
    return unique(src.parent / f"{stem}{ext}")


@contextmanager
def staged(dest: Path):
    tmp = dest.with_name(f"~{dest.stem}.part{dest.suffix}")
    try:
        yield tmp
        os.replace(tmp, dest)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


@contextmanager
def staged_dir(dest: Path):
    tmp = Path(tempfile.mkdtemp(prefix="~satsuma-", dir=dest.parent))
    try:
        yield tmp
        os.replace(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def must_be_smaller(src: Path, out: Path) -> Path:
    if out.stat().st_size >= src.stat().st_size:
        out.unlink()
        raise ConversionError("Already well compressed - the result wasn't smaller, so nothing was saved.")
    return out


# ------------------------------------------------------------------ action registry

@dataclass
class Action:
    id: str
    label: str              # full name, used in the menu
    tile: str               # big text on the tile
    group: str              # "Convert" or "Tools"
    kinds: frozenset
    fn: Callable
    each: bool = True       # True: one job per file; False: one job for all files
    min_files: int = 1
    hint: str = ""          # small text under the tile
    skip_ext: frozenset = frozenset()   # don't offer converting X -> X
    only_ext: frozenset = frozenset()   # only offer for these extensions
    params: tuple = ()      # Param specs the UI asks for before running
    editor: str = ""        # name of a custom dialog that produces the params (dialogs.EDITORS)
    ui: str = ""            # handled entirely by the app, no conversion job (e.g. "phone")
    prio: int = 50          # lower = earlier in the panel

    def applies(self, files: list[Path]) -> bool:
        if len(files) < self.min_files:
            return False
        if any(kind_of(f) not in self.kinds for f in files):
            return False
        if self.only_ext and any(f.suffix.lower() not in self.only_ext for f in files):
            return False
        if self.skip_ext and all(f.suffix.lower() in self.skip_ext for f in files):
            return False
        return True


ACTIONS: list[Action] = []
BY_ID: dict[str, Action] = {}


def register(a: Action) -> Action:
    ACTIONS.append(a)
    BY_ID[a.id] = a
    return a


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    type: str = "text"      # text | password | bool | time | choice
    default: object = ""
    help: str = ""
    choices: tuple = ()     # for "choice": suggestions; the box stays editable


def applicable(files: list[Path]) -> list[Action]:
    acts = [a for a in ACTIONS if a.applies(files)]
    order = {"Presets": 0, "Convert": 1, "Tools": 2}
    return sorted(acts, key=lambda a: (order.get(a.group, 3), a.prio))  # stable within a prio


# ------------------------------------------------------------------------ ffmpeg

def _app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def ffmpeg_exe() -> str:
    candidates = [_app_dir() / "bin" / "ffmpeg.exe", shutil.which("ffmpeg")]
    if os.name == "nt":
        candidates.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
    for c in candidates:
        if c and Path(c).is_file():
            return str(c)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise ConversionError("FFmpeg not found. Run install.bat again or install it with: winget install Gyan.FFmpeg")


def media_duration(exe: str, src: Path) -> float:
    r = subprocess.run([exe, "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NOWIN)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def run_ffmpeg(src: Path, dest: Path, args: list[str], progress: Progress,
               pre: list[str] | None = None, duration: float | None = None) -> Path:
    exe = ffmpeg_exe()
    dur = duration if duration is not None else media_duration(exe, src)
    with staged(dest) as tmp:
        ffmpeg_exec([*(pre or []), "-i", str(src), *args, str(tmp)], dur, progress)
    return dest


def ffmpeg_exec(args: list[str], duration: float, progress: Progress, cwd: str | None = None):
    """Run ffmpeg with the given arguments (inputs, filters, output), reporting progress."""
    exe = ffmpeg_exe()
    cmd = [exe, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
                            encoding="utf-8", errors="replace", creationflags=NOWIN)
    tail: deque[str] = deque(maxlen=25)
    t = threading.Thread(target=lambda: tail.extend(proc.stderr), daemon=True)
    t.start()
    for line in proc.stdout:
        if duration and line.startswith(("out_time_us=", "out_time_ms=")):
            try:
                progress(min(max(int(line.split("=", 1)[1]), 0) / 1e6 / duration, 0.99))
            except ValueError:
                pass
    rc = proc.wait()
    t.join(2)
    if rc != 0:
        msg = next((l.strip() for l in reversed(tail) if l.strip() and not l.startswith(" ")), "FFmpeg failed")
        raise ConversionError(f"FFmpeg: {msg}")


def ffmpeg_action(id, label, tile, group, kinds, ext, args, tag="", hint="", skip=(), only=(), smaller=False):
    def fn(src: Path, progress: Progress):
        a = args(src) if callable(args) else args
        e = ext(src) if callable(ext) else ext
        out = run_ffmpeg(src, beside(src, e, tag), a, progress)
        return must_be_smaller(src, out) if smaller else out
    return register(Action(id, label, tile, group, frozenset(kinds), fn, hint=hint,
                           skip_ext=frozenset(skip), only_ext=frozenset(only)))


# ------------------------------------------------------------------------ images

_heif_ready = False


def _pil():
    global _heif_ready
    from PIL import Image
    if not _heif_ready:
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        _heif_ready = True
    return Image


def open_image(src: Path):
    Image = _pil()
    from PIL import ImageOps
    try:
        im = Image.open(src)
        im.load()
    except Exception as e:
        raise ConversionError(f"Can't read this image ({e.__class__.__name__}).")
    if not getattr(im, "is_animated", False):
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:
            pass
    return im


def flatten(im):
    """RGB on white - for formats without transparency."""
    Image = _pil()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return im if im.mode in ("RGB", "L") else im.convert("RGB")


def save_image(im, dest: Path, fmt: str, quality: int | None = None):
    fmt = fmt.upper()
    params: dict = {}
    animated = getattr(im, "is_animated", False) and fmt in ("GIF", "WEBP", "PNG")
    if animated:
        params.update(save_all=True, loop=im.info.get("loop", 0))
    elif getattr(im, "is_animated", False):
        im.seek(0)
        im = im.copy()
    if fmt == "JPEG":
        im = flatten(im)
        params.update(quality=quality or 92, optimize=True, progressive=True, subsampling="4:2:0" if (quality or 92) < 90 else "4:4:4")
    elif fmt == "WEBP":
        params.update(quality=quality or 90, method=6)
    elif fmt == "AVIF":
        params.update(quality=quality or 70)
    elif fmt == "HEIF":
        params.update(quality=quality or 85)
    elif fmt == "PNG":
        params.update(optimize=True)
    elif fmt == "TIFF":
        params.update(compression="tiff_lzw")
    elif fmt == "ICO":
        im = im.convert("RGBA")
        side = max(im.size)
        params.update(sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= side] or [(16, 16)])
    elif fmt == "BMP":
        im = flatten(im)
    elif fmt == "PDF":
        im = flatten(im)
        params.update(resolution=150)
    elif fmt == "GIF" and im.mode not in ("P", "L"):
        im = im.convert("RGBA") if im.mode in ("RGBA", "LA") else im.convert("RGB")
    icc = im.info.get("icc_profile")
    if icc and fmt in ("JPEG", "PNG", "WEBP", "TIFF", "AVIF", "HEIF"):
        params["icc_profile"] = icc
    if fmt == "AVIF":
        from PIL import features
        if not features.check("avif"):
            raise ConversionError("This Pillow build can't write AVIF - update it: pip install -U pillow")
    im.save(dest, format=fmt, **params)


IMAGE_TARGETS = [  # (tile, PIL format, extension, extensions that are already this format)
    ("JPG", "JPEG", ".jpg", {".jpg", ".jpeg", ".jfif"}),
    ("PNG", "PNG", ".png", {".png"}),
    ("WEBP", "WEBP", ".webp", {".webp"}),
    ("HEIC", "HEIF", ".heic", {".heic", ".heif"}),
    ("AVIF", "AVIF", ".avif", {".avif"}),
    ("PDF", "PDF", ".pdf", set()),
    ("GIF", "GIF", ".gif", {".gif"}),
    ("TIFF", "TIFF", ".tiff", {".tif", ".tiff"}),
    ("BMP", "BMP", ".bmp", {".bmp"}),
    ("ICO", "ICO", ".ico", {".ico"}),
]


def _image_convert(fmt: str, ext: str):
    def fn(src: Path, progress: Progress):
        im = open_image(src)
        dest = beside(src, ext)
        with staged(dest) as tmp:
            save_image(im, tmp, fmt)
        return dest
    return fn


for _tile, _fmt, _ext, _same in IMAGE_TARGETS:
    register(Action(f"img-{_tile.lower()}", f"Convert to {_tile}", _tile, "Convert",
                    frozenset({"image"}), _image_convert(_fmt, _ext), skip_ext=frozenset(_same)))

ffmpeg_action("gif-mp4", "Animated GIF to MP4", "MP4", "Convert", {"image"}, ".mp4",
              ["-movflags", "+faststart", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "20",
               "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"], hint="video", only={".gif"})


def _same_format(src: Path) -> tuple[str, str]:
    e = src.suffix.lower()
    table = {".jpg": "JPEG", ".jpeg": "JPEG", ".jfif": "JPEG", ".png": "PNG", ".webp": "WEBP",
             ".tif": "TIFF", ".tiff": "TIFF", ".gif": "GIF", ".bmp": "BMP", ".avif": "AVIF",
             ".heic": "HEIF", ".heif": "HEIF", ".ico": "ICO"}
    return (table[e], e) if e in table else ("PNG", ".png")


def img_compress(src: Path, progress: Progress):
    im = open_image(src)
    e = src.suffix.lower()
    Image = _pil()
    if e == ".png":
        dest = beside(src, ".png", "compressed")
        if getattr(im, "is_animated", False):
            raise ConversionError("Animated PNGs aren't supported for compression.")
        q = im.convert("RGBA").quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
        with staged(dest) as tmp:
            q.save(tmp, "PNG", optimize=True)
    elif e == ".webp":
        dest = beside(src, ".webp", "compressed")
        with staged(dest) as tmp:
            save_image(im, tmp, "WEBP", 72)
    elif e in (".heic", ".heif"):
        dest = beside(src, e, "compressed")
        with staged(dest) as tmp:
            save_image(im, tmp, "HEIF", 55)
    elif e == ".avif":
        dest = beside(src, ".avif", "compressed")
        with staged(dest) as tmp:
            save_image(im, tmp, "AVIF", 50)
    elif e == ".gif":
        raise ConversionError("For GIFs, convert to MP4 instead - it's usually 5-10x smaller.")
    else:
        dest = beside(src, ".jpg", "compressed")
        with staged(dest) as tmp:
            save_image(im, tmp, "JPEG", 75)
    return must_be_smaller(src, dest)


def _resize(factor: float | None = None, longest: int | None = None, tag: str = ""):
    def fn(src: Path, progress: Progress):
        im = open_image(src)
        if getattr(im, "is_animated", False):
            raise ConversionError("Resizing animated images isn't supported - convert to MP4 first.")
        w, h = im.size
        if factor:
            size = (max(1, round(w * factor)), max(1, round(h * factor)))
        else:
            if max(w, h) <= longest:
                raise ConversionError(f"Already {w}x{h} - no bigger than {longest}px, nothing to do.")
            s = longest / max(w, h)
            size = (max(1, round(w * s)), max(1, round(h * s)))
        Image = _pil()
        out = im.resize(size, Image.Resampling.LANCZOS)
        out.info = im.info
        fmt, ext = _same_format(src)
        dest = beside(src, ext, tag)
        with staged(dest) as tmp:
            save_image(out, tmp, fmt, 90 if fmt == "JPEG" else None)
        return dest
    return fn


def _strip_jpeg(data: bytes) -> bytes:
    """Drop EXIF/XMP/IPTC/comments/MPF from a JPEG without re-encoding (lossless)."""
    if data[:2] != b"\xff\xd8":
        raise ConversionError("Not a valid JPEG file.")
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            raise ConversionError("Unexpected JPEG structure.")
        m = data[i + 1]
        if m == 0xFF:
            i += 1
            continue
        if m == 0xDA:  # start of scan: copy entropy data up to the primary image's end
            end = data.find(b"\xff\xd9", i)
            out += data[i:] if end < 0 else data[i:end + 2]
            break
        if 0xD0 <= m <= 0xD7 or m == 0x01:
            out += data[i:i + 2]
            i += 2
            continue
        ln = int.from_bytes(data[i + 2:i + 4], "big")
        seg = data[i:i + 2 + ln]
        keep = m in (0xE0, 0xEE) or not (0xE0 <= m <= 0xEF or m == 0xFE)
        if m == 0xE2 and seg[4:15] == b"ICC_PROFILE":
            keep = True
        if keep:
            out += seg
        i += 2 + ln
    return bytes(out)


def _strip_png(data: bytes) -> bytes:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ConversionError("Not a valid PNG file.")
    drop = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
    out = bytearray(data[:8])
    i = 8
    while i + 8 <= len(data):
        ln = int.from_bytes(data[i:i + 4], "big")
        typ = data[i + 4:i + 8]
        end = i + 12 + ln
        if typ not in drop:
            out += data[i:end]
        i = end
        if typ == b"IEND":
            break
    return bytes(out)


def img_strip(src: Path, progress: Progress):
    e = src.suffix.lower()
    dest = beside(src, e, "clean")
    data = src.read_bytes()
    if e in (".jpg", ".jpeg", ".jfif"):
        Image = _pil()
        with Image.open(src) as probe:
            orientation = probe.getexif().get(0x0112, 1)
        if orientation in (1, None):  # lossless path keeps every pixel identical
            with staged(dest) as tmp:
                tmp.write_bytes(_strip_jpeg(data))
            return dest
    elif e == ".png":
        with staged(dest) as tmp:
            tmp.write_bytes(_strip_png(data))
        return dest
    # everything else: rebuild the image from raw pixels, keeping only the colour profile
    im = open_image(src)
    if getattr(im, "is_animated", False):
        raise ConversionError("Animated images aren't supported for metadata removal.")
    Image = _pil()
    clean = Image.frombytes(im.mode, im.size, im.tobytes())
    if im.mode == "P":
        clean.putpalette(im.getpalette())
    if im.info.get("icc_profile"):
        clean.info["icc_profile"] = im.info["icc_profile"]
    fmt, ext = _same_format(src)
    with staged(dest) as tmp:
        save_image(clean, tmp, fmt, 95 if fmt in ("JPEG", "WEBP") else None)
    return dest


def _img_transform(op: str, tag: str):
    def fn(src: Path, progress: Progress):
        im = open_image(src)
        if getattr(im, "is_animated", False):
            raise ConversionError("Animated images aren't supported for this tool.")
        Image = _pil()
        if op == "rotate":
            out = im.transpose(Image.Transpose.ROTATE_270)
            out.info = dict(im.info)
        else:  # grayscale; drop the colour profile, it no longer matches
            alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            out = im.convert("RGBA" if alpha else "RGB").convert("LA" if alpha else "L")
        fmt, ext = _same_format(src)
        dest = beside(src, ext, tag)
        with staged(dest) as tmp:
            save_image(out, tmp, fmt, 95 if fmt == "JPEG" else None)
        return dest
    return fn


def images_to_pdf(srcs: list[Path], progress: Progress):
    srcs = sorted(srcs, key=natural_key)
    pages = []
    for n, s in enumerate(srcs):
        pages.append(flatten(open_image(s)))
        progress(n / len(srcs) * 0.8)
    dest = beside(srcs[0], ".pdf", "combined")
    with staged(dest) as tmp:
        pages[0].save(tmp, "PDF", save_all=True, append_images=pages[1:], resolution=150)
    return dest


register(Action("img-compress", "Compress", "Compress", "Tools", frozenset({"image"}), img_compress, hint="smaller file"))
register(Action("img-half", "Resize to 50%", "50%", "Tools", frozenset({"image"}), _resize(factor=0.5, tag="50pct"), hint="resize"))
register(Action("img-1920", "Fit within 1920 px", "1920", "Tools", frozenset({"image"}), _resize(longest=1920, tag="1920"), hint="max px"))
register(Action("img-1080", "Fit within 1080 px", "1080", "Tools", frozenset({"image"}), _resize(longest=1080, tag="1080"), hint="max px"))
register(Action("img-strip", "Remove metadata (EXIF, GPS)", "Clean", "Tools", frozenset({"image"}), img_strip, hint="no EXIF/GPS"))
register(Action("img-rotate", "Rotate 90° clockwise", "Rotate", "Tools", frozenset({"image"}), _img_transform("rotate", "rotated"), hint="90° right"))
register(Action("img-gray", "Black & white", "B/W", "Tools", frozenset({"image"}), _img_transform("gray", "bw"), hint="grayscale"))
register(Action("img-combine", "Combine into one PDF", "1 PDF", "Tools", frozenset({"image"}), images_to_pdf, each=False, min_files=2, hint="combine"))

# ------------------------------------------------------------------------- video

H264 = ["-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart"]


def scale_short(n: int) -> str:
    """Shorter side to at most n px, keep aspect, even dimensions."""
    return (f"scale='if(gte(iw,ih),-2,trunc(min({n},iw)/2)*2)'"
            f":'if(gte(iw,ih),trunc(min({n},ih)/2)*2,-2)'")


GIF_VF = ("fps=12,scale='min(640,iw)':-2:flags=lanczos,split[a][b];"
          "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")

V = {"video"}
ffmpeg_action("vid-mp4", "Convert to MP4 (H.264)", "MP4", "Convert", V, ".mp4", H264 + ["-crf", "20", "-b:a", "192k"], hint="H.264", skip={".mp4"})
ffmpeg_action("vid-h265", "Convert to MP4 (H.265/HEVC)", "HEVC", "Convert", V, ".mp4",
              ["-c:v", "libx265", "-preset", "medium", "-crf", "26", "-tag:v", "hvc1", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"], tag="hevc", hint="H.265")
ffmpeg_action("vid-mov", "Convert to MOV", "MOV", "Convert", V, ".mov", H264 + ["-crf", "20", "-b:a", "192k"], hint="H.264", skip={".mov"})
ffmpeg_action("vid-mkv", "Convert to MKV (no re-encode)", "MKV", "Convert", V, ".mkv",
              ["-map", "0:v?", "-map", "0:a?", "-c", "copy"], hint="lossless", skip={".mkv"})
ffmpeg_action("vid-webm", "Convert to WEBM (VP9)", "WEBM", "Convert", V, ".webm",
              ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-deadline", "good",
               "-cpu-used", "4", "-c:a", "libopus", "-b:a", "128k"], hint="VP9", skip={".webm"})
ffmpeg_action("vid-gif", "Convert to GIF", "GIF", "Convert", V, ".gif", ["-vf", GIF_VF, "-loop", "0"], hint="640px")
ffmpeg_action("vid-mp3", "Extract audio as MP3", "MP3", "Convert", V, ".mp3", ["-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "2"], hint="audio")
ffmpeg_action("vid-wav", "Extract audio as WAV", "WAV", "Convert", V, ".wav", ["-vn", "-map", "0:a:0", "-c:a", "pcm_s16le"], hint="audio")
ffmpeg_action("vid-m4a", "Extract audio as M4A", "M4A", "Convert", V, ".m4a", ["-vn", "-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"], hint="audio")

ffmpeg_action("vid-compress", "Compress", "Compress", "Tools", V, ".mp4", H264 + ["-crf", "28", "-b:a", "128k"],
              tag="compressed", hint="same size", smaller=True)
ffmpeg_action("vid-small", "Compress more (720p)", "Smaller", "Tools", V, ".mp4",
              H264 + ["-crf", "30", "-vf", scale_short(720), "-b:a", "96k"], tag="small", hint="720p", smaller=True)
ffmpeg_action("vid-1080", "Downscale to 1080p", "1080p", "Tools", V, ".mp4",
              H264 + ["-crf", "22", "-vf", scale_short(1080), "-b:a", "160k"], tag="1080p", hint="resize")
ffmpeg_action("vid-720", "Downscale to 720p", "720p", "Tools", V, ".mp4",
              H264 + ["-crf", "23", "-vf", scale_short(720), "-b:a", "128k"], tag="720p", hint="resize")
ffmpeg_action("vid-mute", "Remove audio", "Mute", "Tools", V, lambda s: s.suffix.lower(),
              ["-map", "0", "-map", "-0:a", "-c", "copy"], tag="muted", hint="no sound")
ffmpeg_action("vid-rotate", "Rotate 90° clockwise", "Rotate", "Tools", V, ".mp4",
              H264 + ["-crf", "20", "-vf", "transpose=1", "-b:a", "192k"], tag="rotated", hint="90° right")
ffmpeg_action("vid-thumb", "Save a thumbnail (JPG)", "Frame", "Tools", V, ".jpg",
              ["-vf", "thumbnail=60", "-frames:v", "1", "-update", "1", "-q:v", "2"], hint="thumbnail")
ffmpeg_action("vid-strip", "Remove metadata", "Clean", "Tools", V, lambda s: s.suffix.lower(),
              ["-map", "0", "-map_metadata", "-1", "-map_chapters", "-1", "-c", "copy"], tag="clean", hint="no GPS/dates")

# ------------------------------------------------------------------------- audio

AUDIO_CODEC = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
    ".m4a": ["-c:a", "aac", "-b:a", "256k"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "6"],
    ".opus": ["-c:a", "libopus", "-b:a", "128k"],
    ".aiff": ["-c:a", "pcm_s16be"],
}
A = {"audio"}
for _ext, _codec in AUDIO_CODEC.items():
    _t = _ext[1:].upper()
    ffmpeg_action(f"aud-{_ext[1:]}", f"Convert to {_t}", _t, "Convert", A, _ext,
                  ["-vn", "-map", "0:a:0", *_codec], skip={_ext, ".aif"} if _ext == ".aiff" else {_ext},
                  hint={"MP3": "V0", "FLAC": "lossless", "WAV": "16-bit", "M4A": "AAC"}.get(_t, ""))


def _keep_ext(src: Path) -> str:
    return src.suffix.lower() if src.suffix.lower() in AUDIO_CODEC else ".mp3"


ffmpeg_action("aud-normalize", "Normalize loudness", "Level", "Tools", A, _keep_ext,
              lambda s: ["-vn", "-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000",
                         *AUDIO_CODEC[_keep_ext(s)]], tag="normalized", hint="-16 LUFS")
ffmpeg_action("aud-compress", "Compress (MP3 128k)", "Compress", "Tools", A, ".mp3",
              ["-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "128k"], tag="compressed", hint="128 kbps", smaller=True)
ffmpeg_action("aud-strip", "Remove metadata", "Clean", "Tools", A, lambda s: s.suffix.lower(),
              ["-map", "0:a", "-map_metadata", "-1", "-c", "copy"], tag="clean", hint="no tags")

# --------------------------------------------------------------------------- PDF


def _fitz():
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    return pymupdf


def _open_pdf(src: Path):
    fz = _fitz()
    try:
        doc = fz.open(src)
    except Exception as e:
        raise ConversionError(f"Can't open this PDF ({e}).")
    if doc.needs_pass:
        raise ConversionError("This PDF is password-protected.")
    return doc


def _pdf_images(fmt: str):
    def fn(src: Path, progress: Progress):
        doc = _open_pdf(src)
        ext = ".png" if fmt == "png" else ".jpg"
        kw = {} if fmt == "png" else {"jpg_quality": 90}
        if doc.page_count == 1:
            dest = beside(src, ext)
            with staged(dest) as tmp:
                doc[0].get_pixmap(dpi=150).save(str(tmp), **kw)
            return dest
        dest = beside(src, "", "pages")
        with staged_dir(dest) as tmp:
            width = max(3, len(str(doc.page_count)))
            for i, page in enumerate(doc):
                page.get_pixmap(dpi=150).save(str(tmp / f"{_stem(src)}-{i + 1:0{width}}{ext}"), **kw)
                progress((i + 1) / doc.page_count)
        return dest
    return fn


def pdf_txt(src: Path, progress: Progress):
    doc = _open_pdf(src)
    parts = []
    for i, page in enumerate(doc):
        parts.append(page.get_text())
        progress((i + 1) / doc.page_count)
    text = "\n\n".join(parts)
    if not text.strip():
        raise ConversionError("No text found - this PDF is probably scanned images.")
    dest = beside(src, ".txt")
    with staged(dest) as tmp:
        tmp.write_text(text, encoding="utf-8")
    return dest


def pdf_docx(src: Path, progress: Progress):
    _open_pdf(src).close()
    try:
        from pdf2docx import Converter
    except ImportError:
        raise ConversionError("PDF to Word needs pdf2docx: pip install pdf2docx")
    dest = beside(src, ".docx")
    with staged(dest) as tmp:
        cv = Converter(str(src))
        try:
            cv.convert(str(tmp))
        finally:
            cv.close()
    return dest


def _save_pdf(doc, tmp: Path):
    try:
        doc.save(str(tmp), garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, clean=True, use_objstms=1)
    except TypeError:
        doc.save(str(tmp), garbage=4, deflate=True, clean=True)


def pdf_compress(src: Path, progress: Progress):
    doc = _open_pdf(src)
    if hasattr(doc, "rewrite_images"):
        progress(0.2)
        doc.rewrite_images(dpi_threshold=160, dpi_target=120, quality=70)
    try:
        doc.scrub(metadata=False, xml_metadata=True, thumbnails=True, reset_fields=False,
                  remove_links=False, reset_responses=False, attached_files=False, embedded_files=False,
                  hidden_text=False, javascript=False, redactions=False)
    except Exception:
        pass
    progress(0.7)
    dest = beside(src, ".pdf", "compressed")
    with staged(dest) as tmp:
        _save_pdf(doc, tmp)
    return must_be_smaller(src, dest)


def pdf_strip(src: Path, progress: Progress):
    doc = _open_pdf(src)
    doc.set_metadata({})
    try:
        doc.del_xml_metadata()
    except Exception:
        pass
    dest = beside(src, ".pdf", "clean")
    with staged(dest) as tmp:
        _save_pdf(doc, tmp)
    return dest


def pdf_rotate(src: Path, progress: Progress):
    doc = _open_pdf(src)
    for page in doc:
        page.set_rotation((page.rotation + 90) % 360)
    dest = beside(src, ".pdf", "rotated")
    with staged(dest) as tmp:
        _save_pdf(doc, tmp)
    return dest


def pdf_split(src: Path, progress: Progress):
    doc = _open_pdf(src)
    if doc.page_count < 2:
        raise ConversionError("Only one page - nothing to split.")
    fz = _fitz()
    dest = beside(src, "", "split")
    width = max(3, len(str(doc.page_count)))
    with staged_dir(dest) as tmp:
        for i in range(doc.page_count):
            one = fz.open()
            one.insert_pdf(doc, from_page=i, to_page=i)
            one.save(str(tmp / f"{_stem(src)}-{i + 1:0{width}}.pdf"), garbage=3, deflate=True)
            progress((i + 1) / doc.page_count)
    return dest


def pdf_merge(srcs: list[Path], progress: Progress):
    fz = _fitz()
    srcs = sorted(srcs, key=natural_key)
    out = fz.open()
    for n, s in enumerate(srcs):
        if kind_of(s) == "pdf":
            out.insert_pdf(_open_pdf(s))
        else:
            buf = io.BytesIO()
            flatten(open_image(s)).save(buf, "PDF", resolution=150)
            out.insert_pdf(fz.open("pdf", buf.getvalue()))
        progress((n + 1) / len(srcs) * 0.9)
    dest = beside(srcs[0], ".pdf", "merged")
    with staged(dest) as tmp:
        _save_pdf(out, tmp)
    return dest


P = frozenset({"pdf"})
register(Action("pdf-png", "Pages to PNG", "PNG", "Convert", P, _pdf_images("png"), hint="150 dpi"))
register(Action("pdf-jpg", "Pages to JPG", "JPG", "Convert", P, _pdf_images("jpg"), hint="150 dpi"))
register(Action("pdf-docx", "Convert to Word (DOCX)", "DOCX", "Convert", P, pdf_docx, hint="Word"))
register(Action("pdf-txt", "Extract text (TXT)", "TXT", "Convert", P, pdf_txt, hint="text"))
register(Action("pdf-compress", "Compress", "Compress", "Tools", P, pdf_compress, hint="smaller file"))
register(Action("pdf-merge", "Merge into one PDF", "Merge", "Tools", frozenset({"pdf", "image"}), pdf_merge, each=False, min_files=2, hint="by name"))
register(Action("pdf-split", "Split into single pages", "Split", "Tools", P, pdf_split, hint="1 per page"))
register(Action("pdf-rotate", "Rotate all pages 90°", "Rotate", "Tools", P, pdf_rotate, hint="90° right"))
register(Action("pdf-strip", "Remove metadata", "Clean", "Tools", P, pdf_strip, hint="author etc."))

# ------------------------------------------------------------------ Office docs


def _soffice() -> str | None:
    for c in (shutil.which("soffice"), r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if c and Path(c).is_file():
            return c
    return None


def _libreoffice(src: Path, target: str, dest: Path, timeout=600):
    so = _soffice()
    with tempfile.TemporaryDirectory(prefix="satsuma-lo-") as td:
        profile = (Path(td) / "profile").as_uri()
        subprocess.run([so, "--headless", "--norestore", f"-env:UserInstallation={profile}",
                        "--convert-to", target, "--outdir", td, str(src)],
                       capture_output=True, creationflags=NOWIN, timeout=timeout)
        produced = Path(td) / f"{src.stem}.{target.split(':')[0]}"
        if not produced.exists():
            raise ConversionError("LibreOffice couldn't convert this file.")
        with staged(dest) as tmp:
            shutil.move(str(produced), tmp)
    return dest


def _office_com(src: Path, dest: Path):
    try:
        import pythoncom
        import win32com.client as wc
    except ImportError:
        raise ConversionError("Needs Microsoft Office or LibreOffice installed.")
    pythoncom.CoInitialize()
    s, k = str(src.resolve()), kind_of(src)
    with staged(dest) as tmp:
        d = str(tmp.resolve())
        try:
            if k == "word":
                app = wc.DispatchEx("Word.Application")
                app.Visible, app.DisplayAlerts = False, 0
                try:
                    doc = app.Documents.Open(s, ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False)
                    doc.ExportAsFixedFormat(d, 17)
                    doc.Close(False)
                finally:
                    app.Quit()
            elif k == "slides":
                app = wc.DispatchEx("PowerPoint.Application")
                try:
                    pres = app.Presentations.Open(s, True, False, False)  # ReadOnly, Untitled, WithWindow
                    pres.SaveAs(d, 32)
                    pres.Close()
                finally:
                    app.Quit()
            else:
                app = wc.DispatchEx("Excel.Application")
                app.Visible, app.DisplayAlerts = False, False
                try:
                    wb = app.Workbooks.Open(s, ReadOnly=True)
                    wb.ExportAsFixedFormat(0, d)
                    wb.Close(False)
                finally:
                    app.Quit()
        except pythoncom.com_error as e:
            raise ConversionError(f"Microsoft Office couldn't convert it ({e.args[1] if len(e.args) > 1 else e}).")
    return dest


def office_pdf(src: Path, progress: Progress):
    dest = beside(src, ".pdf")
    if _soffice():
        return _libreoffice(src, "pdf", dest)
    if os.name == "nt":
        return _office_com(src, dest)
    raise ConversionError("Needs Microsoft Office or LibreOffice installed.")


def sheet_csv(src: Path, progress: Progress):
    if src.suffix.lower() != ".xlsx":
        if not _soffice():
            raise ConversionError("Only .xlsx works without LibreOffice installed.")
        return _libreoffice(src, "csv:Text - txt - csv (StarCalc):44,34,76", beside(src, ".csv"))
    from openpyxl import load_workbook
    wb = load_workbook(src, read_only=True, data_only=True)
    outs = []
    sheets = wb.worksheets
    for n, ws in enumerate(sheets):
        tag = "" if len(sheets) == 1 else re.sub(r'[<>:"/\\|?*]', "_", ws.title)
        dest = beside(src, ".csv", tag)
        with staged(dest) as tmp, open(tmp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            for row in ws.iter_rows(values_only=True):
                w.writerow(["" if v is None else v for v in row])
        outs.append(dest)
        progress((n + 1) / len(sheets))
    return outs


def _read_csv(src: Path) -> list[list[str]]:
    raw = src.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.reader(io.StringIO(text), dialect))


_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def csv_xlsx(src: Path, progress: Progress):
    from openpyxl import Workbook
    rows = _read_csv(src)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(_stem(src)[:31] or "Sheet1")
    for r in rows:
        ws.append([(float(v) if "." in v else int(v)) if _NUM.match(v) and len(v) < 16 and not (len(v) > 1 and v[0] == "0" and v[1] != ".") else v for v in r])
    dest = beside(src, ".xlsx")
    with staged(dest) as tmp:
        wb.save(tmp)
    return dest


def csv_json(src: Path, progress: Progress):
    rows = _read_csv(src)
    if not rows:
        raise ConversionError("The CSV is empty.")
    head = rows[0]
    data = [dict(zip(head, r)) for r in rows[1:]]
    dest = beside(src, ".json")
    with staged(dest) as tmp:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def json_csv(src: Path, progress: Progress):
    try:
        data = json.loads(src.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise ConversionError(f"Invalid JSON ({e}).")
    if isinstance(data, dict):
        data = next((v for v in data.values() if isinstance(v, list)), [data])
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise ConversionError("JSON must be a list of objects to become a table.")
    keys: list[str] = []
    for r in data:
        keys += [k for k in r if k not in keys]
    dest = beside(src, ".csv")
    with staged(dest) as tmp, open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(keys)
        for r in data:
            w.writerow([json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else ("" if v is None else v)
                        for v in (r.get(k) for k in keys)])
    return dest


register(Action("doc-pdf", "Convert to PDF", "PDF", "Convert", frozenset({"word", "slides", "sheet"}), office_pdf, hint="via Office"))
register(Action("sheet-csv", "Convert to CSV", "CSV", "Convert", frozenset({"sheet"}), sheet_csv, hint="per sheet"))
register(Action("csv-xlsx", "Convert to Excel (XLSX)", "XLSX", "Convert", frozenset({"csv"}), csv_xlsx, hint="Excel"))
register(Action("csv-json", "Convert to JSON", "JSON", "Convert", frozenset({"csv"}), csv_json))
register(Action("json-csv", "Convert to CSV", "CSV", "Convert", frozenset({"json"}), json_csv, hint="table"))

# ---------------------------------------------------------------------- archives


def _walk(paths: list[Path]):
    for p in paths:
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                yield f, f.relative_to(p.parent)
        else:
            yield p, Path(p.name)


def _archive_dest(srcs: list[Path], ext: str) -> Path:
    if len(srcs) == 1:
        return beside(srcs[0], ext)
    return unique(srcs[0].parent / f"{srcs[0].parent.name or 'Archive'}{ext}")


def make_zip(srcs: list[Path], progress: Progress):
    items = list(_walk(srcs))
    total = sum(f.stat().st_size for f, _ in items if f.is_file()) or 1
    done = 0
    dest = _archive_dest(srcs, ".zip")
    with staged(dest) as tmp, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f, arc in items:
            z.write(f, arc.as_posix())
            if f.is_file():
                done += f.stat().st_size
                progress(done / total)
    return dest


def make_7z(srcs: list[Path], progress: Progress):
    try:
        import py7zr
    except ImportError:
        raise ConversionError("7z needs py7zr: pip install py7zr")
    dest = _archive_dest(srcs, ".7z")
    items = list(_walk(srcs))
    with staged(dest) as tmp, py7zr.SevenZipFile(tmp, "w") as z:
        for n, (f, arc) in enumerate(items):
            z.write(f, arc.as_posix())
            progress((n + 1) / len(items))
    return dest


def extract(src: Path, progress: Progress):
    low = src.name.lower()
    target = unique(src.parent / _stem(src))
    with staged_dir(target) as tmp:
        if low.endswith(".zip"):
            with zipfile.ZipFile(src) as z:
                z.extractall(tmp)
        elif low.endswith(".7z"):
            import py7zr
            with py7zr.SevenZipFile(src) as z:
                z.extractall(tmp)
        elif tarfile.is_tarfile(src):
            with tarfile.open(src) as t:
                try:
                    t.extractall(tmp, filter="data")
                except TypeError:
                    t.extractall(tmp)
        elif low.endswith((".gz", ".bz2", ".xz")):
            import bz2, gzip, lzma
            opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[src.suffix.lower()]
            with opener(src, "rb") as fin, open(tmp / src.stem, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        else:  # .rar and anything else: 7-Zip if installed, else Windows' built-in tar (libarchive)
            seven = next((c for c in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe") if c and Path(c).is_file()), None)
            cmd = [seven, "x", "-y", f"-o{tmp}", str(src)] if seven else ["tar", "-xf", str(src), "-C", str(tmp)]
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN)
            if r.returncode != 0:
                raise ConversionError("Couldn't extract this archive - install 7-Zip for RAR support.")
        progress(0.95)
    # one folder inside? lift it up so you don't get name/name/
    inner = list(target.iterdir())
    if len(inner) == 1 and inner[0].is_dir():
        name = inner[0].name
        tmpname = unique(target.with_name(f"~{name}.lift"))
        os.replace(inner[0], tmpname)
        target.rmdir()
        final = unique(src.parent / name)
        os.replace(tmpname, final)
        return final
    return target


register(Action("arc-extract", "Extract here", "Extract", "Tools", frozenset({"archive"}), extract, hint="unpack"))
register(Action("arc-zip", "Compress to ZIP", "ZIP", "Tools", ANY, make_zip, each=False, hint="archive"))
register(Action("arc-7z", "Compress to 7Z", "7Z", "Tools", ANY, make_7z, each=False, hint="smaller"))


# --------------------------------------------------------------------- child CLI

def _std_handle_writer():
    """In the windowed .exe sys.stdout is None, but the parent still gave us a pipe."""
    try:
        return os.fdopen(1, "w", encoding="utf-8", buffering=1)
    except OSError:
        pass
    if os.name == "nt":
        try:
            import ctypes
            import msvcrt
            h = ctypes.windll.kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            if h and h != -1:
                return open(msvcrt.open_osfhandle(h, os.O_WRONLY), "w", encoding="utf-8", buffering=1)
        except OSError:
            pass
    return open(os.devnull, "w")


def run_cli(action_id: str, args: list[str]) -> int:
    """args: [params-json] file [file ...]. The JSON is optional for older callers."""
    out = sys.stdout if sys.stdout is not None else _std_handle_writer()

    def say(tag: str, text: str):
        out.write(f"{tag} {text.replace(chr(10), ' ').replace(chr(13), ' ')}\n")
        out.flush()

    last = [-1.0]

    def progress(frac: float):
        if frac - last[0] >= 0.01 or frac >= 0.99:
            last[0] = frac
            say("P", f"{max(0.0, min(frac, 1.0)):.3f}")

    params: dict = {}
    if args and args[0].startswith("{"):
        params = json.loads(args[0])
        args = args[1:]
    from presets import load_presets
    load_presets()
    act = BY_ID.get(action_id)
    if not act:
        say("E", f"Unknown action {action_id}")
        return 2
    files = [Path(p).resolve() for p in args]
    missing = [f for f in files if not f.exists()]
    if missing:
        say("E", f"File not found: {missing[0].name}")
        return 2
    try:
        target = files[0] if act.each else files
        res = act.fn(target, progress, params) if (act.params or act.editor) else act.fn(target, progress)
        undo = None
        if isinstance(res, tuple):  # (outputs, undo-record)
            res, undo = res
        for r in (res if isinstance(res, list) else [res]):
            say("O", str(r))
        if undo is not None:
            say("U", json.dumps(undo))
        return 0
    except ConversionError as e:
        say("E", str(e))
        return 1
    except Exception as e:
        traceback.print_exc()
        say("E", f"{e.__class__.__name__}: {e}")
        return 1


# extra features live in their own modules; importing them registers their actions
import features  # noqa: E402,F401
import tools2  # noqa: E402,F401

if __name__ == "__main__":
    import engine as _engine  # use the module that features/presets registered into
    sys.exit(_engine.run_cli(sys.argv[1], sys.argv[2:]))
