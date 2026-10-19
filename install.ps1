# Satsuma installer - per-user, no admin needed.
# Copies the app to %LOCALAPPDATA%\Programs\Satsuma (a short, permanent path), so it works
# even when install.bat is run straight from inside the zip.
$ErrorActionPreference = "Continue"
$Src  = $PSScriptRoot
$Dest = Join-Path $env:LOCALAPPDATA "Programs\Satsuma"
Write-Host "`n  Satsuma - offline file converter`n" -ForegroundColor DarkYellow

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "Machine")
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("-3.12", "-3.13", "-3.11", "-3.10")) {
            & py $v -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", $v) }
        }
    }
    return $null
}

# 0. Close a running copy, then copy the app files to the install folder
$vpy  = Join-Path $Dest ".venv\Scripts\python.exe"
$vpyw = Join-Path $Dest ".venv\Scripts\pythonw.exe"
if (Test-Path $vpy) {
    & $vpy (Join-Path $Dest "app.py") --quit 2>$null
    Start-Sleep -Milliseconds 800
}
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
if ((Resolve-Path $Src).Path -ne (Resolve-Path $Dest).Path) {
    Get-ChildItem -LiteralPath $Src -File | Where-Object { $_.Extension -in ".py", ".txt", ".md", ".bat", ".ps1" } |
        Copy-Item -Destination $Dest -Force
    foreach ($d in @("assets", "models")) {
        Copy-Item -LiteralPath (Join-Path $Src $d) -Destination $Dest -Recurse -Force
    }
}
Write-Host "Installing to $Dest"

# 1. Python
$py = Find-Python
if (-not $py) {
    Write-Host "Python 3.10-3.13 not found - installing Python 3.12 with winget..."
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    Refresh-Path
    $py = Find-Python
    if (-not $py) { Write-Host "Couldn't find Python after installing. Open a new terminal and run install.bat again." -ForegroundColor Red; exit 1 }
}
Write-Host "Using Python: $($py -join ' ')"

# 2. Virtual environment + packages
if (-not (Test-Path $vpy)) {
    & $py[0] $py[1] -m venv (Join-Path $Dest ".venv")
}
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip uninstall -y PySide6 PySide6_Addons PySide6_WebEngine PySide6_Pdf 2>$null | Out-Null
& $vpy -m pip install -r (Join-Path $Dest "requirements.txt") -r (Join-Path $Dest "requirements-ai.txt")
if ($LASTEXITCODE -ne 0) { Write-Host "Package install failed - see the errors above." -ForegroundColor Red; exit 1 }

# 3. FFmpeg: a full build is better than the bundled fallback (more codecs)
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "Installing FFmpeg with winget (optional; a bundled copy is used if this fails)..."
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    Refresh-Path
}

# 4. Register right-click menu, Send To, Start menu, autostart - and start it
Start-Process -FilePath $vpyw -ArgumentList "`"$Dest\app.py`" --install" -WorkingDirectory $Dest

Write-Host "`nDone. Satsuma is running in the tray (look for the orange icon)." -ForegroundColor Green
Write-Host "Hold Shift while dragging any file from Explorer, or right-click a file > Convert with Satsuma."
Write-Host "Installed in $Dest - you can delete the zip and the unzipped folder.`n"
