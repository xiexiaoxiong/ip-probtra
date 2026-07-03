#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${COZE_WORKSPACE_PATH:-$(dirname "$SCRIPT_DIR")}"
PORT=5107

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

load_unified_env() {
  local env_file
  env_file="$(find_env_file || true)"
  if [ -n "$env_file" ]; then
    echo "[startup] 加载环境文件: $env_file"
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  else
    echo "[startup] 未找到 .env/.env.local，继续使用当前 shell 环境"
  fi
}

normalize_env_aliases() {
  if [ -z "${PGDATABASE_URL:-}" ] && [ -n "${DATABASE_URL:-}" ]; then
    export PGDATABASE_URL="$DATABASE_URL"
  fi
  if [ -z "${DATABASE_URL:-}" ] && [ -n "${PGDATABASE_URL:-}" ]; then
    export DATABASE_URL="$PGDATABASE_URL"
  fi
}

validate_required_env() {
  if [ "${SKIP_ENV_VALIDATION:-0}" = "1" ]; then
    echo "[startup] 已跳过环境变量校验"
    return 0
  fi

  local missing=()
  if [ -z "${PGDATABASE_URL:-}" ]; then
    missing+=("PGDATABASE_URL")
  fi

  if [ ${#missing[@]} -gt 0 ]; then
    echo "[startup] 3-product-search 缺少关键环境变量: ${missing[*]}" >&2
    exit 1
  fi
}

check_manual_port_conflict() {
  if [ -n "${pm_id:-}" ]; then
    return 0
  fi
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  if lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "[startup] 端口 $PORT 已被占用，拒绝重复启动 3-product-search" >&2
    lsof -iTCP:"$PORT" -sTCP:LISTEN >&2 || true
    exit 1
  fi
}

load_unified_env
normalize_env_aliases
validate_required_env
check_manual_port_conflict
export PYTHONDONTWRITEBYTECODE=1

PYTHON_BIN="python3"
if [ -x "${WORK_DIR}/.venv/bin/python" ]; then
  PYTHON_BIN="${WORK_DIR}/.venv/bin/python"
elif [ -x "$(dirname "$WORK_DIR")/3-search/.venv/bin/python" ]; then
  PYTHON_BIN="$(dirname "$WORK_DIR")/3-search/.venv/bin/python"
fi

exec "${PYTHON_BIN}" "${WORK_DIR}/src/main.py" -m http -p "$PORT"
