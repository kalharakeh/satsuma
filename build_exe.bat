@echo off
rem Optional: build dist\Satsuma\Satsuma.exe locally (the GitHub workflow does this for releases).
rem Run install.bat first so the .venv exists.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pip install pyinstaller --quiet
".venv\Scripts\pyinstaller.exe" packaging\satsuma.spec --noconfirm --distpath dist --workpath build
echo.
echo Built dist\Satsuma\Satsuma.exe. Put ffmpeg.exe in dist\Satsuma\bin\ and run: dist\Satsuma\Satsuma.exe --install
pause
