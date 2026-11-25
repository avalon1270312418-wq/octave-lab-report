@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
if defined OCTAVE_LAB_PYTHON (
    "%OCTAVE_LAB_PYTHON%" -m octave_lab_report %*
    if errorlevel 1 goto failed
    exit /b 0
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m octave_lab_report %*
    if errorlevel 1 goto failed
    exit /b 0
)
py -3 -c "import sys, docx, PIL, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    py -3 -m octave_lab_report %*
    if errorlevel 1 goto failed
    exit /b 0
)
python -c "import sys, docx, PIL, tkinter; assert sys.version_info >= (3, 10)" >nul 2>&1
if not errorlevel 1 (
    python -m octave_lab_report %*
    if errorlevel 1 goto failed
    exit /b 0
)
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -NonInteractive -Command "Get-AppxPackage -Name 'OpenAI.CodexPrimaryRuntime*' -ErrorAction SilentlyContinue | Sort-Object Version -Descending | Select-Object -First 1 -ExpandProperty InstallLocation"`) do set "OCTAVE_LAB_RUNTIME=%%P\dependencies\python\python.exe"
if exist "%OCTAVE_LAB_RUNTIME%" (
    "%OCTAVE_LAB_RUNTIME%" -m octave_lab_report %*
    if errorlevel 1 goto failed
    exit /b 0
)
echo Python 3.10+ with Tk, python-docx and Pillow is required.
echo After installing Python, run: python -m pip install -r requirements.txt
echo Or double-click setup_lab_report.cmd to create a local Python environment.
echo You can select a Python installation with OCTAVE_LAB_PYTHON.
:failed
if "%~1"=="" pause
exit /b 1
