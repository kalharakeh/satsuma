# Privacy policy

Satsuma runs entirely on your PC. It has no account, no analytics, no telemetry and no ads,
and it never uploads your files.

## What Satsuma sends over the network, and when

| What | When | Where to | How to turn it off |
|---|---|---|---|
| Update check (a request for the latest version number) | 15 s after start, then once a day | api.github.com | Settings → General → *Check for updates once a day* |
| Downloading an update | Only when you click *Update* | github.com | Don't click it |
| AI pack and AI models (one-time downloads) | Only the first time you use an AI tool (background removal, upscale, subtitles), or when you choose *Download AI pack now* | github.com (Satsuma's own releases); as a fallback, the original projects on github.com and huggingface.co | Don't use the AI tools |
| Translate | Only when you drop text on *Translate* | Opens translate.google.com in your browser, with the text you chose | Don't use Translate |
| Send to phone | Only while the *Send to phone* window is open | Your own local network: your phone downloads the file directly from your PC | Close the window |
| Report a problem | Only when you click it | Opens a GitHub issue page in your browser. The diagnostic text goes to your clipboard; you decide what to paste | Don't click it |

These requests contain no personal data, apart from what any web request reveals (your IP address and
a "Satsuma/<version>" user agent). The privacy policies of
[GitHub](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement),
[Hugging Face](https://huggingface.co/privacy) and [Google](https://policies.google.com/privacy)
apply to those services.

## What Satsuma stores on your PC

- Settings, presets and watch folders: `%APPDATA%\Satsuma`
- A log of errors (file paths and error messages, kept under 1 MB): `%APPDATA%\Satsuma\satsuma.log`
- The AI pack and models: `%LOCALAPPDATA%\Satsuma`

Uninstalling removes the program. Delete the two folders above to remove everything else.

## Changes to your system

Satsuma tells you what it changes during installation and can undo all of it. It adds a right-click menu entry,
a Send To shortcut, a Start menu shortcut, starts with Windows, and sets a low-level mouse hook while running.
You can switch each of these off in Settings, and the uninstaller removes them all.
