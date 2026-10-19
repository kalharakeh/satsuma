"""Writes the three winget manifests for a release.

    python packaging/winget/make_manifests.py 1.1.1 <sha256 of Satsuma-Setup.exe> [out_dir]

The first version is submitted by hand (see packaging/winget/README.md); later versions are
submitted by the release build automatically once a WINGET_TOKEN secret exists.
"""
import datetime
import pathlib
import sys

ID = "KassemAlharakeh.Satsuma"
SCHEMA = "1.6.0"


def write(version: str, sha: str, out: pathlib.Path) -> pathlib.Path:
    folder = out / "manifests" / "k" / "KassemAlharakeh" / "Satsuma" / version
    folder.mkdir(parents=True, exist_ok=True)
    head = f"PackageIdentifier: {ID}\nPackageVersion: {version}\n"
    (folder / f"{ID}.yaml").write_text(
        head + f"DefaultLocale: en-US\nManifestType: version\nManifestVersion: {SCHEMA}\n", encoding="utf-8")
    (folder / f"{ID}.installer.yaml").write_text(head + f"""InstallerLocale: en-US
InstallerType: inno
Scope: user
InstallModes:
- interactive
- silent
- silentWithProgress
UpgradeBehavior: install
ProductCode: '{{6A4F2C1E-8B1D-4E5A-9C3F-5A7E0D2B9F41}}_is1'
ReleaseDate: {datetime.date.today().isoformat()}
Installers:
- Architecture: x64
  InstallerUrl: https://github.com/kalharakeh/satsuma/releases/download/v{version}/Satsuma-Setup.exe
  InstallerSha256: {sha.upper()}
ManifestType: installer
ManifestVersion: {SCHEMA}
""", encoding="utf-8")
    (folder / f"{ID}.locale.en-US.yaml").write_text(head + f"""PackageLocale: en-US
Publisher: Kassem Alharakeh
PublisherUrl: https://github.com/kalharakeh
PublisherSupportUrl: https://github.com/kalharakeh/satsuma/issues
PrivacyUrl: https://github.com/kalharakeh/satsuma/blob/main/PRIVACY.md
Author: Kassem Alharakeh
PackageName: Satsuma
PackageUrl: https://github.com/kalharakeh/satsuma
License: AGPL-3.0
LicenseUrl: https://github.com/kalharakeh/satsuma/blob/main/LICENSE
ShortDescription: Offline file converter - hold Shift, drag any file, drop it on a format.
Description: |-
  Satsuma converts files where they already are. Hold Shift while dragging a file in Explorer and a panel of
  formats appears under the cursor; the converted copy is saved next to the original. It handles images (HEIC,
  WEBP, AVIF, PNG, JPG...), video, audio, PDF and Office files, and has tools to compress, fit under a size,
  trim, remove backgrounds, upscale, blur faces, OCR, transcribe speech, sign and edit PDFs. Everything runs
  offline on your PC.
Moniker: satsuma
Tags:
- converter
- file-converter
- image-converter
- video-converter
- pdf
- heic
- offline
- drag-and-drop
- ocr
- compress
ReleaseNotesUrl: https://github.com/kalharakeh/satsuma/releases/tag/v{version}
ManifestType: defaultLocale
ManifestVersion: {SCHEMA}
""", encoding="utf-8")
    return folder


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else pathlib.Path(__file__).resolve().parent
    print(write(sys.argv[1], sys.argv[2], out))
