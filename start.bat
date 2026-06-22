@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================
echo   QQ-MCP Bridge v3.0
echo ============================
echo.

:: 使用项目本地虚拟环境
set "VENV_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment not found.
    echo Please run Install\install.bat first to set up the environment.
    pause
    exit /b 1
)

"%VENV_PYTHON%" server.py
pause
