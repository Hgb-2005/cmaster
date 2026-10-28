@echo off
setlocal
title CMaster - C Learning Platform
set "DIR=%~dp0"
set "PORT=5001"
set "URL=http://127.0.0.1:%PORT%"

cd /d "%DIR%"

rem ---- 1. already running? just open the page -------------------------
powershell -NoProfile -Command "if ((Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue)) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
    echo [CMaster] Server already running, opening page...
    start "" "%URL%"
    timeout /t 2 >nul
    exit /b 0
)

rem ---- 2. python venv -------------------------------------------------
if not exist "runtime\venv\Scripts\python.exe" (
    echo [CMaster] First run: creating virtual environment...
    python -m venv runtime\venv
    if errorlevel 1 (
        echo.
        echo [ERROR] Cannot create venv. Is Python 3.9+ on PATH?
        pause
        exit /b 1
    )
    runtime\venv\Scripts\python.exe -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERROR] pip install failed. Check your network.
        pause
        exit /b 1
    )
)

rem ---- 3. compiler check ---------------------------------------------
if not exist "runtime\w64devkit\bin\gcc.exe" (
    if not exist "data\courses.json" (
        echo [ERROR] No course data found. Run: python tools\build_content.py
        pause
        exit /b 1
    )
    echo [WARN] runtime\w64devkit\bin\gcc.exe not found.
    echo        Lessons still work, but judging will fail.
    echo        See README.md for how to install the portable toolchain.
    echo.
)

rem ---- 4. open browser a bit later, then run ---------------------------
start "" /min powershell -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process '%URL%'"

echo [CMaster] Starting on %URL%
echo [CMaster] Press Ctrl+C to stop.
echo.
set "CMASTER_PORT=%PORT%"
runtime\venv\Scripts\python.exe app.py

echo.
echo [CMaster] Server stopped.
pause
