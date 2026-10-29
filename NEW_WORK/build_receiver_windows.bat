@echo off
setlocal
cd /d "%~dp0"

echo NEW_WORK Receiver build: clean virtual environment, no pip cache
if exist build rmdir /s /q build
if exist dist\new_work_receiver_v3 rmdir /s /q dist\new_work_receiver_v3
if exist .venv-receiver rmdir /s /q .venv-receiver
py -3.12 -m venv .venv-receiver
if errorlevel 1 goto :build_failed

call .venv-receiver\Scripts\activate.bat
python -m pip install --no-cache-dir --index-url https://pypi.org/simple --upgrade pip
if errorlevel 1 goto :build_failed
python -m pip install --no-cache-dir --index-url https://pypi.org/simple -r requirements_receiver.txt
if errorlevel 1 goto :build_failed

python package_receiver_windows.py
if errorlevel 1 goto :build_failed

echo.
echo Build complete: dist\new_work_receiver_v3\new_work_receiver_v3.exe
pause
exit /b 0

:build_failed
set BUILD_EXIT=%errorlevel%
echo.
echo Build failed with exit code %BUILD_EXIT%.
pause
exit /b %BUILD_EXIT%
