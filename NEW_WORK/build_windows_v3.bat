@echo off
setlocal
set "NEW_WORK_BUILD_VERSION=v3"
call "%~dp0build_windows.bat"
exit /b %errorlevel%
