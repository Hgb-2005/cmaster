@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File tools\start-local.ps1
if errorlevel 1 pause
