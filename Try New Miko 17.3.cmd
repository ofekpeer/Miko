@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.13 -X utf8 "%~dp0try_miko_17_3.py"
if errorlevel 1 pause
