@echo off
setlocal
cd /d "%~dp0"

py -3.12 -m venv .venv-build
if errorlevel 1 exit /b %errorlevel%

call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
if errorlevel 1 exit /b %errorlevel%
python -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%

python -m PyInstaller --noconfirm --clean --onefile --windowed --name new_work ^
  --collect-all paddleocr ^
  --collect-all paddlex ^
  --collect-all paddle ^
  --collect-data cv2 ^
  run.py
exit /b %errorlevel%
