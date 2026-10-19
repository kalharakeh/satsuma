# Publishing Satsuma on winget

## First version (once, by hand)

1. Create a GitHub token: GitHub → Settings → Developer settings → Personal access tokens → *Tokens (classic)*,
   scope **public_repo**.
2. On a Windows PC: `winget install Microsoft.WingetCreate`
3. Submit the manifests (generated with `make_manifests.py`, or the ready folder you were given):
   `wingetcreate submit --token <token> <path>\manifests\k\KassemAlharakeh\Satsuma\<version>`
4. This opens a pull request in microsoft/winget-pkgs. A Microsoft bot asks you to accept their contributor
   license agreement with a comment, and the automated validation then installs Satsuma in a sandbox.
   Review usually takes a few days.

## Every version after that (automatic)

Add the same token as a repository secret named `WINGET_TOKEN` (repo → Settings → Secrets and variables →
Actions). The release build then submits each new version with `wingetcreate update`.
