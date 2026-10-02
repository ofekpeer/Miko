@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.13 -X utf8 "%~dp0start_miko.py"
if errorlevel 1 pause
