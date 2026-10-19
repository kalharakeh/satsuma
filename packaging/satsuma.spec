# PyInstaller spec - builds dist/Satsuma/Satsuma.exe (one folder, windowed).
# Run from the repo root:  pyinstaller packaging/satsuma.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = SPECPATH + "/.."

datas = [(ROOT + "/assets", "assets")]
binaries = []
hiddenimports = ["engine", "features", "presets", "hook", "integrate", "dialogs", "watch", "version",
                 "win32com.client", "pythoncom", "send2trash"]
hiddenimports += collect_submodules("pynput")

for pkg in ("pymupdf", "fitz", "faster_whisper", "ctranslate2", "onnxruntime", "rapidocr_onnxruntime", "pdf2docx",
            "pillow_heif", "py7zr", "winrt", "tokenizers"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # optional package missing
        print(f"skip {pkg}: {e}")

a = Analysis(
    [ROOT + "/app.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["imageio_ffmpeg", "tkinter", "matplotlib", "IPython", "pytest"],  # FFmpeg ships in bin/
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Satsuma", console=False,
          icon=ROOT + "/assets/icon.ico", version=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Satsuma", upx=False)
