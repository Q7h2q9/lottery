@echo off
REM multidraw - 双击启动 Web UI
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
