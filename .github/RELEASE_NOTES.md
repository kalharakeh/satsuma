### Download

**[Satsuma-Setup.exe](https://github.com/kalharakeh/satsuma/releases/latest/download/Satsuma-Setup.exe)** is the installer, and no admin rights are needed. If you have 1.1 or later, Satsuma offers this update in the tray.
`Satsuma-portable.zip` is the same app without an installer: unzip it and run `Satsuma.exe --install` once. `Satsuma-AI-pack.zip` is fetched automatically, so you don't need to download it yourself.

> Windows SmartScreen may warn because the app isn't code-signed yet: click **More info → Run anyway**.

### Fixed in 1.1.3

- **Updating from inside Satsuma works again.** In 1.1.2 the installer could close itself along with the old Satsuma, so the app quit and the old version stayed. The installer now only closes Satsuma, and Satsuma always finishes closing within a few seconds. Every build now tests a real in-app update on Windows before it can be released.

### New in 1.1.2

- **Updates you can see:** a progress card shows the download and install. Satsuma then confirms "✓ Updated to 1.x", or tells you clearly if an update didn't finish.
- **Safer updates:** the downloaded installer is checked against GitHub's SHA-256 before it runs. The installer waits for Satsuma to close properly, and every step is logged.
- **Report a problem** (tray) and **Copy diagnostic info** (Settings → About): copies version, Windows version and recent log lines, with your user name removed, for a bug report. Nothing is sent automatically.
- **Privacy policy:** shown in the installer, and in [PRIVACY.md](https://github.com/kalharakeh/satsuma/blob/main/PRIVACY.md). Every network feature can be switched off.
- `Satsuma.exe` now carries proper product name and version details.
