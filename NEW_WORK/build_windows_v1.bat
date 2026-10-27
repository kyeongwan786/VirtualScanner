@echo off
setlocal
cd /d "%~dp0"
set NEW_WORK_BUILD_VERSION=v1
call build_windows.bat
exit /b %errorlevel%
