@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.13 -X utf8 "%~dp0install_miko_17.py"
if errorlevel 1 (
  echo Installation failed. See the message above.
) else (
  echo Update installed. Open Start Miko.cmd on your Desktop.
)
pause
