"""End-to-end smoke test: makes sample files and runs real conversions.

    python tests/smoke_test.py                 # through the source (engine.py)
    python tests/smoke_test.py --exe PATH      # through a built Satsuma.exe (--run mode)
    python tests/smoke_test.py --ai            # also background removal (downloads a model once)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def make_samples(d: Path):
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont
    import engine
    im = Image.new("RGB", (1600, 1000), "white")
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 64)
    except OSError:
        font = ImageFont.load_default(size=64)
    dr.text((80, 120), "Invoice number 4471", font=font, fill="black")
    dr.text((80, 260), "Total due 1250 SEK", font=font, fill="black")
    dr.ellipse([1000, 500, 1400, 900], fill=(255, 120, 20))
    im.save(d / "sample.png")
    im.save(d / "photo.jpg", quality=90)
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 72), f"Page {i + 1}")
        p.insert_image(pymupdf.Rect(72, 100, 500, 400), filename=str(d / "sample.png"))
    doc.save(d / "doc.pdf")
    ff = engine.ffmpeg_exe()
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=f=440", "-t", "3", "-c:v", "libx264", "-c:a", "aac",
                    str(d / "clip.mp4")], check=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440", "-t", "3",
                    str(d / "tone.wav")], check=True)
    (d / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    for n in ("b.txt", "a.txt"):
        (d / "ren").mkdir(exist_ok=True)
        (d / "ren" / n).write_text(n)


CASES = [
    ("img-jpg", ["sample.png"], None), ("img-webp", ["photo.jpg"], None), ("img-heic", ["photo.jpg"], None),
    ("img-avif", ["photo.jpg"], None), ("img-ico", ["sample.png"], None), ("img-compress", ["sample.png"], None),
    ("img-strip", ["photo.jpg"], None), ("img-half", ["photo.jpg"], None), ("img-ocr", ["sample.png"], None),
    ("img-combine", ["sample.png", "photo.jpg"], None),
    ("vid-mp4", ["clip.mp4"], "skip"), ("vid-webm", ["clip.mp4"], None), ("vid-h265", ["clip.mp4"], None),
    ("vid-gif", ["clip.mp4"], None), ("vid-mp3", ["clip.mp4"], None), ("vid-720", ["clip.mp4"], None),
    ("vid-trim", ["clip.mp4"], {"start": "0:01", "end": "0:02"}),
    ("aud-flac", ["tone.wav"], None), ("aud-opus", ["tone.wav"], None), ("aud-normalize", ["tone.wav"], None),
    ("pdf-png", ["doc.pdf"], None), ("pdf-compress", ["doc.pdf"], "maybe"), ("pdf-split", ["doc.pdf"], None),
    ("pdf-docx", ["doc.pdf"], None), ("pdf-ocr", ["doc.pdf"], None),
    ("pdf-lock", ["doc.pdf"], {"password": "secret1", "confirm": "secret1"}),
    ("csv-xlsx", ["data.csv"], None), ("arc-zip", ["sample.png", "data.csv"], None),
    ("rename", ["ren/a.txt", "ren/b.txt"], {"pattern": "File {n}", "start": "1"}),
]


def run(cmd_base: list[str], action: str, files: list[Path], params) -> tuple[bool, str]:
    args = [*cmd_base, action]
    if isinstance(params, dict):
        args.append(json.dumps(params))
    r = subprocess.run([*args, *map(str, files)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    lines = [l for l in r.stdout.splitlines() if l[:2] in ("O ", "E ")]
    outs = [l[2:] for l in lines if l.startswith("O ")]
    ok = r.returncode == 0 and outs and all(Path(o).exists() for o in outs)
    if not ok and params == "maybe" and "wasn't smaller" in r.stdout:
        ok = True
    return bool(ok), " | ".join(lines) or r.stderr[-500:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe")
    ap.add_argument("--ai", action="store_true")
    a = ap.parse_args()
    base = [a.exe, "--run"] if a.exe else [sys.executable, str(ROOT / "engine.py")]
    cases = list(CASES)
    if a.ai:
        cases.append(("img-cutout", ["sample.png"], None))
    failed = 0
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        make_samples(d)
        for action, files, params in cases:
            if params == "skip":
                continue
            ok, info = run(base, action, [d / f for f in files], params)
            print(f"{'PASS' if ok else 'FAIL'}  {action:14} {info[:160]}")
            failed += not ok
        try:
            import features
            win = features._ocr_windows(d / "sample.png")
            print(f"INFO  Windows OCR: {'available' if win else 'not available (fallback OCR used)'}")
        except Exception as e:
            print(f"INFO  Windows OCR check failed: {e}")
    print(f"\n{len([c for c in cases if c[2] != 'skip']) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
