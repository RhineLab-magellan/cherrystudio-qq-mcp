@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================
echo   QQ-MCP Bridge Server
echo ============================
echo.
""C:\Users\RHineLAB\AppData\Local\Python\pythoncore-3.14-64\python.exe"" server.py
pause
