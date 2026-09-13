@echo off
rem Launch the Feishu bridge with a self-check.
rem Keep this file ASCII-only: cmd reads .bat in the system ANSI codepage, so
rem Chinese here shows up as mojibake. All human-readable text comes from
rem scripts\feishu_launch.py, which sets its own stdout to UTF-8.
chcp 65001 >nul
title animechat feishu bridge
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" scripts\feishu_launch.py %*
set "RC=%ERRORLEVEL%"
if not "%~1"=="" goto :end

echo.
echo [exit code %RC%] Press any key to close this window...
pause >nul

:end
exit /b %RC%
