@echo off
setlocal
title RelayScanner Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-RelayScanner.ps1"
set "SETUP_RESULT=%ERRORLEVEL%"
echo.
if not "%SETUP_RESULT%"=="0" echo Setup failed or was cancelled. No existing scanner is overwritten.
pause
exit /b %SETUP_RESULT%
