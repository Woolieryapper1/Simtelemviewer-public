@echo off
rem ===========================================================================
rem  Only needed if the app can't find your files.
rem
rem  1. Copy this file and name the copy  my-settings.bat
rem  2. Right-click my-settings.bat > Edit (opens Notepad)
rem  3. Delete "rem " in front of the lines you need and put in your own folder
rem  4. Save, close the app's window, and start it again with start.bat
rem     (the window lists the folders it's using, so you can check)
rem
rem  my-settings.bat is yours only - it's never uploaded to GitHub.
rem  No quotes around the folder, even if it has spaces in it.
rem ===========================================================================

rem The folder with AC's .ld / .ldx files (sub-folders are searched too). telemetrick
rem exports into Assetto Corsa\apps\telemetrick\exported, CSP's own logger into
rem Assetto Corsa\telemetry: point it at the Assetto Corsa folder to get both.
rem set FS_TELEMETRY_DIR=D:\Telemetry\exported

rem The folder iRacing saves .ibt telemetry files into
rem set FS_IRACING_DIR=D:\iRacing\telemetry

rem The folder with CSV telemetry files (default: the csv folder inside the app's data folder below)
rem set FS_CSV_DIR=D:\Telemetry\csv

rem The Assetto Corsa install folder (for drawing track outlines and reading car steering ratios)
rem set FS_AC_DIR=D:\SteamLibrary\steamapps\common\assettocorsa

rem Your Assetto Corsa setups folder (for importing setups)
rem set FS_AC_SETUPS_DIR=C:\Users\YourName\Documents\Assetto Corsa\setups

rem Where the app keeps its saved layouts, setup library and extra tracks and cars
rem (default: a "FS Telemetry" folder in your user folder)
rem set FS_DATA_DIR=D:\FS Telemetry
