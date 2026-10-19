@echo off
rem Removes the right-click entry, Send To and Start menu shortcuts, autostart, and closes Satsuma.
"%~dp0.venv\Scripts\python.exe" "%~dp0app.py" --uninstall
echo Satsuma removed from Windows. You can now delete %~dp0
pause
