@echo off
rem ---------------------------------------------------------------------------
rem  PalmSentinel V2 -- Windows launcher.
rem
rem  Prefers the packaged standalone build, which needs no Python at all.  Falls
rem  back to running from this source checkout, and says what that needs before
rem  it tries.  The previous version's launcher guessed at a system Python and
rem  told the user nothing when the required packages were absent, so a
rem  double-click could fail silently; everything below is explicit instead.
rem ---------------------------------------------------------------------------
setlocal
title PalmSentinel V2
cd /d "%~dp0"

set "PACKAGED=dist\PalmSentinelV2\PalmSentinelV2.exe"

if exist "%PACKAGED%" (
    echo [PalmSentinel] Starting the packaged build -- no Python required.
    echo [PalmSentinel]   %PACKAGED%
    start "" "%PACKAGED%" %*
    exit /b 0
)

rem No packaged build: run from source. This path DOES need Python and packages.
echo [PalmSentinel] No packaged build found at %PACKAGED%
echo [PalmSentinel] Falling back to this source checkout, which needs Python 3.11+
echo [PalmSentinel] and the packages listed in requirements.txt.
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [PalmSentinel] ERROR: no "python" on PATH.
    echo.
    echo   This source checkout cannot run without Python. Choose one:
    echo     1. Install Python 3.11 or newer, then:  pip install -r requirements.txt
    echo     2. Build the standalone version, which needs no Python:
    echo            pyinstaller --noconfirm PalmSentinelV2.spec
    echo.
    pause
    exit /b 1
)

rem Preflight: resolve every runtime dependency before opening a window, so a
rem missing package is reported here by name rather than becoming a window that
rem never appears.  The probe reports what it found either way, so a failure of
rem the probe itself is never mistaken for a missing package.
rem The probe uses single quotes only: a double quote inside it would be eaten by
rem the interpreter's own command line, corrupting the probe and making a broken
rem probe look like missing packages.  Its status is read from the interpreter
rem directly -- a `for /f` loop would swallow that status and report success.
set "PROBE=import importlib.util as u,sys; m=[n for n in ('flask','webview','bottle','cv2','numpy','PIL') if u.find_spec(n) is None]; print(','.join(m) if m else 'none'); sys.exit(1 if m else 0)"
set "PROBEOUT=%TEMP%\palmsentinel-probe.txt"
python -c "%PROBE%" > "%PROBEOUT%" 2>&1
if not errorlevel 1 goto preflight_ok
set /p MISSING=<"%PROBEOUT%"
del "%PROBEOUT%" >nul 2>&1
echo.
echo [PalmSentinel] ERROR: Python was found, but it cannot import what this app needs.
echo   Missing packages: %MISSING%
echo.
echo   Install them into that interpreter with:
echo.
echo       pip install -r requirements.txt
echo.
echo   Or use the standalone version, which needs nothing installed.
echo.
pause
exit /b 1

:preflight_ok
del "%PROBEOUT%" >nul 2>&1

echo [PalmSentinel] Python and packages OK. Opening the desktop window...
python desktop_app.py %*
exit /b %errorlevel%
