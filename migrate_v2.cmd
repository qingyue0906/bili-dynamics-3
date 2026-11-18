@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    echo Usage: migrate_v2.cmd "C:\path\to\bili-dynamics-2" [--dry-run] [--move] ...
    pause
    exit /b 1
)
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m bili3 migrate --src "%~1" %2 %3 %4 %5 %6
pause
