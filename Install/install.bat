@echo off
setlocal EnableDelayedExpansion
title QQ-MCP Bridge - Environment Setup
echo.
echo   ================================================
echo     QQ-MCP Bridge v3.0 - Bootstrap Installer
echo   ================================================
echo.
echo   Usage: install.bat [--clean] [--skip-playwright] [--skip-verify]
echo     --clean          Remove existing venv and rebuild from scratch
echo     --skip-playwright  Skip Chromium browser download
echo     --skip-verify      Skip post-install verification
echo.

:: ── Resolve paths ──────────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Get project root (parent of Install/)
for %%I in ("%SCRIPT_DIR%\..") do set "PROJECT_ROOT=%%~fI"

:: ── Step 1: Find Python >= 3.10 ───────────────────────────────────────
echo   Searching for Python ^>= 3.10 ...

:: Try py launcher
set "FOUND_PYTHON="
for /f "usebackq delims=" %%P in (`py -3 -c "import sys; print(sys.executable)" 2^>nul`) do (
    set "FOUND_PYTHON=%%P"
)
if defined FOUND_PYTHON (
    echo   [ OK ] Found via py launcher: !FOUND_PYTHON!
    goto :run_setup
)

:: Try PATH
for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do (
    set "FOUND_PYTHON=%%P"
)
if defined FOUND_PYTHON (
    echo   [ OK ] Found in PATH: !FOUND_PYTHON!
    goto :run_setup
)

:: Try known directories
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" (
        set "FOUND_PYTHON=%%D\python.exe"
    )
)
if defined FOUND_PYTHON (
    echo   [ OK ] Found: !FOUND_PYTHON!
    goto :run_setup
)

for /d %%D in ("%LOCALAPPDATA%\Python\pythoncore-*") do (
    if exist "%%D\python.exe" (
        set "FOUND_PYTHON=%%D\python.exe"
    )
)
if defined FOUND_PYTHON (
    echo   [ OK ] Found: !FOUND_PYTHON!
    goto :run_setup
)

:: Not found
echo   [FAIL] Python ^>= 3.10 not found on this system.
echo.
echo   Please install Python from https://www.python.org/downloads/
echo   IMPORTANT: Check "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:: ── Step 2: Run setup script ───────────────────────────────────────────
:run_setup
echo.
echo   Running setup script...
echo.
"!FOUND_PYTHON!" "%SCRIPT_DIR%\setup_env.py" %*
set EXITCODE=%ERRORLEVEL%

echo.
if %EXITCODE% EQU 0 (
    echo   Setup finished successfully.
) else (
    echo   Setup encountered errors (exit code: %EXITCODE%).
    echo   Check Install\install.log for details.
)
echo.
pause
exit /b %EXITCODE%
