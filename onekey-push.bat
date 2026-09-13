@echo off
rem One-key: export local sticker library, commit, push to your GitHub repo.
rem All readable messages are printed by scripts\commit_pack.py (UTF-8).
rem This file stays ASCII: cmd mis-parses multibyte chars inside ( ) blocks.
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" goto novenv
if not exist "%ROOT%scripts\commit_pack.py" goto noscript
"%PY%" "%ROOT%scripts\commit_pack.py" %*
if errorlevel 1 goto fail
echo.
echo [onekey-push] OK.
goto end
:novenv
echo [onekey-push] ERROR: .venv not found. Run scripts\install.ps1 first.
goto endfail
:noscript
echo [onekey-push] ERROR: scripts\commit_pack.py not found.
goto endfail
:fail
echo.
echo [onekey-push] FAILED, read the message above.
:endfail
set "RC=1"
goto end2
:end
set "RC=0"
:end2
echo.
pause
exit /b %RC%
