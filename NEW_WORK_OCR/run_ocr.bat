@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b %errorlevel%
)

if not exist ".venv\.ocr-deps-ready" (
  .venv\Scripts\python.exe -m pip install --upgrade pip
  if errorlevel 1 exit /b %errorlevel%
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 exit /b %errorlevel%
  type nul > ".venv\.ocr-deps-ready"
)

.venv\Scripts\python.exe run_ocr.py %*
exit /b %errorlevel%
