@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b %errorlevel%
)

.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 exit /b %errorlevel%
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%

.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --console --name new_work_ocr ^
  --collect-all paddleocr ^
  --collect-all paddlex ^
  --collect-all paddle ^
  --collect-data cv2 ^
  run_ocr.py
exit /b %errorlevel%
