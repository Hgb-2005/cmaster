@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File tools\install-compiler.ps1
if errorlevel 1 echo [CMaster] Installation failed. See the message above and retry.
pause
