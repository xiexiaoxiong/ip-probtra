#!/bin/bash

set -e
# 导出环境变量

WORK_DIR="${COZE_WORKSPACE_PATH:-.}"
PORT=8000

usage() {
  echo "用法: $0 -p <端口>"
}

while getopts "p:h" opt; do
  case "$opt" in
    p)
      PORT="$OPTARG"
      ;;
    h)
      usage
      exit 0
      ;;
    \?)
      echo "无效选项: -$OPTARG"
      usage
      exit 1
      ;;
  esac
done

if [ -f "${WORK_DIR}/.venv/bin/activate" ]; then
  source "${WORK_DIR}/.venv/bin/activate"
fi

PYTHON_BIN="python3"
if [ -x "${WORK_DIR}/.venv/bin/python" ]; then
  PYTHON_BIN="${WORK_DIR}/.venv/bin/python"
fi

"${PYTHON_BIN}" "${WORK_DIR}/src/main.py" -m http -p "$PORT"
