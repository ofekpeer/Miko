@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.13 -X utf8 "%~dp0collect_miko_logs.py"
if errorlevel 9009 echo Python 3.13 was not found by the py launcher.
pause
