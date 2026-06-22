@echo off
:: QQ-MCP Bridge v3.0 - MCP Server Launcher
:: ==========================================
:: This is the entry point for CherryStudio MCP integration.
:: Configure CherryStudio to call this .bat file as the MCP stdio command.
::
:: IMPORTANT: Run Install\install.bat first to set up the environment.

setlocal

:: ── Resolve project root (where this .bat lives) ──
set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"

:: ── Check virtual environment ──
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo [QQ-MCP Bridge] ERROR: Virtual environment not found.
    echo [QQ-MCP Bridge] Please run Install\install.bat first to set up the environment.
    echo [QQ-MCP Bridge] Expected: %VENV_PYTHON%
    exit /b 1
)

:: ── Change to project root (server.py expects cwd = project root) ──
cd /d "%PROJECT_ROOT%"

:: ── Launch MCP server ──
:: -u : unbuffered stdout/stderr (required for MCP stdio transport)
:: server.py reads JSON-RPC from stdin and writes responses to stdout
"%VENV_PYTHON%" -u server.py
