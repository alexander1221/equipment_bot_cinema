@echo off
rem Starts setup.ps1 bypassing the ExecutionPolicy check. ASCII only.
rem Before running: install Python 3.12 (winget install -e --id Python.Python.3.12)
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo [setup] Python launcher "py" not found.
    echo [setup] Install Python 3.12: winget install -e --id Python.Python.3.12
    pause
    exit /b 1
)
set PYBIN=py -3.12
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 (
    py -3.13 -c "import sys" >nul 2>nul
    if errorlevel 1 (
        set PYBIN=py -3
    ) else (
        set PYBIN=py -3.13
    )
)
echo [setup] Using %PYBIN%
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -PythonBin "%PYBIN%"
pause
