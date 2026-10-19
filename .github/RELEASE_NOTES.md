### Download

**[Satsuma-Setup.exe](https://github.com/kalharakeh/satsuma/releases/latest/download/Satsuma-Setup.exe)** is the installer, and no admin rights are needed. If you already have 1.1, Satsuma offers this update in the tray.
`Satsuma-portable.zip` is the same app without an installer: unzip it and run `Satsuma.exe --install` once. `Satsuma-AI-pack.zip` is fetched automatically, so you don't need to download it yourself.

> Windows SmartScreen may warn because the app isn't code-signed yet: click **More info → Run anyway**.

### New in 1.1.1

- **Welcome tour** on first start: how Shift-drag works for files and text, practice files to try it on, and the main settings. You can reopen it from the tray.
- **"What's new"** notification after each update.
- **Updates no longer re-download the AI pack** unless the AI libraries actually changed.
- **Verified downloads:** the AI pack and every AI model are checked against a SHA-256 checksum. Models come from Satsuma's own mirror first, with the original projects as fallback.
- Dropped connections during big downloads are detected and retried.

See [1.1.0](https://github.com/kalharakeh/satsuma/releases/tag/v1.1.0) for the big feature release: fit under X MB, text tools, send to phone, privacy blur, PDF pages/sign/watermark/tables, video extras, AI upscale, settings window and auto-update.
