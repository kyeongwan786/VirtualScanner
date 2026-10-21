@echo off
setlocal
cd /d "%~dp0"

if exist .venv-build rmdir /s /q .venv-build
py -3.12 -m venv .venv-build
if errorlevel 1 goto :build_failed

call .venv-build\Scripts\activate.bat
rem Skip pip's local wheel cache so stale or damaged cached archives cannot
rem trigger a wheel hash mismatch during this clean build.
python -m pip install --no-cache-dir --upgrade pip
if errorlevel 1 goto :build_failed
python -m pip install --no-cache-dir -r requirements.txt
if errorlevel 1 goto :build_failed

python package_windows.py
if errorlevel 1 goto :build_failed

echo.
echo Build complete: dist\new_work.exe
pause
exit /b 0

:build_failed
set BUILD_EXIT=%errorlevel%
echo.
echo Build failed with exit code %BUILD_EXIT%.
pause
exit /b %BUILD_EXIT%
