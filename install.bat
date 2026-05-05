@echo off
REM multidraw - 一键安装（双击）
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0install.ps1"
pause
