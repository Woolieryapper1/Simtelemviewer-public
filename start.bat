@echo off
rem ===========================================================================
rem  Starts the app: double-click this file.
rem  The app runs in this window and opens http://127.0.0.1:8000 in your browser.
rem  Leave the window open while you use it; close it to stop the app.
rem ===========================================================================
cd /d "%~dp0"
title FS Telemetry

if not exist .venv\Scripts\python.exe (
    echo.
    echo  The app isn't set up yet - double-click setup.bat first.
    echo.
    pause
    exit /b 1
)

rem Your own folder settings, if you made them (see my-settings.example.bat)
if exist my-settings.bat call my-settings.bat

rem python -m, not fs-telemetry.exe: Windows Application Control can block that launcher
.venv\Scripts\python.exe -m fs_telemetry %*

rem Only reached if the app stopped - keep the window open so any error can be read
echo.
echo  The app has stopped.
pause
