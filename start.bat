@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================
echo   QQ-MCP Bridge Server
echo ============================
echo.
python server.py
pause
