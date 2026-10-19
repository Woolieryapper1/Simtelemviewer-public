@echo off
rem ===========================================================================
rem  One-time setup (and after every update): double-click this file.
rem  Installs the app and the exact package versions it needs into .venv\
rem  in this folder. Nothing is installed system-wide, so it's safe to run again.
rem  Only Python is needed - the web pages come ready-built inside the package.
rem ===========================================================================
cd /d "%~dp0"

echo.
echo  [1/3] Checking Python 3.14 is installed...
py -3.14 --version >nul 2>nul
if errorlevel 1 (
    echo.
    echo  Python 3.14 wasn't found. Install it first - see README.md, Getting started.
    goto failed
)

echo.
echo  [2/3] Installing the packages the app uses...
if not exist .venv\Scripts\python.exe (
    py -3.14 -m venv .venv
    if errorlevel 1 goto failed
)
rem The lock file has the exact version of every package, so everyone gets the same
.venv\Scripts\python -m pip install --disable-pip-version-check -r backend\requirements.lock.txt
if errorlevel 1 goto failed

echo.
echo  [3/3] Installing the app...
rem -e ("editable"): runs the code straight from this folder, so updates from git pull apply
.venv\Scripts\python -m pip install --disable-pip-version-check --no-deps -e .
if errorlevel 1 goto failed

echo.
echo  ============================================================
echo   All set. Double-click start.bat to open the app.
echo  ============================================================
echo.
pause
exit /b 0

:failed
echo.
echo  Setup didn't finish - see the message above.
echo  (README.md has a "Something went wrong" section.)
echo.
pause
exit /b 1
