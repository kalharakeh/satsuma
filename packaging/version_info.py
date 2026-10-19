"""Writes packaging/file_version_info.txt (Windows version resource for Satsuma.exe) from version.py."""
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent
ver = re.search(r'__version__ = "([^"]+)"', (root / "version.py").read_text()).group(1)
nums = [int(x) for x in re.findall(r"\d+", ver.split("-")[0])][:3] + [0, 0, 0, 0]
t = tuple(nums[:4])
text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Kassem Alharakeh'),
      StringStruct('FileDescription', 'Satsuma - offline file converter'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'Satsuma'),
      StringStruct('LegalCopyright', 'AGPL-3.0'),
      StringStruct('OriginalFilename', 'Satsuma.exe'),
      StringStruct('ProductName', 'Satsuma'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
(root / "packaging" / "file_version_info.txt").write_text(text, encoding="utf-8")
print(f"version resource: {ver} -> {t}")
sys.exit(0)
