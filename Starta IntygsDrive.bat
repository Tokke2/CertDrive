@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

:check_python
set "PYTHON_LAUNCHER="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON_LAUNCHER=py -3"

if not defined PYTHON_LAUNCHER (
    python3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYTHON_LAUNCHER=python3"
)
if not defined PYTHON_LAUNCHER (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYTHON_LAUNCHER=python"
)

if defined PYTHON_LAUNCHER goto start_app

echo Python hittades inte. IntygsDrive behöver Python för att starta.
choice /C YN /N /M "Vill du öppna den officiella sidan för senaste Python? [Y/N] "
if errorlevel 2 exit /b 0
start "" "https://www.python.org/downloads/windows/"
echo Installera Python fran sidan och atervand sedan hit.
echo Om installationsprogrammet fragar, valj att lagga till Python i PATH.
pause >nul
goto check_python

:start_app
%PYTHON_LAUNCHER% "%~dp0main.pyw" %*
exit /b %errorlevel%
