@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.13 -X utf8 "%~dp0check_miko_camera.py"
