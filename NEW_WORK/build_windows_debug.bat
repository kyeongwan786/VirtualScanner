@echo off
setlocal
set NEW_WORK_DEBUG=1
call "%~dp0build_windows.bat"
exit /b %errorlevel%
