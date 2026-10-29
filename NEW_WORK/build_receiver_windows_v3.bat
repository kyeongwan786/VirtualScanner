@echo off
setlocal
call "%~dp0build_receiver_windows.bat"
exit /b %errorlevel%
