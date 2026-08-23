#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${COZE_WORKSPACE_PATH:-$(dirname "$SCRIPT_DIR")}"
PORT=5108

while getopts "p:h" opt; do
  case "$opt" in
    p) PORT="$OPTARG" ;;
    h)
      echo "用法: $0 -p <端口>"
      exit 0
      ;;
    *) exit 1 ;;
  esac
done

find_env_file() {
  local candidates=(
    "$WORK_DIR/.env.local"
    "$WORK_DIR/.env"
    "$(dirname "$WORK_DIR")/IP-protral/.env.local"
    "$(dirname "$WORK_DIR")/IP-protral/.env"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [ -f "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

env_file="$(find_env_file || true)"
if [ -n "$env_file" ]; then
  echo "[startup] 加载环境文件: $env_file"
  set -a
  # shellcheck disable=SC1090
  source "$env_file"
  set +a
fi

if [ -z "${PGDATABASE_URL:-}" ] && [ -n "${DATABASE_URL:-}" ]; then
  export PGDATABASE_URL="$DATABASE_URL"
fi
if [ -z "${DATABASE_URL:-}" ] && [ -n "${PGDATABASE_URL:-}" ]; then
  export DATABASE_URL="$PGDATABASE_URL"
fi

if [ "${SKIP_ENV_VALIDATION:-0}" != "1" ] && [ -z "${PGDATABASE_URL:-}" ]; then
  echo "[startup] 3-andun-search 缺少 PGDATABASE_URL" >&2
  exit 1
fi

if [ -z "${pm_id:-}" ] && command -v lsof >/dev/null 2>&1 && lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "[startup] 端口 $PORT 已被占用，拒绝重复启动 3-andun-search" >&2
  exit 1
fi

export PYTHONDONTWRITEBYTECODE=1

PYTHON_BIN="python3"
if [ -x "$WORK_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$WORK_DIR/.venv/bin/python"
elif [ -x "$(dirname "$WORK_DIR")/3-product-search/.venv/bin/python" ]; then
  PYTHON_BIN="$(dirname "$WORK_DIR")/3-product-search/.venv/bin/python"
elif [ -x "$(dirname "$WORK_DIR")/3-search/.venv/bin/python" ]; then
  PYTHON_BIN="$(dirname "$WORK_DIR")/3-search/.venv/bin/python"
fi

exec "$PYTHON_BIN" "$WORK_DIR/src/main.py" -m http -p "$PORT"
