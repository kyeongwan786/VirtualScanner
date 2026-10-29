#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
PYTHON_BIN="${RECEIVER_PYTHON:-python3}"
VENV_DIR="$SCRIPT_DIR/.venv-receiver-macos"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3을 찾지 못했습니다. Python 3.12 설치 후 다시 실행하세요."
  read -r -p "Enter를 누르면 닫힙니다. "
  exit 1
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
if ! cmp -s requirements_receiver.txt "$VENV_DIR/requirements-installed.txt"; then
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r requirements_receiver.txt
  cp requirements_receiver.txt "$VENV_DIR/requirements-installed.txt"
fi

"$VENV_DIR/bin/python" package_receiver_macos.py
echo "macOS 앱 생성 완료: $SCRIPT_DIR/dist/new_work_receiver_v3.app"
read -r -p "Enter를 누르면 닫힙니다. "
