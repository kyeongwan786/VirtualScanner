@echo off
setlocal
cd /d "%~dp0"
set "BUILD_EXIT="

if not defined NEW_WORK_BUILD_VERSION set "NEW_WORK_BUILD_VERSION=v3"
echo NEW_WORK %NEW_WORK_BUILD_VERSION% build: clean virtual environment, no pip cache, official PyPI index
if exist .venv-build rmdir /s /q .venv-build
py -3.12 -m venv .venv-build
if errorlevel 1 goto :build_failed

set "BUILD_PYTHON=%CD%\.venv-build\Scripts\python.exe"
if not exist "%BUILD_PYTHON%" (
  echo Build Python was not created: %BUILD_PYTHON%
  set "BUILD_EXIT=1"
  goto :build_failed
)
rem Skip pip's local wheel cache so stale or damaged cached archives cannot
rem trigger a wheel hash mismatch during this clean build.
"%BUILD_PYTHON%" -m pip install --no-cache-dir --index-url https://pypi.org/simple --upgrade pip
if errorlevel 1 goto :build_failed
"%BUILD_PYTHON%" -m pip install --no-cache-dir --index-url https://pypi.org/simple -r requirements.txt
if errorlevel 1 goto :build_failed

"%BUILD_PYTHON%" -c "import paddlex; print('PaddleX import OK')"
if errorlevel 1 goto :build_failed
"%BUILD_PYTHON%" package_windows.py
if errorlevel 1 goto :build_failed

echo.
if /I "%NEW_WORK_BUILD_VERSION%"=="v1" (
  echo Build complete: dist\new_work_v1\new_work_v1.exe
) else if /I "%NEW_WORK_BUILD_VERSION%"=="v2" (
  echo Build complete: dist\new_work\new_work.exe
) else (
  echo Build complete: dist\new_work_v3\new_work_v3.exe
)
pause
exit /b 0

:build_failed
if not defined BUILD_EXIT set BUILD_EXIT=%errorlevel%
echo.
echo Build failed with exit code %BUILD_EXIT%.
pause
exit /b %BUILD_EXIT%
