"""Your own one-click tiles, defined in %APPDATA%\\Satsuma\\presets.json.

Image preset keys:  format (jpg|png|webp|avif|heic), quality (1-100), max_px, strip (bool)
Video/audio preset keys:  ext (".mp4"), ffmpeg ([... FFmpeg output arguments ...])
Common keys:  name, tile (short text on the tile), hint, for (image|video|audio), tag (added to the file name)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from engine import BY_ID, Action, ConversionError, beside, open_image, register, run_ffmpeg, save_image, staged

FORMATS = {"jpg": ("JPEG", ".jpg"), "jpeg": ("JPEG", ".jpg"), "png": ("PNG", ".png"),
           "webp": ("WEBP", ".webp"), "avif": ("AVIF", ".avif"), "heic": ("HEIF", ".heic")}

DEFAULT_PRESETS = [
    {"name": "Web image: WEBP, max 1600 px", "tile": "Web", "hint": "1600 webp", "for": "image",
     "format": "webp", "quality": 80, "max_px": 1600, "strip": True, "tag": "web"},
    {"name": "Email photo: JPG, max 1280 px", "tile": "Email", "hint": "1280 jpg", "for": "image",
     "format": "jpg", "quality": 75, "max_px": 1280, "strip": True, "tag": "email"},
    {"name": "WhatsApp video: 720p MP4", "tile": "Share", "hint": "720p small", "for": "video", "ext": ".mp4",
     "tag": "share",
     "ffmpeg": ["-c:v", "libx264", "-preset", "medium", "-crf", "28", "-pix_fmt", "yuv420p",
                "-vf", "scale='if(gte(iw,ih),-2,min(720,iw))':'if(gte(iw,ih),min(720,ih),-2)'",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart"]},
    {"name": "Voice note: MP3 mono 64k", "tile": "Voice", "hint": "mono 64k", "for": "audio", "ext": ".mp3",
     "tag": "voice", "ffmpeg": ["-vn", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k"]},
]


def presets_path() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Satsuma" / "presets.json"


def ensure_presets_file() -> Path:
    p = presets_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"presets": DEFAULT_PRESETS}, indent=2), encoding="utf-8")
    return p


def _image_fn(cfg: dict):
    fmt, ext = FORMATS.get(str(cfg.get("format", "jpg")).lower(), FORMATS["jpg"])

    def fn(src: Path, progress):
        from PIL import Image
        im = open_image(src)
        if getattr(im, "is_animated", False):
            im.seek(0)
            im = im.copy()
        mx = int(cfg.get("max_px") or 0)
        if mx and max(im.size) > mx:
            s = mx / max(im.size)
            im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.Resampling.LANCZOS)
        if cfg.get("strip"):
            icc = im.info.get("icc_profile")
            im.info = {"icc_profile": icc} if icc else {}
        dest = beside(src, ext, cfg.get("tag", "preset"))
        with staged(dest) as tmp:
            save_image(im, tmp, fmt, int(cfg["quality"]) if cfg.get("quality") else None)
        return dest
    return fn


def _ffmpeg_fn(cfg: dict):
    args = [str(a) for a in cfg.get("ffmpeg", [])]

    def fn(src: Path, progress):
        if not args:
            raise ConversionError("This preset has no 'ffmpeg' arguments.")
        return run_ffmpeg(src, beside(src, cfg.get("ext", src.suffix), cfg.get("tag", "preset")), args, progress)
    return fn


_loaded = False


def load_presets(force: bool = False) -> list[str]:
    """Register presets as actions. Returns a list of problems (shown in the log)."""
    global _loaded
    if _loaded and not force:
        return []
    _loaded = True
    from engine import ACTIONS
    ACTIONS[:] = [a for a in ACTIONS if a.group != "Presets"]
    for k in [k for k in BY_ID if k.startswith("preset-")]:
        del BY_ID[k]
    p = presets_path()
    if not p.exists():
        return []
    try:
        items = json.loads(p.read_text(encoding="utf-8-sig")).get("presets", [])
    except (OSError, ValueError, AttributeError) as e:
        return [f"presets.json can't be read: {e}"]
    problems = []
    for i, cfg in enumerate(items):
        try:
            kind = cfg.get("for", "image")
            fn = _image_fn(cfg) if kind == "image" else _ffmpeg_fn(cfg)
            register(Action(f"preset-{i}", cfg.get("name") or f"Preset {i + 1}", str(cfg.get("tile") or "★")[:9],
                            "Presets", frozenset({kind}), fn, hint=str(cfg.get("hint", ""))[:14], prio=i))
        except Exception as e:
            problems.append(f"preset {i + 1}: {e}")
    return problems
