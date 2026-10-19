# PyInstaller spec - builds dist/Satsuma/Satsuma.exe (one folder, windowed).
# Run from the repo root:  pyinstaller packaging/satsuma.spec --noconfirm
# The AI libraries are NOT bundled - they ship separately as Satsuma-AI-pack.zip (see aipack.py).
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = SPECPATH + "/.."

datas = [(ROOT + "/assets", "assets"), (ROOT + "/models", "models")]
binaries = []
hiddenimports = ["engine", "features", "tools2", "presets", "aipack", "hook", "integrate", "dialogs", "editors",
                 "phone", "updater", "settingswin", "watch", "textdrag", "inject", "version",
                 "win32com.client", "pythoncom", "send2trash", "qrcode", "qrcode.image.pil", "cv2"]
hiddenimports += collect_submodules("pynput")

for pkg in ("pymupdf", "fitz", "pdf2docx", "pillow_heif", "py7zr", "winrt"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:  # optional package missing
        print(f"skip {pkg}: {e}")

AI = ["onnxruntime", "faster_whisper", "ctranslate2", "rapidocr_onnxruntime", "tokenizers", "av",
      "huggingface_hub", "torch", "tensorflow", "onnx"]

a = Analysis(
    [ROOT + "/app.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["imageio_ffmpeg", "tkinter", "matplotlib", "IPython", "pytest", *AI],  # FFmpeg ships in bin/
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Satsuma", console=False,
          icon=ROOT + "/assets/icon.ico", version=None, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Satsuma", upx=False)
