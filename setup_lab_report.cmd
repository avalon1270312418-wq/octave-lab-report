@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"

if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sys; assert sys.version_info >= (3, 10)"
if errorlevel 1 goto failed
if defined OCTAVE_LAB_WHEELHOUSE (
    ".venv\Scripts\python.exe" -m pip install --no-index --find-links "%OCTAVE_LAB_WHEELHOUSE%" -r requirements.txt
) else (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m octave_lab_report --check
if errorlevel 1 goto failed
pause
exit /b 0

:failed
echo Check Python 3.10+, network access for pip and the GNU Octave installation.
pause
exit /b 1
